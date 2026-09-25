"""Regression coverage for required signing keys and default-key forgery."""

import secrets

import pytest
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import create_engine, text


@pytest.mark.parametrize(
    "value", [None, "", "   ", "dev-secret-change-me", " dev-secret-change-me "]
)
def test_startup_rejects_missing_or_public_signing_key(monkeypatch, tmp_path, value):
    from server import create_app

    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    if value is None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY", value)

    with pytest.raises(RuntimeError, match="Set SECRET_KEY"):
        create_app()


@pytest.fixture
def signing_app(monkeypatch, tmp_path):
    from server import create_app

    key = secrets.token_hex(32)
    monkeypatch.setenv("SECRET_KEY", key)
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    app = create_app()
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE Documents (
                id INTEGER PRIMARY KEY, name TEXT, creation TEXT,
                sha256 BLOB, size INTEGER, ownerid INTEGER
            )
        """))
    app.config.update(TESTING=True, _ENGINE=engine)
    yield app, key
    engine.dispose()


@pytest.mark.parametrize("signing_key", ["dev-secret-change-me", "another-key"])
def test_foreign_signing_keys_cannot_authorize_requests(signing_app, signing_key):
    app, _ = signing_app
    token = URLSafeTimedSerializer(signing_key, salt="tatou-auth").dumps(
        {"uid": 1, "login": "owner"}
    )
    response = app.test_client().get(
        "/api/list-documents", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "Invalid token"}


def test_configured_signing_key_authorizes_requests(signing_app):
    app, key = signing_app
    token = URLSafeTimedSerializer(key, salt="tatou-auth").dumps(
        {"uid": 1, "login": "owner"}
    )
    response = app.test_client().get(
        "/api/list-documents", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.get_json() == {"documents": []}

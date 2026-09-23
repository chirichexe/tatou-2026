"""read-watermark adds leak attribution only for the RMAP service account."""

import secrets

import fitz
import pytest
from itsdangerous import URLSafeTimedSerializer
from rmap.keygen import generate_keypair
from sqlalchemy import create_engine, text
from watermarking_utils import apply_watermark

METHOD = "toy-eof"
KEY = "test-watermark-key"


def _pdf(path, secret=None):
    document = fitz.open()
    document.new_page().insert_text((72, 72), "attribution test document")
    data = document.tobytes()
    document.close()
    if secret is not None:
        data = apply_watermark(METHOD, data, secret, KEY)
    path.write_bytes(data)


@pytest.fixture
def attribution_app(tmp_path, monkeypatch):
    key_dir = tmp_path / "keys"
    client_dir = key_dir / "clients"
    client_dir.mkdir(parents=True)
    server_key = generate_keypair("Server", "server@example.test")
    (key_dir / "server_private.asc").write_text(str(server_key))
    (key_dir / "server_public.asc").write_text(str(server_key.pubkey))
    group_key = generate_keypair("Group_07", "group@example.test")
    (client_dir / "Group_07.asc").write_text(str(group_key.pubkey))

    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("RMAP_SERVER_PUBLIC_KEY_PATH", str(key_dir / "server_public.asc"))
    monkeypatch.setenv("RMAP_SERVER_PRIVATE_KEY_PATH", str(key_dir / "server_private.asc"))
    monkeypatch.setenv("RMAP_CLIENT_KEYS_DIR", str(client_dir))
    monkeypatch.delenv("RMAP_SERVER_KEY_PASSPHRASE_FILE", raising=False)
    monkeypatch.delenv("RMAP_SERVER_KEY_PASSPHRASE", raising=False)
    monkeypatch.setenv("RMAP_DOCUMENT_ID", "1")
    monkeypatch.setenv("RMAP_WATERMARK_METHOD", METHOD)
    monkeypatch.setenv("RMAP_WATERMARK_KEY", KEY)
    from server import create_app

    app = create_app()
    storage = app.config["STORAGE_DIR"]
    leaked_secret = "Group_07:" + "a" * 32
    _pdf(storage / "source.pdf")
    _pdf(storage / "leaked.pdf", leaked_secret)
    _pdf(storage / "own.pdf", "my own secret")

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE Documents (
                id INTEGER PRIMARY KEY, name TEXT, path TEXT, ownerid INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE Versions (
                id INTEGER PRIMARY KEY, documentid INTEGER, link TEXT UNIQUE,
                intended_for TEXT, secret TEXT, method TEXT, position TEXT, path TEXT
            )
        """))
        # user 1 = RMAP service account (owns RMAP_DOCUMENT_ID); user 2 = normal user
        conn.execute(text("""
            INSERT INTO Documents (id, name, path, ownerid) VALUES
            (1, 'source.pdf', :src, 1),
            (2, 'leaked.pdf', :leaked, 1),
            (3, 'own.pdf', :own, 2)
        """), {
            "src": str(storage / "source.pdf"),
            "leaked": str(storage / "leaked.pdf"),
            "own": str(storage / "own.pdf"),
        })
        conn.execute(text("""
            INSERT INTO Versions (documentid, link, intended_for, secret, method)
            VALUES (1, :link, 'Group_07', :secret, :method)
        """), {"link": "a" * 32, "secret": leaked_secret, "method": METHOD})
    app.config.update(TESTING=True, _ENGINE=engine)

    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="tatou-auth")

    def headers(uid):
        token = serializer.dumps({"uid": uid, "login": f"u{uid}", "email": None})
        return {"Authorization": f"Bearer {token}", "X-CSRF-Protection": "1"}

    try:
        yield app.test_client(), headers
    finally:
        engine.dispose()


def test_service_account_gets_attribution_for_rmap_copy(attribution_app):
    client, headers = attribution_app
    response = client.post(
        "/api/read-watermark/2", headers=headers(1),
        json={"method": METHOD, "key": KEY},
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["attribution"] == {"intended_for": "Group_07", "link": "a" * 32}


def test_normal_user_gets_no_attribution_field(attribution_app):
    client, headers = attribution_app
    response = client.post(
        "/api/read-watermark/3", headers=headers(2),
        json={"method": METHOD, "key": KEY},
    )
    assert response.status_code == 201
    assert response.get_json() == {
        "documentid": 3, "secret": "my own secret",
        "method": METHOD, "position": None,
    }

from types import SimpleNamespace

import pytest
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import create_engine, text


ROUTES = [
    ("DELETE", "/api/delete-document/42", {}),
    ("DELETE", "/api/delete-document?id=42", {}),
    ("DELETE", "/api/delete-document?documentid=42", {}),
    ("POST", "/api/delete-document", {"json": {"id": 42}}),
]


@pytest.fixture
def document_app(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "delete-document-test-key")
    from server import create_app

    app = create_app()
    engine = create_engine("sqlite://")
    app.config.update(TESTING=True, _ENGINE=engine)
    files = {42: tmp_path / "alice.pdf", 43: tmp_path / "bob.pdf"}
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE Documents (id INTEGER PRIMARY KEY, "
            "ownerid INTEGER NOT NULL, path TEXT NOT NULL)"
        ))
        for doc_id, owner_id in [(42, 7), (43, 9)]:
            files[doc_id].write_bytes(b"test document")
            conn.execute(
                text("INSERT INTO Documents VALUES (:id, :owner, :path)"),
                {"id": doc_id, "owner": owner_id, "path": str(files[doc_id])},
            )

    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="tatou-auth")

    def headers(user_id):
        token = serializer.dumps({"uid": user_id, "login": f"user{user_id}"})
        return {
            "Authorization": f"Bearer {token}",
            "X-CSRF-Protection": "1",
        }

    yield SimpleNamespace(
        app=app, client=app.test_client(), engine=engine, files=files, headers=headers
    )
    engine.dispose()


def assert_documents_unchanged(env):
    with env.engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM Documents ORDER BY id")).scalars().all() == [42, 43]
    for path in env.files.values():
        assert path.read_bytes() == b"test document"


@pytest.mark.parametrize("method,url,kwargs", ROUTES)
@pytest.mark.parametrize("credentials", ["missing", "invalid", "expired"])
def test_delete_requires_valid_auth(document_app, method, url, kwargs, credentials):
    env = document_app
    headers = {"X-CSRF-Protection": "1"}
    if credentials == "invalid":
        headers["Authorization"] = "Bearer invalid-token"
    elif credentials == "expired":
        headers = env.headers(7)
        env.app.config["TOKEN_TTL_SECONDS"] = -1
    response = env.client.open(url, method=method, headers=headers, **kwargs)
    assert response.status_code == 401
    assert_documents_unchanged(env)


@pytest.mark.parametrize("method,url,kwargs", ROUTES)
def test_other_user_cannot_delete_document(document_app, method, url, kwargs):
    env = document_app
    response = env.client.open(url, method=method, headers=env.headers(9), **kwargs)
    assert response.status_code == 404
    assert_documents_unchanged(env)


@pytest.mark.parametrize("method,url,kwargs", ROUTES)
def test_owner_can_delete_document(document_app, method, url, kwargs):
    env = document_app
    response = env.client.open(url, method=method, headers=env.headers(7), **kwargs)
    assert response.status_code == 200
    assert response.get_json()["deleted"] is True
    assert not env.files[42].exists()
    assert env.files[43].read_bytes() == b"test document"
    with env.engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM Documents")).scalars().all() == [43]


@pytest.mark.parametrize("body", [
    "[42]", '"42"', "42", "true", "null", "{", "{}",
    '{"id": true}', '{"id": 1.5}', '{"id": 0}', '{"id": -1}',
    '{"id": "abc"}', '{"id": "1 OR 1=1"}', '{"id": []}',
])
def test_invalid_json_input_cannot_delete(document_app, body):
    env = document_app
    response = env.client.post(
        "/api/delete-document", data=body, content_type="application/json",
        headers=env.headers(7),
    )
    assert response.status_code == 400
    assert_documents_unchanged(env)


def test_body_cannot_override_authenticated_owner(document_app):
    env = document_app
    response = env.client.post(
        "/api/delete-document", headers=env.headers(9),
        json={"id": 42, "uid": 7, "ownerid": 7},
    )
    assert response.status_code == 404
    assert_documents_unchanged(env)


def test_nonexistent_document(document_app):
    env = document_app
    response = env.client.delete("/api/delete-document/999", headers=env.headers(7))
    assert response.status_code == 404
    assert_documents_unchanged(env)


STATE_CHANGING_ROUTES = [
    *[(method, url) for method, url, _kwargs in ROUTES],
    ("POST", "/api/create-user"),
    ("POST", "/api/login"),
    ("POST", "/api/upload-document"),
    ("POST", "/api/create-watermark"),
    ("POST", "/api/create-watermark/42"),
    ("POST", "/api/read-watermark"),
    ("POST", "/api/read-watermark/42"),
    ("POST", "/api/load-plugin"),
]


@pytest.mark.parametrize("method,url", STATE_CHANGING_ROUTES)
@pytest.mark.parametrize("csrf_header", [None, "wrong"])
def test_csrf_rejects_requests_before_endpoint_runs(
    document_app, monkeypatch, method, url, csrf_header,
):
    env = document_app
    headers = env.headers(7)
    headers.pop("X-CSRF-Protection")
    if csrf_header is not None:
        headers["X-CSRF-Protection"] = csrf_header

    def must_not_run(**kwargs):
        pytest.fail("CSRF rejection must happen before the endpoint runs")

    # Check the real Flask routing and before_request hook, with a tripwire
    # in place of each endpoint to prove rejected requests cannot reach it.
    rule, _ = env.app.url_map.bind("localhost").match(url.split("?")[0], method=method)
    monkeypatch.setitem(env.app.view_functions, rule, must_not_run)
    response = env.client.open(url, method=method, headers=headers, json={"id": 42})
    assert response.status_code == 403
    assert response.get_json() == {"error": "CSRF protection header required"}
    assert_documents_unchanged(env)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_csrf_covers_all_mutating_methods(document_app, method):
    response = document_app.client.open("/api/delete-document/42", method=method)
    assert response.status_code == 403
    assert_documents_unchanged(document_app)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_csrf_does_not_block_read_only_requests(document_app, method):
    response = document_app.client.open("/api/get-watermarking-methods", method=method)
    assert response.status_code == 200


@pytest.mark.parametrize("method,url", STATE_CHANGING_ROUTES)
def test_untrusted_cors_preflight_cannot_authorize_csrf_header(document_app, method, url):
    response = document_app.client.options(url, headers={
        "Origin": "https://attacker.example",
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "X-CSRF-Protection, Authorization, Content-Type",
    })
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Credentials" not in response.headers
    assert "Access-Control-Allow-Headers" not in response.headers
    assert_documents_unchanged(document_app)


def test_disabled_plugin_endpoint_with_valid_headers(document_app):
    response = document_app.client.post(
        "/api/load-plugin", headers=document_app.headers(7), json={"filename": "test.pkl"},
    )
    assert response.status_code == 410
    assert_documents_unchanged(document_app)

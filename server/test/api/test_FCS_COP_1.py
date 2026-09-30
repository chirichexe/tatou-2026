"""Second SFR, FDP_ACF.1 (adapted).

The TOE shall ensure that link-based access via get-version only ever serves
the single document tied to a specific, legitimately issued link, and not a
predictable family of links.
"""

import io
import secrets
from types import SimpleNamespace

import fitz
import pytest
from sqlalchemy import create_engine, event, text

from watermarking_methods.add_after_eof import AddAfterEOF

KEY = "test-only-key"


# ---------------------------------------------------------------------------
# Setup: two users, each with their own document and one issued link
# ---------------------------------------------------------------------------

def make_pdf():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Tatou link access fixture")
    data = document.tobytes()
    document.close()
    return data


PDF = make_pdf()


@pytest.fixture
def link_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    import watermarking_utils
    from server import create_app

    # toy-eof is not registered in production, only here
    monkeypatch.setitem(watermarking_utils.METHODS, AddAfterEOF.name, AddAfterEOF())

    app = create_app()

    # in-memory sqlite db with the functions the server expects from mariadb
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def mysql_functions(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("UNHEX", 1, bytes.fromhex)
        connection.create_function(
            "LAST_INSERT_ID", 0,
            lambda: connection.execute("SELECT last_insert_rowid()").fetchone()[0],
        )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE Users (
                id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                login TEXT NOT NULL, hpassword TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE Documents (
                id INTEGER PRIMARY KEY, name TEXT, path TEXT,
                ownerid INTEGER REFERENCES Users(id), sha256 BLOB, size INTEGER,
                creation TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE Versions (
                id INTEGER PRIMARY KEY,
                documentid INTEGER REFERENCES Documents(id),
                link TEXT UNIQUE, intended_for TEXT, secret TEXT,
                method TEXT, position TEXT, path TEXT
            )
        """))
    app.config.update(TESTING=True, _ENGINE=engine)
    client = app.test_client()
    csrf = {"X-CSRF-Protection": "1"}
    users = []
    try:
        for index in range(2):
            # create user
            credentials = {
                "email": f"link-owner-{index}@example.test",
                "password": "test-only-password",
            }
            response = client.post(
                "/api/create-user", headers=csrf,
                json={**credentials, "login": f"link-owner-{index}"},
            )
            assert response.status_code == 201, response.get_json()

            # log in
            response = client.post("/api/login", headers=csrf, json=credentials)
            assert response.status_code == 200, response.get_json()
            headers = {
                **csrf,
                "Authorization": f"Bearer {response.get_json()['token']}",
            }

            # upload one PDF
            response = client.post(
                "/api/upload-document", headers=headers,
                data={"file": (io.BytesIO(PDF), f"link-owner-{index}.pdf")},
            )
            assert response.status_code == 201, response.get_json()
            document_id = response.get_json()["id"]

            # issue one watermark link, with a different secret per user
            secret = f"private-secret-{index}"
            response = client.post(
                f"/api/create-watermark/{document_id}", headers=headers,
                json={"method": "toy-eof", "key": KEY,
                      "secret": secret, "intended_for": f"recipient-{index}"},
            )
            assert response.status_code == 201, response.get_json()

            users.append(SimpleNamespace(
                headers=headers, document_id=document_id,
                secret=secret, version=response.get_json(),
            ))
        yield SimpleNamespace(client=client, users=users)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. A legitimately issued link serves its PDF, without authentication
# ---------------------------------------------------------------------------

def test_issued_link_serves_pdf_without_authentication(link_app):
    link = link_app.users[0].version["link"]
    # valid link
    assert len(link) == 40
    assert all(char in "0123456789abcdef" for char in link)

    # query (without auth)
    response = link_app.client.get(f"/api/get-version/{link}")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# 2. An unissued link, including near neighbours, returns 404
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("make_link", [
    # 1. fresh random token
    lambda issued: secrets.token_hex(20),
    # 2. last character flipped
    lambda issued: issued[:-1] + ("0" if issued[-1] != "0" else "1"),
    # 3. link as a number, plus one
    lambda issued: f"{int(issued, 16) + 1:040x}"[-40:],
    # 4. all zeros
    lambda issued: "0" * 40,
], ids=["random", "last-char-flipped", "plus-one", "all-zero"])
def test_unissued_link_returns_404(link_app, make_link):
    issued = {user.version["link"] for user in link_app.users}
    link = make_link(link_app.users[0].version["link"])
    # never hit the other user's real link by accident
    assert len(link) == 40 and link not in issued

    response = link_app.client.get(f"/api/get-version/{link}")
    assert response.status_code == 404
    assert response.mimetype != "application/pdf"
    assert b"%PDF" not in response.data


# ---------------------------------------------------------------------------
# 3. A link never crosses to another document
# ---------------------------------------------------------------------------

def read_downloaded_secret(client, user, pdf):
    # uploading
    response = client.post(
        "/api/upload-document", headers=user.headers,
        data={"file": (io.BytesIO(pdf), "downloaded.pdf")},
    )
    assert response.status_code == 201, response.get_json()

    # reading the watermark
    response = client.post(
        f"/api/read-watermark/{response.get_json()['id']}", headers=user.headers,
        json={"method": "toy-eof", "key": KEY},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["secret"]


def test_links_do_not_cross_documents(link_app):
    client = link_app.client
    first, second = link_app.users

    # check 2 different documents and 2 different links
    assert first.document_id != second.document_id
    assert first.version["link"] != second.version["link"]

    # use each link for downloading the document
    downloads = []
    for user in link_app.users:
        response = client.get(f"/api/get-version/{user.version['link']}")
        assert response.status_code == 200
        downloads.append(response.data)

    # check if they are different
    assert downloads[0] != downloads[1]

    # each link carries the watermark issued for its own document only
    for user, pdf in zip(link_app.users, downloads, strict=True):
        other = second if user is first else first
        # read the secret
        secret = read_downloaded_secret(client, user, pdf)
        assert secret == user.secret
        assert secret != other.secret

import hashlib
import io
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import fitz
import pytest
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import create_engine, event, text


def make_pdf(*, encrypted=False):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Tatou upload validation fixture")
    options = {}
    if encrypted:
        options = {
            "encryption": fitz.PDF_ENCRYPT_AES_256,
            "owner_pw": "owner-password",
            "user_pw": "user-password",
        }
    data = document.tobytes(**options)
    document.close()
    return data


def make_empty_pdf():
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n",
    ]
    parts = [b"%PDF-1.4\n"]
    offsets = []
    for item in objects:
        offsets.append(sum(map(len, parts)))
        parts.append(item)
    xref_offset = sum(map(len, parts))
    parts.extend([
        b"xref\n0 3\n0000000000 65535 f \n",
        b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets),
        f"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(),
    ])
    return b"".join(parts)


PDF = make_pdf()
WATERMARK = {"method": "toy-eof", "intended_for": "reader", "secret": "secret", "key": "key"}
OPERATIONS = [
    ("GET", "/api/get-document/42", {}),
    ("GET", "/api/get-version/shared", {}),
    ("POST", "/api/create-watermark/42", {"json": WATERMARK}),
    ("POST", "/api/read-watermark/42", {"json": WATERMARK}),
    ("DELETE", "/api/delete-document/42", {}),
]


@pytest.fixture
def file_app(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("STORAGE_DIR", str(storage))
    monkeypatch.setenv("SECRET_KEY", "file-path-test-key")
    import server

    app = server.create_app()
    engine = create_engine("sqlite://")

    # Support the small number of MySQL functions used by these endpoints.
    @event.listens_for(engine, "connect")
    def mysql_functions(connection, _record):
        connection.create_function("UNHEX", 1, bytes.fromhex)
        connection.create_function(
            "LAST_INSERT_ID", 0,
            lambda: connection.execute("SELECT last_insert_rowid()").fetchone()[0],
        )

    original = storage / "legacy-login" / "original.pdf"
    original.parent.mkdir()
    original.write_bytes(PDF)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside sentinel")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE Documents (
                id INTEGER PRIMARY KEY, name TEXT, path TEXT, ownerid INTEGER,
                sha256 BLOB, size INTEGER, creation TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE Versions (
                id INTEGER PRIMARY KEY, documentid INTEGER, link TEXT,
                intended_for TEXT, secret TEXT, method TEXT, position TEXT, path TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO Documents (id, name, path, ownerid, sha256, size)
            VALUES (42, 'original.pdf', :path, 7, :sha, :size)
        """), {"path": str(original), "sha": hashlib.sha256(PDF).digest(), "size": len(PDF)})
        conn.execute(text("""
            INSERT INTO Versions (id, documentid, link, path)
            VALUES (1, 42, 'shared', :path)
        """), {"path": str(original)})
    app.config.update(TESTING=True, _ENGINE=engine)
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="tatou-auth")

    def headers(login="alice", uid=7):
        token = serializer.dumps({"uid": uid, "login": login})
        return {
            "Authorization": f"Bearer {token}",
            "X-CSRF-Protection": "1",
        }

    yield SimpleNamespace(
        app=app, client=app.test_client(), engine=engine, storage=storage, original=original,
        outside=outside, headers=headers, server=server,
    )
    engine.dispose()


def set_paths(env, value):
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE Documents SET path = :path WHERE id = 42"), {"path": value})
        conn.execute(text("UPDATE Versions SET path = :path WHERE id = 1"), {"path": value})


def document_path(env, document_id):
    with env.engine.connect() as conn:
        return Path(conn.execute(
            text("SELECT path FROM Documents WHERE id = :id"), {"id": document_id}
        ).scalar_one())


def upload(env, filename="report.pdf", content=PDF, login="alice"):
    return env.client.post(
        "/api/upload-document", headers=env.headers(login),
        data={"file": (io.BytesIO(content), filename)},
    )


def make_symlink(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        if os.name != "nt" or not target.is_dir():
            pytest.skip(f"Symlink creation unavailable: {error}")
        # Windows junctions exercise directory redirects without symlink privileges.
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference = 'Stop'; "
             "New-Item -ItemType Junction -Path $env:TATOU_TEST_LINK "
             "-Target $env:TATOU_TEST_TARGET | Out-Null"],
            env={**os.environ, "TATOU_TEST_LINK": str(link), "TATOU_TEST_TARGET": str(target)},
            check=True, capture_output=True, text=True,
        )


@pytest.mark.parametrize("method,url,kwargs", OPERATIONS)
@pytest.mark.parametrize("path_kind", ["absolute", "traversal", "prefix", "empty", "null", "root"])
def test_invalid_stored_paths_block_all_operations(file_app, monkeypatch, method, url, kwargs, path_kind):
    env = file_app
    paths = {
        "absolute": str(env.outside), "traversal": "../outside.pdf",
        "prefix": str(env.storage.parent / "storage-other" / "outside.pdf"),
        "empty": "", "null": "bad\x00.pdf", "root": str(env.storage),
    }
    set_paths(env, paths[path_kind])
    spies = []
    for name in ["is_watermarking_applicable", "apply_watermark", "read_watermark"]:
        spy = Mock(side_effect=AssertionError("Watermark code must not run"))
        monkeypatch.setattr(env.server.WMUtils, name, spy)
        spies.append(spy)
    response = env.client.open(url, method=method, headers=env.headers(), **kwargs)
    assert response.status_code == 500, response.get_json()
    assert env.outside.read_bytes() == b"outside sentinel"
    assert env.original.read_bytes() == PDF
    with env.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM Documents")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM Versions")).scalar_one() == 1
    for spy in spies:
        spy.assert_not_called()


@pytest.mark.parametrize("method,url,kwargs", OPERATIONS)
def test_symlink_to_outside_file_is_rejected(file_app, method, url, kwargs):
    env = file_app
    link = env.storage / "escape"
    make_symlink(link, env.outside.parent)
    set_paths(env, str(link / env.outside.name))
    response = env.client.open(url, method=method, headers=env.headers(), **kwargs)
    assert response.status_code == 500
    assert env.outside.read_bytes() == b"outside sentinel"


@pytest.mark.parametrize("filename,login", [
    ("report.pdf", "alice"), ("../../outside.pdf", "../../escape"),
    (r"..\..\outside.pdf", r"..\..\escape"), ("/tmp/outside.pdf", "/tmp/escape"),
])
def test_upload_uses_numeric_owner_and_generated_name(file_app, filename, login):
    env = file_app
    response = upload(env, filename=filename, login=login)
    assert response.status_code == 201, response.get_json()
    path = document_path(env, response.get_json()["id"])
    assert path.parent == env.storage / "files" / "7"
    assert len(path.stem) == 32
    assert path.read_bytes() == PDF
    assert not any(item.name.startswith(".upload-") for item in path.parent.iterdir())
    assert env.outside.read_bytes() == b"outside sentinel"


def test_empty_sanitized_filename_is_rejected(file_app):
    response = upload(file_app, filename="..")
    assert response.status_code == 400
    assert response.get_json() == {"error": "lowercase .pdf extension required"}
    assert not (file_app.storage / "files").exists()


@pytest.mark.parametrize("filename", [
    "document.PDF", "document.Pdf", "document", ".pdf",
    "document.pdf.exe", "document.pdf.txt", "document.exe.pdf",
    "document.pdf.pdf",
])
def test_upload_requires_exact_lowercase_pdf_extension(file_app, filename):
    response = upload(file_app, filename=filename)
    assert response.status_code == 400
    assert response.get_json() == {"error": "lowercase .pdf extension required"}
    assert not (file_app.storage / "files").exists()


@pytest.mark.parametrize("content", [
    b"cos\n(S'payload)\nsystem\n.",
    b"<html><body>not a PDF</body></html>",
    b"",
    b"%PDF-",
    b"%PDF-1.7\nmalformed",
    make_empty_pdf(),
    make_pdf(encrypted=True),
])
def test_upload_rejects_invalid_pdf_content_without_residue(file_app, content):
    response = upload(file_app, filename="payload.pdf", content=content)
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid PDF document"}
    user_dir = file_app.storage / "files" / "7"
    assert not user_dir.exists() or list(user_dir.iterdir()) == []
    with file_app.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM Documents")).scalar_one() == 1


def test_upload_removes_published_file_when_database_insert_fails(file_app):
    failing_engine = Mock()
    failing_engine.begin.side_effect = RuntimeError("database unavailable")
    file_app.app.config["_ENGINE"] = failing_engine

    response = upload(file_app)

    assert response.status_code == 503
    user_dir = file_app.storage / "files" / "7"
    assert user_dir.is_dir()
    assert list(user_dir.iterdir()) == []


@pytest.mark.parametrize("operation", ["upload", "watermark"])
def test_output_directory_symlink_cannot_escape(file_app, operation):
    env = file_app
    outside_dir = env.storage.parent / "outside-dir"
    outside_dir.mkdir()
    if operation == "upload":
        make_symlink(env.storage / "files", outside_dir)
        response = upload(env)
    else:
        make_symlink(env.original.parent / "watermarks", outside_dir)
        response = env.client.post(
            "/api/create-watermark/42", headers=env.headers(), json=WATERMARK,
        )
    assert response.status_code == 500
    assert list(outside_dir.iterdir()) == []


def test_upload_does_not_overwrite_existing_file(file_app, monkeypatch):
    env = file_app
    monkeypatch.setattr(env.server, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    first = upload(env)
    assert first.status_code == 201
    path = document_path(env, first.get_json()["id"])
    assert upload(env, content=PDF).status_code == 500
    assert path.read_bytes() == PDF


def test_watermark_names_are_safe_unique_and_downloadable(file_app):
    env = file_app
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE Documents SET name = :name"), {"name": "../../odd\\name.pdf"})
    created = []
    for _ in range(2):
        response = env.client.post(
            "/api/create-watermark/42", headers=env.headers(),
            json={**WATERMARK, "intended_for": "../../reader\\name"},
        )
        assert response.status_code == 201, response.get_json()
        created.append(response.get_json())
    assert created[0]["filename"] != created[1]["filename"]
    for result in created:
        assert "/" not in result["filename"] and "\\" not in result["filename"]
        output = env.original.parent / "watermarks" / result["filename"]
        assert output.is_file()
        with env.client.get(f'/api/get-version/{result["link"]}') as download:
            assert download.status_code == 200
            marked_pdf = download.data
        assert marked_pdf.startswith(PDF)
        uploaded = upload(env, content=marked_pdf)
        assert uploaded.status_code == 201
        read = env.client.post(
            f'/api/read-watermark/{uploaded.get_json()["id"]}',
            headers=env.headers(), json=WATERMARK,
        )
        assert read.status_code == 201, read.get_json()
        assert read.get_json()["secret"] == WATERMARK["secret"]


@pytest.mark.parametrize("relative", [False, True])
def test_existing_stored_paths_still_download_and_delete(file_app, relative):
    env = file_app
    if relative:
        set_paths(env, env.original.relative_to(env.storage).as_posix())
    for url in ["/api/get-document/42", "/api/get-version/shared"]:
        with env.client.get(url, headers=env.headers()) as response:
            assert response.status_code == 200
            assert response.data == PDF
    assert env.client.delete("/api/delete-document/42", headers=env.headers()).status_code == 200
    assert not env.original.exists()

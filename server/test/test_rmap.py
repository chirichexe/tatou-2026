import hashlib
import secrets
from pathlib import Path

import pymupdf as fitz
import pytest
from rmap import RMAPClient
from rmap.keygen import generate_keypair
from sqlalchemy import create_engine, text
from watermarking_utils import read_watermark


def _write_keypair(directory, stem, name, passphrase=None):
    key = generate_keypair(name, f"{stem}@example.test", passphrase=passphrase)
    private = directory / f"{stem}_private.asc"
    public = directory / f"{stem}_public.asc"
    private.write_text(str(key))
    public.write_text(str(key.pubkey))
    return private, public


def _pdf(path):
    document = fitz.open()
    document.new_page().insert_text((72, 72), "RMAP test document")
    document.save(path)
    document.close()


@pytest.mark.parametrize(
    "method,key",
    [("toy-eof", "test-watermark-key"), ("francesco-watermark", "0123456789abcdef" * 4)],
)
def test_rmap_handshake_returns_a_link_to_the_identity_version(tmp_path, monkeypatch, method, key):
    key_dir = tmp_path / "keys"
    client_dir = key_dir / "clients"
    client_dir.mkdir(parents=True)
    server_passphrase = "test-server-passphrase"
    server_private, server_public = _write_keypair(
        key_dir, "server", "Server", passphrase=server_passphrase,
    )
    client_private, client_public = _write_keypair(key_dir, "group", "Group_01")
    (client_dir / "Group_01.asc").write_text(client_public.read_text())
    passphrase_file = key_dir / "server_passphrase"
    passphrase_file.write_text(server_passphrase)
    passphrase_file.chmod(0o600)

    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("RMAP_SERVER_PUBLIC_KEY_PATH", str(server_public))
    monkeypatch.setenv("RMAP_SERVER_PRIVATE_KEY_PATH", str(server_private))
    monkeypatch.setenv("RMAP_CLIENT_KEYS_DIR", str(client_dir))
    monkeypatch.setenv("RMAP_SERVER_KEY_PASSPHRASE_FILE", str(passphrase_file))
    monkeypatch.setenv("RMAP_DOCUMENT_ID", "1")
    monkeypatch.setenv("RMAP_WATERMARK_METHOD", method)
    monkeypatch.setenv("RMAP_WATERMARK_KEY", key)
    if method == "francesco-watermark":
        monkeypatch.setenv("RMAP_WATERMARK_POSITION", "no-qr")
    from server import create_app

    app = create_app()
    version_path = app.config["STORAGE_DIR"] / "version.pdf"
    _pdf(version_path)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE Documents (
                id INTEGER PRIMARY KEY, name TEXT, path TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE Versions (
                id INTEGER PRIMARY KEY, documentid INTEGER, link TEXT UNIQUE,
                intended_for TEXT, secret TEXT, method TEXT, path TEXT, sha256 BLOB
            )
        """))
        conn.execute(
            text("""
                INSERT INTO Documents (id, name, path)
                VALUES (1, 'confidential.pdf', :path)
            """),
            {"path": str(version_path)},
        )
    app.config.update(TESTING=True, _ENGINE=engine)

    try:
        http = app.test_client()
        links = []
        for _ in range(2):
            client = RMAPClient("Group_01", client_private, server_public)
            response = http.post("/api/rmap-initiate", json=client.build_msg1())
            assert response.status_code == 200
            client.process_resp1(response.get_json())

            response = http.post("/api/rmap-get-link", json=client.build_msg2())
            assert response.status_code == 200
            link = client.process_resp2(response.get_json())
            assert link == client.expected_link
            links.append(link)

            response = http.get(f"/api/get-version/{link}")
            assert response.status_code == 200
            assert response.mimetype == "application/pdf"

            with engine.connect() as conn:
                version = conn.execute(
                    text("SELECT * FROM Versions WHERE link = :link"), {"link": link},
                ).one()
            assert version.intended_for == "Group_01"
            if method == "francesco-watermark":
                assert version.secret != link
                assert len(version.secret) == 22
            else:
                assert version.secret == f"Group_01:{link}"
            assert read_watermark(method, version.path, key) == version.secret
            if method == "francesco-watermark":
                with fitz.open(version.path) as marked:
                    images = marked[0].get_images(full=True)
                    assert len(images) == 8
                    assert any(image[2] != image[3] for image in images)
            assert version.sha256 is not None
            assert len(version.sha256) == 32
            assert version.sha256 == hashlib.sha256(Path(version.path).read_bytes()).digest()

        assert links[0] != links[1]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM Versions")).scalar_one() == 2
            assert conn.execute(text("SELECT COUNT(DISTINCT secret) FROM Versions")).scalar_one() == 2

        replay = http.post("/api/rmap-get-link", json=client.build_msg2())
        assert replay.status_code == 409

        unknown = RMAPClient("Unregistered", client_private, server_public)
        assert http.post("/api/rmap-initiate", json=unknown.build_msg1()).status_code == 400

        failing_client = RMAPClient("Group_01", client_private, server_public)
        response = http.post("/api/rmap-initiate", json=failing_client.build_msg1())
        assert response.status_code == 200
        failing_client.process_resp1(response.get_json())
        monkeypatch.setattr(
            "server.WMUtils.apply_watermark",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("test failure")),
        )
        failed = http.post("/api/rmap-get-link", json=failing_client.build_msg2())
        assert failed.status_code == 500
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM Versions")).scalar_one() == 2
    finally:
        engine.dispose()


def test_rmap_is_explicitly_unavailable_without_key_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.delenv("RMAP_SERVER_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("RMAP_SERVER_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("RMAP_CLIENT_KEYS_DIR", raising=False)
    monkeypatch.delenv("RMAP_SERVER_KEY_PASSPHRASE_FILE", raising=False)
    monkeypatch.delenv("RMAP_DOCUMENT_ID", raising=False)
    monkeypatch.delenv("RMAP_WATERMARK_METHOD", raising=False)
    monkeypatch.delenv("RMAP_WATERMARK_KEY", raising=False)
    from server import create_app

    app = create_app()
    response = app.test_client().post("/api/rmap-initiate", json={})
    assert response.status_code == 503
    assert response.get_json() == {"error": "RMAP is not configured"}


def test_rmap_rejects_an_insecure_passphrase_file(tmp_path, monkeypatch):
    passphrase_file = tmp_path / "server_passphrase"
    passphrase_file.write_text("private-passphrase")
    passphrase_file.chmod(0o644)
    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("RMAP_SERVER_PUBLIC_KEY_PATH", str(tmp_path / "server_public.asc"))
    monkeypatch.setenv("RMAP_SERVER_PRIVATE_KEY_PATH", str(tmp_path / "server_private.asc"))
    monkeypatch.setenv("RMAP_CLIENT_KEYS_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("RMAP_DOCUMENT_ID", "1")
    monkeypatch.setenv("RMAP_WATERMARK_METHOD", "toy-eof")
    monkeypatch.setenv("RMAP_WATERMARK_KEY", "test-watermark-key")
    monkeypatch.setenv("RMAP_SERVER_KEY_PASSPHRASE_FILE", str(passphrase_file))
    from server import create_app

    with pytest.raises(RuntimeError, match="must not be group- or world-accessible"):
        create_app()

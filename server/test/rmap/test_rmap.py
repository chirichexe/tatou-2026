"""End-to-end coverage for the thin HTTP integration around RMAP."""

import secrets

import fitz
import pytest
from rmap import RMAPClient
from rmap.keygen import generate_keypair
from sqlalchemy import create_engine, text

from watermarking_method import WatermarkingError
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
    page = document.new_page()
    sentence = "RMAP links identify recipients through text in this document."
    for row in range(40):
        page.insert_text((70, 80 + row * 16), sentence, fontsize=10)
    document.save(path)
    document.close()


def test_rmap_handshake_returns_a_link_to_the_identity_version(tmp_path, monkeypatch):
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
    monkeypatch.setenv("RMAP_WATERMARK_METHOD", "group13-watermark")
    monkeypatch.setenv("RMAP_WATERMARK_KEY", "test-watermark-key")
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
                intended_for TEXT, secret TEXT, method TEXT, position TEXT, path TEXT
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
            assert version.secret == f"Group_01:{link}"
            assert version.method == "group13-watermark"
            assert read_watermark("group13-watermark", version.path, "test-watermark-key") == version.secret
            assert read_watermark("khaled-text-spacing-watermark", version.path, "test-watermark-key") == version.secret

        assert links[0] != links[1]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM Versions")).scalar_one() == 2

        replay = http.post("/api/rmap-get-link", json=client.build_msg2())
        assert replay.status_code == 409

        unknown = RMAPClient("Unregistered", client_private, server_public)
        assert http.post("/api/rmap-initiate", json=unknown.build_msg1()).status_code == 400

        for malformed in ({"payload": "not base64 !"}, {"payload": 42}, {}, ["payload"]):
            for route in ("/api/rmap-initiate", "/api/rmap-get-link"):
                response = http.post(route, json=malformed)
                assert response.status_code == 400
                assert "payload" not in response.get_json()

        # A failing watermark (generic error, or the method refusing the source)
        # must never produce a link, a version row or a stored file. The error
        # body differs (handled vs. the app's generic 500 handler); the
        # invariant does not.
        for failure in (RuntimeError("test failure"), WatermarkingError("no usable image")):
            failing_client = RMAPClient("Group_01", client_private, server_public)
            response = http.post("/api/rmap-initiate", json=failing_client.build_msg1())
            assert response.status_code == 200
            failing_client.process_resp1(response.get_json())
            monkeypatch.setattr(
                "server.WMUtils.apply_watermark",
                lambda failure=failure, **_kwargs: (_ for _ in ()).throw(failure),
            )
            failed = http.post("/api/rmap-get-link", json=failing_client.build_msg2())
            assert failed.status_code == 500
            assert "payload" not in failed.get_json()
            with engine.connect() as conn:
                assert conn.execute(text("SELECT COUNT(*) FROM Versions")).scalar_one() == 2
            assert len(list((app.config["STORAGE_DIR"] / "rmap").glob("*.pdf"))) == 2
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
    monkeypatch.setenv("RMAP_WATERMARK_METHOD", "group13-watermark")
    monkeypatch.setenv("RMAP_WATERMARK_KEY", "test-watermark-key")
    monkeypatch.setenv("RMAP_SERVER_KEY_PASSPHRASE_FILE", str(passphrase_file))
    from server import create_app

    with pytest.raises(RuntimeError, match="must not be group- or world-accessible"):
        create_app()

"""Issue distinct RMAP copies with both text and image watermarks."""

from __future__ import annotations

import io
import secrets

import numpy as np
import pymupdf as fitz
from PIL import Image
from rmap import RMAPClient
from rmap.keygen import generate_keypair
from sqlalchemy import create_engine, text

from watermarking_utils import read_watermark


def _source_pdf() -> bytes:
    rng = np.random.default_rng(1313)
    pixels = rng.integers(0, 256, size=(640, 640, 3), dtype=np.uint8)
    image = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(image, format="JPEG", quality=90)
    with fitz.open() as doc:
        cover = doc.new_page()
        cover.insert_image(fitz.Rect(80, 80, 500, 500), stream=image.getvalue())
        page = doc.new_page()
        sentence = ("The document carries a unique group link inside its "
                    "selectable letters and its cover image.")
        for row in range(42):
            page.insert_text((35, 35 + row * 18), sentence, fontsize=10)
        return doc.tobytes()


def test_rmap_issues_distinct_versions_with_both_marks(tmp_path, monkeypatch):
    key_dir = tmp_path / "keys"
    client_dir = key_dir / "clients"
    client_dir.mkdir(parents=True)
    server_key = generate_keypair("Server", "server@example.test")
    client_key = generate_keypair("Group_13", "group@example.test")
    server_private = key_dir / "server_private.asc"
    server_public = key_dir / "server_public.asc"
    client_private = key_dir / "group_private.asc"
    server_private.write_text(str(server_key))
    server_public.write_text(str(server_key.pubkey))
    client_private.write_text(str(client_key))
    (client_dir / "Group_13.asc").write_text(str(client_key.pubkey))

    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("RMAP_SERVER_PUBLIC_KEY_PATH", str(server_public))
    monkeypatch.setenv("RMAP_SERVER_PRIVATE_KEY_PATH", str(server_private))
    monkeypatch.setenv("RMAP_CLIENT_KEYS_DIR", str(client_dir))
    monkeypatch.setenv("RMAP_DOCUMENT_ID", "1")
    monkeypatch.setenv("RMAP_WATERMARK_METHOD", "khaled-text-image-watermark")
    monkeypatch.setenv("RMAP_WATERMARK_KEY", "test-only-rmap-watermark-key")
    monkeypatch.delenv("RMAP_SERVER_KEY_PASSPHRASE_FILE", raising=False)
    monkeypatch.delenv("RMAP_SERVER_KEY_PASSPHRASE", raising=False)
    from server import create_app

    app = create_app()
    source_path = app.config["STORAGE_DIR"] / "source.pdf"
    source_path.write_bytes(_source_pdf())
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE Documents (id INTEGER PRIMARY KEY, name TEXT, path TEXT)"))
        conn.execute(text("""CREATE TABLE Versions (
            id INTEGER PRIMARY KEY, documentid INTEGER, link TEXT UNIQUE,
            intended_for TEXT, secret TEXT, method TEXT, position TEXT, path TEXT
        )"""))
        conn.execute(text("INSERT INTO Documents (id, name, path) VALUES (1, 'source.pdf', :path)"),
                     {"path": str(source_path)})
    app.config.update(TESTING=True, _ENGINE=engine)

    issued: list[str] = []
    http = app.test_client()
    for _ in range(2):
        client = RMAPClient("Group_13", client_private, server_public)
        first = http.post("/api/rmap-initiate", json=client.build_msg1())
        assert first.status_code == 200
        client.process_resp1(first.get_json())
        second = http.post("/api/rmap-get-link", json=client.build_msg2())
        assert second.status_code == 200
        link = client.process_resp2(second.get_json())
        issued.append(link)
        assert http.get(f"/api/get-version/{link}").status_code == 200
        with engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM Versions WHERE link = :link"),
                               {"link": link}).one()
        assert row.method == "khaled-text-image-watermark"
        assert row.secret == f"Group_13:{link}"
        assert read_watermark("khaled-text-spacing-watermark", row.path, "test-only-rmap-watermark-key") == row.secret
        assert read_watermark("davide-watermark", row.path, "test-only-rmap-watermark-key") == row.secret

    assert issued[0] != issued[1]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM Versions")).scalar_one() == 2

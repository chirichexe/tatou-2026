from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from unsafe_bash_bridge_append_eof import UnsafeBashBridgeAppendEOF
from watermarking_method import SecretNotFoundError, WatermarkingError
from watermarking_utils import METHODS


PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"%%EOF\n"
)


@pytest.fixture
def method() -> UnsafeBashBridgeAppendEOF:
    return UnsafeBashBridgeAppendEOF()


def test_round_trip_unicode_secret(method):
    secret = "watermark-è-🔐"

    output = method.add_watermark(PDF, secret, key="ignored")

    assert output == PDF + secret.encode("utf-8")
    assert method.read_secret(output, key="ignored") == secret


def test_reads_legacy_document(method):
    legacy = PDF + "legacy-secret".encode("utf-8")

    assert method.read_secret(legacy, key="ignored") == "legacy-secret"


def test_shell_metacharacters_are_literal_and_never_executed(method, tmp_path):
    sentinel = tmp_path / "must-not-exist"
    secret = f'"; touch {sentinel}; # $() `command`\nsecond line\\%s'

    output = method.add_watermark(PDF, secret, key="ignored")

    assert method.read_secret(output, key="ignored") == secret
    assert not sentinel.exists()


def test_path_with_spaces_and_metacharacters_is_safe(method, tmp_path):
    sentinel = tmp_path / "sentinel"
    source = tmp_path / "document ; touch sentinel $(ignored).pdf"
    source.write_bytes(PDF)

    output = method.add_watermark(source, "secret", key="ignored")

    assert method.read_secret(output, key="ignored") == "secret"
    assert not sentinel.exists()


@pytest.mark.parametrize("source_factory", [
    lambda path: path,
    lambda path: str(path),
    lambda path: path.read_bytes(),
    lambda path: BytesIO(path.read_bytes()),
])
def test_supports_every_pdf_source(method, tmp_path, source_factory):
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF)

    output = method.add_watermark(
        source_factory(source_path), "secret", key="ignored", position="ignored",
    )

    assert method.read_secret(output, key="ignored") == "secret"


def test_uses_final_eof_marker(method):
    incrementally_updated = PDF + b"older\n%%EOF\r\n" + "newest".encode("utf-8")

    assert method.read_secret(incrementally_updated, key="ignored") == "newest"


@pytest.mark.parametrize("document", [
    PDF,
    PDF.rstrip(b"\n"),
])
def test_missing_watermark_raises_secret_not_found(method, document):
    with pytest.raises(SecretNotFoundError):
        method.read_secret(document, key="ignored")


def test_non_utf8_watermark_raises_watermarking_error(method):
    with pytest.raises(WatermarkingError):
        method.read_secret(PDF + b"\xff", key="ignored")


def test_method_remains_registered():
    assert METHODS["bash-bridge-eof"].name == "bash-bridge-eof"


def test_method_remains_available_from_api(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "test-only-secret-key")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    import server

    app = server.create_app()

    response = app.test_client().get("/api/get-watermarking-methods")

    assert response.status_code == 200
    assert "bash-bridge-eof" in {
        item["name"] for item in response.get_json()["methods"]
    }

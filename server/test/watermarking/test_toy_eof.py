"""Behavior of the toy EOF method (test-only, not registered)."""

from io import BytesIO

import fitz
import pytest
from watermarking_method import InvalidKeyError, SecretNotFoundError
from watermarking_methods.add_after_eof import AddAfterEOF
from watermarking_utils import METHODS


@pytest.fixture
def pdf_bytes() -> bytes:
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Tatou toy EOF test")
        return document.tobytes()


def test_round_trip_unicode_secret_and_rendering(pdf_bytes):
    method = AddAfterEOF()
    secret = "watermark-è-🔐"

    output = method.add_watermark(BytesIO(pdf_bytes), secret, key="private-key")

    assert method.read_secret(output, key="private-key") == secret
    with fitz.open(stream=output, filetype="pdf") as document:
        assert document.page_count == 1
        document[0].get_pixmap()


def test_wrong_key_is_rejected(pdf_bytes):
    method = AddAfterEOF()
    output = method.add_watermark(pdf_bytes, "secret", key="correct-key")

    with pytest.raises(InvalidKeyError):
        method.read_secret(output, key="wrong-key")


def test_missing_mark_is_reported(pdf_bytes):
    with pytest.raises(SecretNotFoundError):
        AddAfterEOF().read_secret(pdf_bytes, key="private-key")


def test_embedding_is_deterministic(pdf_bytes):
    method = AddAfterEOF()

    assert method.add_watermark(pdf_bytes, "secret", "key") == method.add_watermark(
        pdf_bytes, "secret", "key"
    )


def test_method_is_not_registered():
    assert "toy-eof" not in METHODS

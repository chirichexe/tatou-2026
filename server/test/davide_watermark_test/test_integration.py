from __future__ import annotations

import io
from pathlib import Path
import pymupdf as fitz
import pytest

from davide_watermark.method import DavideWatermark
from watermarking_method import (
    InvalidKeyError,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
)
from watermarking_utils import (
    METHODS,
    apply_watermark,
    get_method,
    is_watermarking_applicable,
    read_watermark,
)


def test_roundtrip_correct_key(sample_carrier_pdf):
    secret = "Tatou2026-integration-secret"
    key = "master-key-xyz"

    watermarked = DavideWatermark.add_watermark(sample_carrier_pdf, secret, key)

    assert isinstance(watermarked, bytes)
    assert watermarked.startswith(b"%PDF-")

    recovered = DavideWatermark.read_secret(watermarked, key)
    assert recovered == secret


def test_reading_with_wrong_key_fails(sample_carrier_pdf):
    secret = "classified-document-secret"
    correct_key = "correct-key"
    wrong_key = "wrong-key"

    watermarked = DavideWatermark.add_watermark(sample_carrier_pdf, secret, correct_key)

    with pytest.raises(InvalidKeyError):
        DavideWatermark.read_secret(watermarked, wrong_key)


def test_unwatermarked_pdf_raises_secret_not_found(sample_carrier_pdf, empty_carrier_pdf, small_carrier_pdf):
    key = "any-key"

    # PDF with carriers but no watermark inserted
    with pytest.raises(SecretNotFoundError):
        DavideWatermark.read_secret(sample_carrier_pdf, key)

    # PDF with zero carrier positions
    with pytest.raises(SecretNotFoundError):
        DavideWatermark.read_secret(empty_carrier_pdf, key)

    # PDF with fewer carrier slots than required for header
    with pytest.raises(SecretNotFoundError):
        DavideWatermark.read_secret(small_carrier_pdf, key)


def test_corrupted_watermark_is_not_silently_accepted(sample_carrier_pdf):
    secret = "tamper-proof-secret"
    key = "test-key"

    watermarked = DavideWatermark.add_watermark(sample_carrier_pdf, secret, key)

    # Tamper with the carrier stream by flipping inserted Tw markers
    with fitz.open(stream=watermarked, filetype="pdf") as doc:
        xref = doc[0].get_contents()[0]
        stream = doc.xref_stream(xref)
        corrupted_stream = stream.replace(b"0.01 Tw", b"0.02 Tw")
        doc.update_stream(xref, corrupted_stream)
        corrupted = doc.tobytes()

    with pytest.raises(WatermarkingError):
        DavideWatermark.read_secret(corrupted, key)


def test_realistic_utf8_secrets(sample_carrier_pdf):
    secrets = [
        "Tatou 2026! 🔐 Secrète été München 🚀",
        "日本語の秘密メッセージ 🌸",
        "Special @#$%^&*()_+=-`~[]{}|;:',.<>?/ chars",
    ]
    key = "utf8-test-key"

    for secret in secrets:
        watermarked = DavideWatermark.add_watermark(sample_carrier_pdf, secret, key)
        recovered = DavideWatermark.read_secret(watermarked, key)
        assert recovered == secret


def test_integration_with_tatou_utility_layer(sample_carrier_pdf, empty_carrier_pdf, tmp_path):
    secret = "tatou-utils-secret"
    key = "tatou-utils-key"

    # Verify registration in METHODS
    assert "davide-watermark" in METHODS
    method = get_method("davide-watermark")
    assert isinstance(method, WatermarkingMethod)
    assert isinstance(method, DavideWatermark)

    # Verify applicability helper
    assert is_watermarking_applicable("davide-watermark", sample_carrier_pdf) is True
    assert is_watermarking_applicable("davide-watermark", empty_carrier_pdf) is False

    # Apply watermark via utility layer
    watermarked = apply_watermark("davide-watermark", sample_carrier_pdf, secret, key)
    assert isinstance(watermarked, bytes)

    # Read watermark via utility layer
    recovered = read_watermark("davide-watermark", watermarked, key)
    assert recovered == secret

    # Test compatibility with filesystem path PdfSource
    pdf_file = tmp_path / "watermarked.pdf"
    pdf_file.write_bytes(watermarked)
    assert read_watermark("davide-watermark", pdf_file, key) == secret

    # Test compatibility with BytesIO PdfSource
    assert read_watermark("davide-watermark", io.BytesIO(watermarked), key) == secret


def test_input_validation(sample_carrier_pdf, small_carrier_pdf):
    key = "valid-key"

    # Empty secret
    with pytest.raises(ValueError, match="Secret must not be empty"):
        DavideWatermark.add_watermark(sample_carrier_pdf, "", key)

    # Empty key
    with pytest.raises(InvalidKeyError, match="Key must not be empty"):
        DavideWatermark.add_watermark(sample_carrier_pdf, "secret", "")

    with pytest.raises(InvalidKeyError, match="Key must not be empty"):
        DavideWatermark.read_secret(sample_carrier_pdf, "")

    # Secret too long (> 128 bytes)
    with pytest.raises(ValueError, match="Secret is too long"):
        DavideWatermark.add_watermark(sample_carrier_pdf, "a" * 129, key)

    # Not enough carrier slots
    with pytest.raises(WatermarkingError, match="not contain enough carrier positions"):
        DavideWatermark.add_watermark(small_carrier_pdf, "secret", key)

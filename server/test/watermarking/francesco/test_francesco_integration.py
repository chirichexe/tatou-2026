"""End-to-end behavior and composition tests."""

from __future__ import annotations

import io
import shutil

import pymupdf as fitz
import pytest
import zxingcpp
from PIL import Image

from watermarking_method import InvalidKeyError, SecretNotFoundError, WatermarkingError
from watermarking_methods.davide.method import DavideWatermark as StructuralWatermark
from watermarking_methods.francesco import crypto, visible
from watermarking_methods.francesco.method import FrancescoWatermark

KEY = "0123456789abcdef" * 4
OTHER_KEY = "fedcba9876543210" * 4
GROUP_POSITION = "group=Group_13"

needs_ocr = pytest.mark.skipif(shutil.which("tesseract") is None,
                               reason="tesseract not installed (visible-label OCR)")


def test_roundtrip_preserves_page_and_text(pdf_bytes):
    method = FrancescoWatermark()
    watermarked = method.add_watermark(
        pdf_bytes, "unique-copy-id", KEY, position=GROUP_POSITION
    )
    assert method.read_secret(watermarked, KEY) == "unique-copy-id"

    with fitz.open(stream=watermarked, filetype="pdf") as doc:
        assert doc.page_count == 1
        assert "A document with a table and a photograph" in doc[0].get_text()
        assert len(doc[0].get_images()) >= 1


@needs_ocr
def test_wrong_key_and_secret_not_found(pdf_bytes):
    method = FrancescoWatermark()
    watermarked = method.add_watermark(
        pdf_bytes, "copy-42", KEY, position=GROUP_POSITION
    )

    # Wrong key
    with pytest.raises(InvalidKeyError):
        method.read_secret(watermarked, OTHER_KEY)

    # Document without watermark
    with pytest.raises(SecretNotFoundError):
        method.read_secret(pdf_bytes, KEY)


def test_conflicting_valid_copies_rejected(pdf_bytes):
    method = FrancescoWatermark()
    first = method.add_watermark(pdf_bytes, "copy-one", KEY, GROUP_POSITION)
    second = method.add_watermark(pdf_bytes, "copy-two", KEY, GROUP_POSITION)

    # Stitch two different watermarked pages together
    with (
        fitz.open(stream=first, filetype="pdf") as left,
        fitz.open(stream=second, filetype="pdf") as right,
    ):
        right.insert_pdf(left)
        stitched = right.tobytes()

    with pytest.raises(WatermarkingError, match="Conflicting"):
        method.read_secret(stitched, KEY)


def test_survives_jpeg_raster_roundtrip(pdf_bytes):
    method = FrancescoWatermark()
    watermarked = method.add_watermark(
        pdf_bytes, "copy-through-jpeg", KEY, GROUP_POSITION
    )

    # Simulate scan / raster conversion to JPEG 85% quality
    with (
        fitz.open(stream=watermarked, filetype="pdf") as source,
        fitz.open() as rebuilt,
    ):
        pix = source[0].get_pixmap(dpi=220, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        page = rebuilt.new_page(
            width=source[0].rect.width, height=source[0].rect.height
        )
        page.insert_image(page.rect, stream=buffer.getvalue())
        candidate = rebuilt.tobytes()

    assert method.read_secret(candidate, KEY) == "copy-through-jpeg"


def test_layer_toggles_and_prerequisites(pdf_bytes, monkeypatch):
    method = FrancescoWatermark()

    # Base layer is mandatory
    monkeypatch.setattr(FrancescoWatermark, "ENABLE_BASE_LAYER", False)
    with pytest.raises(WatermarkingError, match="Base layer"):
        method.add_watermark(pdf_bytes, "test-copy", KEY)
    monkeypatch.undo()

    # Disabling QR watermark prevents reading secret
    monkeypatch.setattr(FrancescoWatermark, "ENABLE_QR_WATERMARK", False)
    with pytest.raises(WatermarkingError, match="QR watermark layer is disabled"):
        method.read_secret(pdf_bytes, KEY)
    monkeypatch.undo()

    # Both the authenticated QR and aesthetic text layers are active by default.
    assert FrancescoWatermark.ENABLE_QR_WATERMARK is True
    assert FrancescoWatermark.ENABLE_VISIBLE_TEXT is True
    watermarked_default = method.add_watermark(
        pdf_bytes, "copy-id", KEY, position="group=Group_13"
    )
    assert method.read_secret(watermarked_default, KEY) == "copy-id"
    with fitz.open(stream=watermarked_default, filetype="pdf") as doc:
        images = doc[0].get_images(full=True)
        assert any(image[2] == image[3] for image in images)


@needs_ocr
def test_qr_and_visible_layers_use_distinct_ciphertexts(pdf_bytes, monkeypatch):
    method = FrancescoWatermark()
    secret = "copy-with-three-ciphertexts"
    encrypted_payloads: list[str] = []
    original_payload = method._payload

    def capture_payload(value: str, key: str) -> str:
        payload = original_payload(value, key)
        encrypted_payloads.append(payload)
        return payload

    monkeypatch.setattr(method, "_payload", capture_payload)
    watermarked = method.add_watermark(pdf_bytes, secret, KEY)

    with fitz.open(stream=watermarked, filetype="pdf") as document:
        pixmap = document[0].get_pixmap(dpi=300, colorspace=fitz.csRGB, alpha=False)
        qr_payloads = {
            result.text
            for result in zxingcpp.read_barcodes(
                Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples),
                formats=zxingcpp.BarcodeFormat.QRCode,
            )
            if crypto.is_candidate_payload(result.text)
        }
        visible_tokens = visible.extract_visible_tokens(document)

    assert len(encrypted_payloads) == 2
    assert len(set(encrypted_payloads)) == 2
    assert qr_payloads == {encrypted_payloads[0]}
    assert visible_tokens
    assert visible.decrypt_visible_tokens(visible_tokens, KEY) == {secret}
    assert {
        crypto.decrypt_qr_payload(payload, KEY) for payload in qr_payloads
    } == {secret}

    assert encrypted_payloads[1] not in qr_payloads


@pytest.mark.parametrize(
    "position",
    [
        None,
        "group=Group_13",
        "group=Group_13;qr-only",
        "group=Group_13;no-text",
        "group=Group_13;text-only",
        "group=Group_13;no-qr",
    ],
)
def test_position_is_ignored_and_both_layers_stay_enabled(pdf_bytes, position):
    method = FrancescoWatermark()
    watermarked = method.add_watermark(pdf_bytes, "copy-options", KEY, position)

    with fitz.open(stream=watermarked, filetype="pdf") as doc:
        images = doc[0].get_images(full=True)
        assert any(image[2] == image[3] for image in images)

    assert method.read_secret(watermarked, KEY) == "copy-options"


@needs_ocr
def test_visible_ciphertext_survives_flattening_without_qr(pdf_bytes, monkeypatch):
    method = FrancescoWatermark()
    secret = "text-fallback-copy"
    monkeypatch.setattr(FrancescoWatermark, "ENABLE_QR_WATERMARK", False)
    text_only = method.add_watermark(pdf_bytes, secret, KEY)
    monkeypatch.undo()

    with (
        fitz.open(stream=text_only, filetype="pdf") as source,
        fitz.open() as rebuilt,
    ):
        pixmap = source[0].get_pixmap(
            dpi=300,
            colorspace=fitz.csRGB,
            alpha=False,
        )
        image = Image.frombytes(
            "RGB",
            (pixmap.width, pixmap.height),
            pixmap.samples,
        )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page = rebuilt.new_page(
            width=source[0].rect.width,
            height=source[0].rect.height,
        )
        page.insert_image(page.rect, stream=buffer.getvalue())
        flattened = rebuilt.tobytes()

    assert method.read_secret(flattened, KEY) == secret


def test_non_ascii_secret_roundtrip(pdf_bytes):
    method = FrancescoWatermark()
    secret = "copia-gruppo-13-è"
    watermarked = method.add_watermark(
        pdf_bytes, secret, KEY, position="group=Group_13"
    )
    assert method.read_secret(watermarked, KEY) == secret



def test_combined_structural_and_visual_watermarks(structural_carrier_pdf):
    structural_secret = "structural-secret-42"
    visual_secret = "Group_01:visual-secret-99"

    structurally_marked = StructuralWatermark.add_watermark(
        structural_carrier_pdf, structural_secret, KEY
    )

    visual_method = FrancescoWatermark()
    combined_pdf = visual_method.add_watermark(
        structurally_marked, visual_secret, KEY
    )

    recovered_structural = StructuralWatermark.read_secret(combined_pdf, KEY)
    recovered_visual = visual_method.read_secret(combined_pdf, KEY)

    assert recovered_structural == structural_secret
    assert recovered_visual == visual_secret

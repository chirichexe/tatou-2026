"""End-to-end integration and dual-watermark compatibility tests."""

from __future__ import annotations

import io

import fitz
import pytest
from davide_watermark.method import DavideWatermark
from francesco_watermark.method import HybridPageWatermark
from PIL import Image
from watermarking_method import InvalidKeyError, SecretNotFoundError, WatermarkingError

KEY = "0123456789abcdef" * 4
OTHER_KEY = "fedcba9876543210" * 4


def test_roundtrip_preserves_page_and_text(pdf_bytes):
    method = HybridPageWatermark()
    watermarked = method.add_watermark(pdf_bytes, "unique-copy-id", KEY)
    assert method.read_secret(watermarked, KEY) == "unique-copy-id"

    with fitz.open(stream=watermarked, filetype="pdf") as doc:
        assert doc.page_count == 1
        assert "A document with a table and a photograph" in doc[0].get_text()
        assert len(doc[0].get_images()) >= 1


def test_wrong_key_and_secret_not_found(pdf_bytes):
    method = HybridPageWatermark()
    watermarked = method.add_watermark(pdf_bytes, "copy-42", KEY)

    # Wrong key
    with pytest.raises(InvalidKeyError):
        method.read_secret(watermarked, OTHER_KEY)

    # Document without watermark
    with pytest.raises(SecretNotFoundError):
        method.read_secret(pdf_bytes, KEY)


def test_conflicting_valid_copies_rejected(pdf_bytes):
    method = HybridPageWatermark()
    first = method.add_watermark(pdf_bytes, "copy-one", KEY)
    second = method.add_watermark(pdf_bytes, "copy-two", KEY)

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
    method = HybridPageWatermark()
    watermarked = method.add_watermark(pdf_bytes, "copy-through-jpeg", KEY)

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
    method = HybridPageWatermark()

    # Base layer is mandatory
    monkeypatch.setattr(HybridPageWatermark, "ENABLE_RASTER_BASE", False)
    with pytest.raises(WatermarkingError, match="Base rasterization layer"):
        method.add_watermark(pdf_bytes, "test-copy", KEY)
    monkeypatch.undo()

    # Disabling QR watermark prevents reading secret
    monkeypatch.setattr(HybridPageWatermark, "ENABLE_QR_WATERMARK", False)
    with pytest.raises(WatermarkingError, match="QR watermark layer is disabled"):
        method.read_secret(pdf_bytes, KEY)
    monkeypatch.undo()

    # Default is QR active, visible text inactive
    assert HybridPageWatermark.ENABLE_QR_WATERMARK is True
    assert HybridPageWatermark.ENABLE_VISIBLE_TEXT is False
    watermarked_default = method.add_watermark(pdf_bytes, "only-qr", KEY)
    assert method.read_secret(watermarked_default, KEY) == "only-qr"


def test_dynamic_group_visible_text(pdf_bytes):
    method = HybridPageWatermark()

    # Test adding watermark for Group 03 with position="with-text"
    watermarked_g03 = method.add_watermark(
        pdf_bytes, "Group_03:abc12345", KEY, position="with-text"
    )
    assert method.read_secret(watermarked_g03, KEY) == "Group_03:abc12345"
    with fitz.open(stream=watermarked_g03, filetype="pdf") as doc:
        assert "GROUP 03" in doc[0].get_text()

    # Test adding watermark for Group 11
    watermarked_g11 = method.add_watermark(
        pdf_bytes, "Group_11:abc12345", KEY, position="with-text"
    )
    assert method.read_secret(watermarked_g11, KEY) == "Group_11:abc12345"
    with fitz.open(stream=watermarked_g11, filetype="pdf") as doc:
        assert "GROUP 11" in doc[0].get_text()

    # Test 3-component secret format: FWM1:Group_42:xyz987
    watermarked_3comp = method.add_watermark(
        pdf_bytes, "FWM1:Group_42:xyz987", KEY, position="with-text"
    )
    assert method.read_secret(watermarked_3comp, KEY) == "FWM1:Group_42:xyz987"
    components = method.read_secret_components(watermarked_3comp, KEY)
    assert components == {
        "prefix": "FWM1",
        "group": "Group_42",
        "string": "xyz987",
        "is_our_watermark": True,
    }
    with fitz.open(stream=watermarked_3comp, filetype="pdf") as doc:
        assert "GROUP 42" in doc[0].get_text()



def test_compatibility_combined_with_davide_watermark(carrier_pdf_for_davide):
    davide_secret = "davide-secret-42"
    francesco_secret = "Group_01:francesco-secret-99"

    # Step A: Apply Davide's watermark (modulates BT content streams)
    davide_watermarked = DavideWatermark.add_watermark(
        carrier_pdf_for_davide, davide_secret, KEY
    )

    # Step B: Apply Francesco's native overlay watermark on top of Davide's output
    francesco_method = HybridPageWatermark()
    combined_pdf = francesco_method.add_watermark(
        davide_watermarked, francesco_secret, KEY
    )

    # Step C: Verify BOTH secrets are completely intact and readable from the SAME PDF!
    recovered_davide = DavideWatermark.read_secret(combined_pdf, KEY)
    recovered_francesco = francesco_method.read_secret(combined_pdf, KEY)

    assert recovered_davide == davide_secret
    assert recovered_francesco == francesco_secret

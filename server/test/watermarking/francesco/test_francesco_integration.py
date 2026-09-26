"""End-to-end behavior and composition tests."""

from __future__ import annotations

import io

import pymupdf as fitz
import pytest
import zxingcpp
from PIL import Image

from watermarking_method import SecretNotFoundError, WatermarkingError
from watermarking_methods.davide.method import DavideWatermark as StructuralWatermark
from watermarking_methods.francesco import crypto, rendering
from watermarking_methods.francesco.method import FrancescoWatermark

KEY = "0123456789abcdef" * 4
OTHER_KEY = "fedcba9876543210" * 4
GROUP_POSITION = "group=Group_13"


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


def test_wrong_key_and_secret_not_found(pdf_bytes):
    method = FrancescoWatermark()
    watermarked = method.add_watermark(pdf_bytes, "copy-42", KEY, position=GROUP_POSITION)

    with pytest.raises(SecretNotFoundError):
        method.read_secret(watermarked, OTHER_KEY)
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


@pytest.mark.parametrize("decoy", ["other-key", "random"])
def test_decoy_qr_does_not_hide_the_real_one(pdf_bytes, decoy):
    method = FrancescoWatermark()
    watermarked = method.add_watermark(pdf_bytes, "real-copy", KEY)
    payload = (
        crypto.encrypt_qr_payload("fake-copy", OTHER_KEY)
        if decoy == "other-key"
        else "A" * 60
    )

    # the leaker pastes a QR code of their own in the middle of the page
    with fitz.open(stream=watermarked, filetype="pdf") as doc:
        doc[0].insert_image(fitz.Rect(220, 350, 380, 510),
                            stream=rendering.build_opaque_qr_bytes(payload))
        with_decoy = doc.tobytes()

    assert method.read_secret(with_decoy, KEY) == "real-copy"


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


def test_qr_encodes_the_secret_and_the_label_shows_it(pdf_bytes, monkeypatch):
    method = FrancescoWatermark()
    secret = "Group_07:da0bb583c432fbfd078959ecc9b62902"
    labels: list[str] = []
    original_stamp = rendering.stamp_random_native_visible_text

    def capture_label(**kwargs):
        labels.append(kwargs["label"])
        return original_stamp(**kwargs)

    monkeypatch.setattr(rendering, "stamp_random_native_visible_text", capture_label)
    watermarked = method.add_watermark(pdf_bytes, secret, KEY)

    with fitz.open(stream=watermarked, filetype="pdf") as document:
        pixmap = document[0].get_pixmap(dpi=300, colorspace=fitz.csRGB, alpha=False)
        qr_payloads = [
            result.text
            for result in zxingcpp.read_barcodes(
                Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples),
                formats=zxingcpp.BarcodeFormat.QRCode,
            )
        ]

    assert labels == [secret]
    # the QR carries the ciphertext, never the secret itself
    assert len(qr_payloads) == 1
    assert secret not in qr_payloads[0]
    assert crypto.decrypt_qr_payload(qr_payloads[0], KEY) == secret


@pytest.mark.parametrize("position", [None, "group=Group_13", "group=Group_13;no-qr"])
def test_position_is_ignored(pdf_bytes, position):
    method = FrancescoWatermark()
    watermarked = method.add_watermark(pdf_bytes, "copy-options", KEY, position)

    with fitz.open(stream=watermarked, filetype="pdf") as doc:
        images = doc[0].get_images(full=True)
        assert any(image[2] == image[3] for image in images)

    assert method.read_secret(watermarked, KEY) == "copy-options"


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

"""Visual rendering and layout tests for QR and visible-label layers."""

from __future__ import annotations

import io

import pymupdf as fitz
import pytest
from PIL import Image

from watermarking_methods.francesco import rendering

KEY = "0123456789abcdef" * 4


def test_qr_png_bytes_are_opaque():
    payload = "v1.testpayload"
    png_bytes = rendering.build_opaque_qr_bytes(payload, target_pixel_size=240)
    assert isinstance(png_bytes, bytes)
    assert png_bytes.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(png_bytes)) as png:
        assert png.getchannel("A").getextrema() == (255, 255)


def test_random_qr_avoids_content_and_stays_on_the_border():
    text_box = (100.0, 300.0, 450.0, 350.0)
    placed_boxes = [text_box]
    first = rendering.random_qr_rect(595.0, 842.0, placed_boxes, b"seed-qr-test", min_gap=15.0)
    placed_boxes.append(first)
    second = rendering.random_qr_rect(595.0, 842.0, placed_boxes, b"other-seed", min_gap=15.0)

    edge_inset = max(4.0, min(595.0, 842.0) * 0.012)
    qr_side = min(595.0, 842.0) * rendering.DEFAULT_QR_FRACTION
    for x0, y0, x1, y1 in (first, second):
        assert abs((x1 - x0) - qr_side) < 0.01
        assert (
            x0 <= edge_inset + 0.01
            or y0 <= edge_inset + 0.01
            or x1 >= 595.0 - edge_inset - 0.01
            or y1 >= 842.0 - edge_inset - 0.01
        )
    assert not rendering.boxes_overlap(first, text_box, min_gap=15.0)
    assert not rendering.boxes_overlap(second, text_box, min_gap=15.0)
    assert not rendering.boxes_overlap(first, second, min_gap=15.0)


def test_qr_refuses_to_cover_content_when_no_border_space_exists():
    with pytest.raises(ValueError, match="No text-free border position"):
        rendering.random_qr_rect(595.0, 842.0, [(0.0, 0.0, 595.0, 842.0)], b"blocked-page")


def test_corner_qr_is_flush_to_a_corner():
    x0, y0, x1, y1 = rendering.corner_qr_rect(595.0, 842.0, b"seed")
    side = 595.0 * rendering.DEFAULT_QR_FRACTION
    assert x0 in (0.0, 595.0 - side) and y0 in (0.0, 842.0 - side)
    assert (x1 - x0, y1 - y0) == (side, side)


def test_visible_labels_never_overlap_the_qr(pdf_bytes):
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        qr_box = rendering.random_qr_rect(page.rect.width, page.rect.height, [], b"seed-qr-test")
        placed_boxes = [qr_box]

        # Place visible labels everywhere else, including across page content.
        text_boxes = rendering.stamp_random_native_visible_text(
            page=page,
            label="Group_05:da0bb583c432fbfd078959ecc9b62902",
            placed_boxes=placed_boxes,
            seed_material=b"seed-text-test",
        )
        assert len(text_boxes) == rendering.DEFAULT_VISIBLE_TEXT_COUNT
        assert len(placed_boxes) == rendering.DEFAULT_VISIBLE_TEXT_COUNT + 1
        for text_box in text_boxes:
            assert not rendering.boxes_overlap(text_box, qr_box, min_gap=8.0)

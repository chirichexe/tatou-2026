"""Visual rendering and layout tests for QR and visible-label layers."""

from __future__ import annotations

import io

import pymupdf as fitz
import pytest
from watermarking_methods.francesco import rendering
from PIL import Image

KEY = "0123456789abcdef" * 4


def test_qr_image_and_png_bytes_are_opaque():
    payload = "FWM1:v1.testpayload"
    img = rendering.build_opaque_qr_image(payload, width=1000, height=1000)
    assert isinstance(img, Image.Image)
    assert img.mode == "RGBA"
    assert img.getchannel("A").getextrema() == (255, 255)

    # Verify PNG bytes generation for native PyMuPDF stamping
    png_bytes = rendering.build_opaque_qr_bytes(payload, target_pixel_size=240)
    assert isinstance(png_bytes, bytes)
    assert png_bytes.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(png_bytes)) as png:
        assert png.getchannel("A").getextrema() == (255, 255)


def test_random_qr_collision_avoidance():
    # Pre-populate placed boxes (e.g. existing text band)
    placed_boxes = [(100.0, 300.0, 450.0, 350.0)]
    rects = rendering.generate_random_qr_rects(
        page_width=595.0,
        page_height=842.0,
        count=2,
        placed_boxes=placed_boxes,
        seed_material=b"seed-qr-test",
        min_gap=15.0,
    )
    assert len(rects) == 2
    r1, r2 = rects[0], rects[1]

    text_box = (100.0, 300.0, 450.0, 350.0)
    edge_inset = max(4.0, min(595.0, 842.0) * 0.012)
    qr_side = min(595.0, 842.0) * rendering.DEFAULT_QR_FRACTION
    for x0, y0, x1, y1 in rects:
        assert abs((x1 - x0) - qr_side) < 0.01
        assert (
            x0 <= edge_inset + 0.01
            or y0 <= edge_inset + 0.01
            or x1 >= 595.0 - edge_inset - 0.01
            or y1 >= 842.0 - edge_inset - 0.01
        )
    # Neither QR collides with the pre-existing text
    assert not rendering.boxes_overlap(
        (r1[0] / 595, r1[1] / 842, r1[2] / 595, r1[3] / 842),
        (text_box[0] / 595, text_box[1] / 842, text_box[2] / 595, text_box[3] / 842),
        min_gap=0.01,
    )
    # QR 1 and QR 2 do not collide with each other.
    assert not rendering.boxes_overlap(
        (r1[0] / 595, r1[1] / 842, r1[2] / 595, r1[3] / 842),
        (r2[0] / 595, r2[1] / 842, r2[2] / 595, r2[3] / 842),
        min_gap=0.01,
    )


def test_qr_refuses_to_cover_content_when_no_border_space_exists():
    with pytest.raises(ValueError, match="No text-free border position"):
        rendering.generate_random_qr_rects(
            page_width=595.0,
            page_height=842.0,
            count=2,
            placed_boxes=[(0.0, 0.0, 595.0, 842.0)],
            seed_material=b"blocked-page",
        )


def test_simultaneous_random_text_and_qr_never_overlap(pdf_bytes):
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        placed_boxes: list[tuple[float, float, float, float]] = []

        # Reserve the QR areas first.
        qr_boxes = rendering.generate_random_qr_rects(
            page_width=page.rect.width,
            page_height=page.rect.height,
            count=2,
            placed_boxes=placed_boxes,
            seed_material=b"seed-qr-test",
            min_gap=15.0,
        )
        assert len(qr_boxes) == 2
        assert len(placed_boxes) == 2

        # Place visible labels everywhere else, including across page content.
        text_boxes = rendering.stamp_random_native_visible_text(
            page=page,
            label="GROUP 05",
            placed_boxes=placed_boxes,
            seed_material=b"seed-text-test",
        )
        assert len(text_boxes) == rendering.DEFAULT_VISIBLE_TEXT_COUNT
        assert len(placed_boxes) == rendering.DEFAULT_VISIBLE_TEXT_COUNT + 2

        # Every text label remains outside both QR areas.
        for text_box in text_boxes:
            for qr_box in qr_boxes:
                assert not rendering.boxes_overlap(text_box, qr_box, min_gap=0.01)

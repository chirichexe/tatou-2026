"""Visual rendering and layout tests (frosted QR, dynamic group text, collision avoidance)."""

from __future__ import annotations

import fitz
from francesco_watermark import rendering
from PIL import Image

KEY = "0123456789abcdef" * 4


def test_frosted_qr_image_and_png_bytes():
    payload = "FWM1:v1.testpayload"
    img = rendering.build_frosted_qr_image(payload, width=1000, height=1000)
    assert isinstance(img, Image.Image)
    assert img.mode == "RGBA"

    # Verify PNG bytes generation for native PyMuPDF stamping
    png_bytes = rendering.build_frosted_qr_bytes(payload, target_pixel_size=240)
    assert isinstance(png_bytes, bytes)
    assert png_bytes.startswith(b"\x89PNG")


def test_dynamic_group_identity_extraction():
    # Dynamic per-group extraction without hardcoded strings
    assert rendering.extract_group_identity("Group_01:link123") == "GROUP 01"
    assert rendering.extract_group_identity("Group_03:link123") == "GROUP 03"
    assert rendering.extract_group_identity("Group_11:link123") == "GROUP 11"
    assert rendering.extract_group_identity("Group_42:link123") == "GROUP 42"
    assert rendering.extract_group_identity("alice:link123") == "ALICE"

    # From position parameter hint
    assert (
        rendering.extract_group_identity("secret", "with-text;group=Group_08")
        == "GROUP 08"
    )
    assert (
        rendering.extract_group_identity("secret", "with-text;intended_for=Group_99")
        == "GROUP 99"
    )

    # Label formatting
    assert rendering.format_visible_label("Group_11") == "GROUP 11"
    assert rendering.format_visible_label("Group_05") == "GROUP 05"
    assert rendering.format_visible_label("") == "GROUP"


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
    # Neither QR collides with the pre-existing text
    assert not rendering.boxes_overlap(
        (r1[0] / 595, r1[1] / 842, r1[2] / 595, r1[3] / 842),
        (text_box[0] / 595, text_box[1] / 842, text_box[2] / 595, text_box[3] / 842),
        min_gap=0.01,
    )
    # QR 1 and QR 2 do not collide with each other
    assert not rendering.boxes_overlap(
        (r1[0] / 595, r1[1] / 842, r1[2] / 595, r1[3] / 842),
        (r2[0] / 595, r2[1] / 842, r2[2] / 595, r2[3] / 842),
        min_gap=0.01,
    )


def test_simultaneous_random_text_and_qr_never_overlap(pdf_bytes):
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        placed_boxes: list[tuple[float, float, float, float]] = []

        # 1. Place 2 random diagonal text instances
        text_boxes = rendering.stamp_random_native_visible_text(
            page=page,
            label="GROUP 05",
            placed_boxes=placed_boxes,
            count=2,
            seed_material=b"seed-text-test",
        )
        assert len(text_boxes) == 2
        assert len(placed_boxes) == 2

        # 2. Place 2 random frosted QR codes against the existing text boxes
        qr_boxes = rendering.generate_random_qr_rects(
            page_width=page.rect.width,
            page_height=page.rect.height,
            count=2,
            placed_boxes=placed_boxes,
            seed_material=b"seed-qr-test",
            min_gap=15.0,
        )
        assert len(qr_boxes) == 2
        assert len(placed_boxes) == 4

        # 3. Assert zero overlap between any pair among all 4 placed elements
        for i in range(len(placed_boxes)):
            for j in range(i + 1, len(placed_boxes)):
                box_a = placed_boxes[i]
                box_b = placed_boxes[j]
                norm_a = (
                    box_a[0] / 595,
                    box_a[1] / 842,
                    box_a[2] / 595,
                    box_a[3] / 842,
                )
                norm_b = (
                    box_b[0] / 595,
                    box_b[1] / 842,
                    box_b[2] / 595,
                    box_b[3] / 842,
                )
                assert not rendering.boxes_overlap(norm_a, norm_b, min_gap=0.01)

"""Every method of the group can replace any other: one at a time or all together."""

from __future__ import annotations

import io

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image

from watermarking_method import WatermarkingError
from watermarking_utils import METHODS, apply_watermark, is_watermarking_applicable, read_watermark

SECRET = "Group_07:da0bb583c432fbfd078959ecc9b62902"
KEY = "test-only-interchangeable-key"
GROUP = ["davide-watermark", "khaled-text-spacing-watermark", "francesco-watermark", "group13-watermark"]


@pytest.fixture(scope="module")
def source() -> bytes:
    """A PDF that fits every method: a photo, and a page of simple text with free borders"""
    rng = np.random.default_rng(7)
    y, x = np.mgrid[0:640, 0:800]
    pixels = np.stack([128 + 60 * np.sin(x / 37), 128 + 60 * np.cos(y / 29),
                       128 + 40 * np.sin((x + y) / 53)], -1) + rng.normal(0, 12, (640, 800, 3))
    photo = io.BytesIO()
    Image.fromarray(pixels.clip(0, 255).astype(np.uint8)).save(photo, "JPEG", quality=90)
    with fitz.open() as doc:
        doc.new_page().insert_image(fitz.Rect(100, 150, 495, 466), stream=photo.getvalue())
        page = doc.new_page()
        sentence = "Every method of the group reads the same secret in these letters."
        for row in range(40):
            page.insert_text((70, 110 + row * 16), sentence, fontsize=10)
        return doc.tobytes()


@pytest.fixture(scope="module")
def marked(source) -> dict[str, bytes]:
    return {name: apply_watermark(name, source, SECRET, KEY) for name in GROUP}


def test_every_method_of_the_group_is_registered():
    assert set(GROUP) <= set(METHODS)


@pytest.mark.parametrize("name", GROUP)
def test_same_interface_for_every_method(name, source, marked):
    assert is_watermarking_applicable(name, source)
    assert read_watermark(name, marked[name], KEY) == SECRET
    assert METHODS[name].get_usage()
    with pytest.raises((WatermarkingError, ValueError)):
        read_watermark(name, marked[name], "another-key")


@pytest.mark.parametrize("name", GROUP)
def test_group13_reads_a_copy_made_by_any_single_method(name, marked):
    assert read_watermark("group13-watermark", marked[name], KEY) == SECRET


@pytest.mark.parametrize("name", GROUP[:3])
def test_a_single_method_reads_its_layer_of_a_group13_copy(name, marked):
    assert read_watermark(name, marked["group13-watermark"], KEY) == SECRET

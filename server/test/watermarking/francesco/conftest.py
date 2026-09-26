"""Shared fixtures and constants for Francesco watermark tests."""

from __future__ import annotations

import pymupdf as fitz
import pytest

KEY = "0123456789abcdef" * 4
OTHER_KEY = "fedcba9876543210" * 4


@pytest.fixture
def master_key() -> str:
    return KEY


@pytest.fixture
def other_key() -> str:
    return OTHER_KEY


@pytest.fixture
def pdf_bytes() -> bytes:
    """Single-page test PDF with vector text."""
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), "A document with a table and a photograph")
        return document.tobytes()


@pytest.fixture
def structural_carrier_pdf() -> bytes:
    """PDF with a photo and text for the dual watermark test with DavideWatermark."""
    import io
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(1234)
    light = np.zeros((512, 512))
    for cell in (64, 16, 4):
        blobs = rng.normal(0, 1, (512 // cell + 2, 512 // cell + 2)).astype(np.float32)
        light += np.sqrt(cell) * np.asarray(Image.fromarray(blobs).resize((512 + 2 * cell, 512 + 2 * cell), Image.BICUBIC))[:512, :512]
    light = 110 + 45 * light / light.std()
    pixels = light[..., None] + np.array([10, 40, -20]) + rng.normal(0, 6, (512, 512, 3))
    img = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")

    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 60), "A document with a table and a photograph")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        page.insert_image(fitz.Rect(72, 80, 522, 600), stream=buf.getvalue())
        return doc.tobytes()

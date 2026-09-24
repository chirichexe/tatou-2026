"""Synthetic photo-like PDFs for the watermark tests (no real documents)."""

from __future__ import annotations

import io

import numpy as np
import pymupdf as fitz
from PIL import Image


def make_photo(width: int, height: int, seed: int = 1234, smooth: bool = False) -> Image.Image:
    """Textured RGB image; ``smooth`` skips the noise to keep huge images small."""
    y, x = np.mgrid[0:height, 0:width]
    pixels = (96 + 60 * np.sin(x / 37.0) * np.cos(y / 23.0))[..., None] + np.array([30, 60, 0])
    if not smooth:
        pixels = pixels + np.random.default_rng(seed).normal(0, 18, (height, width, 3))
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")


def photo_pdf(images: list[Image.Image], text: str | None = None) -> bytes:
    """One page with the given images stacked vertically, stored as JPEG."""
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 60), text)
    top = 80
    height = (page.rect.height - 100) / len(images)
    for img in images:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        page.insert_image(fitz.Rect(72, top, 522, top + height - 10), stream=buf.getvalue())
        top += height
    return doc.tobytes()

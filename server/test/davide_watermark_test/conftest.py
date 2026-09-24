from __future__ import annotations

import pymupdf as fitz
import pytest

from photos import make_photo, photo_pdf


@pytest.fixture(scope="session")
def image_pdf() -> bytes:
    """One-page PDF with a single 640x640 textured JPEG, like a photo document."""
    return photo_pdf([make_photo(640, 640)], text="Confidential photo")


@pytest.fixture(scope="session")
def text_only_pdf() -> bytes:
    """PDF without images: nothing to carry the watermark."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Text only")
    return doc.tobytes()

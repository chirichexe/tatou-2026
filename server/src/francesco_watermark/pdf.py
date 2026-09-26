"""PDF geometry validation, page rasterization, and compressed PDF document reconstruction."""

from __future__ import annotations

import io
import math
from typing import Final

import pymupdf as fitz
from PIL import Image
from watermarking_method import WatermarkingError

DEFAULT_DPI: Final[int] = 300
READ_DPI: Final[int] = 300
MAX_INPUT_BYTES: Final[int] = 64 * 1024 * 1024
MAX_PAGES: Final[int] = 10
MAX_PIXELS_PER_PAGE: Final[int] = 16_000_000
MAX_OUTPUT_IMAGE_BYTES: Final[int] = 64 * 1024 * 1024


def is_document_applicable(
    data: bytes,
    position: str | None = None,
) -> bool:
    """Validate document limits: page count, unencrypted status, and DPI pixel bounds."""
    if (position is not None and not isinstance(position, str)) or len(
        data
    ) > MAX_INPUT_BYTES:
        return False
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            if document.is_encrypted or not 1 <= document.page_count <= MAX_PAGES:
                return False
            for page in document:
                width = math.ceil(page.rect.width * DEFAULT_DPI / 72)
                height = math.ceil(page.rect.height * DEFAULT_DPI / 72)
                if width < 500 or height < 500 or width * height > MAX_PIXELS_PER_PAGE:
                    return False
    except (fitz.FileDataError, ValueError, RuntimeError):
        return False
    return True


def rasterize_page(page: fitz.Page, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Rasterize a single PDF page into an RGB PIL Image at the specified DPI."""
    pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def stamp_qr_on_page(
    page: fitz.Page,
    qr_bytes: bytes,
    rect: fitz.Rect | tuple[float, float, float, float],
) -> None:
    """Stamp an opaque QR PNG onto a PDF page without modifying existing streams."""
    target_rect = rect if isinstance(rect, fitz.Rect) else fitz.Rect(*rect)
    page.insert_image(target_rect, stream=qr_bytes, keep_proportion=True, overlay=True)


def assemble_pdf_from_images(
    page_records: list[tuple[Image.Image, float, float]],
) -> bytes:
    """Rebuild a clean, flattened PDF from watermarked page images using Flate compression."""
    image_bytes_total = 0
    with fitz.open() as output:
        for marked_image, orig_width, orig_height in page_records:
            buffer = io.BytesIO()
            marked_image.save(buffer, format="PNG", optimize=False)
            image_bytes_total += buffer.tell()
            if image_bytes_total > MAX_OUTPUT_IMAGE_BYTES:
                raise WatermarkingError("Watermarked pages exceed output size limit")
            new_page = output.new_page(width=orig_width, height=orig_height)
            new_page.insert_image(new_page.rect, stream=buffer.getvalue())
        return output.tobytes(garbage=4, deflate=True, no_new_id=True)

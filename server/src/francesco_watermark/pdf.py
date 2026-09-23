"""PDF geometry validation, page rasterization, and compressed PDF document reconstruction."""
from __future__ import annotations

import io
import math
from typing import Final, Sequence
import fitz
from PIL import Image
from watermarking_method import WatermarkingError

DEFAULT_DPI: Final[int] = 300
READ_DPI: Final[int] = 220
MAX_INPUT_BYTES: Final[int] = 64 * 1024 * 1024
MAX_PAGES: Final[int] = 10
MAX_PIXELS_PER_PAGE: Final[int] = 16_000_000
MAX_OUTPUT_IMAGE_BYTES: Final[int] = 64 * 1024 * 1024
ASSIGNED_PHOTO_FRACTIONS: Final[tuple[float, float, float, float]] = (
    0.1563, 0.1243, 0.8436, 0.6243,
)


def find_primary_photo_rect(page: fitz.Page) -> fitz.Rect | None:
    """Locate the largest embedded image occupying at least 10% of the page area."""
    candidates = []
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            if rect.get_area() >= page.rect.get_area() * 0.10:
                candidates.append(rect)
    return max(candidates, key=lambda rect: rect.get_area(), default=None)


def is_photo_geometry_matching(
    photo: fitz.Rect,
    page: fitz.Page,
    expected_ratios: Sequence[float] = ASSIGNED_PHOTO_FRACTIONS,
) -> bool:
    """Check if the photo bounding box matches the assigned template within tolerance."""
    ratios = (
        photo.x0 / page.rect.width,
        photo.y0 / page.rect.height,
        photo.x1 / page.rect.width,
        photo.y1 / page.rect.height,
    )
    return all(abs(actual - expected) <= 0.02 for actual, expected in zip(ratios, expected_ratios))


def is_document_applicable(
    data: bytes,
    position: str | None = None,
    experimental_position: str = "experimental-trustmark",
) -> bool:
    """Validate document limits: page count, unencrypted status, DPI pixel bounds, and photo layout."""
    if position not in (None, "", experimental_position) or len(data) > MAX_INPUT_BYTES:
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
                if position == experimental_position:
                    photo = find_primary_photo_rect(page)
                    if document.page_count != 1 or photo is None:
                        return False
                    if not is_photo_geometry_matching(photo, page):
                        return False
    except (fitz.FileDataError, ValueError, RuntimeError):
        return False
    return True


def rasterize_page(page: fitz.Page, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Rasterize a single PDF page into an RGB PIL Image at the specified DPI."""
    pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


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

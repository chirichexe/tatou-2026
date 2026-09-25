"""Visual rendering components: typography, visible labels, and opaque QR codes.

Consolidates all visual watermark layers and collision-free random layout generation.
"""

from __future__ import annotations

import hashlib
import io
import random
from collections.abc import Sequence
from typing import Final

import pymupdf as fitz
import zxingcpp
from PIL import Image, ImageDraw, ImageFont

# -----------------------------------------------------------------------------
# Configuration Constants
# -----------------------------------------------------------------------------
# QR codes are deliberately opaque for maximum scanner contrast.
DEFAULT_ALPHA_BG: Final[int] = 255
DEFAULT_ALPHA_DARK: Final[int] = 255
DEFAULT_QR_FRACTION: Final[float] = (
    0.06  # ~6% of the shortest page dimension
)
DEFAULT_VISIBLE_TEXT_COUNT: Final[int] = 6
VISIBLE_TEXT_ALPHA: Final[int] = 145
VISIBLE_TEXT_STROKE_ALPHA: Final[int] = 165
VISIBLE_TEXT_CHUNK_SIZE: Final[int] = 32

# Default fallback placement fractions for QR codes (x, y)
QR_COORDINATES: Final[Sequence[tuple[float, float]]] = (
    (0.04, 0.23),
    (0.74, 0.40),
)

# -----------------------------------------------------------------------------
# Geometry & Collision Avoidance
# -----------------------------------------------------------------------------
def collect_page_content_boxes(
    page: fitz.Page,
    padding: float = 8.0,
) -> list[tuple[float, float, float, float]]:
    """Return padded boxes for existing text, images, and vector drawings."""

    bounds = page.rect
    boxes: list[tuple[float, float, float, float]] = []

    def add(rect_like) -> None:
        rect = fitz.Rect(rect_like) & bounds
        if rect.is_empty or rect.is_infinite:
            return
        boxes.append(
            (
                max(bounds.x0, rect.x0 - padding),
                max(bounds.y0, rect.y0 - padding),
                min(bounds.x1, rect.x1 + padding),
                min(bounds.y1, rect.y1 + padding),
            )
        )

    for block in page.get_text("blocks"):
        add(block[:4])
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            add(rect)
    for drawing in page.get_drawings():
        add(drawing["rect"])

    return boxes


def boxes_overlap(
    box1: tuple[float, float, float, float],
    box2: tuple[float, float, float, float],
    min_gap: float = 0.05,
) -> bool:
    """Return True if two normalized (x0, y0, x1, y1) bounding boxes overlap or are closer than min_gap."""
    x0_1, y0_1, x1_1, y1_1 = box1
    x0_2, y0_2, x1_2, y1_2 = box2
    return not (
        x1_1 + min_gap <= x0_2
        or x1_2 + min_gap <= x0_1
        or y1_1 + min_gap <= y0_2
        or y1_2 + min_gap <= y0_1
    )


# -----------------------------------------------------------------------------
# QR Code Generation & Styling
# -----------------------------------------------------------------------------
def build_opaque_qr_image(
    payload: str,
    width: int,
    height: int,
    alpha_bg: int = DEFAULT_ALPHA_BG,
    alpha_dark: int = DEFAULT_ALPHA_DARK,
    qr_fraction: float = DEFAULT_QR_FRACTION,
) -> Image.Image:
    """Generate a compact, high-contrast QR code with an opaque background."""
    barcode = zxingcpp.create_barcode(
        payload,
        zxingcpp.BarcodeFormat.QRCode,
        ec_level="H",
    )
    raw_img = barcode.to_image(scale=6)
    qr_mask = Image.fromarray(raw_img).convert("L")

    qr_side = max(100, min(int(width * qr_fraction), int(height * qr_fraction)))
    qr_mask = qr_mask.resize((qr_side, qr_side), Image.Resampling.NEAREST)

    dark_layer = Image.new("RGBA", qr_mask.size, (15, 25, 35, alpha_dark))
    light_layer = Image.new("RGBA", qr_mask.size, (255, 255, 255, alpha_bg))

    qr_layer = Image.composite(light_layer, dark_layer, qr_mask)

    border = max(6, qr_side // 16)
    total_side = qr_side + 2 * border
    backed = Image.new("RGBA", (total_side, total_side), (255, 255, 255, alpha_bg))
    backed.paste(qr_layer, (border, border), qr_layer)

    return backed


def build_opaque_qr_bytes(
    payload: str,
    target_pixel_size: int = 240,
    alpha_bg: int = DEFAULT_ALPHA_BG,
    alpha_dark: int = DEFAULT_ALPHA_DARK,
) -> bytes:
    """Generate an opaque QR code and return its PNG bytes for PDF stamping."""
    barcode = zxingcpp.create_barcode(
        payload,
        zxingcpp.BarcodeFormat.QRCode,
        ec_level="H",
    )
    raw_img = barcode.to_image(scale=6)
    qr_mask = Image.fromarray(raw_img).convert("L")
    qr_mask = qr_mask.resize(
        (target_pixel_size, target_pixel_size), Image.Resampling.NEAREST
    )

    dark_layer = Image.new("RGBA", qr_mask.size, (15, 25, 35, alpha_dark))
    light_layer = Image.new("RGBA", qr_mask.size, (255, 255, 255, alpha_bg))
    qr_layer = Image.composite(light_layer, dark_layer, qr_mask)

    border = max(6, target_pixel_size // 16)
    total_side = target_pixel_size + 2 * border
    backed = Image.new("RGBA", (total_side, total_side), (255, 255, 255, alpha_bg))
    backed.paste(qr_layer, (border, border), qr_layer)

    buffer = io.BytesIO()
    backed.save(buffer, format="PNG")
    return buffer.getvalue()


def build_qr_image(payload: str, width: int, height: int) -> Image.Image:
    """Compatibility wrapper generating the opaque QR image."""
    return build_opaque_qr_image(payload, width, height)


def generate_random_qr_rects(
    page_width: float,
    page_height: float,
    count: int = 2,
    placed_boxes: list[tuple[float, float, float, float]] | None = None,
    seed_material: bytes | None = None,
    min_gap: float = 8.0,
    qr_fraction: float = DEFAULT_QR_FRACTION,
) -> list[tuple[float, float, float, float]]:
    """Generate random, collision-free QR boxes along the page borders.

    Existing text/content boxes are hard constraints. If the requested number of
    border positions does not exist, fail instead of covering page content.
    """
    boxes = placed_boxes if placed_boxes is not None else []
    rng = (
        random.Random(
            int.from_bytes(hashlib.sha256(seed_material + b"/qr").digest()[:8], "big")
        )
        if seed_material is not None
        else random.Random()
    )

    qr_side = min(page_width, page_height) * qr_fraction
    edge_inset = max(4.0, min(page_width, page_height) * 0.012)
    if page_width < qr_side + 2 * edge_inset or page_height < qr_side + 2 * edge_inset:
        raise ValueError("Page is too small for a border QR code")

    chosen: list[tuple[float, float, float, float]] = []

    for _ in range(count):
        best_candidate: tuple[float, float, float, float] | None = None

        for _attempt in range(500):
            edge = rng.randrange(4)
            if edge == 0:  # top
                x0 = rng.uniform(edge_inset, page_width - qr_side - edge_inset)
                y0 = edge_inset
            elif edge == 1:  # bottom
                x0 = rng.uniform(edge_inset, page_width - qr_side - edge_inset)
                y0 = page_height - qr_side - edge_inset
            elif edge == 2:  # left
                x0 = edge_inset
                y0 = rng.uniform(edge_inset, page_height - qr_side - edge_inset)
            else:  # right
                x0 = page_width - qr_side - edge_inset
                y0 = rng.uniform(edge_inset, page_height - qr_side - edge_inset)
            candidate = (x0, y0, x0 + qr_side, y0 + qr_side)

            collision = False
            for bx0, by0, bx1, by1 in boxes:
                if not (
                    candidate[2] + min_gap <= bx0
                    or bx1 + min_gap <= candidate[0]
                    or candidate[3] + min_gap <= by0
                    or by1 + min_gap <= candidate[1]
                ):
                    collision = True
                    break

            if not collision:
                best_candidate = candidate
                break

        if best_candidate is None:
            raise ValueError("No text-free border position available for QR code")

        boxes.append(best_candidate)
        chosen.append(best_candidate)

    return chosen


def compute_dynamic_qr_coordinates(
    seed_material: bytes,
    width: int,
    height: int,
    qr_fraction: float = DEFAULT_QR_FRACTION,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Compute pseudo-random, non-overlapping normalized coordinates for 2 QR codes."""
    rects = generate_random_qr_rects(
        float(width),
        float(height),
        count=2,
        placed_boxes=[],
        seed_material=seed_material,
        qr_fraction=qr_fraction,
    )
    return (
        (rects[0][0] / width, rects[0][1] / height),
        (rects[1][0] / width, rects[1][1] / height),
    )


def embed_qr_codes(
    image: Image.Image,
    payload: str,
    coordinates: Sequence[tuple[float, float]] | None = None,
    alpha_bg: int = DEFAULT_ALPHA_BG,
    alpha_dark: int = DEFAULT_ALPHA_DARK,
) -> Image.Image:
    """Paste two redundant opaque QR codes."""
    width, height = image.size
    backed_qr = build_opaque_qr_image(
        payload, width, height, alpha_bg=alpha_bg, alpha_dark=alpha_dark
    )

    result = image.convert("RGBA")
    overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))

    coords = coordinates if coordinates is not None else QR_COORDINATES

    for x_fraction, y_fraction in coords:
        x = min(int(width * x_fraction), width - backed_qr.width)
        y = min(int(height * y_fraction), height - backed_qr.height)
        overlay.paste(backed_qr, (x, y), backed_qr)

    return Image.alpha_composite(result, overlay).convert("RGB")


def detect_qr_payloads(image: Image.Image) -> list[str]:
    """Scan image with zxingcpp and return all decoded text strings."""
    rgb_img = image.convert("RGB")
    results = zxingcpp.read_barcodes(rgb_img)
    return [r.text for r in results if r.text]


apply_qr_codes = embed_qr_codes


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load an OCR-friendly monospaced font or fall back to PIL's default."""
    try:
        return ImageFont.truetype("DejaVuSansMono.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def stamp_random_native_visible_text(
    page: fitz.Page,
    label: str,
    placed_boxes: list[tuple[float, float, float, float]],
    count: int = DEFAULT_VISIBLE_TEXT_COUNT,
    fontsize: float = 10.0,
    rotate: int = 30,
    seed_material: bytes | None = None,
    min_gap: float = 8.0,
) -> list[tuple[float, float, float, float]]:
    """Stamp semi-transparent labels without adding PDF text operators.

    The supplied boxes are hard exclusions, normally the QR areas. Labels may
    cross document content; their centers are spread across the page to limit
    mutual overlap without making placement fail on small pages.
    """
    width = page.rect.width
    height = page.rect.height
    protected_boxes = tuple(placed_boxes)
    rng = (
        random.Random(
            int.from_bytes(hashlib.sha256(seed_material + b"/text").digest()[:8], "big")
        )
        if seed_material is not None
        else random.Random()
    )

    prefix, separator, encoded = label.rpartition("-")
    if separator and encoded:
        chunks = [
            encoded[offset : offset + VISIBLE_TEXT_CHUNK_SIZE]
            for offset in range(0, len(encoded), VISIBLE_TEXT_CHUNK_SIZE)
        ]
        rendered_label = "\n".join([f"{prefix}-{chunks[0]}", *chunks[1:]])
    else:
        rendered_label = label

    estimated_width_per_character = 0.62
    max_label_width = width * 0.72
    longest_line = max(rendered_label.splitlines(), key=len)
    effective_fontsize = min(
        fontsize,
        max(
            6.0,
            max_label_width
            / (len(longest_line) * estimated_width_per_character),
        ),
    )

    scale = 6
    font = load_font(max(1, round(effective_fontsize * scale)))
    stroke_width = 0
    padding = max(4, round(effective_fontsize * scale * 0.25))
    probe = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
    text_bbox = ImageDraw.Draw(probe).multiline_textbbox(
        (0, 0),
        rendered_label,
        font=font,
        spacing=round(effective_fontsize * scale * 0.15),
        stroke_width=stroke_width,
    )
    canvas_width = text_bbox[2] - text_bbox[0] + 2 * padding
    canvas_height = text_bbox[3] - text_bbox[1] + 2 * padding
    canvas = Image.new(
        "RGBA",
        (max(1, canvas_width), max(1, canvas_height)),
        (255, 255, 255, 0),
    )
    draw = ImageDraw.Draw(canvas)
    draw.multiline_text(
        (padding - text_bbox[0], padding - text_bbox[1]),
        rendered_label,
        font=font,
        spacing=round(effective_fontsize * scale * 0.15),
        fill=(38, 56, 76, VISIBLE_TEXT_ALPHA),
        stroke_width=stroke_width,
        stroke_fill=(255, 255, 255, VISIBLE_TEXT_STROKE_ALPHA),
    )
    rotated = canvas.rotate(rotate, expand=True, resample=Image.Resampling.BICUBIC)
    span_x = rotated.width / scale
    span_y = rotated.height / scale
    label_buffer = io.BytesIO()
    rotated.save(label_buffer, format="PNG")
    label_png = label_buffer.getvalue()

    margin_x = max(20.0, width * 0.05)
    margin_y = max(20.0, height * 0.05)

    chosen_boxes: list[tuple[float, float, float, float]] = []

    for _ in range(count):
        best_pt: tuple[float, float] | None = None
        best_box: tuple[float, float, float, float] | None = None
        best_distance = -1.0

        for _attempt in range(1000):
            min_x = margin_x
            max_x = max(min_x + 1.0, width - span_x - margin_x)
            min_y = margin_y
            max_y = max(min_y + 1.0, height - span_y - margin_y)

            px = rng.uniform(min_x, max_x)
            py = rng.uniform(min_y, max_y)

            cand_box = (
                min(px, px + span_x) - 10.0,
                min(py, py + span_y) - 10.0,
                max(px, px + span_x) + 10.0,
                max(py, py + span_y) + 10.0,
            )

            collision = False
            for bx0, by0, bx1, by1 in protected_boxes:
                if not (
                    cand_box[2] + min_gap <= bx0
                    or bx1 + min_gap <= cand_box[0]
                    or cand_box[3] + min_gap <= by0
                    or by1 + min_gap <= cand_box[1]
                ):
                    collision = True
                    break

            if not collision:
                center_x = (cand_box[0] + cand_box[2]) / 2
                center_y = (cand_box[1] + cand_box[3]) / 2
                distance = min(
                    (
                        center_x - (box[0] + box[2]) / 2
                    ) ** 2
                    + (
                        center_y - (box[1] + box[3]) / 2
                    ) ** 2
                    for box in chosen_boxes
                ) if chosen_boxes else 0.0
                if distance <= best_distance:
                    continue
                best_pt = (px, py)
                best_box = cand_box
                best_distance = distance
                if not chosen_boxes:
                    break

        if best_pt is None or best_box is None:
            raise ValueError("Page has insufficient room for visible labels")

        placed_boxes.append(best_box)
        chosen_boxes.append(best_box)

        page.insert_image(
            fitz.Rect(best_pt[0], best_pt[1], best_pt[0] + span_x, best_pt[1] + span_y),
            stream=label_png,
            overlay=True,
        )

    return chosen_boxes


def stamp_native_visible_text(
    page: fitz.Page,
    label: str,
    placed_boxes: list[tuple[float, float, float, float]],
    fontsize: float = 22.0,
    rotate: int = 30,
) -> None:
    """Compatibility wrapper calling stamp_random_native_visible_text."""
    stamp_random_native_visible_text(
        page=page,
        label=label,
        placed_boxes=placed_boxes,
        count=DEFAULT_VISIBLE_TEXT_COUNT,
        fontsize=fontsize,
        rotate=rotate,
    )


__all__ = [
    # Constants
    "DEFAULT_ALPHA_BG",
    "DEFAULT_ALPHA_DARK",
    "DEFAULT_QR_FRACTION",
    "DEFAULT_VISIBLE_TEXT_COUNT",
    "QR_COORDINATES",
    "apply_qr_codes",
    # Geometry & collision
    "boxes_overlap",
    "build_opaque_qr_bytes",
    "collect_page_content_boxes",
    # QR code operations
    "build_opaque_qr_image",
    "build_qr_image",
    "compute_dynamic_qr_coordinates",
    "detect_qr_payloads",
    "embed_qr_codes",
    # Text operations
    "generate_random_qr_rects",
    "load_font",
    "stamp_native_visible_text",
    "stamp_random_native_visible_text",
]

"""Visual rendering components: typography, visible watermark text overlay, and frosted QR codes.

Consolidates all visual watermark layers and collision-free random layout generation.
"""

from __future__ import annotations

import hashlib
import io
import math
import random
from collections.abc import Sequence
from typing import Final

import fitz
import zxingcpp
from PIL import Image, ImageDraw, ImageFont

# -----------------------------------------------------------------------------
# Configuration Constants
# -----------------------------------------------------------------------------
# Frosted glass opacity defaults (0 = transparent, 255 = opaque)
DEFAULT_ALPHA_BG: Final[int] = (
    180  # ~70% opacity: softens underlying text while keeping it visible
)
DEFAULT_ALPHA_DARK: Final[int] = (
    230  # ~90% opacity: dark modules maintain high optical contrast
)
DEFAULT_QR_FRACTION: Final[float] = (
    0.085  # ~8.5% of page dimension (~1.8 cm at 300 DPI)
)

# Default fallback placement fractions for QR codes (x, y)
QR_COORDINATES: Final[Sequence[tuple[float, float]]] = (
    (0.04, 0.23),
    (0.74, 0.40),
)

# Default fallback placement fractions for visible text labels (x, y) - at most twice
VISIBLE_TEXT_COORDINATES: Final[Sequence[tuple[float, float]]] = (
    (0.18, 0.40),
    (0.22, 0.65),
)


# -----------------------------------------------------------------------------
# Geometry & Collision Avoidance
# -----------------------------------------------------------------------------
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
# QR Code Generation & Styling (Frosted Glass)
# -----------------------------------------------------------------------------
def build_frosted_qr_image(
    payload: str,
    width: int,
    height: int,
    alpha_bg: int = DEFAULT_ALPHA_BG,
    alpha_dark: int = DEFAULT_ALPHA_DARK,
    qr_fraction: float = DEFAULT_QR_FRACTION,
) -> Image.Image:
    """Generate a compact QR code with a frosted-glass translucent background."""
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

    frosted_qr = Image.composite(light_layer, dark_layer, qr_mask)

    border = max(6, qr_side // 16)
    total_side = qr_side + 2 * border
    backed = Image.new("RGBA", (total_side, total_side), (255, 255, 255, alpha_bg))
    backed.paste(frosted_qr, (border, border), frosted_qr)

    return backed


def build_frosted_qr_bytes(
    payload: str,
    target_pixel_size: int = 240,
    alpha_bg: int = DEFAULT_ALPHA_BG,
    alpha_dark: int = DEFAULT_ALPHA_DARK,
) -> bytes:
    """Generate a frosted-glass QR code and return its raw PNG bytes for native PDF stamping."""
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
    frosted_qr = Image.composite(light_layer, dark_layer, qr_mask)

    border = max(6, target_pixel_size // 16)
    total_side = target_pixel_size + 2 * border
    backed = Image.new("RGBA", (total_side, total_side), (255, 255, 255, alpha_bg))
    backed.paste(frosted_qr, (border, border), frosted_qr)

    buffer = io.BytesIO()
    backed.save(buffer, format="PNG")
    return buffer.getvalue()


def build_qr_image(payload: str, width: int, height: int) -> Image.Image:
    """Compatibility wrapper generating the frosted-glass QR image."""
    return build_frosted_qr_image(payload, width, height)


def generate_random_qr_rects(
    page_width: float,
    page_height: float,
    count: int = 2,
    placed_boxes: list[tuple[float, float, float, float]] | None = None,
    seed_material: bytes | None = None,
    min_gap: float = 20.0,
    qr_fraction: float = DEFAULT_QR_FRACTION,
) -> list[tuple[float, float, float, float]]:
    """Generate collision-free random bounding boxes for QR codes on a page.

    Checks collision against all boxes in `placed_boxes` (including pre-placed text).
    Reiterates candidate positions on collision until a free placement is found.
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
    margin_x = max(15.0, page_width * 0.04)
    margin_y = max(15.0, page_height * 0.04)

    chosen: list[tuple[float, float, float, float]] = []

    for _ in range(count):
        best_candidate: tuple[float, float, float, float] | None = None

        for _attempt in range(250):
            x0 = rng.uniform(
                margin_x, max(margin_x + 1.0, page_width - qr_side - margin_x)
            )
            y0 = rng.uniform(
                margin_y, max(margin_y + 1.0, page_height - qr_side - margin_y)
            )
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
            idx = len(chosen)
            if idx == 0:
                best_candidate = (
                    margin_x,
                    margin_y,
                    margin_x + qr_side,
                    margin_y + qr_side,
                )
            else:
                best_candidate = (
                    page_width - qr_side - margin_x,
                    page_height - qr_side - margin_y,
                    page_width - margin_x,
                    page_height - margin_y,
                )

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
    """Paste two redundant frosted-glass QR codes using alpha compositing."""
    width, height = image.size
    backed_qr = build_frosted_qr_image(
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


# -----------------------------------------------------------------------------
# Dynamic Text Watermarking & Group Identity
# -----------------------------------------------------------------------------
def extract_group_identity(secret: str, position: str | None = None) -> str:
    """Extract the downloading group's name dynamically from secret or position parameter."""
    if position:
        for part in position.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                if (
                    k.strip().lower() in ("intended_for", "group", "identity")
                    and v.strip()
                ):
                    return v.strip().replace("_", " ").upper()
            elif part.lower().startswith("group"):
                return part.replace("_", " ").upper()

    if ":" in secret:
        parts = [p.strip() for p in secret.split(":") if p.strip()]
        if len(parts) >= 2:
            if parts[0].upper().startswith("FWM"):
                return parts[1].replace("_", " ").upper()
            return parts[0].replace("_", " ").upper()

    clean = secret.strip().replace("_", " ")
    if clean.upper().startswith("GROUP"):
        return clean.upper()

    if len(clean) <= 32:
        return clean.upper()

    return "CONFIDENTIAL"


def format_visible_label(identity: str | None, code: str = "") -> str:
    """Format visible copy label with group identity only (e.g. 'GROUP 01', 'GROUP 11')."""
    if identity and identity.strip():
        return identity.strip().replace("_", " ").upper()
    return "GROUP"


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load DejaVuSans-Bold or fall back gracefully to PIL default font."""
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def render_visible_text(
    image: Image.Image, code: str, identity: str | None = None
) -> Image.Image:
    """Draw semi-transparent diagonal copy labels on the page image with contrast borders."""
    width, height = image.size
    result = image.convert("RGBA")
    text_layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)
    font_size = max(20, min(width // 65, 42))
    font = load_font(font_size)
    label = format_visible_label(identity, code)

    for x_fraction, y_fraction in VISIBLE_TEXT_COORDINATES:
        x = int(width * x_fraction)
        y = int(height * y_fraction)
        draw.text(
            (x, y),
            label,
            font=font,
            fill=(10, 25, 35, 150),
            stroke_width=2,
            stroke_fill=(255, 255, 255, 190),
        )

    return Image.alpha_composite(result, text_layer).convert("RGB")


def stamp_random_native_visible_text(
    page: fitz.Page,
    label: str,
    placed_boxes: list[tuple[float, float, float, float]],
    count: int = 2,
    fontsize: float = 22.0,
    rotate: int = 30,
    seed_material: bytes | None = None,
    min_gap: float = 20.0,
) -> list[tuple[float, float, float, float]]:
    """Stamp up to `count` (default 2) diagonal visible text labels at random, non-overlapping coordinates.

    Appends bounding boxes to `placed_boxes` so that subsequent QR placements avoid them.
    Reiterates candidate positions on collision until a collision-free placement is found.
    """
    width = page.rect.width
    height = page.rect.height
    rng = (
        random.Random(
            int.from_bytes(hashlib.sha256(seed_material + b"/text").digest()[:8], "big")
        )
        if seed_material is not None
        else random.Random()
    )

    approx_len = len(label) * fontsize * 0.60
    rad = math.radians(rotate)
    span_x = approx_len * math.cos(rad)
    span_y = approx_len * math.sin(rad)

    margin_x = max(20.0, width * 0.05)
    margin_y = max(20.0, height * 0.05)

    chosen_boxes: list[tuple[float, float, float, float]] = []

    for _ in range(count):
        best_pt: tuple[float, float] | None = None
        best_box: tuple[float, float, float, float] | None = None

        for _attempt in range(250):
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
            for bx0, by0, bx1, by1 in placed_boxes:
                if not (
                    cand_box[2] + min_gap <= bx0
                    or bx1 + min_gap <= cand_box[0]
                    or cand_box[3] + min_gap <= by0
                    or by1 + min_gap <= cand_box[1]
                ):
                    collision = True
                    break

            if not collision:
                best_pt = (px, py)
                best_box = cand_box
                break

        if best_pt is None:
            idx = len(chosen_boxes)
            fallback_x = width * (0.15 if idx == 0 else 0.25)
            fallback_y = height * (0.35 if idx == 0 else 0.65)
            best_pt = (fallback_x, fallback_y)
            best_box = (
                fallback_x - 10.0,
                fallback_y - 10.0,
                fallback_x + span_x + 10.0,
                fallback_y + span_y + 10.0,
            )

        placed_boxes.append(best_box)
        chosen_boxes.append(best_box)

        pt = fitz.Point(best_pt[0], best_pt[1])
        page.insert_text(
            pt,
            label,
            fontsize=fontsize,
            color=(0.15, 0.22, 0.30),
            morph=(pt, fitz.Matrix(rotate)),
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
        count=2,
        fontsize=fontsize,
        rotate=rotate,
    )


# Convenience alias
apply_visible_text = render_visible_text

__all__ = [
    # Constants
    "DEFAULT_ALPHA_BG",
    "DEFAULT_ALPHA_DARK",
    "DEFAULT_QR_FRACTION",
    "QR_COORDINATES",
    "VISIBLE_TEXT_COORDINATES",
    "apply_qr_codes",
    "apply_visible_text",
    # Geometry & collision
    "boxes_overlap",
    "build_frosted_qr_bytes",
    # QR code operations
    "build_frosted_qr_image",
    "build_qr_image",
    "compute_dynamic_qr_coordinates",
    "detect_qr_payloads",
    "embed_qr_codes",
    # Text operations
    "extract_group_identity",
    "format_visible_label",
    "generate_random_qr_rects",
    "load_font",
    "render_visible_text",
    "stamp_native_visible_text",
    "stamp_random_native_visible_text",
]

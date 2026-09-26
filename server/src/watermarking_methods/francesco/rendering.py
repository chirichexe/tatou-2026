"""Drawing of the watermark: the opaque QR code, its placement and the visible labels"""

from __future__ import annotations

import hashlib
import io
import math
import random
import textwrap
from typing import Final

import pymupdf as fitz
import zxingcpp
from PIL import Image, ImageDraw, ImageFont

Box = tuple[float, float, float, float]

# ~10% of the shortest page dimension (about 2 cm on A4): at 6% a code has
# fewer than 2 pixels per module at the 300 DPI read resolution and did not decode reliably
DEFAULT_QR_FRACTION: Final[float] = 0.10
DEFAULT_VISIBLE_TEXT_COUNT: Final[int] = 6
DEFAULT_VISIBLE_TEXT_ROTATION: Final[int] = 45
VISIBLE_TEXT_ALPHA: Final[int] = 105
VISIBLE_WRAPPED_TEXT_ALPHA: Final[int] = VISIBLE_TEXT_ALPHA


def _rng(seed_material: bytes, label: bytes) -> random.Random:
    # placement only, nothing secret depends on it: the same copy gets the same layout
    seed = hashlib.sha256(seed_material + label).digest()[:8]
    return random.Random(int.from_bytes(seed, "big"))  # noqa: S311


# -----------------------------------------------------------------------------
# Geometry & collision avoidance
# -----------------------------------------------------------------------------
def collect_page_content_boxes(page: fitz.Page, padding: float = 8.0) -> list[Box]:
    """Return padded boxes for existing text, images, and vector drawings."""
    bounds = page.rect
    boxes: list[Box] = []

    def add(rect_like) -> None:
        rect = fitz.Rect(rect_like) & bounds
        if rect.is_empty or rect.is_infinite:
            return
        boxes.append((
            max(bounds.x0, rect.x0 - padding),
            max(bounds.y0, rect.y0 - padding),
            min(bounds.x1, rect.x1 + padding),
            min(bounds.y1, rect.y1 + padding),
        ))

    for block in page.get_text("blocks"):
        add(block[:4])
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            add(rect)
    for drawing in page.get_drawings():
        add(drawing["rect"])

    return boxes


def boxes_overlap(box1: Box, box2: Box, min_gap: float) -> bool:
    """Return True if two (x0, y0, x1, y1) boxes overlap or are closer than min_gap."""
    x0_1, y0_1, x1_1, y1_1 = box1
    x0_2, y0_2, x1_2, y1_2 = box2
    return not (
        x1_1 + min_gap <= x0_2
        or x1_2 + min_gap <= x0_1
        or y1_1 + min_gap <= y0_2
        or y1_2 + min_gap <= y0_1
    )


# -----------------------------------------------------------------------------
# QR code
# -----------------------------------------------------------------------------
def build_opaque_qr_bytes(payload: str, target_pixel_size: int = 240) -> bytes:
    """Generate an opaque QR code (maximum scanner contrast) as PNG bytes."""
    barcode = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.QRCode, ec_level="H")
    # Integer pixels per module: resizing to an arbitrary size makes modules
    # uneven, and about half of the codes then failed to decode at 300 DPI.
    modules = barcode.to_image(scale=1).shape[0]
    raw_img = barcode.to_image(scale=max(1, target_pixel_size // modules))
    qr_mask = Image.fromarray(raw_img).convert("L")

    dark_layer = Image.new("RGBA", qr_mask.size, (15, 25, 35, 255))
    light_layer = Image.new("RGBA", qr_mask.size, (255, 255, 255, 255))
    qr_layer = Image.composite(light_layer, dark_layer, qr_mask)

    border = max(6, target_pixel_size // 16)
    total_side = target_pixel_size + 2 * border
    backed = Image.new("RGBA", (total_side, total_side), (255, 255, 255, 255))
    backed.paste(qr_layer, (border, border), qr_layer)

    buffer = io.BytesIO()
    backed.save(buffer, format="PNG")
    return buffer.getvalue()


def random_qr_rect(
    page_width: float,
    page_height: float,
    placed_boxes: list[Box],
    seed_material: bytes,
    min_gap: float = 8.0,
    qr_fraction: float = DEFAULT_QR_FRACTION,
) -> Box:
    """A random QR box along the page border that does not touch `placed_boxes`.

    Existing content boxes are hard constraints: if there is no free border
    position, fail instead of covering page content.
    """
    rng = _rng(seed_material, b"/qr")
    qr_side = min(page_width, page_height) * qr_fraction
    edge_inset = max(4.0, min(page_width, page_height) * 0.012)
    if page_width < qr_side + 2 * edge_inset or page_height < qr_side + 2 * edge_inset:
        raise ValueError("Page is too small for a border QR code")

    for _attempt in range(500):
        edge = rng.randrange(4)
        if edge == 0:  # top
            x0, y0 = rng.uniform(edge_inset, page_width - qr_side - edge_inset), edge_inset
        elif edge == 1:  # bottom
            x0 = rng.uniform(edge_inset, page_width - qr_side - edge_inset)
            y0 = page_height - qr_side - edge_inset
        elif edge == 2:  # left
            x0, y0 = edge_inset, rng.uniform(edge_inset, page_height - qr_side - edge_inset)
        else:  # right
            x0 = page_width - qr_side - edge_inset
            y0 = rng.uniform(edge_inset, page_height - qr_side - edge_inset)
        candidate = (x0, y0, x0 + qr_side, y0 + qr_side)
        if not any(boxes_overlap(candidate, box, min_gap) for box in placed_boxes):
            return candidate

    raise ValueError("No text-free border position available for QR code")


def corner_qr_rect(
    page_width: float,
    page_height: float,
    seed_material: bytes,
    qr_fraction: float = DEFAULT_QR_FRACTION,
) -> Box:
    """A QR box flush to a random page corner, allowed to cover content."""
    if page_width <= 0 or page_height <= 0 or not 0 < qr_fraction <= 0.5:
        raise ValueError("Invalid page dimensions or QR placement parameters")
    side = min(page_width, page_height) * qr_fraction
    x = _rng(seed_material, b"/qr-edge").choice((0.0, page_width - side))
    y = _rng(seed_material, b"/qr-edge-y").choice((0.0, page_height - side))
    return (x, y, x + side, y + side)


# -----------------------------------------------------------------------------
# Visible labels
# -----------------------------------------------------------------------------
def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a monospaced font or fall back to PIL's default."""
    try:
        return ImageFont.truetype("DejaVuSansMono.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def _label_png(
    label: str, fontsize: float, rotate: int, alpha: int = VISIBLE_TEXT_ALPHA,
) -> tuple[bytes, float, float]:
    """The label as a rotated transparent PNG, and its size in points"""
    scale = 6  # render at 6x the point size, so the label stays sharp when zoomed
    font = load_font(max(1, round(fontsize * scale)))
    padding = max(4, round(fontsize * scale * 0.25))
    spacing = max(1, round(fontsize * scale * 0.25))
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    if "\n" in label:
        x0, y0, x1, y1 = measure.multiline_textbbox(
            (0, 0), label, font=font, spacing=spacing,
        )
    else:
        x0, y0, x1, y1 = measure.textbbox((0, 0), label, font=font)
    canvas = Image.new("RGBA", (x1 - x0 + 2 * padding, y1 - y0 + 2 * padding), (255, 255, 255, 0))
    draw = ImageDraw.Draw(canvas)
    if "\n" in label:
        draw.multiline_text(
            (padding - x0, padding - y0), label, font=font, spacing=spacing,
            fill=(38, 56, 76, alpha),
        )
    else:
        draw.text(
            (padding - x0, padding - y0), label, font=font, fill=(38, 56, 76, alpha),
        )
    rotated = canvas.rotate(rotate, expand=True, resample=Image.Resampling.BICUBIC)
    rotated.putalpha(rotated.getchannel("A").point(lambda alpha: min(alpha, VISIBLE_TEXT_ALPHA)))
    buffer = io.BytesIO()
    rotated.save(buffer, format="PNG")
    return buffer.getvalue(), rotated.width / scale, rotated.height / scale


def _wrap_visible_label(label: str, fontsize: float, max_width: float) -> str:
    """Keep the group heading and evenly sized ciphertext chunks on separate lines."""
    scale = 6
    font = load_font(max(1, round(fontsize * scale)))
    padding = max(4, round(fontsize * scale * 0.25))
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    character_width = measure.textlength("M", font=font)
    max_chars = math.floor((max_width * scale - 2 * padding) / character_width)
    if max_chars < 8:
        raise ValueError("Page is too narrow for a visible label")

    if " - " not in label:
        return "\n".join(textwrap.wrap(label, max_chars, break_long_words=True))

    group, payload = label.split(" - ", maxsplit=1)
    heading = textwrap.wrap(f"{group} -", max_chars, break_long_words=True)
    chunk_count = max(1, math.ceil(len(payload) / max_chars))
    chunk_size = math.ceil(len(payload) / chunk_count)
    chunks = [payload[index:index + chunk_size] for index in range(0, len(payload), chunk_size)]
    return "\n".join([*heading, *chunks])


def stamp_random_native_visible_text(
    page: fitz.Page,
    label: str,
    placed_boxes: list[Box],
    count: int = DEFAULT_VISIBLE_TEXT_COUNT,
    fontsize: float = 8.0,
    rotate: int = DEFAULT_VISIBLE_TEXT_ROTATION,
    seed_material: bytes = b"",
    min_gap: float = 8.0,
) -> list[Box]:
    """Stamp semi-transparent labels as images without adding PDF text operators."""
    width, height = page.rect.width, page.rect.height
    protected_boxes = tuple(placed_boxes)
    content_boxes = collect_page_content_boxes(page, padding=0)
    rng = _rng(seed_material, b"/text")

    margin_x = 10.0
    margin_y = 10.0
    usable_width = width - 2 * margin_x
    usable_height = height - 2 * margin_y
    if usable_width <= 0 or usable_height <= 0:
        raise ValueError("Page is too small for visible labels")

    wrap_width = min(
        usable_width,
        math.sqrt(usable_width * usable_height / max(1, count + 1)) * 1.15,
    )

    def label_images(size: float) -> list[tuple[bytes, float, float]]:
        direct_png, direct_width, direct_height = _label_png(label, size, rotate)
        needs_wrapping = (
            direct_width > usable_width
            or direct_height > usable_height
            or direct_width * direct_height * count > usable_width * usable_height
        )
        if not needs_wrapping:
            return [(direct_png, direct_width, direct_height)] * count

        wrapped_label = _wrap_visible_label(label, size, wrap_width)
        wrapped_png, wrapped_width, wrapped_height = _label_png(
            wrapped_label, size, 0, alpha=VISIBLE_WRAPPED_TEXT_ALPHA,
        )
        angled_size = size
        while direct_width > usable_width or direct_height > usable_height:
            if angled_size <= 0.5:
                raise ValueError("Page is too small for a visible label")
            angled_size = max(0.5, angled_size - 0.5)
            direct_png, direct_width, direct_height = _label_png(label, angled_size, rotate)
        return [(direct_png, direct_width, direct_height)] + [
            (wrapped_png, wrapped_width, wrapped_height) for _ in range(count - 1)
        ]

    def find_best_box(
        span_x: float, span_y: float, chosen: list[Box],
    ) -> Box | None:
        max_x = width - span_x - margin_x
        max_y = height - span_y - margin_y
        best_box: Box | None = None
        best_score = (-float("inf"), -1.0)
        for _attempt in range(1000):
            px, py = rng.uniform(margin_x, max_x), rng.uniform(margin_y, max_y)
            candidate = (px - 10.0, py - 10.0, px + span_x + 10.0, py + span_y + 10.0)
            if any(boxes_overlap(candidate, box, min_gap) for box in (*protected_boxes, *chosen)):
                continue
            content_hits = sum(boxes_overlap(candidate, box, 0) for box in content_boxes)
            center_x = (candidate[0] + candidate[2]) / 2
            center_y = (candidate[1] + candidate[3]) / 2
            distance = min(
                (center_x - (box[0] + box[2]) / 2) ** 2
                + (center_y - (box[1] + box[3]) / 2) ** 2
                for box in chosen
            ) if chosen else 0.0
            score = (-content_hits, distance)
            if score > best_score:
                best_box, best_score = candidate, score
        return best_box

    effective_fontsize = fontsize
    while effective_fontsize >= 0.5:
        try:
            images = label_images(effective_fontsize)
        except ValueError:
            effective_fontsize -= 0.5
            continue
        chosen_boxes: list[Box] = []
        for _png, span_x, span_y in images:
            if span_x > usable_width or span_y > usable_height:
                break
            box = find_best_box(span_x, span_y, chosen_boxes)
            if box is None:
                break
            chosen_boxes.append(box)
        if len(chosen_boxes) == count:
            break
        effective_fontsize -= 0.5
    else:
        raise ValueError("Page has insufficient room for visible labels")

    for (png, span_x, span_y), box in zip(images, chosen_boxes, strict=True):
        placed_boxes.append(box)
        px, py = box[0] + 10.0, box[1] + 10.0
        page.insert_image(fitz.Rect(px, py, px + span_x, py + span_y), stream=png, overlay=True)

    return chosen_boxes

"""Drawing of the watermark: the opaque QR code, its placement and the visible labels"""

from __future__ import annotations

import hashlib
import io
import random
from typing import Final

import pymupdf as fitz
import zxingcpp
from PIL import Image, ImageDraw, ImageFont

Box = tuple[float, float, float, float]

# ~10% of the shortest page dimension (about 2 cm on A4): at 6% a code has
# fewer than 2 pixels per module at the 300 DPI read resolution and did not decode reliably
DEFAULT_QR_FRACTION: Final[float] = 0.10
DEFAULT_VISIBLE_TEXT_COUNT: Final[int] = 6
VISIBLE_TEXT_ALPHA: Final[int] = 110


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


def _label_png(label: str, fontsize: float, rotate: int) -> tuple[bytes, float, float]:
    """The label as a rotated transparent PNG, and its size in points"""
    scale = 6  # render at 6x the point size, so the label stays sharp when zoomed
    font = load_font(max(1, round(fontsize * scale)))
    padding = max(4, round(fontsize * scale * 0.25))
    x0, y0, x1, y1 = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), label, font=font)
    canvas = Image.new("RGBA", (x1 - x0 + 2 * padding, y1 - y0 + 2 * padding), (255, 255, 255, 0))
    ImageDraw.Draw(canvas).text(
        (padding - x0, padding - y0), label, font=font, fill=(38, 56, 76, VISIBLE_TEXT_ALPHA),
    )
    rotated = canvas.rotate(rotate, expand=True, resample=Image.Resampling.BICUBIC)
    buffer = io.BytesIO()
    rotated.save(buffer, format="PNG")
    return buffer.getvalue(), rotated.width / scale, rotated.height / scale


def stamp_random_native_visible_text(
    page: fitz.Page,
    label: str,
    placed_boxes: list[Box],
    count: int = DEFAULT_VISIBLE_TEXT_COUNT,
    fontsize: float = 8.0,
    rotate: int = 30,
    seed_material: bytes = b"",
    min_gap: float = 8.0,
) -> list[Box]:
    """Stamp semi-transparent labels as images, without adding PDF text operators.

    The supplied boxes are hard exclusions, normally the QR area. Labels may
    cross document content; their centers are spread across the page to limit
    mutual overlap without making placement fail on small pages.
    """
    width, height = page.rect.width, page.rect.height
    protected_boxes = tuple(placed_boxes)
    rng = _rng(seed_material, b"/text")

    # shrink long labels to about 72% of the page width, never below 6 pt
    effective_fontsize = min(fontsize, max(6.0, width * 0.72 / (len(label) * 0.62)))
    label_png, span_x, span_y = _label_png(label, effective_fontsize, rotate)

    margin_x = max(20.0, width * 0.05)
    margin_y = max(20.0, height * 0.05)
    max_x = max(margin_x + 1.0, width - span_x - margin_x)
    max_y = max(margin_y + 1.0, height - span_y - margin_y)

    chosen_boxes: list[Box] = []
    for _ in range(count):
        best_box: Box | None = None
        best_distance = -1.0

        for _attempt in range(1000):
            px, py = rng.uniform(margin_x, max_x), rng.uniform(margin_y, max_y)
            cand_box = (px - 10.0, py - 10.0, px + span_x + 10.0, py + span_y + 10.0)
            if any(boxes_overlap(cand_box, box, min_gap) for box in protected_boxes):
                continue
            if not chosen_boxes:
                best_box = cand_box
                break
            # keep the candidate farthest from the labels already placed
            center_x, center_y = (cand_box[0] + cand_box[2]) / 2, (cand_box[1] + cand_box[3]) / 2
            distance = min(
                (center_x - (box[0] + box[2]) / 2) ** 2 + (center_y - (box[1] + box[3]) / 2) ** 2
                for box in chosen_boxes
            )
            if distance > best_distance:
                best_box, best_distance = cand_box, distance

        if best_box is None:
            raise ValueError("Page has insufficient room for visible labels")

        placed_boxes.append(best_box)
        chosen_boxes.append(best_box)
        px, py = best_box[0] + 10.0, best_box[1] + 10.0
        page.insert_image(fitz.Rect(px, py, px + span_x, py + span_y), stream=label_png, overlay=True)

    return chosen_boxes

"""Visual rendering components: typography, visible watermark overlay, and QR code embedding."""
from __future__ import annotations

import hashlib
from typing import Final, Sequence
from PIL import Image, ImageDraw, ImageFont
import zxingcpp

# Default placement fractions for visible text labels (x, y)
VISIBLE_TEXT_COORDINATES: Final[Sequence[tuple[float, float]]] = (
    (0.07, 0.15),
    (0.28, 0.38),
    (0.64, 0.635),
    (0.07, 0.94),
)

# Default placement fractions for QR codes (x, y)
QR_COORDINATES: Final[Sequence[tuple[float, float]]] = (
    (0.04, 0.23),
    (0.74, 0.40),
)


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load DejaVuSans-Bold or fall back gracefully to PIL default font."""
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def render_visible_text(image: Image.Image, code: str) -> Image.Image:
    """Draw semi-transparent diagonal copy labels on the page image with contrast borders."""
    width, height = image.size
    result = image.convert("RGBA")
    text_layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)
    font_size = max(20, min(width // 65, 42))
    font = load_font(font_size)
    label = f"TATOU COPY {code}"

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


def build_qr_image(payload: str, width: int, height: int) -> Image.Image:
    """Generate a high-ECC QR code enclosed within a quiet-zone white border."""
    barcode = zxingcpp.create_barcode(
        payload,
        zxingcpp.BarcodeFormat.QRCode,
        ec_level="H",
    )
    qr = Image.fromarray(barcode.to_image(scale=8)).convert("RGB")
    qr_side = min(int(width * 0.145), int(height * 0.13))
    qr = qr.resize((qr_side, qr_side), Image.Resampling.NEAREST)
    border = max(8, qr_side // 18)
    backed = Image.new("RGB", (qr_side + 2 * border, qr_side + 2 * border), "white")
    backed.paste(qr, (border, border))
    return backed


def compute_dynamic_qr_coordinates(
    seed_material: bytes,
    width: int,
    height: int,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Compute pseudo-random, non-overlapping coordinates for 2 QR codes.

    The coordinates are pseudo-random and derived from document/secret content,
    completely independent of the master key, preventing fixed-coordinate erasure attacks.
    """
    h = hashlib.sha256(seed_material + b"/qr_coords/v1").digest()
    # QR 1 in top-left region: x in [0.03, 0.20], y in [0.10, 0.35]
    r1_x = int.from_bytes(h[0:4], "big") / (2**32)
    r1_y = int.from_bytes(h[4:8], "big") / (2**32)
    x1 = 0.03 + r1_x * 0.17
    y1 = 0.10 + r1_y * 0.25

    # QR 2 in bottom-right region: x in [0.55, 0.75], y in [0.45, 0.70]
    r2_x = int.from_bytes(h[8:12], "big") / (2**32)
    r2_y = int.from_bytes(h[12:16], "big") / (2**32)
    x2 = 0.55 + r2_x * 0.20
    y2 = 0.45 + r2_y * 0.25

    return ((x1, y1), (x2, y2))


def embed_qr_codes(
    image: Image.Image,
    payload: str,
    coordinates: Sequence[tuple[float, float]] | None = None,
) -> Image.Image:
    """Paste two redundant QR codes at designated or dynamic locations on the page."""
    width, height = image.size
    backed_qr = build_qr_image(payload, width, height)
    result = image.copy()

    coords = coordinates if coordinates is not None else QR_COORDINATES

    for x_fraction, y_fraction in coords:
        x = min(int(width * x_fraction), width - backed_qr.width)
        y = min(int(height * y_fraction), height - backed_qr.height)
        result.paste(backed_qr, (x, y))

    return result


# Convenience aliases
apply_visible_text = render_visible_text
apply_qr_codes = embed_qr_codes



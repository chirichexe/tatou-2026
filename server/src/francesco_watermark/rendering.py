"""Visual rendering components: typography, visible watermark overlay, and QR code embedding."""
from __future__ import annotations

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


def embed_qr_codes(image: Image.Image, payload: str) -> Image.Image:
    """Paste two redundant QR codes at opposite diagonal locations on the page."""
    width, height = image.size
    backed_qr = build_qr_image(payload, width, height)
    result = image.copy()

    for x_fraction, y_fraction in QR_COORDINATES:
        x = min(int(width * x_fraction), width - backed_qr.width)
        y = min(int(height * y_fraction), height - backed_qr.height)
        result.paste(backed_qr, (x, y))

    return result


# Convenience aliases
apply_visible_text = render_visible_text
apply_qr_codes = embed_qr_codes


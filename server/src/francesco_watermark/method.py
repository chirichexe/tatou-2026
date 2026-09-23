"""Rasterized, visibly redundant PDF watermark with authenticated QR payloads.

The optional TrustMark experiment is deliberately separate and never enabled by
the default method.  The printable code is a keyed fingerprint of ``secret``;
only the encrypted QR can recover the full secret.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import math
import re
from typing import Final

import fitz
import zxingcpp
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from PIL import Image, ImageDraw, ImageFont
from watermarking_method import (
    InvalidKeyError,
    PdfSource,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    load_pdf_bytes,
)


class HybridPageWatermark(WatermarkingMethod):
    """Visible copy fingerprint plus repeated AES-SIV-protected QR codes."""

    name: Final[str] = "hybrid-page"
    DPI: Final[int] = 300
    MAX_INPUT_BYTES: Final[int] = 64 * 1024 * 1024
    MAX_PAGES: Final[int] = 10
    MAX_PIXELS_PER_PAGE: Final[int] = 16_000_000
    MAX_SECRET_BYTES: Final[int] = 64
    MAX_OUTPUT_IMAGE_BYTES: Final[int] = 64 * 1024 * 1024
    PREFIX: Final[str] = "TW1:"
    EXPERIMENTAL_POSITION: Final[str] = "experimental-trustmark"
    ASSIGNED_PHOTO_FRACTIONS: Final[tuple[float, float, float, float]] = (
        0.1563, 0.1243, 0.8436, 0.6243,
    )
    _HEX_KEY: Final[re.Pattern[str]] = re.compile(r"[0-9a-fA-F]{64}\Z")

    @staticmethod
    def get_usage() -> str:
        return (
            "Rasterized visible copy code and authenticated QR codes; "
            "use a random 32-byte hex key. Optional position: experimental-trustmark."
        )

    @classmethod
    def _key_material(cls, key: str) -> bytes:
        if not isinstance(key, str) or not cls._HEX_KEY.fullmatch(key):
            raise ValueError("hybrid-page requires a 32-byte key encoded as 64 hex characters")
        return bytes.fromhex(key)

    @classmethod
    def _derive(cls, key: str, purpose: bytes, length: int) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=length, salt=None,
            info=b"tatou/hybrid-page/v1/" + purpose,
        ).derive(cls._key_material(key))

    @classmethod
    def visible_code(cls, secret: str, key: str) -> str:
        """Short printable fingerprint; not a standalone proof of authenticity."""
        digest = hmac.new(
            cls._derive(key, b"visible", 32), secret.encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        return digest[:16].upper()

    @classmethod
    def expected_photo_tag(cls, secret: str, key: str) -> str:
        """Experimental keyed tag to compare with a decoded photo tag."""
        from .trustmark_experiment import payload_for_copy

        return payload_for_copy(secret, cls._derive(key, b"trustmark-tag", 32))

    @classmethod
    def _payload(cls, secret: str, key: str) -> str:
        ciphertext = AESSIV(cls._derive(key, b"qr-aessiv", 64)).encrypt(
            secret.encode("utf-8"), [b"tatou/hybrid-page/qr/v1"],
        )
        return cls.PREFIX + base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii")

    @classmethod
    def _unpack(cls, payload: str, key: str) -> str:
        encoded = payload[len(cls.PREFIX):]
        if not encoded or len(encoded) > 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
            raise InvalidKeyError("Malformed hybrid-page QR")
        try:
            ciphertext = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii") != encoded:
                raise ValueError("Noncanonical QR encoding")
            plaintext = AESSIV(cls._derive(key, b"qr-aessiv", 64)).decrypt(
                ciphertext, [b"tatou/hybrid-page/qr/v1"],
            )
            secret = plaintext.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeError, binascii.Error) as exc:
            raise InvalidKeyError("Hybrid-page QR authentication failed") from exc
        if not secret:
            raise InvalidKeyError("Empty hybrid-page QR")
        return secret

    @classmethod
    def _check_document(cls, data: bytes, position: str | None) -> bool:
        if position not in (None, "", cls.EXPERIMENTAL_POSITION) or len(data) > cls.MAX_INPUT_BYTES:
            return False
        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                if document.is_encrypted or not 1 <= document.page_count <= cls.MAX_PAGES:
                    return False
                for page in document:
                    width = math.ceil(page.rect.width * cls.DPI / 72)
                    height = math.ceil(page.rect.height * cls.DPI / 72)
                    if width < 500 or height < 500 or width * height > cls.MAX_PIXELS_PER_PAGE:
                        return False
                    if position == cls.EXPERIMENTAL_POSITION:
                        photo = cls._photo_rect(page)
                        if document.page_count != 1 or photo is None:
                            return False
                        ratios = (
                            photo.x0 / page.rect.width, photo.y0 / page.rect.height,
                            photo.x1 / page.rect.width, photo.y1 / page.rect.height,
                        )
                        if any(abs(actual - expected) > 0.02 for actual, expected in
                                zip(ratios, cls.ASSIGNED_PHOTO_FRACTIONS)):
                            return False
        except (fitz.FileDataError, ValueError, RuntimeError):
            return False
        return True

    def is_watermark_applicable(self, pdf: PdfSource, position: str | None = None) -> bool:
        try:
            return self._check_document(load_pdf_bytes(pdf), position)
        except (OSError, TypeError, ValueError):
            return False

    @staticmethod
    def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        except OSError:
            return ImageFont.load_default(size=size)

    @classmethod
    def _mark_page(cls, image: Image.Image, payload: str, code: str) -> Image.Image:
        width, height = image.size
        result = image.convert("RGBA")
        text_layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_layer)
        font = cls._font(max(20, min(width // 65, 42)))
        label = f"TATOU COPY {code}"
        for x_fraction, y_fraction in (
            (0.07, 0.15), (0.28, 0.38), (0.64, 0.635), (0.07, 0.94),
        ):
            x = int(width * x_fraction)
            y = int(height * y_fraction)
            draw.text(
                (x, y), label, font=font, fill=(10, 25, 35, 150),
                stroke_width=2, stroke_fill=(255, 255, 255, 190),
            )
        result = Image.alpha_composite(result, text_layer)

        barcode = zxingcpp.create_barcode(
            payload, zxingcpp.BarcodeFormat.QRCode, ec_level="H",
        )
        qr = Image.fromarray(barcode.to_image(scale=8)).convert("RGB")
        qr_side = min(int(width * 0.145), int(height * 0.13))
        qr = qr.resize((qr_side, qr_side), Image.Resampling.NEAREST)
        border = max(8, qr_side // 18)
        backed = Image.new("RGB", (qr_side + 2 * border, qr_side + 2 * border), "white")
        backed.paste(qr, (border, border))
        for x_fraction, y_fraction in ((0.04, 0.23), (0.74, 0.40)):
            x = min(int(width * x_fraction), width - backed.width)
            y = min(int(height * y_fraction), height - backed.height)
            result.paste(backed, (x, y))
        return result.convert("RGB")

    @staticmethod
    def _photo_rect(page: fitz.Page) -> fitz.Rect | None:
        candidates = []
        for image in page.get_images(full=True):
            for rect in page.get_image_rects(image[0]):
                if rect.get_area() >= page.rect.get_area() * 0.10:
                    candidates.append(rect)
        return max(candidates, key=lambda rect: rect.get_area(), default=None)

    @classmethod
    def _mark_photo_experiment(
        cls, image: Image.Image, page: fitz.Page, secret: str, key: str,
    ) -> Image.Image:
        from .trustmark_experiment import embed_photo

        rect = cls._photo_rect(page)
        if rect is None:
            raise ValueError("No suitable photographic region for TrustMark")
        scale_x = image.width / page.rect.width
        scale_y = image.height / page.rect.height
        box = (
            max(0, int(rect.x0 * scale_x)), max(0, int(rect.y0 * scale_y)),
            min(image.width, int(rect.x1 * scale_x)),
            min(image.height, int(rect.y1 * scale_y)),
        )
        crop = image.crop(box)
        marked = embed_photo(crop, secret, cls._derive(key, b"trustmark-tag", 32))
        if marked.size != crop.size:
            raise WatermarkingError("TrustMark changed photo dimensions")
        image.paste(marked, box[:2])
        return image

    def add_watermark(
        self, pdf: PdfSource, secret: str, key: str, position: str | None = None,
    ) -> bytes:
        self._key_material(key)
        if not isinstance(secret, str) or not 0 < len(secret.encode("utf-8")) <= self.MAX_SECRET_BYTES:
            raise ValueError("hybrid-page secret must be 1-64 UTF-8 bytes")
        data = load_pdf_bytes(pdf)
        if not self._check_document(data, position):
            raise ValueError("PDF is not applicable to hybrid-page")
        payload = self._payload(secret, key)
        code = self.visible_code(secret, key)
        image_bytes_total = 0
        with fitz.open(stream=data, filetype="pdf") as source, fitz.open() as output:
            for page in source:
                pixmap = page.get_pixmap(dpi=self.DPI, colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                if position == self.EXPERIMENTAL_POSITION:
                    image = self._mark_photo_experiment(image, page, secret, key)
                marked = self._mark_page(image, payload, code)
                buffer = io.BytesIO()
                marked.save(buffer, format="PNG", optimize=False)
                image_bytes_total += buffer.tell()
                if image_bytes_total > self.MAX_OUTPUT_IMAGE_BYTES:
                    raise WatermarkingError("Watermarked pages exceed output size limit")
                new_page = output.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(new_page.rect, stream=buffer.getvalue())
            return output.tobytes(garbage=4, deflate=True, no_new_id=True)

    def read_secret(self, pdf: PdfSource, key: str) -> str:
        self._key_material(key)
        data = load_pdf_bytes(pdf)
        if not self._check_document(data, None):
            raise ValueError("PDF is not applicable to hybrid-page")
        found: set[str] = set()
        invalid = False
        with fitz.open(stream=data, filetype="pdf") as document:
            for page in document:
                pixmap = page.get_pixmap(dpi=220, colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                for barcode in zxingcpp.read_barcodes(
                    image, formats=zxingcpp.BarcodeFormat.QRCode,
                ):
                    if not barcode.text.startswith(self.PREFIX):
                        continue
                    try:
                        found.add(self._unpack(barcode.text, key))
                    except InvalidKeyError:
                        invalid = True
        if invalid:
            raise InvalidKeyError("At least one hybrid-page QR is invalid")
        if len(found) > 1:
            raise WatermarkingError("Conflicting hybrid-page copy identifiers")
        if not found:
            raise SecretNotFoundError("No authenticated hybrid-page QR found")
        return found.pop()

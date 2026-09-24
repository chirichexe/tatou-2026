"""Rasterized, visibly redundant PDF watermark with authenticated QR payloads.

Aggregates modular components:
- crypto: Key derivation (HKDF) and authenticated AES-SIV QR payloads
- rendering: Typography, visible overlays, and QR code placement
- pdf: Document validation, page rasterization, and PDF reconstruction
- trustmark_experiment: Optional deep learning photographic watermarking

Supports layer toggling via configurable class-level flags.
"""
from __future__ import annotations

import re
from typing import ClassVar, Final

import fitz
from PIL import Image
import zxingcpp

from watermarking_method import (
    InvalidKeyError,
    PdfSource,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    load_pdf_bytes,
    validate_secret_string,
)

from . import crypto
from . import pdf as pdf_ops
from . import rendering


class HybridPageWatermark(WatermarkingMethod):
    """Visible copy fingerprint plus repeated AES-SIV-protected QR codes."""

    name: Final[str] = "hybrid-page"
    DPI: Final[int] = pdf_ops.DEFAULT_DPI
    MAX_INPUT_BYTES: Final[int] = pdf_ops.MAX_INPUT_BYTES
    MAX_PAGES: Final[int] = pdf_ops.MAX_PAGES
    MAX_PIXELS_PER_PAGE: Final[int] = pdf_ops.MAX_PIXELS_PER_PAGE
    MAX_SECRET_BYTES: Final[int] = 64
    MAX_OUTPUT_IMAGE_BYTES: Final[int] = pdf_ops.MAX_OUTPUT_IMAGE_BYTES
    PREFIX: Final[str] = "TW1:"
    EXPERIMENTAL_POSITION: Final[str] = "experimental-trustmark"
    ASSIGNED_PHOTO_FRACTIONS: Final[tuple[float, float, float, float]] = (
        pdf_ops.ASSIGNED_PHOTO_FRACTIONS
    )
    _HEX_KEY: Final[re.Pattern[str]] = crypto.HEX_KEY_PATTERN

    # -------------------------------------------------------------------------
    # Layer activation flags (hardcoded to True by default, customizable)
    # -------------------------------------------------------------------------
    ENABLE_RASTER_BASE: ClassVar[bool] = True      # Step 0: Mandatory raster base layer
    ENABLE_VISIBLE_TEXT: ClassVar[bool] = True     # Step 1: Visible "TATOU COPY" overlay
    ENABLE_QR_WATERMARK: ClassVar[bool] = True     # Step 2: Encrypted AES-SIV QR codes
    ENABLE_TRUSTMARK_PHOTO: ClassVar[bool] = True  # Step 3: Optional TrustMark photo embedding

    @staticmethod
    def get_usage() -> str:
        return (
            "Rasterized visible copy code and authenticated QR codes; "
            "use a random 32-byte hex key. Optional position: experimental-trustmark."
        )

    # -------------------------------------------------------------------------
    # Cryptographic delegates & compatibility methods
    # -------------------------------------------------------------------------
    @classmethod
    def _key_material(cls, key: str) -> bytes:
        return crypto.parse_hex_key(key)

    @classmethod
    def _derive(cls, key: str, purpose: bytes, length: int) -> bytes:
        return crypto.derive_sub_key(key, purpose, length)

    @classmethod
    def visible_code(cls, secret: str, key: str) -> str:
        """Short printable fingerprint; not a standalone proof of authenticity."""
        return crypto.compute_visible_code(secret, key)

    @classmethod
    def expected_photo_tag(cls, secret: str, key: str) -> str:
        """Experimental keyed tag to compare with a decoded photo tag."""
        return crypto.compute_expected_photo_tag(secret, key)

    @classmethod
    def _payload(cls, secret: str, key: str) -> str:
        return crypto.encrypt_qr_payload(secret, key, cls.PREFIX)

    @classmethod
    def _unpack(cls, payload: str, key: str) -> str:
        return crypto.decrypt_qr_payload(payload, key, cls.PREFIX)

    # -------------------------------------------------------------------------
    # PDF & visual helpers
    # -------------------------------------------------------------------------
    @classmethod
    def _check_document(cls, data: bytes, position: str | None) -> bool:
        return pdf_ops.is_document_applicable(data, position, cls.EXPERIMENTAL_POSITION)

    @staticmethod
    def _photo_rect(page: fitz.Page) -> fitz.Rect | None:
        return pdf_ops.find_primary_photo_rect(page)

    @staticmethod
    def _font(size: int):
        return rendering.load_font(size)

    @classmethod
    def _mark_page(cls, image: Image.Image, payload: str, code: str) -> Image.Image:
        """Combine visible text and QR code layers on an image."""
        if cls.ENABLE_VISIBLE_TEXT and code:
            image = rendering.render_visible_text(image, code)
        if cls.ENABLE_QR_WATERMARK and payload:
            seed_material = (code + ":" + payload).encode("ascii")
            coords = rendering.compute_dynamic_qr_coordinates(
                seed_material, image.width, image.height,
            )
            image = rendering.embed_qr_codes(image, payload, coordinates=coords)
        return image

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
            max(0, int(rect.x0 * scale_x)),
            max(0, int(rect.y0 * scale_y)),
            min(image.width, int(rect.x1 * scale_x)),
            min(image.height, int(rect.y1 * scale_y)),
        )
        crop = image.crop(box)
        key_material = cls._derive(key, b"trustmark-tag", 32)
        marked = embed_photo(crop, secret, key_material)
        if marked.size != crop.size:
            raise WatermarkingError("TrustMark changed photo dimensions")
        image.paste(marked, box[:2])
        return image

    # -------------------------------------------------------------------------
    # Primary Watermarking Interface
    # -------------------------------------------------------------------------
    def is_watermark_applicable(self, pdf: PdfSource, position: str | None = None) -> bool:
        try:
            return self._check_document(load_pdf_bytes(pdf), position)
        except (OSError, TypeError, ValueError):
            return False

    def add_watermark(
        self, pdf: PdfSource, secret: str, key: str, position: str | None = None,
    ) -> bytes:
        # Step 0: Ensure foundational base layer is active
        if not self.ENABLE_RASTER_BASE:
            raise WatermarkingError("Base rasterization layer is required for hybrid-page watermarking")

        self._key_material(key)
        validate_secret_string(secret, self.MAX_SECRET_BYTES)

        data = load_pdf_bytes(pdf)
        if not self._check_document(data, position):
            raise ValueError("PDF is not applicable to hybrid-page")

        payload = self._payload(secret, key) if self.ENABLE_QR_WATERMARK else ""
        code = self.visible_code(secret, key) if self.ENABLE_VISIBLE_TEXT else ""

        page_records: list[tuple[Image.Image, float, float]] = []

        with fitz.open(stream=data, filetype="pdf") as source:
            for page in source:
                image = pdf_ops.rasterize_page(page, dpi=self.DPI)

                # Optional Step 3: Experimental TrustMark photo watermark
                if position == self.EXPERIMENTAL_POSITION:
                    if not self.ENABLE_TRUSTMARK_PHOTO:
                        raise ValueError("TrustMark photo layer is disabled")
                    image = self._mark_photo_experiment(image, page, secret, key)

                # Step 1 & 2: Visible text and encrypted QR codes
                marked = self._mark_page(image, payload, code)
                page_records.append((marked, page.rect.width, page.rect.height))

        return pdf_ops.assemble_pdf_from_images(page_records)

    def read_secret(self, pdf: PdfSource, key: str) -> str:
        # Step prerequisite: Reading secret requires the QR watermark layer
        if not self.ENABLE_QR_WATERMARK:
            raise WatermarkingError("QR watermark layer is disabled; cannot extract secret")

        self._key_material(key)
        data = load_pdf_bytes(pdf)
        if not self._check_document(data, None):
            raise ValueError("PDF is not applicable to hybrid-page")

        found: set[str] = set()
        invalid = False

        with fitz.open(stream=data, filetype="pdf") as document:
            for page in document:
                image = pdf_ops.rasterize_page(page, dpi=pdf_ops.READ_DPI)
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

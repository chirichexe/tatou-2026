"""Native PDF watermarking with authenticated frosted-glass QR codes and optional visible copy labels.

Aggregates modular components:
- crypto: Key derivation (HKDF) and authenticated AES-SIV QR payloads
- qr: Frosted-glass QR generation, collision-free random placement, and detection
- rendering: Typography and native visible text overlays
- pdf: Native PDF stamping without rasterization, preserving original text streams (BT/Tw)

Supports layer toggling via configurable class-level flags and runtime position parameters.
"""

from __future__ import annotations

import re
from typing import ClassVar, Final

import pymupdf as fitz
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
from . import rendering as render_ops

text_ops = qr_ops = render_ops


NAME: Final[str] = "francesco-watermark"


class FrancescoWatermark(WatermarkingMethod):
    """Visible copy fingerprint plus repeated AES-SIV-protected QR codes."""

    name: Final[str] = NAME
    DPI: Final[int] = pdf_ops.DEFAULT_DPI
    MAX_INPUT_BYTES: Final[int] = pdf_ops.MAX_INPUT_BYTES
    MAX_PAGES: Final[int] = pdf_ops.MAX_PAGES
    MAX_PIXELS_PER_PAGE: Final[int] = pdf_ops.MAX_PIXELS_PER_PAGE
    MAX_SECRET_BYTES: Final[int] = 64
    MAX_OUTPUT_IMAGE_BYTES: Final[int] = pdf_ops.MAX_OUTPUT_IMAGE_BYTES
    PREFIX: Final[str] = "FWM1:"
    _HEX_KEY: Final[re.Pattern[str]] = crypto.HEX_KEY_PATTERN

    # -------------------------------------------------------------------------
    # Layer activation flags (customizable, defaults: QR active, visible text inactive)
    # -------------------------------------------------------------------------
    ENABLE_RASTER_BASE: ClassVar[bool] = True  # Step 0: Foundational watermark layer
    ENABLE_QR_WATERMARK: ClassVar[bool] = (
        True  # Step 1: Encrypted frosted-glass QR codes (Default: TRUE)
    )
    ENABLE_VISIBLE_TEXT: ClassVar[bool] = (
        False  # Step 2: Visible diagonal text with group name (Default: FALSE)
    )

    @staticmethod
    def get_usage() -> str:
        return (
            "Native PDF overlay with authenticated frosted QR codes and optional visible copy labels; "
            "preserves original text streams. Supports any passphrase or hex key."
        )

    # -------------------------------------------------------------------------
    # Cryptographic delegates & compatibility methods
    # -------------------------------------------------------------------------
    @classmethod
    def _key_material(cls, key: str) -> bytes:
        return crypto.derive_aes_key(key)

    @classmethod
    def _derive(cls, key: str, purpose: bytes, length: int) -> bytes:
        return crypto.derive_sub_key(key, purpose, length)

    @classmethod
    def visible_code(cls, secret: str, key: str) -> str:
        """Short printable fingerprint; not a standalone proof of authenticity."""
        return crypto.compute_visible_code(secret, key)

    @classmethod
    def _payload(cls, secret: str, key: str) -> str:
        return crypto.encrypt_qr_payload(secret, key)

    @classmethod
    def _unpack(cls, payload: str, key: str) -> str:
        return crypto.decrypt_qr_payload(payload, key)

    # -------------------------------------------------------------------------
    # PDF & visual helpers
    # -------------------------------------------------------------------------
    @classmethod
    def _check_document(cls, data: bytes, position: str | None) -> bool:
        return pdf_ops.is_document_applicable(data, position)

    @staticmethod
    def _font(size: int):
        return text_ops.load_font(size)

    # -------------------------------------------------------------------------
    # Primary Watermarking Interface
    # -------------------------------------------------------------------------
    def is_watermark_applicable(
        self, pdf: PdfSource, position: str | None = None
    ) -> bool:
        try:
            return self._check_document(load_pdf_bytes(pdf), position)
        except (OSError, TypeError, ValueError):
            return False

    def add_watermark(
        self,
        pdf: PdfSource,
        secret: str,
        key: str,
        position: str | None = None,
    ) -> bytes:
        # Step 0: Base layer check
        if not self.ENABLE_RASTER_BASE:
            raise WatermarkingError(
                "Base rasterization layer is required for francesco-watermark watermarking"
            )

        self._key_material(key)
        validate_secret_string(secret, self.MAX_SECRET_BYTES)

        data = load_pdf_bytes(pdf)
        if not self._check_document(data, position):
            raise ValueError("PDF is not applicable to francesco-watermark")

        # Layer determination from defaults and optional runtime position hint
        enable_qr = self.ENABLE_QR_WATERMARK
        enable_text = self.ENABLE_VISIBLE_TEXT

        if position:
            pos_lower = position.lower()
            if any(k in pos_lower for k in ("text", "all", "full", "with-text")):
                enable_text = True
            if "no-qr" in pos_lower or "text-only" in pos_lower:
                enable_qr = False
            if "qr-only" in pos_lower:
                enable_qr = True
                enable_text = False

        payload = self._payload(secret, key) if enable_qr else ""
        self.visible_code(secret, key)

        # Extract group identity dynamically for whichever group downloads the file
        group_name = text_ops.extract_group_identity(secret, position)
        code_label = text_ops.format_visible_label(group_name)

        # Pre-generate frosted QR PNG bytes once per document
        qr_bytes = qr_ops.build_frosted_qr_bytes(payload) if payload else b""

        seed_material = (secret + ":" + key).encode("ascii")

        # Native PDF overlay without flattening/rasterizing existing text streams
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                # Maintain list of placed bounding boxes across steps to prevent overlap
                placed_boxes: list[tuple[float, float, float, float]] = []

                # Step 1: Visible diagonal text with group name (random, at most 2, collision-free)
                if enable_text and code_label:
                    text_ops.stamp_random_native_visible_text(
                        page=page,
                        label=code_label,
                        placed_boxes=placed_boxes,
                        count=2,
                        seed_material=seed_material,
                    )

                # Step 2: Encrypted frosted-glass QR codes (if enabled)
                if enable_qr and qr_bytes:
                    qr_rects = qr_ops.generate_random_qr_rects(
                        page_width=page.rect.width,
                        page_height=page.rect.height,
                        count=2,
                        placed_boxes=placed_boxes,
                        seed_material=seed_material,
                    )
                    for r in qr_rects:
                        pdf_ops.stamp_qr_on_page(page, qr_bytes, r)

            return doc.tobytes(
                garbage=0, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE
            )

    def read_secret(self, pdf: PdfSource, key: str) -> str:
        # Prerequisite: Reading secret requires the QR watermark layer
        if not self.ENABLE_QR_WATERMARK:
            raise WatermarkingError(
                "QR watermark layer is disabled; cannot extract secret"
            )

        self._key_material(key)
        data = load_pdf_bytes(pdf)
        if not self._check_document(data, None):
            raise ValueError("PDF is not applicable to francesco-watermark")

        found: set[str] = set()
        invalid = False

        # In-memory rendering for barcode scanning (does not alter the PDF on disk)
        with fitz.open(stream=data, filetype="pdf") as document:
            for page in document:
                image = pdf_ops.rasterize_page(page, dpi=pdf_ops.READ_DPI)
                for barcode in zxingcpp.read_barcodes(
                    image,
                    formats=zxingcpp.BarcodeFormat.QRCode,
                ):
                    if not crypto.is_candidate_payload(barcode.text):
                        continue
                    try:
                        found.add(self._unpack(barcode.text, key))
                    except InvalidKeyError:
                        invalid = True

        if invalid:
            raise InvalidKeyError("At least one francesco-watermark QR is invalid")
        if len(found) > 1:
            raise WatermarkingError("Conflicting francesco-watermark copy identifiers")
        if not found:
            raise SecretNotFoundError("No authenticated francesco-watermark QR found")

        return found.pop()

    def read_secret_components(self, pdf: PdfSource, key: str) -> dict[str, str | bool]:
        """Recover secret and return structured components (prefix, group, string, is_our_watermark)."""
        secret = self.read_secret(pdf, key)
        return crypto.parse_secret_components(secret)


# Backward-compatible alias
HybridPageWatermark = FrancescoWatermark

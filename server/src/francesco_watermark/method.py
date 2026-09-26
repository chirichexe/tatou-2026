"""Native PDF watermarking with authenticated QR and visible ciphertext layers.

Aggregates modular components:
- crypto: Key derivation (HKDF) and authenticated AES-SIV QR payloads
- qr: Opaque QR generation, collision-free border placement, and detection
- rendering: Typography and visible label overlays
- pdf: Native PDF stamping without rasterizing the source document

The shared ``position`` argument is accepted for API compatibility and ignored.
"""

from __future__ import annotations

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
from . import visible as visible_ops


NAME: Final[str] = "francesco-watermark"


class FrancescoWatermark(WatermarkingMethod):
    """Independent AES-SIV QR payloads plus a repeated visible ciphertext."""

    name: Final[str] = NAME
    DPI: Final[int] = pdf_ops.DEFAULT_DPI
    MAX_INPUT_BYTES: Final[int] = pdf_ops.MAX_INPUT_BYTES
    MAX_PAGES: Final[int] = pdf_ops.MAX_PAGES
    MAX_PIXELS_PER_PAGE: Final[int] = pdf_ops.MAX_PIXELS_PER_PAGE
    MAX_SECRET_BYTES: Final[int] = 64
    MAX_OUTPUT_IMAGE_BYTES: Final[int] = pdf_ops.MAX_OUTPUT_IMAGE_BYTES
    PREFIX: Final[str] = "FWM1:"
    # QR side as a fraction of the shortest page side, largest first; below
    # 6% a code no longer decodes reliably at READ_DPI
    QR_FRACTIONS: Final[tuple[float, ...]] = (render_ops.DEFAULT_QR_FRACTION, 0.08, 0.06)
    # Both output layers are enabled by default.
    ENABLE_BASE_LAYER: ClassVar[bool] = True
    ENABLE_QR_WATERMARK: ClassVar[bool] = True
    ENABLE_VISIBLE_TEXT: ClassVar[bool] = True

    @staticmethod
    def get_usage() -> str:
        return (
            "Native PDF overlay with authenticated opaque QR codes and an OCR-readable "
            "visible ciphertext; "
            "preserves original text streams. Supports any passphrase or hex key."
        )

    @classmethod
    def _key_material(cls, key: str) -> bytes:
        return crypto.derive_aes_key(key)

    @classmethod
    def _payload(cls, secret: str, key: str) -> str:
        return crypto.encrypt_qr_payload(secret, key)

    @classmethod
    def _unpack(cls, payload: str, key: str) -> str:
        return crypto.decrypt_qr_payload(payload, key)

    @classmethod
    def _check_document(cls, data: bytes, position: str | None) -> bool:
        return pdf_ops.is_document_applicable(data, position)

    def is_watermark_applicable(
        self, pdf: PdfSource, position: str | None = None
    ) -> bool:
        try:
            return self._check_document(load_pdf_bytes(pdf), None)
        except (OSError, TypeError, ValueError):
            return False

    def add_watermark(
        self,
        pdf: PdfSource,
        secret: str,
        key: str,
        position: str | None = None,
    ) -> bytes:
        if not self.ENABLE_BASE_LAYER:
            raise WatermarkingError(
                "Base layer is required for francesco-watermark watermarking"
            )

        self._key_material(key)
        validate_secret_string(secret, self.MAX_SECRET_BYTES)

        data = load_pdf_bytes(pdf)
        if not self._check_document(data, None):
            raise ValueError("PDF is not applicable to francesco-watermark")

        enable_qr = self.ENABLE_QR_WATERMARK
        enable_text = self.ENABLE_VISIBLE_TEXT

        qr_payloads = (
            [self._payload(secret, key) for _ in range(2)] if enable_qr else []
        )
        qr_images = [
            render_ops.build_opaque_qr_bytes(payload) for payload in qr_payloads
        ]
        visible_payload = self._payload(secret, key) if enable_text else ""
        visible_label = (
            crypto.qr_payload_to_visible_token(visible_payload)
            if visible_payload
            else ""
        )

        seed_material = (secret + ":" + key).encode("utf-8")

        with fitz.open(stream=data, filetype="pdf") as doc:
            qr_pages = 0
            for page in doc:
                content_boxes = render_ops.collect_page_content_boxes(page)
                qr_rects = (
                    self._place_qr_codes(page, content_boxes, seed_material)
                    if qr_images
                    else []
                )
                if qr_rects:
                    for qr_image, rect in zip(qr_images, qr_rects, strict=True):
                        pdf_ops.stamp_qr_on_page(page, qr_image, rect)
                    qr_pages += 1

                if visible_label:
                    render_ops.stamp_random_native_visible_text(
                        page=page,
                        label=visible_label,
                        placed_boxes=list(qr_rects),
                        count=render_ops.DEFAULT_VISIBLE_TEXT_COUNT,
                        seed_material=seed_material,
                    )

            if qr_images and not qr_pages:
                raise ValueError("No text-free border position available for QR code")
            return doc.tobytes(
                garbage=0, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE
            )

    @classmethod
    def _place_qr_codes(
        cls,
        page: fitz.Page,
        content_boxes: list[tuple[float, float, float, float]],
        seed_material: bytes,
    ) -> list[tuple[float, float, float, float]]:
        """Two free border boxes, as large as the page allows, or none.

        A dense page may have no room for the largest codes: smaller ones are
        tried, and a page without any room gets only the visible labels.
        """
        for fraction in cls.QR_FRACTIONS:
            try:
                return render_ops.generate_random_qr_rects(
                    page_width=page.rect.width,
                    page_height=page.rect.height,
                    count=2,
                    placed_boxes=list(content_boxes),
                    seed_material=seed_material,
                    qr_fraction=fraction,
                )
            except ValueError:
                continue
        return []

    def read_secret(self, pdf: PdfSource, key: str) -> str:
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
            return self.read_visible_secret(data, key)

        return found.pop()

    def read_visible_secret(self, pdf: PdfSource, key: str) -> str:
        """Recover and authenticate the secret from semi-transparent text."""
        self._key_material(key)
        data = load_pdf_bytes(pdf)
        if not self._check_document(data, None):
            raise ValueError("PDF is not applicable to francesco-watermark")

        with fitz.open(stream=data, filetype="pdf") as document:
            tokens = visible_ops.extract_visible_tokens(document)
        if not tokens:
            raise SecretNotFoundError("No visible francesco-watermark token found")

        secrets = visible_ops.decrypt_visible_tokens(tokens, key)
        if len(secrets) > 1:
            raise WatermarkingError("Conflicting visible copy identifiers")
        if not secrets:
            raise InvalidKeyError("Visible francesco-watermark authentication failed")
        return secrets.pop()

    def read_secret_components(self, pdf: PdfSource, key: str) -> dict[str, str | bool]:
        """Recover secret and return structured components (prefix, group, string, is_our_watermark)."""
        secret = self.read_secret(pdf, key)
        return crypto.parse_secret_components(secret)

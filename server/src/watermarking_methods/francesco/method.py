"""Native PDF watermarking with an authenticated QR and a visible copy label.

Aggregates modular components:
- crypto: key derivation and authenticated AES-SIV payloads
- rendering: opaque QR codes, collision-free border placement, visible labels
- pdf: document limits, page rasterization, native PDF stamping
- visible: OCR recovery of the visible labels

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


class FrancescoWatermark(WatermarkingMethod):
    """An AES-SIV QR payload and repeated visible recipient/secret labels."""

    name: Final[str] = "francesco-watermark"
    MAX_SECRET_BYTES: Final[int] = 64
    # Keep the QR size fixed; placement may fall back to the page corners.
    # Both output layers are enabled by default.
    ENABLE_BASE_LAYER: ClassVar[bool] = True
    ENABLE_QR_WATERMARK: ClassVar[bool] = True
    ENABLE_VISIBLE_TEXT: ClassVar[bool] = True

    @staticmethod
    def get_usage() -> str:
        return (
            "Native PDF overlay with an authenticated opaque QR code and visible "
            "recipient/secret labels; "
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

    @staticmethod
    def _check_document(data: bytes) -> bool:
        return pdf_ops.is_document_applicable(data)

    def is_watermark_applicable(
        self, pdf: PdfSource, position: str | None = None
    ) -> bool:
        try:
            return self._check_document(load_pdf_bytes(pdf))
        except (OSError, TypeError, ValueError):
            return False

    def add_watermark(
        self,
        pdf: PdfSource,
        secret: str,
        key: str,
        position: str | None = None,
        intended_for: str | None = None,
    ) -> bytes:
        if not self.ENABLE_BASE_LAYER:
            raise WatermarkingError(
                "Base layer is required for francesco-watermark watermarking"
            )

        self._key_material(key)
        validate_secret_string(secret, self.MAX_SECRET_BYTES)

        data = load_pdf_bytes(pdf)
        if not self._check_document(data):
            raise ValueError("PDF is not applicable to francesco-watermark")

        enable_qr = self.ENABLE_QR_WATERMARK
        enable_text = self.ENABLE_VISIBLE_TEXT

        qr_payloads = (
            [self._payload(secret, key)] if enable_qr else []
        )
        qr_images = [
            render_ops.build_opaque_qr_bytes(payload) for payload in qr_payloads
        ]
        visible_label = (
            f"GROUP {intended_for or 'UNKNOWN'} - {secret}"
            if enable_text
            else ""
        )

        seed_material = (secret + ":" + key).encode("utf-8")

        with fitz.open(stream=data, filetype="pdf") as doc:
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

                if visible_label:
                    render_ops.stamp_random_native_visible_text(
                        page=page,
                        label=visible_label,
                        placed_boxes=list(qr_rects),
                        count=render_ops.DEFAULT_VISIBLE_TEXT_COUNT,
                        seed_material=seed_material,
                    )

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
        """Place one fixed-size code, falling back to an extreme page corner.

        Free border positions are preferred. If the page is dense, the code
        retains the 10% size and may cover existing content at a corner.
        """
        try:
            return render_ops.generate_random_qr_rects(
                page_width=page.rect.width,
                page_height=page.rect.height,
                count=1,
                placed_boxes=list(content_boxes),
                seed_material=seed_material,
                qr_fraction=render_ops.DEFAULT_QR_FRACTION,
            )
        except ValueError:
            return render_ops.generate_edge_qr_rects(
                page_width=page.rect.width,
                page_height=page.rect.height,
                count=1,
                seed_material=seed_material,
                qr_fraction=render_ops.DEFAULT_QR_FRACTION,
            )

    def read_secret(self, pdf: PdfSource, key: str) -> str:
        if not self.ENABLE_QR_WATERMARK:
            raise WatermarkingError(
                "QR watermark layer is disabled; cannot extract secret"
            )

        self._key_material(key)
        data = load_pdf_bytes(pdf)
        if not self._check_document(data):
            raise ValueError("PDF is not applicable to francesco-watermark")

        found: set[str] = set()
        invalid = False

        with fitz.open(stream=data, filetype="pdf") as document:
            for page in document:
                image = pdf_ops.rasterize_page(page, dpi=pdf_ops.DEFAULT_DPI)
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
        if not self._check_document(data):
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

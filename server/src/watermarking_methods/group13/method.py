"""group13-watermark: the three methods of the group, one after the other

It contains no watermarking code: it calls the public methods of
davide-watermark, khaled-text-spacing-watermark and francesco-watermark with
the same secret and key. Every method is also registered on its own, so a
PDF can be marked with one of them or with all three, and read back by the
same method or by this one.

The layers fail under different attacks (image processing, text rewriting,
overlay removal), so a leak must defeat all of them to become anonymous.
"""

from __future__ import annotations

import logging

from watermarking_method import (
    InvalidKeyError,
    PdfSource,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    load_pdf_bytes,
)
from watermarking_methods.davide import DavideWatermark
from watermarking_methods.francesco import FrancescoWatermark
from watermarking_methods.khaled import KhaledTextSpacingWatermark

logger = logging.getLogger(__name__)

DAVIDE = DavideWatermark()
KHALED = KhaledTextSpacingWatermark()
FRANCESCO = FrancescoWatermark()

# the smallest limit among the methods (khaled): a secret fits every layer
MAX_SECRET_BYTES = 48

# errors of a layer that is missing or damaged: the other layers still count
_LAYER_ERRORS = (WatermarkingError, ValueError, RuntimeError, OSError)


class Group13Watermark(WatermarkingMethod):
    name = "group13-watermark"

    # attribution without a readable secret is davide's fingerprint
    ATTRIBUTION_THRESHOLD = DavideWatermark.ATTRIBUTION_THRESHOLD

    @staticmethod
    def get_usage() -> str:
        return ("davide + khaled + francesco watermarks with the same encrypted secret "
                f"(at most {MAX_SECRET_BYTES} UTF-8 bytes); position is ignored")

    @classmethod
    def is_watermark_applicable(cls, pdf: PdfSource, position: str | None = None) -> bool:
        # at least one invisible layer must fit: a large image or enough simple text
        try:
            data = load_pdf_bytes(pdf)
        except (TypeError, ValueError, OSError):
            return False
        return DAVIDE.is_watermark_applicable(data) or KHALED.is_watermark_applicable(data)

    @classmethod
    def add_watermark(cls, pdf: PdfSource, secret: str, key: str, position: str | None = None) -> bytes:
        if not key:
            raise InvalidKeyError("Key must not be empty")
        if not isinstance(secret, str) or not 0 < len(secret.encode("utf-8")) <= MAX_SECRET_BYTES:
            raise ValueError(f"Secret must be 1-{MAX_SECRET_BYTES} UTF-8 bytes")
        data = load_pdf_bytes(pdf)
        applied: list[WatermarkingMethod] = []

        # ---- 1. davide: encrypted secret + recipient fingerprint in the images
        if DAVIDE.is_watermark_applicable(data):
            data = DAVIDE.add_watermark(data, secret, key)
            applied.append(DAVIDE)

        # ---- 2. khaled: encrypted secret in the spacing of the text
        if KHALED.is_watermark_applicable(data):
            data = KHALED.add_watermark(data, secret, key)
            applied.append(KHALED)

        if not applied:
            raise WatermarkingError("PDF has neither a large enough image nor enough simple text")

        # ---- 3. francesco: QR codes and visible labels drawn on top. Last,
        # otherwise davide would re-encode its pictures; optional, because
        # only here we learn if the page borders have room for the QR codes
        if FRANCESCO.is_watermark_applicable(data):
            try:
                data = FRANCESCO.add_watermark(data, secret, key)
                applied.append(FRANCESCO)
            except ValueError as error:
                logger.warning("group13-watermark: %s skipped: %s", FRANCESCO.name, error)

        # ---- every layer must still be readable in the final PDF
        for layer in applied:
            if layer.read_secret(data, key) != secret:
                raise WatermarkingError(f"{layer.name} did not survive the other layers")
        return data

    @classmethod
    def read_secret(cls, pdf: PdfSource, key: str) -> str:
        if not key:
            raise InvalidKeyError("Key must not be empty")
        data = load_pdf_bytes(pdf)

        # cheapest first: text (instant), images, QR codes / OCR (slowest).
        # Nobody can forge AES-SIV without the key, so the first secret found
        # is a real recipient, even when a leak mixes pages of two copies
        for layer in (KHALED, DAVIDE, FRANCESCO):
            try:
                return layer.read_secret(data, key)
            except _LAYER_ERRORS as error:
                logger.debug("group13-watermark: %s not read: %s", layer.name, error)
        raise SecretNotFoundError("No watermark found")

    @classmethod
    def score_recipients(cls, pdf: PdfSource, original: PdfSource, key: str,
                         secrets: list[str]) -> dict[str, float]:
        return DAVIDE.score_recipients(pdf, original, key, secrets)

    @classmethod
    def embedded_layers(cls, pdf: PdfSource, key: str, secret: str) -> list[str]:
        """Names of the methods whose layer in `pdf` carries `secret` (diagnostics)"""
        data = load_pdf_bytes(pdf)
        found = []
        for layer in (DAVIDE, KHALED, FRANCESCO):
            try:
                if layer.read_secret(data, key) == secret:
                    found.append(layer.name)
            except _LAYER_ERRORS:
                pass
        return found

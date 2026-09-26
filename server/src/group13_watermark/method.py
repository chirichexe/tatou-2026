"""group13-watermark: the three group methods stacked in one watermark

Layers, embedded in this order, each one leaving the others readable:
1. davide: AES-SIV secret + recipient fingerprint in the images' pixels
2. khaled: AES-SIV secret in the positions of the text glyphs
3. francesco: AES-SIV secret in QR codes and visible labels, drawn on top.
   It goes last: the image layer would otherwise re-encode its pictures

They fail under different attacks (image processing, text rewriting,
overlay removal), so a leak must defeat all of them to become anonymous.

A layer that does not fit the PDF (no large image, not enough simple text,
no free border for the QR codes) is skipped, but at least one of the two
invisible layers must be embedded. Every embedded layer is read back from
the final bytes before returning.

The reader tries the layers from the cheapest and returns the first secret
that authenticates. Nobody can forge AES-SIV without the key, so any secret
found belongs to a real recipient, even when a leak mixes two copies.
"""

from __future__ import annotations

import logging

from davide_watermark.method import DavideWatermark
from francesco_watermark import FrancescoWatermark
from khaled_watermark import KhaledTextSpacingWatermark
from watermarking_method import (
    InvalidKeyError,
    PdfSource,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    load_pdf_bytes,
)

logger = logging.getLogger(__name__)

# the smallest limit among the layers (khaled): a secret fits every layer
MAX_SECRET_BYTES = 48

DAVIDE = DavideWatermark()
KHALED = KhaledTextSpacingWatermark()
FRANCESCO = FrancescoWatermark()

EMBED_ORDER: tuple[WatermarkingMethod, ...] = (DAVIDE, KHALED, FRANCESCO)
READ_ORDER: tuple[WatermarkingMethod, ...] = (KHALED, DAVIDE, FRANCESCO)
INVISIBLE = (DAVIDE, KHALED)


class Group13Watermark(WatermarkingMethod):
    name = "group13-watermark"

    ATTRIBUTION_THRESHOLD = DavideWatermark.ATTRIBUTION_THRESHOLD

    @staticmethod
    def get_usage() -> str:
        return ("Image, text-spacing and QR/visible layers with the same encrypted secret "
                f"(at most {MAX_SECRET_BYTES} UTF-8 bytes); position is ignored")

    @classmethod
    def is_watermark_applicable(cls, pdf: PdfSource, position: str | None = None) -> bool:
        try:
            data = load_pdf_bytes(pdf)
        except (TypeError, ValueError, OSError):
            return False
        return any(layer.is_watermark_applicable(data) for layer in INVISIBLE)

    @classmethod
    def embedded_layers(cls, pdf: PdfSource, key: str, secret: str) -> list[str]:
        """Names of the layers of `pdf` that carry `secret`"""
        data = load_pdf_bytes(pdf)
        return [layer.name for layer in EMBED_ORDER if _read(layer, data, key) == secret]

    @classmethod
    def add_watermark(cls, pdf: PdfSource, secret: str, key: str, position: str | None = None) -> bytes:
        if not key:
            raise InvalidKeyError("Key must not be empty")
        if not isinstance(secret, str) or not 0 < len(secret.encode("utf-8")) <= MAX_SECRET_BYTES:
            raise ValueError(f"Secret must be 1-{MAX_SECRET_BYTES} UTF-8 bytes")

        data = load_pdf_bytes(pdf)
        applied = []
        for layer in EMBED_ORDER:
            if not layer.is_watermark_applicable(data):
                continue
            try:
                data = layer.add_watermark(data, secret, key)
            except ValueError as error:
                # francesco only knows at embedding time if the borders are free
                if layer is not FRANCESCO:
                    raise
                logger.warning("group13-watermark: %s skipped: %s", layer.name, error)
                continue
            applied.append(layer)

        if not any(layer in applied for layer in INVISIBLE):
            raise WatermarkingError("PDF has neither a large enough image nor enough simple text")
        for layer in applied:
            if _read(layer, data, key) != secret:
                raise WatermarkingError(f"{layer.name} layer did not survive the other layers")
        return data

    @classmethod
    def read_secret(cls, pdf: PdfSource, key: str) -> str:
        if not key:
            raise InvalidKeyError("Key must not be empty")
        data = load_pdf_bytes(pdf)
        for layer in READ_ORDER:
            secret = _read(layer, data, key)
            if secret is not None:
                return secret
        raise SecretNotFoundError("No watermark found")

    @classmethod
    def score_recipients(cls, pdf: PdfSource, original: PdfSource, key: str,
                         secrets: list[str]) -> dict[str, float]:
        """Fingerprint of the image layer: works when no secret can be read"""
        return DAVIDE.score_recipients(pdf, original, key, secrets)


def _read(layer: WatermarkingMethod, data: bytes, key: str) -> str | None:
    """The layer's authenticated secret, or None if it is missing or damaged"""
    try:
        return layer.read_secret(data, key)
    except (WatermarkingError, ValueError, RuntimeError, OSError) as error:
        logger.debug("group13-watermark: %s not read: %s", layer.name, error)
        return None

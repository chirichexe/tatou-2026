"""Davide Chirichella's PDF watermarking method for Tatou.

The watermark lives in the pixels of the PDF's images (see :mod:`.image`):

- a blind layer carrying the AES-SIV encrypted secret (see :mod:`.crypto`),
  readable with the key alone: ``read_secret``;
- a per-recipient fingerprint, detected against the original document and the
  list of issued secrets: ``score_recipients``. It still attributes a leak
  after cropping, rescaling, heavy recompression or a screenshot.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Final

import pymupdf as fitz
from PIL import Image

from .crypto import _MAX_SECRET_BYTES, build_payload, open_payload
from .encoding import bits_to_bytes, bytes_to_bits
from .image import (
    embed_fingerprint,
    embed_payload,
    extract_header,
    extract_payload,
    fingerprint_scores,
    payload_capacity,
)

from watermarking_method import (
    InvalidKeyError,
    PdfSource,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    load_pdf_bytes,
)


NAME: Final[str] = "davide-watermark"

# Bound memory and CPU on untrusted uploads (processing peaks at ~100 bytes
# per pixel, and the server runs a single worker): images above the per-image
# limit are skipped, and at most _MAX_TOTAL_PIXELS are processed per document.
_MAX_PIXELS: Final[int] = 2048 * 2048
_MAX_TOTAL_PIXELS: Final[int] = 4 * _MAX_PIXELS

# Every payload bit must be spread over at least this many slots.
_MIN_REPETITIONS: Final[int] = 8

_JPEG_QUALITY: Final[int] = 92


def _images(doc) -> Iterator[tuple[int, Image.Image]]:
    """Yield every usable image of the document once, as (xref, RGB image).

    Images are decoded one at a time, so only one is held in memory.
    """

    seen = set()
    budget = _MAX_TOTAL_PIXELS

    for page in doc:
        for info in page.get_images(full=True):
            xref, width, height = info[0], info[2], info[3]

            if xref in seen or width * height > min(_MAX_PIXELS, budget):
                continue
            seen.add(xref)

            # Stencil masks are 1-bit shapes painted with the fill colour: they
            # have no pixels to mark, and rewriting them as RGB would corrupt them.
            if doc.xref_get_key(xref, "ImageMask")[1] == "true":
                continue

            try:
                img = Image.open(io.BytesIO(doc.extract_image(xref)["image"])).convert("RGB")
            except Exception:
                continue

            budget -= width * height
            yield xref, img


def _page_box(doc, xref: int) -> tuple[float, float, float, float] | None:
    """Where the image is drawn, as fractions of its page (x0, y0, x1, y1)."""

    for page in doc:
        rects = page.get_image_rects(xref)
        if rects:
            r, p = rects[0], page.rect
            return (r.x0 / p.width, r.y0 / p.height, r.x1 / p.width, r.y1 / p.height)

    return None


def _replace_image(doc, xref: int, img: Image.Image) -> None:
    """Overwrite the image object in place: no unmarked copy stays in the file."""

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=_JPEG_QUALITY, subsampling=0)

    doc.update_stream(xref, buf.getvalue(), compress=False)
    # The new stream is a plain RGB JPEG: reset the keys describing the old one.
    doc.xref_set_key(xref, "Filter", "/DCTDecode")
    doc.xref_set_key(xref, "DecodeParms", "null")
    doc.xref_set_key(xref, "Decode", "null")
    doc.xref_set_key(xref, "ColorSpace", "/DeviceRGB")
    doc.xref_set_key(xref, "BitsPerComponent", "8")
    doc.xref_set_key(xref, "Width", str(img.width))
    doc.xref_set_key(xref, "Height", str(img.height))


class DavideWatermark(WatermarkingMethod):
    name: Final[str] = NAME

    # Fingerprint z-score above which a recipient is considered the source of
    # a leak. Innocent recipients score ~N(0, 1): 6 means ~1e-9 false positives.
    ATTRIBUTION_THRESHOLD: Final[float] = 6.0

    @classmethod
    def get_usage(cls) -> str:
        return "Embed an encrypted watermark and a recipient fingerprint in the PDF's images."

    @classmethod
    def is_watermark_applicable(
        cls,
        pdf: PdfSource,
        position: str | None = None,
    ) -> bool:
        """Return whether one image can hold a maximum-size secret."""

        needed = (6 + _MAX_SECRET_BYTES + 16) * 8 * _MIN_REPETITIONS

        try:
            with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as doc:
                # "position" is not used by this method.
                return not doc.is_encrypted and any(
                    payload_capacity(img) >= needed for _, img in _images(doc)
                )
        except Exception:
            return False

    @classmethod
    def add_watermark(
        cls,
        pdf: PdfSource,
        secret: str,
        key: str,
        position: str | None = None,
    ) -> bytes:
        """Embed the payload and the fingerprint into every large enough image."""

        if not secret:
            raise ValueError("Secret must not be empty")

        if not key:
            raise InvalidKeyError("Key must not be empty")

        # 1. Encrypt and authenticate the secret, then turn it into bits.
        bits = bytes_to_bits(build_payload(secret, key))

        with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as doc:
            marked = 0

            for xref, img in _images(doc):
                # 2. Skip images without enough room for this payload.
                if payload_capacity(img) < len(bits) * _MIN_REPETITIONS:
                    continue

                # 3. Mark the image with both layers and write it back.
                img = embed_payload(img, bits, key)
                img = embed_fingerprint(img, key, secret)
                _replace_image(doc, xref, img)
                marked += 1

            if not marked:
                raise WatermarkingError("PDF does not contain a large enough image")

            # 4. Drop unreferenced objects and return the new PDF.
            return doc.tobytes(garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)

    @classmethod
    def read_secret(
        cls,
        pdf: PdfSource,
        key: str,
    ) -> str:
        """Recover and authenticate the secret from the blind layer."""

        if not key:
            raise InvalidKeyError("Key must not be empty")

        with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as doc:
            for _, img in _images(doc):
                # 1. The header gives the version and the secret length.
                header = bits_to_bytes(extract_header(img, key))
                secret_length = int.from_bytes(header[4:6], "big")

                if header[:4] != b"DWM1" or secret_length > _MAX_SECRET_BYTES:
                    continue

                # 2. Payload = 6-byte header + secret + 16-byte AES-SIV tag.
                payload_bits = (6 + secret_length + 16) * 8
                payload = bits_to_bytes(extract_payload(img, key, payload_bits))

                # 3. Decrypt; a single wrong bit fails authentication, never a wrong secret.
                try:
                    return open_payload(payload, key)
                except InvalidKeyError:
                    continue

        raise SecretNotFoundError("No watermark found")

    @classmethod
    def score_recipients(
        cls,
        pdf: PdfSource,
        original: PdfSource,
        key: str,
        secrets: list[str],
    ) -> dict[str, float]:
        """Fingerprint z-score of every candidate secret; ``original`` is the unmarked source."""

        if not key:
            raise InvalidKeyError("Key must not be empty")

        with fitz.open(stream=load_pdf_bytes(original), filetype="pdf") as doc:
            originals = [(img, _page_box(doc, xref)) for xref, img in _images(doc)]

        scores = {secret: float("-inf") for secret in secrets}

        with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as doc:
            # Images may have been reordered: try every leaked/original pair.
            for _, leak in _images(doc):
                for source, box in originals:
                    candidates = [leak]

                    # The leak may be a screenshot or print of the whole page:
                    # also cut the picture out where the source page draws it.
                    if box is not None:
                        candidates.append(leak.crop((
                            round(box[0] * leak.width), round(box[1] * leak.height),
                            round(box[2] * leak.width), round(box[3] * leak.height),
                        )))

                    for candidate in candidates:
                        for secret, z in fingerprint_scores(candidate, source, key, secrets).items():
                            scores[secret] = max(scores[secret], z)

        return scores

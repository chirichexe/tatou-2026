"""davide-watermark: watermarks the pictures inside a PDF

Text, fonts and layout are not touched. Every large enough image gets:
- the secret encrypted with AES-SIV, that read_secret reads with the key alone
- a fingerprint of the recipient, that score_recipients recognises even when
  the secret can't be read anymore (screenshot, resize, crop, rotation...)

Only AES-SIV(secret) is embedded, without any header: the reader tries every
possible length and keeps the one whose tag verifies
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator

import numpy as np
import pymupdf as fitz
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from PIL import Image

from watermarking_method import (
    InvalidKeyError,
    PdfSource,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    load_pdf_bytes,
)

from .image import capacity, embed_fingerprint, embed_payload, fingerprint_scores, read_votes, vote


# the server runs a single worker: bigger images are skipped and every
# document has a total budget of pixels
_MAX_PIXELS = 2048 * 2048
_MAX_TOTAL_PIXELS = 4 * _MAX_PIXELS

MAX_SECRET_BYTES = 128
TAG_BYTES = 16        # added by AES-SIV
MIN_REPETITIONS = 8   # every bit is written at least this many times
JPEG_QUALITY = 92


# ---------------------------------------------------------------- AES-SIV

def _cipher(key: str) -> AESSIV:
    # AES-SIV needs a 64 byte key, the configured key can be any string
    return AESSIV(hashlib.sha512(key.encode("utf-8")).digest())


def encrypt(secret: str, key: str) -> bytes:
    data = secret.encode("utf-8")
    if not 0 < len(data) <= MAX_SECRET_BYTES:
        raise ValueError(f"Secret must be 1-{MAX_SECRET_BYTES} bytes")
    return _cipher(key).encrypt(data, None)


def decrypt(ciphertext: bytes, key: str) -> str | None:
    """The secret, or None if the key, the length or any bit is wrong"""
    try:
        return _cipher(key).decrypt(ciphertext, None).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------- PDF

def _images(doc) -> Iterator[tuple[int, Image.Image]]:
    """(xref, image) of every image that can be marked, decoded one at a time"""
    seen = set()
    budget = _MAX_TOTAL_PIXELS

    for page in doc:
        for info in page.get_images(full=True):
            xref, width, height = info[0], info[2], info[3]
            if xref in seen or width * height > min(_MAX_PIXELS, budget):
                continue
            seen.add(xref)

            # stencil masks are 1-bit shapes: nothing to mark, and RGB would break them
            if doc.xref_get_key(xref, "ImageMask")[1] == "true":
                continue
            try:
                img = Image.open(io.BytesIO(doc.extract_image(xref)["image"])).convert("RGB")
            except Exception:
                continue

            budget -= width * height
            yield xref, img


def _read_image(img: Image.Image, key: str) -> str | None:
    votes = read_votes(img, key)
    # the length is not stored: only the right one passes AES-SIV
    for length in range(1, MAX_SECRET_BYTES + 1):
        secret = decrypt(np.packbits(vote(votes, (length + TAG_BYTES) * 8)).tobytes(), key)
        if secret is not None:
            return secret
    return None


def _page_box(doc, xref: int) -> tuple[float, float, float, float] | None:
    """Where the image is drawn on its page, as fractions of the page"""
    for page in doc:
        rects = page.get_image_rects(xref)
        if rects:
            r, p = rects[0], page.rect
            return r.x0 / p.width, r.y0 / p.height, r.x1 / p.width, r.y1 / p.height
    return None


def _replace_image(doc, xref: int, img: Image.Image) -> None:
    """Overwrite the image object, so no unmarked copy stays in the file"""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, subsampling=0)
    doc.update_stream(xref, buf.getvalue(), compress=False)
    for name, value in (("Filter", "/DCTDecode"), ("DecodeParms", "null"), ("Decode", "null"),
                        ("ColorSpace", "/DeviceRGB"), ("BitsPerComponent", "8"),
                        ("Width", str(img.width)), ("Height", str(img.height))):
        doc.xref_set_key(xref, name, value)


# ---------------------------------------------------------------- method

class DavideWatermark(WatermarkingMethod):
    name = "davide-watermark"

    # innocent scores follow N(0, 1): with 64 recipients and 4 comparisons
    # each, a threshold of 6 accuses an innocent less than once in 10^6 reads
    ATTRIBUTION_THRESHOLD = 6.0

    @classmethod
    def get_usage(cls) -> str:
        return "Embed an encrypted watermark and a recipient fingerprint in the PDF's images"

    @classmethod
    def is_watermark_applicable(cls, pdf: PdfSource, position: str | None = None) -> bool:
        # position is not used: at least one image must hold the longest secret
        needed = (MAX_SECRET_BYTES + TAG_BYTES) * 8 * MIN_REPETITIONS
        try:
            with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as doc:
                return not doc.is_encrypted and any(capacity(img) >= needed for _, img in _images(doc))
        except Exception:
            return False

    @classmethod
    def add_watermark(cls, pdf: PdfSource, secret: str, key: str, position: str | None = None) -> bytes:
        if not key:
            raise InvalidKeyError("Key must not be empty")
        bits = np.unpackbits(np.frombuffer(encrypt(secret, key), dtype=np.uint8))

        with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as doc:
            marked = 0
            for xref, img in _images(doc):
                if capacity(img) < len(bits) * MIN_REPETITIONS:
                    continue
                img = embed_fingerprint(embed_payload(img, bits, key), key, secret)
                _replace_image(doc, xref, img)
                marked += 1

            if not marked:
                raise WatermarkingError("PDF does not contain a large enough image")
            # garbage=3 drops the old unmarked image streams
            return doc.tobytes(garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)

    @classmethod
    def read_secret(cls, pdf: PdfSource, key: str) -> str:
        if not key:
            raise InvalidKeyError("Key must not be empty")

        with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as doc:
            found = {secret for _, img in _images(doc) if (secret := _read_image(img, key)) is not None}

        # images taken from two different copies: better no answer than a wrong one
        if len(found) > 1:
            raise WatermarkingError("Conflicting watermarks: the images come from different copies")
        if not found:
            raise SecretNotFoundError("No watermark found")
        return found.pop()

    @classmethod
    def score_recipients(cls, pdf: PdfSource, original: PdfSource, key: str, secrets: list[str]) -> dict[str, float]:
        """Fingerprint score of every secret in the leaked `pdf`, compared with the unmarked `original`"""
        if not key:
            raise InvalidKeyError("Key must not be empty")

        with fitz.open(stream=load_pdf_bytes(original), filetype="pdf") as doc:
            originals = [(img, _page_box(doc, xref)) for xref, img in _images(doc)]

        scores = {secret: float("-inf") for secret in secrets}
        with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as doc:
            # images may have been reordered, so every pair is tried
            for _, leak in _images(doc):
                for source, box in originals:
                    candidates = [leak]
                    # the leak may be a screenshot of the whole page
                    if box is not None:
                        x0, y0, x1, y1 = box
                        candidates.append(leak.crop((round(x0 * leak.width), round(y0 * leak.height),
                                                     round(x1 * leak.width), round(y1 * leak.height))))
                    for candidate in candidates:
                        for secret, z in fingerprint_scores(candidate, source, key, secrets).items():
                            scores[secret] = max(scores[secret], z)
        return scores

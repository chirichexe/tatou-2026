"""group13-watermark: every layer survives the others and each one alone suffices."""

from __future__ import annotations

import io

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image

from watermarking_method import SecretNotFoundError, WatermarkingError
from watermarking_methods.davide.method import DavideWatermark
from watermarking_methods.group13 import Group13Watermark
from watermarking_methods.group13.method import MAX_SECRET_BYTES
from watermarking_methods.khaled import KhaledTextSpacingWatermark
from watermarking_utils import METHODS, apply_watermark, read_watermark

SECRET = "Group_07:da0bb583c432fbfd078959ecc9b62902"
OTHER = "Group_09:0123456789abcdef0123456789abcdef"
KEY = "test-only-group13-watermark-key"
ALL_LAYERS = ["davide-watermark", "khaled-text-spacing-watermark", "francesco-watermark"]

G13 = Group13Watermark()


def _photo() -> bytes:
    rng = np.random.default_rng(13)
    y, x = np.mgrid[0:700, 0:900]
    pixels = np.stack([128 + 60 * np.sin(x / 37), 128 + 60 * np.cos(y / 29),
                       128 + 40 * np.sin((x + y) / 53)], -1) + rng.normal(0, 12, (700, 900, 3))
    buf = io.BytesIO()
    Image.fromarray(pixels.clip(0, 255).astype(np.uint8)).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _pdf(image: bool = True, text: bool = True) -> bytes:
    with fitz.open() as doc:
        if image:
            doc.new_page().insert_image(fitz.Rect(100, 150, 495, 457), stream=_photo())
        if text:
            page = doc.new_page()
            sentence = "Tatou carries a traceable secret in selectable letters here."
            for row in range(40):
                page.insert_text((70, 110 + row * 16), sentence, fontsize=10)
        if not image and not text:
            doc.new_page()
        return doc.tobytes()


@pytest.fixture(scope="module")
def source() -> bytes:
    return _pdf()


@pytest.fixture(scope="module")
def marked(source) -> bytes:
    return G13.add_watermark(source, SECRET, KEY)


def test_registered_and_every_layer_readable(source, marked):
    assert isinstance(METHODS["group13-watermark"], Group13Watermark)
    assert G13.is_watermark_applicable(source)
    assert G13.embedded_layers(marked, KEY, SECRET) == ALL_LAYERS
    assert read_watermark("group13-watermark", marked, KEY) == SECRET
    assert G13.read_secret(io.BytesIO(marked), KEY) == SECRET
    with fitz.open(stream=source, filetype="pdf") as a, fitz.open(stream=marked, filetype="pdf") as b:
        assert [p.get_text() for p in a] == [p.get_text() for p in b]


def test_distinct_recipients_get_distinct_copies(source, marked):
    other = apply_watermark("group13-watermark", source, OTHER, KEY)
    assert other != marked
    assert G13.read_secret(other, KEY) == OTHER


def test_wrong_key_and_unmarked_pdf_find_nothing(source, marked):
    with pytest.raises(SecretNotFoundError):
        G13.read_secret(marked, "another-key")
    with pytest.raises(SecretNotFoundError):
        G13.read_secret(source, KEY)


def test_secret_limit(source):
    with pytest.raises(ValueError):
        G13.add_watermark(source, "x" * (MAX_SECRET_BYTES + 1), KEY)
    with pytest.raises(ValueError):
        G13.add_watermark(source, "", KEY)


def test_missing_carriers_skip_their_layer():
    image_only = G13.add_watermark(_pdf(text=False), SECRET, KEY)
    assert G13.embedded_layers(image_only, KEY, SECRET) == ["davide-watermark", "francesco-watermark"]
    text_only = G13.add_watermark(_pdf(image=False), SECRET, KEY)
    assert G13.embedded_layers(text_only, KEY, SECRET) == ["khaled-text-spacing-watermark",
                                                           "francesco-watermark"]


def test_pdf_without_invisible_carrier_is_rejected():
    empty = _pdf(image=False, text=False)
    assert not G13.is_watermark_applicable(empty)
    with pytest.raises(WatermarkingError):
        G13.add_watermark(empty, SECRET, KEY)


def test_resave_with_garbage_collection_keeps_every_layer(marked):
    # garbage=4 merges identical streams of the overlays: the text layer used to reject them
    with fitz.open(stream=marked, filetype="pdf") as doc:
        resaved = doc.tobytes(garbage=4, deflate=True, clean=True)
    assert G13.embedded_layers(resaved, KEY, SECRET) == ALL_LAYERS


def test_text_layer_survives_dropping_the_cover(marked):
    with fitz.open(stream=marked, filetype="pdf") as doc:
        doc.delete_page(0)
        no_cover = doc.tobytes(garbage=3, deflate=True)
    assert KhaledTextSpacingWatermark.read_secret(no_cover, KEY) == SECRET


def test_each_layer_alone_is_enough(marked):
    with fitz.open(stream=marked, filetype="pdf") as doc:
        # remove every image: the image and QR layers are gone, the text stays
        for page in doc:
            for xref in {info[0] for info in page.get_images(full=True)}:
                page.delete_image(xref)
        text_left = doc.tobytes(garbage=4, deflate=True)
    assert G13.embedded_layers(text_left, KEY, SECRET) == ["khaled-text-spacing-watermark"]
    assert G13.read_secret(text_left, KEY) == SECRET

    with fitz.open(stream=marked, filetype="pdf") as doc:
        doc.delete_page(1)  # the text page
        image_left = doc.tobytes(garbage=3, deflate=True)
    assert "davide-watermark" in G13.embedded_layers(image_left, KEY, SECRET)
    assert G13.read_secret(image_left, KEY) == SECRET


def test_mixed_copies_return_a_real_recipient(source, marked):
    other = G13.add_watermark(source, OTHER, KEY)
    with fitz.open(stream=marked, filetype="pdf") as a, fitz.open(stream=other, filetype="pdf") as b:
        mixed = fitz.open()
        mixed.insert_pdf(a, from_page=0, to_page=0)   # images of SECRET
        mixed.insert_pdf(b, from_page=1, to_page=1)   # text of OTHER
        leak = mixed.tobytes()
        mixed.close()
    assert G13.read_secret(leak, KEY) in {SECRET, OTHER}


def test_fingerprint_attribution(source, marked):
    scores = G13.score_recipients(marked, source, KEY, [SECRET, OTHER])
    assert scores[SECRET] > G13.ATTRIBUTION_THRESHOLD > scores[OTHER]
    assert G13.ATTRIBUTION_THRESHOLD == DavideWatermark.ATTRIBUTION_THRESHOLD

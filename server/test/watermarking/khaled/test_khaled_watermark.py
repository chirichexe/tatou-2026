"""Contract, PDF preservation, error correction, and image composition."""

from __future__ import annotations

import io

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image

from khaled_watermark import KhaledTextSpacingWatermark
from khaled_watermark.method import STEP_PT, _keys, _ordered
from khaled_watermark.pdf_text import carriers, collect_runs, replace_runs
from watermarking_method import SecretNotFoundError, WatermarkingError


SECRET = "Group_13:" + "a" * 32
OTHER = "Group_13:" + "b" * 32
KEY = "test-only-high-entropy-watermark-key"


@pytest.fixture(scope="module")
def mixed_pdf() -> bytes:
    rng = np.random.default_rng(913)
    pixels = rng.integers(0, 256, size=(640, 640, 3), dtype=np.uint8)
    image = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(image, format="JPEG", quality=92)

    with fitz.open() as doc:
        cover = doc.new_page()
        cover.insert_image(fitz.Rect(80, 80, 500, 500), stream=image.getvalue())
        text = doc.new_page()
        sentence = ("Tatou carries a traceable secret in selectable letters "
                    "while preserving every word on this document.")
        for row in range(42):
            text.insert_text((30, 35 + row * 18), sentence, fontsize=10)
        return doc.tobytes()


def _page_text(pdf: bytes) -> list[str]:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


def test_khaled_watermark_roundtrip_preserves_text_and_image(mixed_pdf):
    assert KhaledTextSpacingWatermark.capacity_bits(mixed_pdf) >= 640
    assert KhaledTextSpacingWatermark.is_watermark_applicable(mixed_pdf)

    marked = KhaledTextSpacingWatermark.add_watermark(mixed_pdf, SECRET, KEY)
    assert KhaledTextSpacingWatermark.read_secret(marked, KEY) == SECRET
    assert KhaledTextSpacingWatermark.read_secret(io.BytesIO(marked), KEY) == SECRET
    assert KhaledTextSpacingWatermark.add_watermark(mixed_pdf, SECRET, KEY) == marked
    assert _page_text(marked) == _page_text(mixed_pdf)
    with fitz.open(stream=marked, filetype="pdf") as optimized:
        resaved = optimized.tobytes(garbage=3, clean=True, deflate=True,
                                   no_new_id=True)
    assert KhaledTextSpacingWatermark.read_secret(resaved, KEY) == SECRET
    another = KhaledTextSpacingWatermark.add_watermark(mixed_pdf, OTHER, KEY)
    assert another != marked
    assert KhaledTextSpacingWatermark.read_secret(another, KEY) == OTHER

    with fitz.open(stream=mixed_pdf, filetype="pdf") as original, \
         fitz.open(stream=marked, filetype="pdf") as result:
        old_xref = original[0].get_images(full=True)[0][0]
        new_xref = result[0].get_images(full=True)[0][0]
        assert original.extract_image(old_xref)["image"] == result.extract_image(new_xref)["image"]

    with pytest.raises(SecretNotFoundError):
        KhaledTextSpacingWatermark.read_secret(marked, "wrong-key")
    with pytest.raises(SecretNotFoundError):
        KhaledTextSpacingWatermark.read_secret(mixed_pdf, KEY)


def test_khaled_watermark_rejects_missing_capacity_and_oversized_secret(mixed_pdf):
    with fitz.open(stream=mixed_pdf, filetype="pdf") as doc:
        only_image = fitz.open()
        only_image.insert_pdf(doc, from_page=0, to_page=0)
        image_pdf = only_image.tobytes()
        only_image.close()
    assert not KhaledTextSpacingWatermark.is_watermark_applicable(image_pdf)
    with pytest.raises(WatermarkingError, match="Insufficient text capacity"):
        KhaledTextSpacingWatermark.add_watermark(image_pdf, "secret", KEY)
    with pytest.raises(ValueError, match="48 UTF-8 bytes"):
        KhaledTextSpacingWatermark.add_watermark(mixed_pdf, "x" * 49, KEY)
    with pytest.raises(ValueError, match="automatic placement"):
        KhaledTextSpacingWatermark.add_watermark(mixed_pdf, "secret", KEY, position="page=2")


def test_reed_solomon_repairs_one_changed_carrier(mixed_pdf):
    marked = KhaledTextSpacingWatermark.add_watermark(mixed_pdf, SECRET, KEY)
    with fitz.open(stream=marked, filetype="pdf") as doc:
        item = _ordered(carriers(collect_runs(doc)), _keys(KEY)[1])[0]
        adjustment = STEP_PT * 1000 / (2 * item.run.font_size)
        item.run.gaps[item.middle - 1] -= adjustment
        item.run.gaps[item.middle] += adjustment
        replace_runs(doc, [item.run])
        damaged = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    assert KhaledTextSpacingWatermark.read_secret(damaged, KEY) == SECRET


def test_text_inheriting_its_font_is_used_and_survives_clean():
    # the font set in one text object stays in effect in the next ones; a
    # resave with clean=True writes Tf in each of them explicitly
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((70, 72), "font", fontsize=10)  # registers /helv
        lines = b"".join(b"BT 70 %d Td (Tatou carries a traceable secret in selectable letters) Tj ET\n"
                         % (760 - row * 16) for row in range(45))
        doc.update_stream(page.get_contents()[0], b"BT /helv 10 Tf ET\n" + lines)
        inherited = doc.tobytes()
    assert KhaledTextSpacingWatermark.capacity_bits(inherited) >= 640

    marked = KhaledTextSpacingWatermark.add_watermark(inherited, SECRET, KEY)
    with fitz.open(stream=marked, filetype="pdf") as doc:
        cleaned = doc.tobytes(garbage=4, clean=True, deflate=True)
    assert KhaledTextSpacingWatermark.read_secret(cleaned, KEY) == SECRET

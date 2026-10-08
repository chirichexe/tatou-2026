"""Contract, PDF preservation, error correction, and image composition."""

from __future__ import annotations

import io

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image

from watermarking_method import SecretNotFoundError, WatermarkingError
from watermarking_methods.khaled import KhaledTextSpacingWatermark
from watermarking_methods.khaled.method import STEP_PT, _keys, _ordered
from watermarking_methods.khaled.pdf_text import carriers, collect_runs, replace_runs

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


def _scaled_text_pdf() -> bytes:
    """Ghostscript/Word style text: 1 Tf + scaled Tm inside a scaled cm, Tc set,
    plus a page with an inline image that the strict parser rejects."""
    sentence = "Tatou carries a traceable secret in selectable letters on scaled text"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((30, 40), "x", fontsize=10)  # adds a WinAnsi Helvetica resource
        font = page.get_fonts()[0][4]
        lines = [f"BT /{font} 1 Tf 0.01 Tc 10 0 0 10.4 30 {40 + row * 18} Tm ({sentence}) Tj ET"
                 for row in range(42)]
        content = "q 0.1 0 0 0.1 0 0 cm q 10 0 0 10 0 0 cm " + " ".join(lines) + " Q Q"
        doc.update_stream(page.get_contents()[0], content.encode())
        inline = doc.new_page()
        inline.insert_text((30, 40), "x", fontsize=10)
        doc.update_stream(inline.get_contents()[0],
                          b"q 10 0 0 10 50 50 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x80 EI Q")
        return doc.tobytes()


def test_khaled_tolerant_parsing_handles_scaled_text_and_inline_images():
    pdf = _scaled_text_pdf()
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        with pytest.raises(ValueError):
            collect_runs(doc)  # strict: the inline image page rejects the document
    assert KhaledTextSpacingWatermark.capacity_bits(pdf, tolerant=True) >= 640
    assert KhaledTextSpacingWatermark.is_watermark_applicable(pdf)

    marked = KhaledTextSpacingWatermark.add_watermark(pdf, SECRET, KEY)
    assert KhaledTextSpacingWatermark.read_secret(marked, KEY) == SECRET
    assert _page_text(marked) == _page_text(pdf)


def test_khaled_simple_text_still_uses_strict_carriers(mixed_pdf):
    # documents the strict parser handles are marked exactly as before
    strict = KhaledTextSpacingWatermark.capacity_bits(mixed_pdf)
    assert strict == KhaledTextSpacingWatermark.capacity_bits(mixed_pdf, tolerant=True)
    marked = KhaledTextSpacingWatermark.add_watermark(mixed_pdf, SECRET, KEY)
    with fitz.open(stream=marked, filetype="pdf") as doc:
        selected = _ordered(carriers(collect_runs(doc)), _keys(KEY)[1])
    assert len(selected) == strict

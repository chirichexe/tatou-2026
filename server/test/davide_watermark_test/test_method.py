"""Tests for the image-based ``davide-watermark`` method.

Attacks are applied to the PDF the way a leaker would: by editing the image,
re-rendering the page or converting the file with Ghostscript.
"""

from __future__ import annotations

import io
import shutil
import subprocess

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image, ImageFilter

import davide_watermark.method as method_module
from davide_watermark.crypto import build_payload
from davide_watermark.encoding import bytes_to_bits
from davide_watermark.image import embed_payload
from davide_watermark.method import DavideWatermark, _images, _replace_image
from watermarking_method import InvalidKeyError, SecretNotFoundError, WatermarkingError
from watermarking_utils import METHODS, apply_watermark, read_watermark

from photos import make_photo, photo_pdf

KEY = "rmap-server-key"
SECRETS = [f"Group_{i:02d}:{i:032x}" for i in range(1, 21)]
LEAKER = SECRETS[6]
OTHER = SECRETS[0]
THRESHOLD = DavideWatermark.ATTRIBUTION_THRESHOLD

needs_ghostscript = pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed")


# ---------------------------------------------------------------- helpers

def _image(pdf: bytes, index: int = 0) -> Image.Image:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return list(_images(doc))[index][1]


def _with_image(pdf: bytes, img: Image.Image) -> bytes:
    """Return ``pdf`` with its first image replaced, as a leaker's edit would do."""
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        _replace_image(doc, next(_images(doc))[0], img)
        return doc.tobytes(garbage=3)


def _jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return Image.open(buf).convert("RGB")


def _screenshot(pdf: bytes, dpi: int = 96) -> bytes:
    """Rasterise the whole page and wrap the picture in a new PDF."""
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        shot = page.get_pixmap(dpi=dpi).tobytes("png")
        with fitz.open() as out:
            out.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, stream=shot)
            return out.tobytes()


def _ghostscript(pdf: bytes, tmp_path, *options: str) -> bytes:
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    src.write_bytes(pdf)
    subprocess.run(["gs", "-q", "-o", str(dst), "-sDEVICE=pdfwrite", *options, str(src)], check=True)
    return dst.read_bytes()


def _raw_images(pdf: bytes) -> list[tuple[str, bytes]]:
    """(object dictionary without /Length, raw stream) of every image, in page order.

    Object numbers are not compared: saving a PDF may renumber them.
    """
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        result = []
        for info in doc[0].get_images(full=True):
            obj = "\n".join(line for line in doc.xref_object(info[0]).splitlines() if "/Length" not in line)
            result.append((obj, doc.xref_stream_raw(info[0])))
        return result


def _average(a: bytes, b: bytes) -> bytes:
    mix = (np.asarray(_image(a), dtype=np.float64) + np.asarray(_image(b), dtype=np.float64)) / 2
    return _with_image(a, Image.fromarray(mix.round().astype(np.uint8)))


def _attributed(scores: dict[str, float]) -> set[str]:
    return {secret for secret, z in scores.items() if z > THRESHOLD}


@pytest.fixture(scope="module")
def leaked(image_pdf) -> bytes:
    return DavideWatermark.add_watermark(image_pdf, LEAKER, KEY)


@pytest.fixture(scope="module")
def other_copy(image_pdf) -> bytes:
    return DavideWatermark.add_watermark(image_pdf, OTHER, KEY)


# ---------------------------------------------------------------- interface

def test_registered_under_its_name():
    assert isinstance(METHODS["davide-watermark"], DavideWatermark)
    assert DavideWatermark.name == "davide-watermark"


def test_input_validation(image_pdf):
    with pytest.raises(ValueError):
        DavideWatermark.add_watermark(image_pdf, "", KEY)
    with pytest.raises(InvalidKeyError):
        DavideWatermark.add_watermark(image_pdf, "secret", "")
    with pytest.raises(InvalidKeyError):
        DavideWatermark.read_secret(image_pdf, "")
    with pytest.raises(InvalidKeyError):
        DavideWatermark.score_recipients(image_pdf, image_pdf, "", SECRETS)
    with pytest.raises(ValueError):
        DavideWatermark.add_watermark(image_pdf, "x" * 129, KEY)


# ---------------------------------------------------------------- blind layer

@pytest.mark.parametrize("secret", [LEAKER, "Secrète été 🔐 日本語", "x" * 128])
def test_blind_roundtrip_through_registry(image_pdf, tmp_path, secret):
    watermarked = apply_watermark("davide-watermark", image_pdf, secret, KEY)
    assert read_watermark("davide-watermark", watermarked, KEY) == secret

    # Every PdfSource form: bytes, path, binary stream.
    path = tmp_path / "wm.pdf"
    path.write_bytes(watermarked)
    assert read_watermark("davide-watermark", path, KEY) == secret
    assert read_watermark("davide-watermark", io.BytesIO(watermarked), KEY) == secret


def test_wrong_key_reads_nothing(leaked):
    with pytest.raises(SecretNotFoundError):
        DavideWatermark.read_secret(leaked, "wrong-key")


def test_unmarked_pdf_reads_nothing(image_pdf):
    with pytest.raises(SecretNotFoundError):
        DavideWatermark.read_secret(image_pdf, KEY)


def test_tampered_payload_is_rejected_not_misread(image_pdf):
    # Embed a valid payload with a single flipped ciphertext bit: the QIM layer
    # decodes it perfectly, so only AES-SIV authentication can catch it.
    bits = bytes_to_bits(build_payload(LEAKER, KEY))
    bits[80] ^= 1
    with fitz.open(stream=image_pdf, filetype="pdf") as doc:
        xref, img = next(_images(doc))
        _replace_image(doc, xref, embed_payload(img, bits, KEY))
        tampered = doc.tobytes()

    with pytest.raises(SecretNotFoundError):
        DavideWatermark.read_secret(tampered, KEY)


def test_each_recipient_gets_a_distinct_readable_copy(leaked, other_copy):
    assert leaked != other_copy
    assert DavideWatermark.read_secret(leaked, KEY) == LEAKER
    assert DavideWatermark.read_secret(other_copy, KEY) == OTHER


def test_blind_read_of_averaged_copies_never_returns_a_wrong_secret(leaked, other_copy):
    try:
        secret = DavideWatermark.read_secret(_average(leaked, other_copy), KEY)
    except SecretNotFoundError:
        return
    assert secret in {LEAKER, OTHER}


def test_blind_layer_survives_jpeg_recompression(leaked):
    assert DavideWatermark.read_secret(_with_image(leaked, _jpeg(_image(leaked), 50)), KEY) == LEAKER


@needs_ghostscript
def test_blind_layer_survives_ghostscript_rewrite(leaked, tmp_path):
    assert DavideWatermark.read_secret(_ghostscript(leaked, tmp_path), KEY) == LEAKER


# ---------------------------------------------------------------- PDF handling

def test_image_is_replaced_in_place_and_page_kept(image_pdf, leaked):
    with fitz.open(stream=leaked, filetype="pdf") as doc:
        # A single image object: no unmarked copy left in the file.
        assert len(doc[0].get_images()) == 1
        assert "Confidential photo" in doc[0].get_text()

    original = np.asarray(_image(image_pdf), dtype=np.float64)
    marked = np.asarray(_image(leaked), dtype=np.float64)
    psnr = 10 * np.log10(255 ** 2 / np.mean((original - marked) ** 2))
    assert psnr > 32


def test_pdf_without_usable_image_is_rejected(text_only_pdf):
    tiny = photo_pdf([make_photo(64, 64)])
    for pdf in (text_only_pdf, tiny):
        assert not DavideWatermark.is_watermark_applicable(pdf)
        with pytest.raises(WatermarkingError):
            DavideWatermark.add_watermark(pdf, LEAKER, KEY)


def test_images_above_the_pixel_limit_are_left_untouched():
    side = int(method_module._MAX_PIXELS ** 0.5) + 8
    big = make_photo(side, side, smooth=True)

    only_big = photo_pdf([big])
    assert not DavideWatermark.is_watermark_applicable(only_big)
    with pytest.raises(WatermarkingError):
        DavideWatermark.add_watermark(only_big, LEAKER, KEY)

    mixed = photo_pdf([big, make_photo(640, 640)])
    out = DavideWatermark.add_watermark(mixed, LEAKER, KEY)
    before, after = _raw_images(mixed), _raw_images(out)
    assert after[0] == before[0]
    assert after[1][1] != before[1][1]
    assert DavideWatermark.read_secret(out, KEY) == LEAKER


def test_total_pixel_budget_bounds_the_work(monkeypatch):
    # With a budget of two images, the third one is neither decoded nor marked.
    monkeypatch.setattr(method_module, "_MAX_TOTAL_PIXELS", 2 * 640 * 640)
    pdf = photo_pdf([make_photo(640, 640, seed=s) for s in (1, 2, 3)])
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert len(list(_images(doc))) == 2


def test_multiple_images_are_all_marked_and_each_carries_the_watermark():
    pdf = photo_pdf([make_photo(640, 640, seed=1), make_photo(512, 512, seed=2)])
    out = DavideWatermark.add_watermark(pdf, LEAKER, KEY)

    for (_, before), (_, after) in zip(_raw_images(pdf), _raw_images(out), strict=True):
        assert before != after

    # Remove the first image: the second one alone still carries both layers.
    with fitz.open(stream=out, filetype="pdf") as doc:
        doc[0].delete_image(doc[0].get_images()[0][0])
        second_only = doc.tobytes(garbage=3)
    assert DavideWatermark.read_secret(second_only, KEY) == LEAKER
    assert _attributed(DavideWatermark.score_recipients(second_only, pdf, KEY, SECRETS)) == {LEAKER}


def test_stencil_masks_are_not_modified():
    pdf = photo_pdf([make_photo(640, 640)])
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        mask = doc.get_new_xref()
        doc.update_object(mask, "<< /Type /XObject /Subtype /Image /Width 640 /Height 640 "
                                "/ImageMask true /BitsPerComponent 1 >>")
        doc.update_stream(mask, np.packbits(np.random.default_rng(0).random((640, 640)) > 0.5, axis=1).tobytes())
        kind, value = doc.xref_get_key(page.xref, "Resources")
        resources = int(value.split()[0]) if kind == "xref" else page.xref
        kind, value = doc.xref_get_key(resources, "XObject")
        if kind == "xref":
            doc.xref_set_key(int(value.split()[0]), "Msk", f"{mask} 0 R")
        else:
            doc.xref_set_key(resources, "XObject/Msk", f"{mask} 0 R")
        contents = page.get_contents()[0]
        doc.update_stream(contents, doc.xref_stream(contents) + b"\nq 200 0 0 200 72 100 cm /Msk Do Q\n")
        pdf = doc.tobytes()

    out = DavideWatermark.add_watermark(pdf, LEAKER, KEY)

    # The photo is marked; the stencil keeps its type and decoded pixels (the
    # final save may recompress it losslessly, so raw bytes are not compared).
    with fitz.open(stream=pdf, filetype="pdf") as src, fitz.open(stream=out, filetype="pdf") as dst:
        (photo_before, mask_before), (photo_after, mask_after) = (
            [info[0] for info in d[0].get_images(full=True)] for d in (src, dst)
        )
        assert src.xref_stream_raw(photo_before) != dst.xref_stream_raw(photo_after)
        for key, value in (("ImageMask", "true"), ("BitsPerComponent", "1"), ("ColorSpace", "null")):
            assert dst.xref_get_key(mask_after, key)[1] == value
        assert dst.xref_stream(mask_after) == src.xref_stream(mask_before)


# ---------------------------------------------------------------- fingerprint

ATTACKS = {
    "untouched": lambda pdf, tmp: pdf,
    "crop": lambda pdf, tmp: _with_image(pdf, _image(pdf).crop((5, 3, 600, 620))),
    "resize-40%": lambda pdf, tmp: _with_image(pdf, _image(pdf).resize((256, 256))),
    "jpeg-q20": lambda pdf, tmp: _with_image(pdf, _jpeg(_image(pdf), 20)),
    "blur": lambda pdf, tmp: _with_image(pdf, _image(pdf).filter(ImageFilter.GaussianBlur(1.5))),
    "screenshot": lambda pdf, tmp: _screenshot(pdf),
    "ghostscript-screen": pytest.param(
        lambda pdf, tmp: _ghostscript(pdf, tmp, "-dPDFSETTINGS=/screen"), marks=needs_ghostscript,
    ),
}


@pytest.mark.parametrize("attack", list(ATTACKS.values()), ids=list(ATTACKS))
def test_attacked_copy_is_attributed_to_its_recipient_only(image_pdf, leaked, tmp_path, attack):
    scores = DavideWatermark.score_recipients(attack(leaked, tmp_path), image_pdf, KEY, SECRETS)
    assert _attributed(scores) == {LEAKER}


def test_two_copies_of_the_same_source_are_distinguishable(image_pdf, leaked, other_copy):
    assert _attributed(DavideWatermark.score_recipients(leaked, image_pdf, KEY, SECRETS)) == {LEAKER}
    assert _attributed(DavideWatermark.score_recipients(other_copy, image_pdf, KEY, SECRETS)) == {OTHER}


def test_averaged_copies_name_only_the_two_colluders(image_pdf, leaked, other_copy):
    scores = DavideWatermark.score_recipients(_average(leaked, other_copy), image_pdf, KEY, SECRETS)
    assert _attributed(scores) == {LEAKER, OTHER}


def test_unmarked_original_and_unrelated_pdf_are_not_attributed(image_pdf):
    unrelated = photo_pdf([make_photo(640, 640, seed=99)])
    for pdf in (image_pdf, unrelated):
        assert _attributed(DavideWatermark.score_recipients(pdf, image_pdf, KEY, SECRETS)) == set()


def test_fingerprint_needs_the_key(image_pdf, leaked):
    assert _attributed(DavideWatermark.score_recipients(leaked, image_pdf, "wrong-key", SECRETS)) == set()

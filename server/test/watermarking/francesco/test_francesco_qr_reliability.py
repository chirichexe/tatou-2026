"""QR codes must decode on their own, without the OCR fallback."""

from __future__ import annotations

import pymupdf as fitz
import zxingcpp

from watermarking_methods.francesco import FrancescoWatermark, crypto
from watermarking_methods.francesco import pdf as pdf_ops

KEY = "0123456789abcdef" * 4
SECRET = "Group_07:da0bb583c432fbfd078959ecc9b62902"


def test_every_qr_decodes_whatever_the_random_salt(pdf_bytes):
    # every payload has a random salt: before the codes were drawn with whole
    # pixels per module and at 10% of the page, about half of them failed
    method = FrancescoWatermark()
    for _ in range(6):
        marked = method.add_watermark(pdf_bytes, SECRET, KEY)
        with fitz.open(stream=marked, filetype="pdf") as doc:
            image = pdf_ops.rasterize_page(doc[0], dpi=pdf_ops.READ_DPI)
        codes = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
        assert [crypto.decrypt_qr_payload(code.text, KEY) for code in codes] == [SECRET, SECRET]


def _dense_page(doc: fitz.Document) -> None:
    page = doc.new_page(width=595, height=842)
    for row in range(84):  # text from edge to edge: no free border
        page.insert_text((2, 10 + row * 10), "busy " * 40, fontsize=9)


def test_dense_page_gets_labels_only_and_other_pages_keep_their_codes(pdf_bytes):
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        _dense_page(doc)
        mixed = doc.tobytes()
    method = FrancescoWatermark()
    marked = method.add_watermark(mixed, SECRET, KEY)
    with fitz.open(stream=marked, filetype="pdf") as doc:
        found = [len(zxingcpp.read_barcodes(pdf_ops.rasterize_page(page, dpi=pdf_ops.READ_DPI),
                                            formats=zxingcpp.BarcodeFormat.QRCode)) for page in doc]
    assert found == [2, 0]
    assert method.read_secret(marked, KEY) == SECRET


def test_no_room_on_any_page_is_rejected():
    import pytest

    with fitz.open() as doc:
        _dense_page(doc)
        dense = doc.tobytes()
    with pytest.raises(ValueError, match="No text-free border"):
        FrancescoWatermark().add_watermark(dense, SECRET, KEY)

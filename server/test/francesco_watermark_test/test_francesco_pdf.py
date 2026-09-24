"""PDF geometry validation and native PyMuPDF overlay tests."""

from __future__ import annotations

import fitz
from francesco_watermark import pdf as pdf_ops
from francesco_watermark.method import HybridPageWatermark

KEY = "0123456789abcdef" * 4


def test_is_document_applicable(pdf_bytes):
    # Valid document
    assert pdf_ops.is_document_applicable(pdf_bytes)
    assert pdf_ops.is_document_applicable(pdf_bytes, position="with-text")
    assert pdf_ops.is_document_applicable(pdf_bytes, position="qr-only")

    # Invalid input
    assert not pdf_ops.is_document_applicable(b"not-a-pdf")

    # Document too large
    huge_bytes = b"0" * (pdf_ops.MAX_INPUT_BYTES + 1)
    assert not pdf_ops.is_document_applicable(huge_bytes)

    # Empty page count
    empty_pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
    assert not pdf_ops.is_document_applicable(empty_pdf_bytes)

    # Page dimension out of bounds
    with fitz.open() as huge_doc:
        huge_doc.new_page(width=3500, height=3500)
        assert not pdf_ops.is_document_applicable(huge_doc.tobytes())


def test_native_overlay_preserves_text_and_streams(pdf_bytes):
    method = HybridPageWatermark()
    watermarked = method.add_watermark(pdf_bytes, "copy-check", KEY)

    with fitz.open(stream=watermarked, filetype="pdf") as doc:
        page = doc[0]
        # Text is preserved intact (NOT flattened into a full-page raster image)
        text = page.get_text()
        assert "A document with a table and a photograph" in text
        # Images contain the frosted QR code overlay XObjects
        images = page.get_images()
        assert len(images) >= 1

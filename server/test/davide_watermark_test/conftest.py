from __future__ import annotations

import pymupdf as fitz
import pytest


@pytest.fixture
def sample_carrier_pdf() -> bytes:
    """Deterministic PDF with 2000 BT carrier slots."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "x")
    xref = page.get_contents()[0]
    stream = b"".join(b"BT /Helv 10 Tf 50 50 Td (x) Tj ET\n" for _ in range(2000))
    doc.update_stream(xref, stream)
    return doc.tobytes()


@pytest.fixture
def empty_carrier_pdf() -> bytes:
    """Valid PDF containing zero text carrier slots."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"%%EOF\n"
    )


@pytest.fixture
def small_carrier_pdf() -> bytes:
    """PDF with only 50 carrier slots, fewer than required for a watermark header."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "x")
    xref = page.get_contents()[0]
    stream = b"".join(b"BT /Helv 10 Tf 50 50 Td (x) Tj ET\n" for _ in range(50))
    doc.update_stream(xref, stream)
    return doc.tobytes()

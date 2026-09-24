"""Shared fixtures and constants for Francesco watermark tests."""

from __future__ import annotations

import fitz
import pytest

KEY = "0123456789abcdef" * 4
OTHER_KEY = "fedcba9876543210" * 4


@pytest.fixture
def master_key() -> str:
    return KEY


@pytest.fixture
def other_key() -> str:
    return OTHER_KEY


@pytest.fixture
def pdf_bytes() -> bytes:
    """Single-page test PDF with vector text."""
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), "A document with a table and a photograph")
        return document.tobytes()


@pytest.fixture
def carrier_pdf_for_davide() -> bytes:
    """PDF with 1500 BT carrier slots for testing coexistence with DavideWatermark."""
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), "x")
        xref = page.get_contents()[0]
        stream = b"".join(b"BT /Helv 10 Tf 50 50 Td (x) Tj ET\n" for _ in range(1500))
        doc.update_stream(xref, stream)
        return doc.tobytes()

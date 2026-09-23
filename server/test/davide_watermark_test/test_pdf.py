from __future__ import annotations

import pymupdf as fitz

from davide_watermark.pdf import read_slot_values, write_carriers


def test_write_and_read_carriers(sample_carrier_pdf):
    with fitz.open(stream=sample_carrier_pdf, filetype="pdf") as doc:
        page = doc[0]
        xref = page.get_contents()[0]

        # Write bit 0 into slot index 2, and bit 1 into slot index 5
        carriers = [(xref, 2, 0), (xref, 5, 1)]
        write_carriers(doc, carriers)

        values = read_slot_values(doc, [(xref, 2), (xref, 5)])
        assert values == [0, 1]


def test_read_slot_values_unmarked_slots(sample_carrier_pdf):
    with fitz.open(stream=sample_carrier_pdf, filetype="pdf") as doc:
        page = doc[0]
        xref = page.get_contents()[0]

        values = read_slot_values(doc, [(xref, 0), (xref, 1)])
        assert values == [None, None]


def test_read_slot_values_out_of_range_index(sample_carrier_pdf):
    with fitz.open(stream=sample_carrier_pdf, filetype="pdf") as doc:
        page = doc[0]
        xref = page.get_contents()[0]

        values = read_slot_values(doc, [(xref, 999999)])
        assert values == [None]


def test_read_slot_values_unrecognized_tw():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "x")
    xref = page.get_contents()[0]
    # Insert 0.50 Tw after BT, which is neither 0.01 nor 0.02
    doc.update_stream(xref, b"BT 0.50 Tw /Helv 10 Tf (x) Tj ET\n")

    values = read_slot_values(doc, [(xref, 0)])
    assert values == [None]


def test_write_multiple_carriers_preserves_stream_integrity(sample_carrier_pdf):
    with fitz.open(stream=sample_carrier_pdf, filetype="pdf") as doc:
        page = doc[0]
        xref = page.get_contents()[0]

        # Write alternating bits across several indexes in arbitrary order
        indexes = [0, 1, 3, 10, 15, 20]
        bits = [0, 1, 1, 0, 1, 0]
        carriers = [(xref, idx, bit) for idx, bit in zip(indexes, bits)]

        write_carriers(doc, carriers)

        slots_to_read = [(xref, idx) for idx in indexes]
        recovered_values = read_slot_values(doc, slots_to_read)
        assert recovered_values == bits

from __future__ import annotations

import re


# "BT" marks the beginning of a text section in a PDF content stream.
_BT_RE = re.compile(rb"\bBT\b")

# Values used to represent watermark bits through PDF word spacing.
_TW_BIT_0 = 0.01
_TW_BIT_1 = 0.02

# After "BT", we look for a previously inserted "Tw" command.
_TW_AFTER_BT_RE = re.compile(rb"\s+([0-9]+(?:\.[0-9]+)?)\s+Tw\b")


def write_carriers(
    doc,
    carriers: list[tuple[int, int, int]],
) -> None:
    """
    Write watermark bits into selected carrier positions.

    Each carrier is:
        (xref, local_index, bit)

    The bit is encoded as:
        0 -> 0.01 Tw
        1 -> 0.02 Tw
    """

    # Group carriers by content-stream xref.
    # This allows us to read and update each PDF stream only once
    by_xref: dict[int, list[tuple[int, int]]] = {}

    for xref, index, bit in carriers:
        by_xref.setdefault(xref, []).append((index, bit))

    for xref, selected in by_xref.items():
        stream = doc.xref_stream(xref)

        # Find all BT positions in this content stream
        matches = list(_BT_RE.finditer(stream))

        # Insert from right to left so earlier offsets do not change
        for index, bit in sorted(selected, reverse=True):
            match = matches[index]

            if bit == 0:
                value = _TW_BIT_0
            else:
                value = _TW_BIT_1

            marker = f" {value:.2f} Tw".encode("ascii")

            # Insert the marker immediately after BT
            position = match.end()
            stream = stream[:position] + marker + stream[position:]

        # Store the modified content stream back into the PDF
        doc.update_stream(xref, stream)


def read_slot_values(
    doc,
    slots: list[tuple[int, int]],
) -> list[int | None]:
    """
    Read watermark bits from the given carrier slots.

    Returns:
        0 for a detected bit 0
        1 for a detected bit 1
        None if no valid watermark marker is present
    """

    # Cache streams and their BT positions
    # m slots can belong to the same content stream
    streams = {}
    matches = {}

    values = []

    for xref, index in slots:
        if xref not in streams:
            streams[xref] = doc.xref_stream(xref)
            matches[xref] = list(_BT_RE.finditer(streams[xref]))

        stream = streams[xref]
        bt_matches = matches[xref]

        # The slot is invalid if the requested BT does not exist
        if index >= len(bt_matches):
            values.append(None)
            continue

        # Look immediately after BT for our Tw marker
        position = bt_matches[index].end()
        match = _TW_AFTER_BT_RE.match(stream, position)

        if match is None:
            values.append(None)
            continue

        value = float(match.group(1))

        # Allow a tiny floating-point tolerance.ù
        if abs(value - _TW_BIT_0) < 0.001:
            values.append(0)
        elif abs(value - _TW_BIT_1) < 0.001:
            values.append(1)
        else:
            values.append(None)

    return values
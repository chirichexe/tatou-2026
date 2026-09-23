from __future__ import annotations

import hashlib
import hmac
import random
import re


_BT_RE = re.compile(rb"\bBT\b")
_CARRIER_DOMAIN = b"tatou/davide-watermark/carriers/v1"


def find_candidate_slots(doc) -> list[tuple[int, int]]:
    """Find all BT positions that can be used as watermark carriers."""

    slots = []
    # we search for BT operators in the content streams of all pages, and record their xref and index
    for page in doc:
        for xref in page.get_contents():
            # if the content stream is compressed, we need to decompress it first
            stream = doc.xref_stream(xref)

            for index, _ in enumerate(_BT_RE.finditer(stream)):
                slots.append((xref, index))

    return slots


def derive_carrier_seed(key: str) -> bytes:
    """Derive a deterministic seed for carrier ordering."""

    return hmac.new(
        key.encode("utf-8"),
        _CARRIER_DOMAIN,
        hashlib.sha256,
    ).digest()


def permutation(size: int, seed: bytes) -> list[int]:
    """Return a deterministic permutation of carrier indexes."""

    indexes = list(range(size))

    seed_int = int.from_bytes(
        hashlib.sha256(seed + b"/shuffle").digest(),
        "big",
    )

    random.Random(seed_int).shuffle(indexes)

    return indexes
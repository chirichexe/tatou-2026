from __future__ import annotations

import pymupdf as fitz

from davide_watermark.carriers import (
    derive_carrier_seed,
    find_candidate_slots,
    permutation,
)


def test_find_candidate_slots_in_carrier_pdf(sample_carrier_pdf):
    with fitz.open(stream=sample_carrier_pdf, filetype="pdf") as doc:
        slots = find_candidate_slots(doc)

    assert len(slots) == 2000
    for xref, index in slots:
        assert isinstance(xref, int)
        assert isinstance(index, int)


def test_find_candidate_slots_empty_pdf(empty_carrier_pdf):
    with fitz.open(stream=empty_carrier_pdf, filetype="pdf") as doc:
        slots = find_candidate_slots(doc)

    assert slots == []


def test_derive_carrier_seed_properties():
    seed1_a = derive_carrier_seed("secret-key")
    seed1_b = derive_carrier_seed("secret-key")
    seed2 = derive_carrier_seed("different-key")

    assert seed1_a == seed1_b
    assert len(seed1_a) == 32
    assert seed1_a != seed2


def test_permutation_deterministic_and_bijective():
    seed = derive_carrier_seed("test-key")
    size = 100

    perm1 = permutation(size, seed)
    perm2 = permutation(size, seed)

    assert perm1 == perm2
    assert sorted(perm1) == list(range(size))


def test_permutation_different_seeds():
    seed1 = derive_carrier_seed("key-alpha")
    seed2 = derive_carrier_seed("key-beta")

    assert permutation(50, seed1) != permutation(50, seed2)


def test_permutation_edge_cases():
    seed = derive_carrier_seed("edge-cases")

    assert permutation(0, seed) == []
    assert permutation(1, seed) == [0]

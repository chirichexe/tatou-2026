from __future__ import annotations

import pytest

from davide_watermark.encoding import (
    bits_to_bytes,
    bytes_to_bits,
    majority,
    repeat_bits,
)


def test_bytes_to_bits_single_byte():
    assert bytes_to_bits(b"\xA2") == [1, 0, 1, 0, 0, 0, 1, 0]
    assert bytes_to_bits(b"\x00") == [0, 0, 0, 0, 0, 0, 0, 0]
    assert bytes_to_bits(b"\x01") == [0, 0, 0, 0, 0, 0, 0, 1]


def test_bytes_to_bits_multiple_bytes():
    assert bytes_to_bits(b"\xA2\x01") == [
        1, 0, 1, 0, 0, 0, 1, 0,
        0, 0, 0, 0, 0, 0, 0, 1,
    ]


def test_bytes_to_bits_empty():
    assert bytes_to_bits(b"") == []


def test_bits_to_bytes_valid():
    assert bits_to_bytes([1, 1, 0, 0, 0, 1, 1, 0]) == b"\xc6"
    assert bits_to_bytes([]) == b""


def test_bits_to_bytes_invalid_length_raises_value_error():
    with pytest.raises(ValueError, match="multiple of 8"):
        bits_to_bytes([1, 0, 1])


def test_bytes_and_bits_roundtrip():
    data = b"Tatou-2026-Test\x00\xff\xaa"
    assert bits_to_bytes(bytes_to_bits(data)) == data


def test_repeat_bits_valid():
    assert repeat_bits([1, 0], repeat=3) == [1, 1, 1, 0, 0, 0]
    assert repeat_bits([1], repeat=1) == [1]
    assert repeat_bits([], repeat=3) == []


def test_repeat_bits_invalid_repeat_raises_value_error():
    with pytest.raises(ValueError, match="repeat must be positive"):
        repeat_bits([1, 0], repeat=0)

    with pytest.raises(ValueError, match="repeat must be positive"):
        repeat_bits([1, 0], repeat=-1)


def test_majority_voting():
    assert majority([0, 0, 1]) == 0
    assert majority([1, 1, 0]) == 1
    assert majority([1, 1, 1]) == 1
    assert majority([0, 0, 0]) == 0
    assert majority([1, 1, None]) == 1
    assert majority([0, 0, None]) == 0


def test_majority_insufficient_observations():
    with pytest.raises(ValueError, match="Not enough known observations"):
        majority([1, None, None])

    with pytest.raises(ValueError, match="Not enough known observations"):
        majority([None, None, None])

    with pytest.raises(ValueError, match="Not enough known observations"):
        majority([])
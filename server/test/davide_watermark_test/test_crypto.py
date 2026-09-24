from __future__ import annotations

import struct
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
import pytest

from davide_watermark.crypto import (
    _MAX_SECRET_BYTES,
    build_payload,
    derive_aes_key,
    open_payload,
)
from watermarking_method import InvalidKeyError


def test_build_and_open_payload_roundtrip():
    secret = "Tatou watermark test secret"
    key = "master-key-123"

    payload = build_payload(secret, key)

    assert isinstance(payload, bytes)
    assert open_payload(payload, key) == secret


def test_build_and_open_payload_utf8():
    secret = "Test avec des caractères accentués et émojis: 🔐 éàçüö"
    key = "utf8-secret-key"

    payload = build_payload(secret, key)
    assert open_payload(payload, key) == secret


def test_encryption_is_deterministic():
    secret = "deterministic-secret"
    key = "fixed-key"

    payload1 = build_payload(secret, key)
    payload2 = build_payload(secret, key)

    assert payload1 == payload2


def test_derive_aes_key_deterministic_and_unique():
    key1 = "my-key"
    key2 = "other-key"

    derived1_a = derive_aes_key(key1)
    derived1_b = derive_aes_key(key1)
    derived2 = derive_aes_key(key2)

    assert derived1_a == derived1_b
    assert len(derived1_a) == 64
    assert derived1_a != derived2


def test_empty_secret_raises_value_error():
    with pytest.raises(ValueError, match="Secret must not be empty"):
        build_payload("", "some-key")


def test_secret_too_long_raises_value_error():
    secret = "a" * (_MAX_SECRET_BYTES + 1)
    with pytest.raises(ValueError, match="Secret is too long"):
        build_payload(secret, "some-key")


def test_wrong_key_raises_invalid_key_error():
    payload = build_payload("secret-data", "correct-key")
    with pytest.raises(InvalidKeyError):
        open_payload(payload, "wrong-key")


def test_payload_too_short_raises_invalid_key_error():
    with pytest.raises(InvalidKeyError, match="Invalid watermark payload"):
        open_payload(b"short", "key")


def test_unsupported_version_raises_invalid_key_error():
    payload = build_payload("test-secret", "key")
    corrupted_header = b"DWM2" + payload[4:]
    with pytest.raises(InvalidKeyError, match="Unsupported watermark version"):
        open_payload(corrupted_header, "key")


def test_declared_length_overflow_raises_invalid_key_error():
    # Construct a header with length declared above _MAX_SECRET_BYTES
    header = b"DWM1" + struct.pack(">H", 500)
    fake_payload = header + b"x" * (500 + 16)
    with pytest.raises(InvalidKeyError, match="Invalid secret length"):
        open_payload(fake_payload, "key")


def test_payload_length_mismatch_raises_invalid_key_error():
    payload = build_payload("test-secret", "key")
    with pytest.raises(InvalidKeyError, match="Invalid watermark payload"):
        open_payload(payload[:-1], "key")


def test_tampered_ciphertext_raises_invalid_key_error():
    payload = bytearray(build_payload("test-secret", "key"))
    payload[-1] ^= 0xFF
    with pytest.raises(InvalidKeyError):
        open_payload(bytes(payload), "key")


def test_invalid_utf8_plaintext_raises_invalid_key_error():
    key = "utf8-test-key"
    header = b"DWM1" + struct.pack(">H", 2)
    invalid_utf8 = b"\xff\xff"
    cipher = AESSIV(derive_aes_key(key))
    ciphertext = cipher.encrypt(invalid_utf8, [header])
    payload = header + ciphertext

    with pytest.raises(InvalidKeyError, match="Invalid UTF-8 secret"):
        open_payload(payload, key)
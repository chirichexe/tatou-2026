"""Cryptographic tests for key derivation and authenticated QR payloads."""

from __future__ import annotations

import pytest

from watermarking_method import InvalidKeyError
from watermarking_methods.francesco import crypto

DUMMY_KEY = "0123456789abcdef" * 4
OTHER_DUMMY_KEY = "fedcba9876543210" * 4


def test_aes_siv_key_derivation():
    k1 = crypto.derive_aes_key(DUMMY_KEY)
    assert len(k1) == 64
    k2 = crypto.derive_aes_key("dummy-test-passphrase")
    assert len(k2) == 64
    assert k1 != k2

    # Deterministic for same key
    assert crypto.derive_aes_key("same-key") == crypto.derive_aes_key("same-key")

    with pytest.raises(InvalidKeyError):
        crypto.derive_aes_key("")


def test_aes_siv_with_salt_semantic_security():
    # Encrypting the exact same payload twice produces DIFFERENT ciphertexts due to random salt
    dummy_payload = "dummy-copy-link-token-12345"
    p1 = crypto.encrypt_qr_payload(dummy_payload, DUMMY_KEY)
    p2 = crypto.encrypt_qr_payload(dummy_payload, DUMMY_KEY)
    assert p1 != p2  # Semantic security (IND-CPA)
    assert crypto.is_candidate_payload(p1)
    assert crypto.is_candidate_payload(p2)

    # Both decrypt to the identical original payload
    assert crypto.decrypt_qr_payload(p1, DUMMY_KEY) == dummy_payload
    assert crypto.decrypt_qr_payload(p2, DUMMY_KEY) == dummy_payload

    # Wrong key fails authentication
    with pytest.raises(InvalidKeyError):
        crypto.decrypt_qr_payload(p1, OTHER_DUMMY_KEY)

    # Tampered ciphertext fails authentication (MAC / SIV tag mismatch)
    tampered = p1[:-1] + ("A" if p1[-1] != "A" else "B")
    with pytest.raises(InvalidKeyError):
        crypto.decrypt_qr_payload(tampered, DUMMY_KEY)


def test_rmap_secret_roundtrip():
    secret = "Group_07:da0bb583c432fbfd078959ecc9b62902"
    payload = crypto.encrypt_qr_payload(secret, DUMMY_KEY)
    assert crypto.decrypt_qr_payload(payload, DUMMY_KEY) == secret

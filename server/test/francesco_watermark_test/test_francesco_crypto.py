"""Cryptographic unit tests for Francesco's watermark (Davide key derivation, AES-SIV with salt, blind fingerprint)."""

from __future__ import annotations

import pytest
from francesco_watermark import crypto
from watermarking_method import InvalidKeyError

KEY = "0123456789abcdef" * 4
OTHER_KEY = "fedcba9876543210" * 4


def test_davide_compatible_key_derivation():
    # Supports any string key, produces 64 bytes for AES-SIV
    k1 = crypto.derive_aes_key(KEY)
    assert len(k1) == 64
    k2 = crypto.derive_aes_key("simple-string-passphrase")
    assert len(k2) == 64
    assert k1 != k2

    # Deterministic for same key
    assert crypto.derive_aes_key("same-key") == crypto.derive_aes_key("same-key")

    with pytest.raises(InvalidKeyError):
        crypto.derive_aes_key("")


def test_aes_siv_with_salt_semantic_security():
    # Encrypting the exact same secret twice produces DIFFERENT ciphertexts due to random salt
    secret = "unique-copy-link-token-12345"
    p1 = crypto.encrypt_qr_payload(secret, KEY)
    p2 = crypto.encrypt_qr_payload(secret, KEY)
    assert p1 != p2  # Semantic security (IND-CPA)
    assert crypto.is_candidate_payload(p1)
    assert crypto.is_candidate_payload(p2)

    # Both decrypt to the identical original secret
    assert crypto.decrypt_qr_payload(p1, KEY) == secret
    assert crypto.decrypt_qr_payload(p2, KEY) == secret

    # Wrong key fails authentication
    with pytest.raises(InvalidKeyError):
        crypto.decrypt_qr_payload(p1, OTHER_KEY)

    # Tampered ciphertext fails authentication (MAC / SIV tag mismatch)
    tampered = p1[:-1] + ("A" if p1[-1] != "A" else "B")
    with pytest.raises(InvalidKeyError):
        crypto.decrypt_qr_payload(tampered, KEY)


def test_single_step_decryption_and_verification():
    # 3-component secret roundtrip
    secret_3 = "FWM1:Group_11:d3a8f10b"
    payload = crypto.encrypt_qr_payload(secret_3, KEY)
    decrypted = crypto.decrypt_qr_payload(payload, KEY)
    assert decrypted == secret_3

    # Parse components in one shot
    components = crypto.parse_secret_components(decrypted)
    assert components["prefix"] == "FWM1"
    assert components["group"] == "Group_11"
    assert components["string"] == "d3a8f10b"
    assert components["is_our_watermark"] is True

    # 2-component secret roundtrip
    secret_2 = "Group_05:xyz789"
    payload_2 = crypto.encrypt_qr_payload(secret_2, KEY)
    decrypted_2 = crypto.decrypt_qr_payload(payload_2, KEY)
    assert decrypted_2 == secret_2
    comp_2 = crypto.parse_secret_components(decrypted_2)
    assert comp_2["prefix"] == "FWM1"
    assert comp_2["group"] == "Group_05"
    assert comp_2["string"] == "xyz789"
    assert comp_2["is_our_watermark"] is True

    # Simplified single link token secret roundtrip
    secret_link = "d3a8f10b4c7e29a1"
    payload_link = crypto.encrypt_qr_payload(secret_link, KEY)
    decrypted_link = crypto.decrypt_qr_payload(payload_link, KEY)
    assert decrypted_link == secret_link
    comp_link = crypto.parse_secret_components(decrypted_link)
    assert comp_link["prefix"] == "FWM1"
    assert comp_link["group"] == ""
    assert comp_link["string"] == "d3a8f10b4c7e29a1"
    assert comp_link["is_our_watermark"] is True


def test_blind_fingerprint_properties():
    # Blind fingerprint is one-way, 16 hex chars, independent of master key
    fp1 = crypto.compute_visible_code("secret-A", KEY)
    fp1_other = crypto.compute_visible_code("secret-A", OTHER_KEY)
    fp2 = crypto.compute_visible_code("secret-B", KEY)

    assert len(fp1) == 16
    assert set(fp1) <= set("0123456789ABCDEF")
    # Same secret yields exact same blind fingerprint (does not leak key material)
    assert fp1 == fp1_other
    # Different secrets yield distinct fingerprints
    assert fp1 != fp2


def test_parse_secret_components():
    # 3-component format: FWM1:Group_11:abc12345
    parsed3 = crypto.parse_secret_components("FWM1:Group_11:abc12345")
    assert parsed3 == {
        "prefix": "FWM1",
        "group": "Group_11",
        "string": "abc12345",
        "is_our_watermark": True,
    }

    # 2-component format: Group_11:abc12345
    parsed2 = crypto.parse_secret_components("Group_11:abc12345")
    assert parsed2 == {
        "prefix": "FWM1",
        "group": "Group_11",
        "string": "abc12345",
        "is_our_watermark": True,
    }

    # Single token / link format: abc12345
    parsed_link = crypto.parse_secret_components("abc12345")
    assert parsed_link == {
        "prefix": "FWM1",
        "group": "",
        "string": "abc12345",
        "is_our_watermark": True,
    }

    # Unknown prefix
    parsed_other = crypto.parse_secret_components("OTHER:Group_05:xyz")
    assert parsed_other["prefix"] == "OTHER"
    assert parsed_other["is_our_watermark"] is False

"""Cryptographic tests for key derivation, authenticated payloads, and fingerprints."""

from __future__ import annotations

import pytest

from watermarking_method import InvalidKeyError
from watermarking_methods.francesco import crypto, visible

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


def test_visible_token_reencodes_the_same_ciphertext():
    payload = crypto.encrypt_qr_payload(
        "copy-token", DUMMY_KEY, salt=b"visible-salt-001"
    )
    token = crypto.qr_payload_to_visible_token(payload)

    assert token.startswith("FWM1-")
    assert crypto.visible_token_to_qr_payload(token) == payload
    assert crypto.decrypt_qr_payload(
        crypto.visible_token_to_qr_payload(token), DUMMY_KEY
    ) == "copy-token"


@pytest.mark.parametrize("edit", ["substitute", "insert", "delete"])
def test_visible_token_repairs_one_ocr_edit_only_after_authentication(edit):
    secret = "copy-token"
    payload = crypto.encrypt_qr_payload(secret, DUMMY_KEY, salt=b"visible-salt-001")
    token = crypto.qr_payload_to_visible_token(payload)
    prefix, length_pair, encoded = token.split("-", maxsplit=2)
    offset = len(encoded) // 2

    if edit == "substitute":
        replacement = next(
            character
            for character in crypto.VISIBLE_ALPHABET
            if character != encoded[offset]
        )
        observed = encoded[:offset] + replacement + encoded[offset + 1 :]
    elif edit == "insert":
        observed = encoded[:offset] + crypto.VISIBLE_ALPHABET[0] + encoded[offset:]
    else:
        observed = encoded[:offset] + encoded[offset + 1 :]

    damaged = f"{prefix}-{length_pair}-{observed}"
    assert visible.decrypt_visible_tokens([damaged], DUMMY_KEY) == {secret}
    assert visible.decrypt_visible_tokens([damaged], OTHER_DUMMY_KEY) == set()


def test_single_step_decryption_and_verification():
    # 3-component roundtrip
    dummy_secret_3 = "FWM1:Group_11:dummy-token-sample"
    payload = crypto.encrypt_qr_payload(dummy_secret_3, DUMMY_KEY)
    decrypted = crypto.decrypt_qr_payload(payload, DUMMY_KEY)
    assert decrypted == dummy_secret_3

    # Parse components in one shot
    components = crypto.parse_secret_components(decrypted)
    assert components["prefix"] == "FWM1"
    assert components["group"] == "Group_11"
    assert components["string"] == "dummy-token-sample"
    assert components["is_our_watermark"] is True

    # 2-component roundtrip
    dummy_secret_2 = "Group_05:dummy-token-sample"
    payload_2 = crypto.encrypt_qr_payload(dummy_secret_2, DUMMY_KEY)
    decrypted_2 = crypto.decrypt_qr_payload(payload_2, DUMMY_KEY)
    assert decrypted_2 == dummy_secret_2
    comp_2 = crypto.parse_secret_components(decrypted_2)
    assert comp_2["prefix"] == "FWM1"
    assert comp_2["group"] == "Group_05"
    assert comp_2["string"] == "dummy-token-sample"
    assert comp_2["is_our_watermark"] is True

    # Simplified single link token roundtrip
    dummy_link = "dummy-link-token-sample"
    payload_link = crypto.encrypt_qr_payload(dummy_link, DUMMY_KEY)
    decrypted_link = crypto.decrypt_qr_payload(payload_link, DUMMY_KEY)
    assert decrypted_link == dummy_link
    comp_link = crypto.parse_secret_components(decrypted_link)
    assert comp_link["prefix"] == "FWM1"
    assert comp_link["group"] == ""
    assert comp_link["string"] == "dummy-link-token-sample"
    assert comp_link["is_our_watermark"] is True


def test_blind_fingerprint_properties():
    # Blind fingerprint is one-way, 16 hex chars, independent of master key
    fp1 = crypto.compute_visible_code("dummy-sample-A", DUMMY_KEY)
    fp1_other = crypto.compute_visible_code("dummy-sample-A", OTHER_DUMMY_KEY)
    fp2 = crypto.compute_visible_code("dummy-sample-B", DUMMY_KEY)

    assert len(fp1) == 16
    assert set(fp1) <= set("0123456789ABCDEF")
    # Same secret yields exact same blind fingerprint (does not leak key material)
    assert fp1 == fp1_other
    # Different secrets yield distinct fingerprints
    assert fp1 != fp2


def test_parse_secret_components():
    # 3-component format: FWM1:Group_11:dummy-token-sample
    parsed3 = crypto.parse_secret_components("FWM1:Group_11:dummy-token-sample")
    assert parsed3 == {
        "prefix": "FWM1",
        "group": "Group_11",
        "string": "dummy-token-sample",
        "is_our_watermark": True,
    }

    # 2-component format: Group_11:dummy-token-sample
    parsed2 = crypto.parse_secret_components("Group_11:dummy-token-sample")
    assert parsed2 == {
        "prefix": "FWM1",
        "group": "Group_11",
        "string": "dummy-token-sample",
        "is_our_watermark": True,
    }

    # Single token / link format: dummy-token-sample
    parsed_link = crypto.parse_secret_components("dummy-token-sample")
    assert parsed_link == {
        "prefix": "FWM1",
        "group": "",
        "string": "dummy-token-sample",
        "is_our_watermark": True,
    }

    # Unknown prefix
    parsed_other = crypto.parse_secret_components("OTHER:Group_05:dummy-token-sample")
    assert parsed_other["prefix"] == "OTHER"
    assert parsed_other["is_our_watermark"] is False

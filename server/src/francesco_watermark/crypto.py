"""Cryptographic operations, key derivation, and payload encoding for Francesco's watermark."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from watermarking_method import InvalidKeyError

# Key format: strictly 32 bytes encoded as 64 hexadecimal characters
HEX_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-fA-F]{64}\Z")
DOMAIN_PREFIX: Final[bytes] = b"tatou/hybrid-page/v1/"
QR_DOMAIN: Final[bytes] = b"tatou/hybrid-page/qr/v1"
BASE64_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+")


def parse_hex_key(key: str) -> bytes:
    """Validate and decode a 32-byte key represented as 64 hexadecimal characters."""
    if not isinstance(key, str) or not HEX_KEY_PATTERN.fullmatch(key):
        raise ValueError("hybrid-page requires a 32-byte key encoded as 64 hex characters")
    return bytes.fromhex(key)


def derive_sub_key(key: str, purpose: bytes, length: int) -> bytes:
    """Derive a purpose-specific key using HKDF-SHA256."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=DOMAIN_PREFIX + purpose,
    ).derive(parse_hex_key(key))


def compute_visible_code(secret: str, key: str | None = None) -> str:
    """Compute a blind, one-way 16-hex fingerprint of the secret.

    Does NOT expose or leak any part of the master key or the secret.
    Uses SHA-256 with a domain separator to create an irreversible code.
    """
    digest = hashlib.sha256(
        b"tatou/blind-fingerprint/v1/" + secret.encode("utf-8")
    ).hexdigest()
    return digest[:16].upper()


def encrypt_qr_payload(secret: str, key: str, prefix: str = "TW1:") -> str:
    """Encrypt and authenticate the secret using AES-SIV, encoded as URL-safe base64."""
    aes_key = derive_sub_key(key, b"qr-aessiv", 64)
    ciphertext = AESSIV(aes_key).encrypt(secret.encode("utf-8"), [QR_DOMAIN])
    encoded = base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii")
    return prefix + encoded


def decrypt_qr_payload(payload: str, key: str, prefix: str = "TW1:") -> str:
    """Decrypt and verify an AES-SIV QR payload, recovering the plaintext secret."""
    if prefix:
        if not payload.startswith(prefix):
            raise InvalidKeyError(f"Missing required QR prefix {prefix!r}")
        encoded = payload[len(prefix):]
    else:
        encoded = payload

    if not encoded or len(encoded) > 128 or not BASE64_URL_PATTERN.fullmatch(encoded):
        raise InvalidKeyError("Malformed hybrid-page QR")

    try:
        ciphertext = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii") != encoded:
            raise ValueError("Noncanonical QR encoding")
        aes_key = derive_sub_key(key, b"qr-aessiv", 64)
        plaintext = AESSIV(aes_key).decrypt(ciphertext, [QR_DOMAIN])
        secret = plaintext.decode("utf-8")
    except (InvalidTag, ValueError, UnicodeError, binascii.Error) as exc:
        raise InvalidKeyError("Hybrid-page QR authentication failed") from exc

    if not secret:
        raise InvalidKeyError("Empty hybrid-page QR")
    return secret


def compute_expected_photo_tag(secret: str, key: str) -> str:
    """Compute the keyed TrustMark correlation tag for the secret."""
    from .trustmark_experiment import payload_for_copy

    return payload_for_copy(secret, derive_sub_key(key, b"trustmark-tag", 32))

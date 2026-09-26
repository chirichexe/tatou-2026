"""Key derivation and the authenticated AES-SIV payload of the QR code."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from watermarking_method import InvalidKeyError

_VERSION: Final[bytes] = b"\x01"
_AES_KEY_DOMAIN: Final[bytes] = b"tatou/francesco-watermark/aes-siv-key/v1\0"
SALT_BYTES: Final[int] = 16

BASE64_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+")


def derive_aes_key(key: str) -> bytes:
    """Derive a domain-separated 64-byte AES-SIV key."""
    if not isinstance(key, str) or not key:
        raise InvalidKeyError("Invalid watermark key")
    return hashlib.sha512(_AES_KEY_DOMAIN + key.encode("utf-8")).digest()


def is_candidate_payload(payload: str) -> bool:
    """Check if a string matches the format of an encrypted QR payload (URL-safe base64)."""
    if not isinstance(payload, str):
        return False
    text = payload.strip()
    return 20 <= len(text) <= 200 and bool(BASE64_URL_PATTERN.fullmatch(text))


def encrypt_qr_payload(secret: str, key: str, salt: bytes | None = None) -> str:
    """Encrypt and authenticate the secret using AES-SIV with a random salt.

    The salt (16 random bytes) ensures semantic security (IND-CPA): encrypting the
    same secret twice produces completely distinct ciphertexts.
    """
    if not secret:
        raise ValueError("Secret must not be empty")
    if salt is None:
        salt = os.urandom(SALT_BYTES)
    elif len(salt) != SALT_BYTES:
        raise ValueError(f"Salt must be exactly {SALT_BYTES} bytes")

    aes_key = derive_aes_key(key)
    plaintext = salt + secret.encode("utf-8")
    ciphertext = AESSIV(aes_key).encrypt(plaintext, [_VERSION])
    return base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii")


def decrypt_qr_payload(payload: str, key: str) -> str:
    """Decrypt and verify an AES-SIV QR payload, extracting the secret and discarding the salt."""
    encoded = payload.strip()
    if not encoded or len(encoded) > 200 or not BASE64_URL_PATTERN.fullmatch(encoded):
        raise InvalidKeyError("Malformed francesco-watermark QR")

    try:
        ciphertext = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii") != encoded:
            raise ValueError("Noncanonical QR encoding")
        aes_key = derive_aes_key(key)
        plaintext = AESSIV(aes_key).decrypt(ciphertext, [_VERSION])
    except (InvalidTag, ValueError, UnicodeError, binascii.Error) as exc:
        raise InvalidKeyError("Francesco-watermark QR authentication failed") from exc

    if len(plaintext) < SALT_BYTES:
        raise InvalidKeyError("Payload shorter than salt length")

    secret_bytes = plaintext[SALT_BYTES:]
    try:
        secret = secret_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidKeyError("Invalid UTF-8 secret") from exc

    if not secret:
        raise InvalidKeyError("Empty francesco-watermark QR secret")

    return secret

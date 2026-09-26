"""Cryptographic operations, key derivation, and payload encoding for Francesco's watermark."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from watermarking_method import InvalidKeyError

_VERSION: Final[bytes] = b"FWM1"
_AES_KEY_DOMAIN: Final[bytes] = b"tatou/francesco-watermark/aes-siv-key/v1\0"
SALT_BYTES: Final[int] = 16

HEX_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-fA-F]{64}\Z")
BASE64_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+")
VISIBLE_PREFIX: Final[str] = "FWM1-"
# Sixteen glyphs selected to avoid common OCR pairs such as 0/O, 1/I/L,
# 5/S, 6/G, 7/T, 8/B, J/U, and Q/O.
VISIBLE_ALPHABET: Final[str] = "ABCDEFGHJKMNPRST"
VISIBLE_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"FWM1-([{VISIBLE_ALPHABET}]{{2}})-([{VISIBLE_ALPHABET}]+)\Z"
)


def derive_aes_key(key: str) -> bytes:
    """Derive a domain-separated 64-byte AES-SIV key."""
    if not isinstance(key, str) or not key:
        raise InvalidKeyError("Invalid watermark key")
    return hashlib.sha512(_AES_KEY_DOMAIN + key.encode("utf-8")).digest()


def parse_hex_key(key: str) -> bytes:
    """Validate and decode a 32-byte key represented as 64 hexadecimal characters."""
    if not isinstance(key, str) or not HEX_KEY_PATTERN.fullmatch(key):
        raise ValueError(
            "francesco-watermark requires a 32-byte key encoded as 64 hex characters"
        )
    return bytes.fromhex(key)


def derive_sub_key(key: str, purpose: bytes, length: int) -> bytes:
    """Derive a purpose-specific key using HKDF-SHA256."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=b"tatou/francesco-watermark/v1/" + purpose,
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


def qr_payload_to_visible_token(payload: str) -> str:
    """Encode one encrypted payload as an uppercase, OCR-friendly token."""
    if not is_candidate_payload(payload):
        raise ValueError("Invalid encrypted watermark payload")
    ciphertext = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    if len(ciphertext) > 255:
        raise ValueError("Visible watermark ciphertext is too long")

    def encode_byte(value: int) -> str:
        return VISIBLE_ALPHABET[value >> 4] + VISIBLE_ALPHABET[value & 0x0F]

    encoded = "".join(encode_byte(value) for value in ciphertext)
    return f"{VISIBLE_PREFIX}{encode_byte(len(ciphertext))}-{encoded}"


def visible_token_to_qr_payload(token: str) -> str:
    """Recover the canonical QR representation from a visible token."""
    match = VISIBLE_TOKEN_PATTERN.fullmatch(token.strip().upper())
    if match is None:
        raise ValueError("Invalid visible watermark token")
    alphabet_index = {character: index for index, character in enumerate(VISIBLE_ALPHABET)}

    def decode_pair(pair: str) -> int:
        return (alphabet_index[pair[0]] << 4) | alphabet_index[pair[1]]

    expected_bytes = decode_pair(match.group(1))
    encoded = match.group(2)
    expected_characters = expected_bytes * 2
    if len(encoded) != expected_characters:
        raise ValueError("Invalid visible watermark token length")
    ciphertext = bytes(
        decode_pair(encoded[offset : offset + 2])
        for offset in range(0, len(encoded), 2)
    )
    if len(ciphertext) != expected_bytes:
        raise ValueError("Invalid visible watermark ciphertext length")
    return base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii")


def parse_secret_components(secret: str) -> dict[str, str | bool]:
    """Parse a watermark secret into its constituent components: prefix, group, and string.

    Returns a dictionary with:
    - 'prefix': the algorithm/version prefix (e.g. 'FWM1')
    - 'group': the intended recipient group (e.g. 'Group_11' or '' if link-only)
    - 'string': the unique copy identifier / link token
    - 'is_our_watermark': True if the prefix matches our watermark identifier ('FWM1')
    """
    if not isinstance(secret, str) or not secret:
        return {
            "prefix": "",
            "group": "",
            "string": "",
            "is_our_watermark": False,
        }

    parts = secret.split(":")
    if len(parts) >= 3:
        prefix = parts[0]
        group = parts[1]
        token = ":".join(parts[2:])
        return {
            "prefix": prefix,
            "group": group,
            "string": token,
            "is_our_watermark": prefix == "FWM1",
        }
    elif len(parts) == 2:
        # Standard Tatou/RMAP format: group:link
        return {
            "prefix": "FWM1",
            "group": parts[0],
            "string": parts[1],
            "is_our_watermark": True,
        }
    # Simplified single link token format
    return {
        "prefix": "FWM1",
        "group": "",
        "string": secret,
        "is_our_watermark": True,
    }

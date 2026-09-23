from __future__ import annotations

import hashlib
import struct
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from watermarking_method import InvalidKeyError


_VERSION: Final[bytes] = b"DWM1"
_MAX_SECRET_BYTES: Final[int] = 128
_AES_KEY_DOMAIN: Final[bytes] = b"tatou/davide-watermark/aes-siv-key/v1\0"


def derive_aes_key(key: str) -> bytes:
    """Derive the AES-SIV key from the user key."""

    # Convert the user key to bytes and hash it with a domain separator.
    return hashlib.sha512(
        _AES_KEY_DOMAIN + key.encode("utf-8")
    ).digest()


def build_payload(secret: str, key: str) -> bytes:
    """
    Build an authenticated encrypted payload.

    Format:
    | 4 bytes version | 2 bytes secret length | tag + encrypted secret |
    """

    if not secret:
        raise ValueError("Secret must not be empty")

    # Convert the secret to UTF-8 bytes
    secret_bytes = secret.encode("utf-8")

    if len(secret_bytes) > _MAX_SECRET_BYTES:
        raise ValueError("Secret is too long")

    # Header = version + secret length 
    header = _VERSION + struct.pack(">H", len(secret_bytes))

    # Derive the symmetric key and encrypt the secret
    cipher = AESSIV(derive_aes_key(key))

    # The header is authenticated as associated data
    ciphertext = cipher.encrypt(secret_bytes, [header])

    # Store the plaintext header together with the encrypted payload
    return header + ciphertext


def open_payload(payload: bytes, key: str) -> str:
    # Header = version + secret length (big-endian).
    """Validate, decrypt, and return the secret."""

    # The header is 6 bytes: 4-byte version + 2-byte length
    if len(payload) < 6:
        raise InvalidKeyError("Invalid watermark payload")

    # Split header from encrypted secret
    header = payload[:6]
    ciphertext = payload[6:]

    # Check the protocol version
    if header[:4] != _VERSION:
        raise InvalidKeyError("Unsupported watermark version")

    # Read the declared secret length
    secret_length = struct.unpack(">H", header[4:6])[0]

    if secret_length > _MAX_SECRET_BYTES:
        raise InvalidKeyError("Invalid secret length")

    # AES-SIV adds a 16-byte authentication tag
    expected_length = 6 + secret_length + 16

    if len(payload) != expected_length:
        raise InvalidKeyError("Invalid watermark payload")

    cipher = AESSIV(derive_aes_key(key))

    try:
        # Decrypt and verify authenticity using the same associated data
        secret_bytes = cipher.decrypt(ciphertext, [header])
    except InvalidTag as exc:
        raise InvalidKeyError("Wrong key or corrupted watermark") from exc

    # Convert the recovered bytes back to the original string
    try:
        return secret_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidKeyError("Invalid UTF-8 secret") from exc
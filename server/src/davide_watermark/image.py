"""Image-domain watermark: the core algorithm.

Both layers work on the 8x8 DCT of the luminance channel, like JPEG does, so
they survive JPEG recompression. They use disjoint coefficients and do not
interfere with each other.

1. Blind layer (dithered QIM, mid frequencies)
   Carries the encrypted payload. Each coefficient is quantised onto one of two
   interleaved lattices (bit 0 or bit 1), shifted by a key-derived dither, so the
   bits cannot be located or read without the key. Every bit is spread over many
   coefficients in key-dependent order and decoded by summing soft votes.

2. Fingerprint (spread spectrum, low frequencies)
   A ±1 pseudo-noise pattern derived from (key, secret) is added to the low
   frequencies, which survive blur, down-scaling and screenshots. It is detected
   by aligning the leak onto the original, subtracting it and correlating the
   residual with the pattern of every issued secret.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

import numpy as np
from PIL import Image


_DOMAIN: Final[bytes] = b"tatou/davide-watermark/image/v1/"

# DCT coefficients (row, column) used by each layer.
_FINGERPRINT_COEFS: Final = ((0, 1), (1, 0), (1, 1), (0, 2), (2, 0), (1, 2), (2, 1))
_QIM_COEFS: Final = ((0, 3), (3, 0), (2, 2), (1, 3), (3, 1), (2, 3), (3, 2))

# Distance between two lattice points of the same bit. Larger = more robust, more visible.
_QIM_STEP: Final[float] = 28.0

# Average amplitude of the fingerprint pattern.
_FINGERPRINT_STRENGTH: Final[float] = 5.0

# The first 1/8 of the slots carry only the 6-byte payload header (version and
# secret length), so the reader learns the payload length before decoding it.
_HEADER_BITS: Final[int] = 6 * 8
_HEADER_SHARE: Final[int] = 8

# 8x8 orthonormal DCT-II matrix: dct = D @ block @ D.T, block = D.T @ dct @ D.
_D: Final = np.array([
    [np.sqrt((1 if k == 0 else 2) / 8) * np.cos((2 * n + 1) * k * np.pi / 16) for n in range(8)]
    for k in range(8)
])


def _rng(key: str, label: bytes) -> np.random.Generator:
    """Deterministic random generator derived from the key (HMAC-SHA256 seed)."""

    seed = hmac.new(key.encode("utf-8"), _DOMAIN + label, hashlib.sha256).digest()
    return np.random.default_rng(int.from_bytes(seed, "big"))


def _dct_blocks(img: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Return the YCbCr pixels and the DCT of every full 8x8 luminance block."""

    ycc = np.asarray(img.convert("YCbCr"), dtype=np.float64).copy()
    h, w = (ycc.shape[0] // 8) * 8, (ycc.shape[1] // 8) * 8
    blocks = (ycc[:h, :w, 0] - 128).reshape(h // 8, 8, w // 8, 8).swapaxes(1, 2)

    # shape: (blocks_y, blocks_x, 8, 8)
    return ycc, _D @ blocks @ _D.T


def _from_dct_blocks(ycc: np.ndarray, dct: np.ndarray) -> Image.Image:
    """Write the DCT blocks back into the luminance channel and return an RGB image."""

    blocks = _D.T @ dct @ _D
    h, w = blocks.shape[0] * 8, blocks.shape[1] * 8
    ycc[:h, :w, 0] = blocks.swapaxes(1, 2).reshape(h, w) + 128

    return Image.fromarray(np.clip(ycc, 0, 255).round().astype(np.uint8), "YCbCr").convert("RGB")


def _select(dct: np.ndarray, coefs) -> np.ndarray:
    """Pick the given coefficients of every block: shape (blocks_y, blocks_x, len(coefs))."""

    return np.stack([dct[:, :, u, v] for u, v in coefs], axis=-1)


def _assign(dct: np.ndarray, coefs, values: np.ndarray) -> None:
    for i, (u, v) in enumerate(coefs):
        dct[:, :, u, v] = values[..., i]


# ---------------------------------------------------------------------------
# Blind layer
# ---------------------------------------------------------------------------

def payload_capacity(img: Image.Image) -> int:
    """Number of QIM slots available to the payload (header slots excluded)."""

    slots = (img.height // 8) * (img.width // 8) * len(_QIM_COEFS)
    return slots - slots // _HEADER_SHARE


def _qim_slots(size: int, key: str) -> tuple[np.ndarray, np.ndarray]:
    """Key-dependent slot order and per-slot dither."""

    order = _rng(key, b"qim/order").permutation(size)
    dither = _rng(key, b"qim/dither").uniform(0, _QIM_STEP, size)
    return order, dither


def embed_payload(img: Image.Image, bits: list[int], key: str) -> Image.Image:
    """Embed the payload bits (header first) into the mid-frequency coefficients."""

    ycc, dct = _dct_blocks(img)
    values = _select(dct, _QIM_COEFS)
    flat = values.reshape(-1)
    order, dither = _qim_slots(flat.size, key)
    header_slots = flat.size // _HEADER_SHARE

    # The i-th slot in key order carries bit i % 48 (header region),
    # then bit i % len(bits) (payload region).
    bit_of_rank = np.concatenate([
        np.arange(header_slots) % _HEADER_BITS,
        np.arange(flat.size - header_slots) % len(bits),
    ])
    target = np.empty(flat.size, dtype=np.int64)
    target[order] = np.asarray(bits)[bit_of_rank]

    # Move every coefficient to the nearest point of its bit's lattice:
    # dither + k * step for bit 0, dither + k * step + step / 2 for bit 1.
    offset = dither + target * _QIM_STEP / 2
    flat[:] = _QIM_STEP * np.round((flat - offset) / _QIM_STEP) + offset

    _assign(dct, _QIM_COEFS, values)
    return _from_dct_blocks(ycc, dct)


def _soft_votes(img: Image.Image, key: str) -> tuple[np.ndarray, int]:
    """Per-slot vote in key order (+1 = bit 0, -1 = bit 1) and the header size."""

    _, dct = _dct_blocks(img)
    flat = _select(dct, _QIM_COEFS).reshape(-1)
    order, dither = _qim_slots(flat.size, key)

    # Position of the coefficient between two bit-0 lattice points, in [0, 1):
    # 0 means bit 0, 0.5 means bit 1. The cosine turns it into a soft vote.
    phase = np.mod(flat[order] - dither[order], _QIM_STEP) / _QIM_STEP
    return np.cos(2 * np.pi * phase), flat.size // _HEADER_SHARE


def _decode(votes: np.ndarray, n_bits: int) -> list[int]:
    """Sum the votes of every bit's slots and take the sign."""

    totals = np.bincount(np.arange(votes.size) % n_bits, weights=votes, minlength=n_bits)
    return (totals < 0).astype(int).tolist()


def extract_header(img: Image.Image, key: str) -> list[int]:
    votes, header_slots = _soft_votes(img, key)
    return _decode(votes[:header_slots], _HEADER_BITS)


def extract_payload(img: Image.Image, key: str, n_bits: int) -> list[int]:
    votes, header_slots = _soft_votes(img, key)
    return _decode(votes[header_slots:], n_bits)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def _pattern(shape: tuple[int, ...], key: str, secret: str) -> np.ndarray:
    """±1 pseudo-noise pattern unique to (key, secret)."""

    return _rng(key, b"fingerprint/" + secret.encode("utf-8")).choice((-1.0, 1.0), size=shape)


def embed_fingerprint(img: Image.Image, key: str, secret: str) -> Image.Image:
    """Add the recipient's pattern to the low-frequency coefficients."""

    ycc, dct = _dct_blocks(img)
    values = _select(dct, _FINGERPRINT_COEFS)

    # Stronger in textured blocks, where it is invisible; weaker in flat areas.
    activity = np.abs(dct[:, :, 1:, 1:]).mean(axis=(2, 3))
    gain = _FINGERPRINT_STRENGTH * np.clip(activity / (activity.mean() + 1e-9), 0.4, 2.0)

    _assign(dct, _FINGERPRINT_COEFS, values + gain[..., None] * _pattern(values.shape, key, secret))
    return _from_dct_blocks(ycc, dct)


def _translation(reference: np.ndarray, moved: np.ndarray) -> tuple[int, int]:
    """(dy, dx) shift between two same-size grayscale arrays, by phase correlation."""

    cross = np.fft.fft2(reference) * np.conj(np.fft.fft2(moved))
    corr = np.fft.ifft2(cross / (np.abs(cross) + 1e-9)).real
    dy, dx = np.unravel_index(np.argmax(corr), corr.shape)
    h, w = corr.shape
    return int(dy - h if dy > h // 2 else dy), int(dx - w if dx > w // 2 else dx)


def _alignments(leak: Image.Image, original: Image.Image) -> list[Image.Image]:
    """Two candidate registrations of the leak onto the original's pixel grid."""

    # 1. The leak was rescaled (down-sampling, screenshot): stretch it back.
    rescaled = leak.resize(original.size, Image.BICUBIC)

    # 2. The leak was cropped at the same scale: find the offset and paste it
    #    over the original, so the missing border contributes no residual.
    w, h = min(leak.width, original.width), min(leak.height, original.height)
    moved = np.zeros((original.height, original.width))
    moved[:h, :w] = np.asarray(leak.convert("L"), dtype=np.float64)[:h, :w]
    dy, dx = _translation(np.asarray(original.convert("L"), dtype=np.float64), moved)
    shifted = original.convert("RGB")
    shifted.paste(leak.convert("RGB").crop((0, 0, w, h)), (dx, dy))

    return [rescaled, shifted]


def fingerprint_scores(
    leak: Image.Image,
    original: Image.Image,
    key: str,
    secrets: list[str],
) -> dict[str, float]:
    """
    z-score of every candidate secret.

    For a recipient whose copy did not leak the score is ~N(0, 1): the pattern
    is independent of the residual. For the leaker it grows with the number of
    surviving coefficients (hundreds on an intact copy).
    """

    scores = {secret: float("-inf") for secret in secrets}
    _, reference = _dct_blocks(original)
    reference = _select(reference, _FINGERPRINT_COEFS)

    for aligned in _alignments(leak, original):
        _, dct = _dct_blocks(aligned)
        residual = _select(dct, _FINGERPRINT_COEFS) - reference
        residual -= residual.mean()
        norm = np.linalg.norm(residual)
        if norm == 0:
            continue

        for secret in secrets:
            z = float((residual * _pattern(residual.shape, key, secret)).sum() / norm)
            scores[secret] = max(scores[secret], z)

    return scores

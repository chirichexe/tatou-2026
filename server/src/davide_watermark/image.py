"""Watermark of a single image

The image is split in 8x8 blocks and each block goes through a DCT, as in
JPEG. Only the brightness channel (Y) is changed, in two layers:

- payload: the encrypted secret in the middle frequencies, written with QIM
  (each coefficient is rounded onto one of two grids, one for 0 and one for 1).
  The key decides the order of the coefficients and shifts the grids, so the
  bits can only be read with the key. Every bit is written many times

- fingerprint: +-1 noise that depends on the secret, added to the low
  frequencies. It can't be read, only recognised by comparing a leak with the
  original. It survives screenshots, resizing and blur
"""

from __future__ import annotations

import hashlib
import hmac

import numpy as np
from PIL import Image

from .align import align


# (row, col) of the coefficients used in every 8x8 block
FINGERPRINT = [(0, 1), (1, 0), (1, 1), (0, 2), (2, 0), (1, 2), (2, 1)]  # low frequencies
PAYLOAD = [(0, 3), (3, 0), (2, 2), (1, 3), (3, 1), (2, 3), (3, 2)]      # middle frequencies

QIM_STEP = 28.0             # grid spacing: bigger is more robust but more visible
FINGERPRINT_STRENGTH = 5.0  # average change of a fingerprint coefficient

# 8x8 DCT matrix: dct = D @ block @ D.T and block = D.T @ dct @ D
D = np.array([[np.sqrt((1 if k == 0 else 2) / 8) * np.cos((2 * n + 1) * k * np.pi / 16)
               for n in range(8)] for k in range(8)])


def _rng(key: str, label: bytes) -> np.random.Generator:
    # the writer and the reader get the same random numbers from the same key
    seed = hmac.new(key.encode("utf-8"), label, hashlib.sha256).digest()
    return np.random.default_rng(int.from_bytes(seed, "big"))


def _dct(img: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """YCbCr pixels and the DCT of every full 8x8 block of Y, shape (rows, cols, 8, 8)"""
    ycc = np.asarray(img.convert("YCbCr"), dtype=np.float64).copy()
    h, w = ycc.shape[0] // 8 * 8, ycc.shape[1] // 8 * 8
    blocks = (ycc[:h, :w, 0] - 128).reshape(h // 8, 8, w // 8, 8).swapaxes(1, 2)
    return ycc, D @ blocks @ D.T


def _to_image(ycc: np.ndarray, dct: np.ndarray) -> Image.Image:
    blocks = D.T @ dct @ D
    h, w = blocks.shape[0] * 8, blocks.shape[1] * 8
    ycc[:h, :w, 0] = blocks.swapaxes(1, 2).reshape(h, w) + 128
    return Image.fromarray(np.clip(ycc, 0, 255).round().astype(np.uint8), "YCbCr").convert("RGB")


def _get(dct: np.ndarray, positions: list[tuple[int, int]]) -> np.ndarray:
    rows, cols = zip(*positions)
    return dct[:, :, list(rows), list(cols)]


def _set(dct: np.ndarray, positions: list[tuple[int, int]], values: np.ndarray) -> None:
    rows, cols = zip(*positions)
    dct[:, :, list(rows), list(cols)] = values


# ---------------------------------------------------------------- payload

def capacity(img: Image.Image) -> int:
    """Number of payload coefficients (slots) of the image"""
    return (img.width // 8) * (img.height // 8) * len(PAYLOAD)


def _order_and_dither(n: int, key: str) -> tuple[np.ndarray, np.ndarray]:
    order = _rng(key, b"qim/order").permutation(n)
    dither = _rng(key, b"qim/dither").uniform(0, QIM_STEP, n)
    return order, dither


def embed_payload(img: Image.Image, bits: np.ndarray, key: str) -> Image.Image:
    ycc, dct = _dct(img)
    coefs = _get(dct, PAYLOAD).reshape(-1)
    order, dither = _order_and_dither(coefs.size, key)

    # the i-th slot in key order carries bit i % len(bits), so the payload is repeated
    wanted = np.empty(coefs.size, dtype=np.int64)
    wanted[order] = bits[np.arange(coefs.size) % len(bits)]

    # nearest point of the grid: dither + k * step for 0, half a step further for 1
    offset = dither + wanted * QIM_STEP / 2
    coefs = QIM_STEP * np.round((coefs - offset) / QIM_STEP) + offset

    _set(dct, PAYLOAD, coefs.reshape(dct.shape[0], dct.shape[1], len(PAYLOAD)))
    return _to_image(ycc, dct)


def read_votes(img: Image.Image, key: str) -> np.ndarray:
    """One vote per slot in key order: +1 means 0, -1 means 1, around 0 means damaged"""
    _, dct = _dct(img)
    coefs = _get(dct, PAYLOAD).reshape(-1)
    order, dither = _order_and_dither(coefs.size, key)

    # 0 on the grid of the zeros, 0.5 on the grid of the ones
    phase = np.mod(coefs[order] - dither[order], QIM_STEP) / QIM_STEP
    return np.cos(2 * np.pi * phase)


def vote(votes: np.ndarray, n_bits: int) -> np.ndarray:
    """Bit i is the sign of the sum of slots i, i + n_bits, i + 2 * n_bits..."""
    totals = np.bincount(np.arange(votes.size) % n_bits, weights=votes, minlength=n_bits)
    return (totals < 0).astype(np.uint8)


# ---------------------------------------------------------------- fingerprint

def _noise(shape: tuple[int, ...], key: str, secret: str) -> np.ndarray:
    return _rng(key, b"fingerprint/" + secret.encode("utf-8")).choice((-1.0, 1.0), size=shape)


def embed_fingerprint(img: Image.Image, key: str, secret: str) -> Image.Image:
    ycc, dct = _dct(img)
    coefs = _get(dct, FINGERPRINT)

    # stronger in detailed blocks, weaker in flat ones where it would be visible
    detail = np.abs(dct[:, :, 1:, 1:]).mean(axis=(2, 3))
    strength = FINGERPRINT_STRENGTH * np.clip(detail / (detail.mean() + 1e-9), 0.4, 2.0)

    _set(dct, FINGERPRINT, coefs + strength[..., None] * _noise(coefs.shape, key, secret))
    return _to_image(ycc, dct)


def fingerprint_scores(leak: Image.Image, original: Image.Image, key: str, secrets: list[str]) -> dict[str, float]:
    """
    z-score of every secret's noise in the difference between leak and original

    If the noise of a secret is not in the leak the score follows N(0, 1),
    for the real recipient it is large (around 200 on an intact copy)
    """
    scores = {secret: float("-inf") for secret in secrets}
    reference = _get(_dct(original)[1], FINGERPRINT)

    for aligned in align(leak, original):
        diff = _get(_dct(aligned)[1], FINGERPRINT) - reference
        diff -= diff.mean()
        # outliers (white corners of a rotation, pasted logos) carry no noise:
        # clip them to 3 robust standard deviations
        limit = 3 * 1.4826 * np.median(np.abs(diff))
        diff = np.clip(diff, -limit, limit)
        norm = np.linalg.norm(diff)
        if norm == 0:
            continue
        for secret in secrets:
            z = float((diff * _noise(diff.shape, key, secret)).sum() / norm)
            scores[secret] = max(scores[secret], z)

    return scores

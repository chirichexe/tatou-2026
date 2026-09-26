"""Putting a leaked image back onto the pixels of the original

The fingerprint can only be compared if the leak lies on the original within
a pixel or two. The leaker may have mirrored, rotated, cropped and resized
the picture, so these edits are searched and undone
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps


def align(leak: Image.Image, original: Image.Image) -> list[Image.Image]:
    """The leak on the original's pixels in two ways: a plain resize (it also
    covers changed proportions) and the full registration"""
    return [leak.resize(original.size, Image.BICUBIC), register(leak, original)]


def register(leak: Image.Image, original: Image.Image) -> Image.Image:
    """
    Find mirror, angle and scale of the leak, then paste it over the original

    A coarse grid of guesses on small images, then a pattern search: move to
    the best of the 3x3 neighbours, halve the steps when the current guess is
    already the best. Only image similarity chooses the guess, never the
    secrets, so the scores of innocent recipients are not affected
    """
    side = max(original.size)
    if max(leak.size) > 1.5 * side:
        # e.g. a whole page: the extra pixels only slow the search down
        k = 1.5 * side / max(leak.size)
        leak = leak.resize((round(leak.width * k), round(leak.height * k)), Image.BICUBIC)

    scales = [min(max(leak.width / original.width, 0.2), 1.5), 1.0, *(0.25 * 1.25 ** i for i in range(8))]
    guess = _best(leak, original, 256 / side, [(f, a, s) for f in (False, True) for a in (-3, 0, 3) for s in scales])

    angle_step, scale_step = 1.5, 0.1
    for _ in range(30):
        if angle_step < 0.03:
            break
        flip, angle, scale = guess
        neighbours = [(flip, angle + i * angle_step, scale * (1 + j * scale_step)) for i in (-1, 0, 1) for j in (-1, 0, 1)]
        # a tiny scale would blow the leak up to a huge image
        neighbours = [(f, a, s) for f, a, s in neighbours if abs(a) <= 6 and 0.2 <= s <= 1.5] or [guess]
        best = _best(leak, original, min(1.0, (256 if angle_step > 0.3 else 512) / side), neighbours)
        if best == guess:
            angle_step, scale_step = angle_step / 2, scale_step / 2
        guess = best

    # the part that was cut away stays as in the original, so it counts as unchanged
    piece = _undo(leak.convert("RGB"), *guess)
    mask = _undo(Image.new("L", leak.size, 255), *guess)
    target = _gray(original)
    dy, dx = _offset(np.fft.fft2(target - target.mean()), _gray(piece), whiten=1.0)
    placed = original.convert("RGB")
    placed.paste(piece, (dx, dy), mask)
    return placed


def _undo(img: Image.Image, flip: bool, angle: float, scale: float) -> Image.Image:
    """Undo a mirror, a rotation and a resize (scale = leak size / original size)"""
    if flip:
        img = ImageOps.mirror(img)
    if angle:
        img = img.rotate(angle, Image.BILINEAR, expand=True)
    return img.resize((max(1, round(img.width / scale)), max(1, round(img.height / scale))), Image.BILINEAR)


def _best(leak: Image.Image, original: Image.Image, size: float, guesses: list) -> tuple:
    """The (flip, angle, scale) guess that makes the leak most similar to the
    original, working at `size` times the resolution"""
    target = _details(original, size)
    target_fft = np.fft.fft2(target - target.mean())
    leak = Image.fromarray(_gray(leak, size).astype(np.uint8))
    return max(guesses, key=lambda guess: _similarity(target, target_fft, _details(_undo(leak, *guess))))


def _similarity(original: np.ndarray, original_fft: np.ndarray, piece: np.ndarray) -> float:
    """Correlation between the piece and the part of the original under it"""
    dy, dx = _offset(original_fft, piece, whiten=0.5)
    y0, x0 = max(dy, 0), max(dx, 0)
    y1, x1 = min(dy + piece.shape[0], original.shape[0]), min(dx + piece.shape[1], original.shape[1])
    if y1 <= y0 or x1 <= x0 or (y1 - y0) * (x1 - x0) < 0.05 * original.size:
        return -1.0
    left = original[y0:y1, x0:x1].ravel()
    right = piece[y0 - dy:y1 - dy, x0 - dx:x1 - dx].ravel()
    left = left - left.mean()
    right = right - right.mean()
    denominator = np.linalg.norm(left) * np.linalg.norm(right)
    if denominator == 0:
        return -1.0
    return float(np.dot(left, right) / denominator)


def _offset(original_fft: np.ndarray, piece: np.ndarray, whiten: float) -> tuple[int, int]:
    """
    (dy, dx) of the piece inside the original, by phase correlation

    whiten = 1 gives a very sharp peak, used for the final placement.
    whiten = 0.5 still finds the peak when scale or angle are a few percent
    off, which the search needs
    """
    canvas = np.zeros(original_fft.shape)
    h, w = min(piece.shape[0], canvas.shape[0]), min(piece.shape[1], canvas.shape[1])
    canvas[:h, :w] = piece[:h, :w] - piece.mean()
    cross = original_fft * np.conj(np.fft.fft2(canvas))
    peak = np.fft.ifft2(cross / (np.abs(cross) + 1e-9) ** whiten).real
    dy, dx = np.unravel_index(np.argmax(peak), peak.shape)
    h, w = peak.shape
    # past the middle means a negative shift
    return int(dy - h if dy > h // 2 else dy), int(dx - w if dx > w // 2 else dx)


def _gray(img: Image.Image, size: float = 1.0) -> np.ndarray:
    img = img.convert("L")
    if size != 1.0:
        img = img.resize((max(1, round(img.width * size)), max(1, round(img.height * size))))
    return np.asarray(img, dtype=np.float64)


def _details(img: Image.Image, size: float = 1.0) -> np.ndarray:
    """Edges and texture (image minus its blur): they make the match precise"""
    small = Image.fromarray(_gray(img, size).astype(np.uint8))
    return _gray(small) - _gray(small.filter(ImageFilter.GaussianBlur(3)))

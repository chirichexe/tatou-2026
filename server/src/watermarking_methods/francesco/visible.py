"""OCR recovery for the AES-SIV-encrypted visible labels."""

from __future__ import annotations

import io
import itertools
import re
import shutil
import subprocess
import time
from collections import Counter
from difflib import SequenceMatcher

import pymupdf as fitz
from PIL import Image

from watermarking_method import InvalidKeyError, WatermarkingError

from . import crypto
from .rendering import DEFAULT_VISIBLE_TEXT_COUNT

_PAYLOAD_PATTERN = re.compile(r"[A-Za-z2-7]{44,200}", re.IGNORECASE)
_LABEL_PAYLOAD_PATTERN = re.compile(
    r"GROUP[_A-Za-z0-9]*-_*([A-Za-z2-7]{44,200})", re.IGNORECASE,
)
_BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
# 0, 1, 8 and 9 are not Base32: allowing them lets Tesseract read "o" as "0"
# or "l" as "1" (sometimes both, "o0"), which splits or lengthens the payload
_OCR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz234567_-"
# OCR runs whenever no QR code authenticates, i.e. on any uploaded PDF, so its
# cost is capped: the labels repeat on every page, the first pages are enough
_MAX_OCR_PAGES = 2
_MAX_LABEL_CROPS = DEFAULT_VISIBLE_TEXT_COUNT
_OCR_BUDGET_SECONDS = 60.0


def _ocr(image: Image.Image, deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return ""
    executable = shutil.which("tesseract")
    if executable is None:
        raise WatermarkingError("Visible watermark OCR requires Tesseract")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    try:
        # Tesseract is resolved from PATH and receives fixed arguments and image bytes on stdin.
        completed = subprocess.run(  # noqa: S603
            [
                executable,
                "stdin",
                "stdout",
                "--psm",
                "6",
                "-l",
                "eng",
                "-c",
                f"tessedit_char_whitelist={_OCR_WHITELIST}",
            ],
            input=buffer.getvalue(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=min(30.0, remaining),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ""
    except (OSError, subprocess.SubprocessError) as error:
        raise WatermarkingError("Visible watermark OCR failed") from error
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode("utf-8", "ignore")


def _candidate_payloads(text: str) -> list[str]:
    candidates: list[str] = []
    for line in text.splitlines():
        label_match = _LABEL_PAYLOAD_PATTERN.search(line)
        if label_match:
            candidates.append(label_match.group(1))
        else:
            candidates.extend(_PAYLOAD_PATTERN.findall(line))
    return candidates


def _consensus_candidates(candidates: list[str]) -> set[str]:
    """Combine repeated OCR reads and enumerate ties for AES authentication."""
    length_counts = Counter(map(len, candidates))
    if not length_counts:
        return set()
    most_common_count = length_counts.most_common(1)[0][1]
    anchor_lengths = {
        length for length, count in length_counts.items() if count == most_common_count
    }
    consensuses: set[str] = set()
    for anchor in candidates:
        if len(anchor) not in anchor_lengths:
            continue
        votes = [Counter() for _char in anchor]
        for candidate in candidates:
            if abs(len(candidate) - len(anchor)) > 2:
                continue
            for tag, a_start, a_end, b_start, b_end in SequenceMatcher(
                None, anchor, candidate, autojunk=False,
            ).get_opcodes():
                if tag == "equal":
                    for offset in range(a_end - a_start):
                        votes[a_start + offset][candidate[b_start + offset]] += 1
                elif tag == "replace":
                    shared = min(a_end - a_start, b_end - b_start)
                    for offset in range(shared):
                        votes[a_start + offset][candidate[b_start + offset]] += 1
        choices = []
        for vote in votes:
            top_count = vote.most_common(1)[0][1]
            choices.append([char for char, count in vote.items() if count == top_count])
        for variant in itertools.islice(itertools.product(*choices), 128):
            consensuses.add("".join(variant))
    return consensuses


def _single_character_repairs(candidates: list[str]):
    """Yield payloads with one OCR insertion or deletion repaired."""
    for candidate, _count in Counter(candidates).most_common(3):
        for index in range(len(candidate) + 1):
            for char in _BASE32_ALPHABET:
                yield candidate[:index] + char + candidate[index:]
        for index in range(len(candidate)):
            yield candidate[:index] + candidate[index + 1:]


def _ocr_confusable_variants(candidates: list[str]) -> set[str]:
    variants: set[str] = set()
    for candidate in candidates:
        choices = []
        for char in candidate:
            choices.append((char, "l") if char == "I" else (char,))
        for variant in itertools.islice(itertools.product(*choices), 128):
            variants.add("".join(variant))
    return variants


def read_visible_secrets(document: fitz.Document, key: str) -> set[str]:
    """Rasterize every page and authenticate candidate encrypted labels."""
    executable = shutil.which("tesseract")
    if executable is None:
        raise WatermarkingError("Visible watermark OCR requires Tesseract")

    found: set[str] = set()
    deadline = time.monotonic() + _OCR_BUDGET_SECONDS
    for page in itertools.islice(document, _MAX_OCR_PAGES):
        if time.monotonic() >= deadline:
            break
        dpi = 300
        pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        scale = dpi / 72
        prepared_page = image.rotate(
            -30,
            expand=True,
            resample=Image.Resampling.BICUBIC,
            fillcolor="white",
        )
        text_parts = [_ocr(prepared_page, deadline)]
        seen_boxes = set()
        for image_info in page.get_images(full=True):
            xref = image_info[0]
            for rect in page.get_image_rects(xref):
                if rect.width < rect.height * 1.3:
                    continue
                box = (
                    max(0, round(rect.x0 * scale) - 8),
                    max(0, round(rect.y0 * scale) - 8),
                    min(image.width, round(rect.x1 * scale) + 8),
                    min(image.height, round(rect.y1 * scale) + 8),
                )
                if box in seen_boxes or len(seen_boxes) >= _MAX_LABEL_CROPS:
                    continue
                seen_boxes.add(box)
                label_image = image.crop(box).rotate(
                    -30,
                    expand=True,
                    resample=Image.Resampling.BICUBIC,
                    fillcolor="white",
                )
                text_parts.append(_ocr(label_image, deadline))

        text = "\n".join(text_parts)
        candidates = _candidate_payloads(text)
        for candidate in candidates:
            try:
                found.add(crypto.decrypt_visible_payload(candidate, key))
            except InvalidKeyError:
                continue

        for candidate in _consensus_candidates(candidates):
            try:
                found.add(crypto.decrypt_visible_payload(candidate, key))
            except InvalidKeyError:
                continue

        for candidate in _ocr_confusable_variants(candidates):
            try:
                found.add(crypto.decrypt_visible_payload(candidate, key))
                break
            except InvalidKeyError:
                continue

        if not found:
            for candidate in _single_character_repairs(candidates):
                try:
                    found.add(crypto.decrypt_visible_payload(candidate, key))
                    break
                except InvalidKeyError:
                    continue

        if found:
            break

    return found

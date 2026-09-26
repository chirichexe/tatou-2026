"""OCR recovery for the AES-SIV-encrypted visible labels."""

from __future__ import annotations

import io
import itertools
import re
import shutil
import subprocess
from collections import Counter
from difflib import SequenceMatcher

import pymupdf as fitz
from PIL import Image

from watermarking_method import InvalidKeyError, WatermarkingError

from . import crypto

_PAYLOAD_PATTERN = re.compile(r"[A-Za-z0-9]{44,200}", re.IGNORECASE)
_LABEL_PAYLOAD_PATTERN = re.compile(
    r"GROUP[\s_A-Za-z0-9]*\s*-\s*([A-Za-z0-9]{44,200})", re.IGNORECASE,
)
_BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_OCR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
_OCR_DIGIT_TRANSLATION = str.maketrans({"0": "o", "1": "l", "8": "b", "9": "g"})


def _ocr(image: Image.Image) -> str:
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
            timeout=30,
            check=False,
        )
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
            candidates.append(label_match.group(1).translate(_OCR_DIGIT_TRANSLATION))
        else:
            candidates.extend(
                candidate.translate(_OCR_DIGIT_TRANSLATION)
                for candidate in _PAYLOAD_PATTERN.findall(line)
            )
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
    for page in document:
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
        text_parts = [_ocr(prepared_page)]
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
                if box in seen_boxes:
                    continue
                seen_boxes.add(box)
                label_image = image.crop(box).rotate(
                    -30,
                    expand=True,
                    resample=Image.Resampling.BICUBIC,
                    fillcolor="white",
                )
                text_parts.append(_ocr(label_image))

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

    return found

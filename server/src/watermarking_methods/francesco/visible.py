"""OCR recovery for the repeated semi-transparent watermark token on every page."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from collections.abc import Iterable

import pymupdf as fitz
from PIL import Image

from watermarking_method import WatermarkingError

from . import crypto

_OCR_PATTERN = re.compile(
    rf"(?:FWM1-)?([{crypto.VISIBLE_ALPHABET}]{{2}})-"
    rf"([{crypto.VISIBLE_ALPHABET}W1]{{40,220}})"
)
_OCR_HEADER_PATTERN = re.compile(
    rf"(?:FWM1-)?([{crypto.VISIBLE_ALPHABET}]{{2}})-"
)
_OCR_WHITELIST = f"FWM1-{crypto.VISIBLE_ALPHABET}"


def _ocr(image: Image.Image, page_segmentation_mode: int) -> str:
    executable = shutil.which("tesseract")
    if executable is None:
        raise WatermarkingError("Visible watermark OCR is unavailable")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    try:
        completed = subprocess.run(
            [
                executable,
                "stdin",
                "stdout",
                "--psm",
                str(page_segmentation_mode),
                "-l",
                "eng",
                "-c",
                f"tessedit_char_whitelist={_OCR_WHITELIST}",
            ],
            input=buffer.getvalue(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WatermarkingError("Visible watermark OCR failed") from exc
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode("utf-8", "ignore")


def _tokens_from_ocr(text: str) -> set[str]:
    tokens: set[str] = set()
    candidates = [text, *text.splitlines()]
    for candidate in candidates:
        compact = re.sub(r"[^A-Za-z0-9_-]", "", candidate).upper()
        headers = list(_OCR_HEADER_PATTERN.finditer(compact))
        for index, header in enumerate(headers):
            length_pair = header.group(1)
            alphabet_index = {
                character: index
                for index, character in enumerate(crypto.VISIBLE_ALPHABET)
            }
            byte_length = (
                alphabet_index[length_pair[0]] << 4
            ) | alphabet_index[length_pair[1]]
            encoded_length = byte_length * 2
            segment_end = (
                headers[index + 1].start()
                if index + 1 < len(headers)
                else len(compact)
            )
            allowed = set(crypto.VISIBLE_ALPHABET + "W1")
            encoded = "".join(
                character
                for character in compact[header.end() : segment_end]
                if character in allowed
            )
            for observed_length in (encoded_length - 1, encoded_length, encoded_length + 1):
                if observed_length >= 40 and len(encoded) >= observed_length:
                    tokens.add(f"{length_pair}-{encoded[:observed_length]}")
    return tokens


def extract_visible_tokens(document: fitz.Document) -> set[str]:
    """Rasterize every page and collect OCR candidates for visible labels."""
    tokens: set[str] = set()
    for page in document:
        pixmap = page.get_pixmap(dpi=300, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        prepared = image.rotate(
            -30,
            expand=True,
            resample=Image.Resampling.BICUBIC,
            fillcolor="white",
        )
        tokens.update(_tokens_from_ocr(_ocr(prepared, page_segmentation_mode=11)))
    return tokens


def decrypt_visible_tokens(tokens: Iterable[str], key: str) -> set[str]:
    """Return all secrets whose OCR tokens pass authenticated decryption."""
    secrets: set[str] = set()
    token_list = list(tokens)
    failed_tokens: list[str] = []
    for token in token_list:
        try:
            payload = crypto.visible_token_to_qr_payload(token)
            secrets.add(crypto.decrypt_qr_payload(payload, key))
        except (ValueError, WatermarkingError):
            failed_tokens.append(token)

    # A single OCR edit can be repaired safely because AES-SIV authentication,
    # rather than visual similarity, decides whether a candidate is valid.
    for token in failed_tokens[:8]:
        match = _OCR_PATTERN.fullmatch(token)
        if match is None:
            continue
        length_pair, encoded = match.groups()
        alphabet_index = {
            character: index
            for index, character in enumerate(crypto.VISIBLE_ALPHABET)
        }
        expected_bytes = (
            alphabet_index[length_pair[0]] << 4
        ) | alphabet_index[length_pair[1]]
        expected_length = expected_bytes * 2
        candidates: Iterable[str]
        if len(encoded) == expected_length:
            candidates = (
                encoded[:offset] + replacement + encoded[offset + 1 :]
                for offset, observed in enumerate(encoded)
                for replacement in crypto.VISIBLE_ALPHABET
                if replacement != observed
            )
        elif len(encoded) == expected_length + 1:
            candidates = (
                encoded[:offset] + encoded[offset + 1 :]
                for offset in range(len(encoded))
            )
        elif len(encoded) == expected_length - 1:
            candidates = (
                encoded[:offset] + replacement + encoded[offset:]
                for offset in range(len(encoded) + 1)
                for replacement in crypto.VISIBLE_ALPHABET
            )
        else:
            continue

        for candidate in candidates:
            corrected = f"{length_pair}-{candidate}"
            try:
                payload = crypto.visible_token_to_qr_payload(corrected)
                secrets.add(crypto.decrypt_qr_payload(payload, key))
            except (ValueError, WatermarkingError):
                continue
    return secrets

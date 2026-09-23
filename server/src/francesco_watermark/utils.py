"""Utility functions and re-exports for Francesco's watermarking package."""
from __future__ import annotations

from .trustmark_experiment import (
    embed_photo,
    payload_for_copy,
    read_assigned_pdf_tag,
    read_photo_tag,
)

__all__ = [
    "embed_photo",
    "payload_for_copy",
    "read_assigned_pdf_tag",
    "read_photo_tag",
]

"""khaled-text-spacing-watermark: encrypted secret in the spacing of the text

Public: KhaledTextSpacingWatermark (add_watermark, read_secret, is_watermark_applicable,
get_usage). Private to this package: pdf_text.py (parser and editor of the text
operators).
"""

from __future__ import annotations

from .method import KhaledTextSpacingWatermark

__all__ = ["KhaledTextSpacingWatermark"]

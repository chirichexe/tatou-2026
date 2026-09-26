"""francesco-watermark: encrypted secret in QR codes and visible labels

Public: FrancescoWatermark (add_watermark, read_secret, is_watermark_applicable,
get_usage). Private to this package: crypto.py, rendering.py, pdf.py, visible.py (QR and
label drawing, OCR).
"""

from __future__ import annotations

from .method import FrancescoWatermark

__all__ = ["FrancescoWatermark"]

"""francesco-watermark: encrypted secret in a QR code, plus visible labels

Public: FrancescoWatermark (add_watermark, read_secret, is_watermark_applicable,
get_usage). Private to this package: crypto.py, rendering.py, pdf.py (QR and
label drawing).
"""

from __future__ import annotations

from .method import FrancescoWatermark

__all__ = ["FrancescoWatermark"]

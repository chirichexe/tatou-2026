"""davide-watermark: encrypted secret (QIM) and recipient fingerprint in the images

Public: DavideWatermark (add_watermark, read_secret, is_watermark_applicable,
get_usage, score_recipients). Private to this package: image.py (payload and fingerprint of one
image), align.py (registration of a leak on the original).
"""

from __future__ import annotations

from .method import DavideWatermark

__all__ = ["DavideWatermark"]

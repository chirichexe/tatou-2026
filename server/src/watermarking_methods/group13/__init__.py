"""group13-watermark: davide + khaled + francesco in one pipeline

Public: Group13Watermark (add_watermark, read_secret, is_watermark_applicable,
get_usage, score_recipients). Private to this package: nothing: it only calls the public classes of the
other three packages.
"""

from __future__ import annotations

from .method import Group13Watermark

__all__ = ["Group13Watermark"]

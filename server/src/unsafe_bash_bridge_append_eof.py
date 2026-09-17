"""Legacy watermarking method that appends a secret after the PDF EOF marker."""
from __future__ import annotations

from typing import Final

from watermarking_method import (
    PdfSource,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    load_pdf_bytes,
)


class UnsafeBashBridgeAppendEOF(WatermarkingMethod):
    """Append a UTF-8 secret after the PDF EOF without invoking a shell.

    The class and public method name are retained for compatibility with
    existing callers and documents produced by the original implementation.
    """

    name: Final[str] = "bash-bridge-eof"

    # ---------------------
    # Public API overrides
    # ---------------------
    @staticmethod
    def get_usage() -> str:
        return (
            "Legacy method that appends a watermark record after the PDF EOF. "
            "Position and key are ignored."
        )

    def add_watermark(
        self,
        pdf,
        secret: str,
        key: str,
        position: str | None = None,
    ) -> bytes:
        """Return a new PDF with a watermark record appended.

        The ``position`` and ``key`` parameters are accepted for API compatibility but
        ignored by this method.
        """
        data = load_pdf_bytes(pdf)
        return data + secret.encode("utf-8")

    def is_watermark_applicable(
        self,
        pdf: PdfSource,
        position: str | None = None,
    ) -> bool:
        return True

    def read_secret(self, pdf: PdfSource, key: str) -> str:
        """Return the UTF-8 content appended after the final ``%%EOF`` marker."""
        data = load_pdf_bytes(pdf)
        marker_index = data.rfind(b"%%EOF")
        if marker_index < 0:
            raise SecretNotFoundError("No appended secret found")

        payload = data[marker_index + len(b"%%EOF"):]
        if payload.startswith(b"\r\n"):
            payload = payload[2:]
        elif payload.startswith((b"\n", b"\r")):
            payload = payload[1:]

        if not payload:
            raise SecretNotFoundError("No appended secret found")

        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WatermarkingError("Appended secret is not valid UTF-8") from error



__all__ = ["UnsafeBashBridgeAppendEOF"]

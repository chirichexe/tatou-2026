from __future__ import annotations

import base64
from typing import Final

from watermarking_method import (
    InvalidKeyError,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    PdfSource,
    load_pdf_bytes,
)


class DavideWatermark(WatermarkingMethod):

    name: Final[str] = "davide-watermark"
    _MARKER: Final[bytes] = b"\n% DAVIDE-WATERMARK:v1:"

    @staticmethod
    def get_usage() -> str:
        return "Embeds a Base64-encoded secret in a PDF comment."

    def add_watermark(
        self,
        pdf: PdfSource,
        secret: str,
        key: str,
        position: str | None = None,
    ) -> bytes:

        data = load_pdf_bytes(pdf)

        if not secret:
            raise WatermarkingError("Secret cannot be empty.")

        if not key:
            raise WatermarkingError("Key cannot be empty.")

        # watermarking algorithm ---------------------------------

        encoded_secret = base64.b64encode(secret.encode("utf-8"))
        watermark = self._MARKER + encoded_secret

        return data + watermark

        # --------------------------------------------------------

    def is_watermark_applicable(
        self,
        pdf: PdfSource,
        position: str | None = None,
    ) -> bool:

        data = load_pdf_bytes(pdf)

        # Check whether the algorithm can be applied
        return data.startswith(b"%PDF-")

    def read_secret(
        self,
        pdf: PdfSource,
        key: str,
    ) -> str:

        data = load_pdf_bytes(pdf)

        # YOUR EXTRACTION ALGORITHM HERE

        # --------------------------------------------------------

        marker_position = data.rfind(self._MARKER)

        if marker_position == -1:
            raise SecretNotFoundError("Davide watermark not found.")

        encoded_secret = data[
            marker_position + len(self._MARKER):
        ]

        try:
            secret = base64.b64decode(
                encoded_secret,
                validate=True,
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecretNotFoundError(
                "Davide watermark is malformed."
            ) from exc

        return secret


__all__ = ["DavideWatermark"]
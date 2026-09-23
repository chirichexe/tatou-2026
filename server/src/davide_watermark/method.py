"""Davide Chirichella's PDF watermarking method for Tatou."""

from __future__ import annotations

from typing import Final

import pymupdf as fitz

from .carriers import (
    derive_carrier_seed,
    find_candidate_slots,
    permutation,
)
from .crypto import _MAX_SECRET_BYTES, build_payload, open_payload
from .encoding import (
    bits_to_bytes,
    bytes_to_bits,
    majority,
    repeat_bits,
)
from .pdf import read_slot_values, write_carriers

from watermarking_method import (
    InvalidKeyError,
    PdfSource,
    SecretNotFoundError,
    WatermarkingError,
    WatermarkingMethod,
    load_pdf_bytes,
)


NAME: Final[str] = "davide-watermark"


class DavideWatermark(WatermarkingMethod):
    name: Final[str] = NAME

    @classmethod
    def get_usage(cls) -> str:
        return "Embed an encrypted watermark inside PDF text streams."

    _MIN_CARRIERS: Final[int] = (6 + 1 + 16) * 8 * 3

    @classmethod
    def is_watermark_applicable(
        cls,
        pdf: PdfSource,
        position: str | None = None,
    ) -> bool:
        """Return whether the PDF contains usable carrier positions."""

        try:
            pdf_bytes = load_pdf_bytes(pdf)

            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                if doc.is_encrypted:
                    return False

                if doc.page_count == 0:
                    return False

                # "position" is not used by this method.
                return len(find_candidate_slots(doc)) >= cls._MIN_CARRIERS

        except Exception:
            return False

    @classmethod
    def add_watermark(
        cls,
        pdf: PdfSource,
        secret: str,
        key: str,
        position: str | None = None,
    ) -> bytes:
        """Embed an authenticated watermark into the PDF."""

        if not secret:
            raise ValueError("Secret must not be empty")

        if not key:
            raise InvalidKeyError("Key must not be empty")

        # 1. Encrypt and authenticate the secret.
        payload = build_payload(secret, key)

        # 2. Convert the payload into bits.
        bits = bytes_to_bits(payload)

        # 3. Repeat every bit three times for error correction.
        bits = repeat_bits(bits)

        # 4. Load the source PDF.
        pdf_bytes = load_pdf_bytes(pdf)

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:

            # 5. Find all possible carrier positions.
            slots = find_candidate_slots(doc)

            if len(slots) < len(bits):
                raise WatermarkingError(
                    "PDF does not contain enough carrier positions"
                )

            # 6. Derive a deterministic, key-dependent carrier order.
            seed = derive_carrier_seed(key)
            order = permutation(len(slots), seed)

            # 7. Select the carriers used by this watermark.
            selected_slots = [
                slots[index]
                for index in order[:len(bits)]
            ]

            # 8. Associate each selected carrier with one watermark bit.
            carriers = [
                (slot[0], slot[1], bit)
                for slot, bit in zip(selected_slots, bits)
            ]

            # 9. Write the watermark bits into the PDF.
            write_carriers(doc, carriers)

            # 10. Return the modified PDF.
            return doc.tobytes(
                garbage=0,
                deflate=True,
                incremental=False,
                encryption=fitz.PDF_ENCRYPT_NONE,
            )

    @classmethod
    def read_secret(
        cls,
        pdf: PdfSource,
        key: str,
    ) -> str:
        """Recover and authenticate the embedded secret."""

        if not key:
            raise InvalidKeyError("Key must not be empty")

        # 1. Load the source PDF.
        pdf_bytes = load_pdf_bytes(pdf)

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:

            # 2. Find all possible carrier positions.
            slots = find_candidate_slots(doc)

            if not slots:
                raise SecretNotFoundError("No watermark carriers found")

            # 3. Recreate the same key-dependent carrier order.
            seed = derive_carrier_seed(key)
            order = permutation(len(slots), seed)

            ordered_slots = [
                slots[index]
                for index in order
            ]

            # 4. Read the values stored in the carrier positions.
            values = read_slot_values(doc, ordered_slots)

            # The header is:
            # 4 bytes version + 2 bytes secret length = 6 bytes.
            # Each bit is repeated 3 times.
            header_bit_count = 6 * 8
            header_carrier_count = header_bit_count * 3

            if len(values) < header_carrier_count or not any(v is not None for v in values):
                raise SecretNotFoundError("No watermark found")

            # 5. Recover the header using majority voting.
            header_bits = []

            for i in range(0, header_carrier_count, 3):
                try:
                    bit = majority(values[i:i + 3])
                except ValueError as exc:
                    raise InvalidKeyError("Wrong key") from exc

                header_bits.append(bit)

            header = bits_to_bytes(header_bits)

            # 6. Check the protocol version.
            if header[:4] != b"DWM1":
                raise InvalidKeyError("Wrong key")

            # 7. Read the declared secret length.
            secret_length = int.from_bytes(
                header[4:6],
                byteorder="big",
            )

            if secret_length > _MAX_SECRET_BYTES:
                raise InvalidKeyError("Wrong key")

            # 8. The payload contains:
            #    6-byte header + secret + 16-byte AES-SIV tag.
            payload_length = 6 + secret_length + 16

            # Every payload bit was repeated three times.
            carrier_count = payload_length * 8 * 3

            if len(values) < carrier_count:
                raise SecretNotFoundError("Incomplete watermark")

            # 9. Recover all payload bits.
            payload_bits = []

            for i in range(0, carrier_count, 3):
                try:
                    bit = majority(values[i:i + 3])
                except ValueError as exc:
                    raise InvalidKeyError("Wrong key") from exc

                payload_bits.append(bit)

            # 10. Convert the recovered bits back to bytes.
            payload = bits_to_bytes(payload_bits)

            # 11. Decrypt and authenticate the payload.
            return open_payload(payload, key)
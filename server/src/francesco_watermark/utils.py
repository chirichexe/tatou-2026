"""Utility functions, modular helpers, and re-exports for Francesco's watermarking package."""
from __future__ import annotations

from .crypto import (
    compute_expected_photo_tag,
    compute_visible_code,
    decrypt_qr_payload,
    derive_sub_key,
    encrypt_qr_payload,
    parse_hex_key,
)
from .pdf import (
    assemble_pdf_from_images,
    find_primary_photo_rect,
    is_document_applicable,
    rasterize_page,
)
from .rendering import (
    apply_qr_codes,
    apply_visible_text,
    build_qr_image,
    compute_dynamic_qr_coordinates,
    load_font,
)
from .trustmark_experiment import (
    embed_photo,
    payload_for_copy,
    read_assigned_pdf_tag,
    read_photo_tag,
)

__all__ = [
    # TrustMark utilities
    "embed_photo",
    "payload_for_copy",
    "read_assigned_pdf_tag",
    "read_photo_tag",
    # Crypto utilities
    "parse_hex_key",
    "derive_sub_key",
    "compute_visible_code",
    "encrypt_qr_payload",
    "decrypt_qr_payload",
    "compute_expected_photo_tag",
    # PDF utilities
    "is_document_applicable",
    "rasterize_page",
    "assemble_pdf_from_images",
    "find_primary_photo_rect",
    # Rendering utilities
    "load_font",
    "apply_visible_text",
    "build_qr_image",
    "apply_qr_codes",
    "compute_dynamic_qr_coordinates",
]

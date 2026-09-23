"""Optional TrustMark photo layer; never required for primary QR verification.

The upstream package downloads model weights on demand.  This adapter checks
the exact required model files *before* constructing TrustMark, so an RMAP
request cannot trigger an implicit network download.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.metadata
import importlib.util
from pathlib import Path

import fitz
from PIL import Image
from watermarking_method import PdfSource, load_pdf_bytes

PACKAGE_VERSION = "0.9.2"
# Checksums published in adobe/trustmark python/trustmark/trustmark.py for Q.
MODEL_MD5 = {
    "trustmark_Q.yaml": "fe40df84a7feeebfceb7a7678d7e6ec6",
    "decoder_Q.ckpt": "4ced90e9cfe13e3295ad082887fe9187",
    "encoder_Q.ckpt": "700328b8754db934b2f6cb5e5185d81f",
}


def payload_for_copy(secret: str, key_material: bytes) -> str:
    """61-bit keyed lookup tag, fitting TrustMark's BCH_5 capacity."""
    digest = hmac.new(
        key_material, b"tatou/hybrid-page/trustmark/v1/" + secret.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return format(int.from_bytes(digest[:8], "big") >> 3, "061b")


def _ready_model() -> None:
    try:
        if importlib.metadata.version("trustmark") != PACKAGE_VERSION:
            raise RuntimeError("TrustMark version is not pinned to 0.9.2")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("TrustMark experimental extra is not installed") from exc
    spec = importlib.util.find_spec("trustmark")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("TrustMark package cannot be located")
    model_dir = Path(next(iter(spec.submodule_search_locations))) / "models"
    for name, checksum in MODEL_MD5.items():
        path = model_dir / name
        if not path.is_file():
            raise RuntimeError(f"TrustMark model is not preinstalled: {name}")
        with path.open("rb") as model_file:
            digest = hashlib.file_digest(model_file, "md5").hexdigest()
        if digest != checksum:
            raise RuntimeError(f"TrustMark model checksum mismatch: {name}")


def _engine():
    _ready_model()
    trustmark_module = importlib.import_module("trustmark")
    TrustMark = trustmark_module.TrustMark

    return TrustMark(
        verbose=False, model_type="Q", device="cpu",
        encoding_type=TrustMark.Encoding.BCH_5, loadRemover=False,
    )


def embed_photo(image: Image.Image, secret: str, key_material: bytes) -> Image.Image:
    """Embed a short correlation tag in a photographic crop."""
    return _engine().encode(
        image.convert("RGB"), payload_for_copy(secret, key_material), MODE="binary",
    )


def read_photo_tag(image: Image.Image) -> str | None:
    """Decode only a tag; the operator must compare it against issued records."""
    decoded, present, _schema = _engine().decode(image.convert("RGB"), MODE="binary")
    return decoded if present else None


def read_assigned_pdf_tag(pdf: PdfSource) -> str | None:
    """Read experimental tag from the fixed photo region of Group 13's page."""
    # The output is rasterized, so the original embedded-image rectangle is no
    # longer available as a PDF object.  These normalized bounds are the
    # measured layout of the assigned one-page document.
    from .method import HybridPageWatermark

    with fitz.open(stream=load_pdf_bytes(pdf), filetype="pdf") as document:
        if document.page_count != 1:
            raise ValueError("Experimental photo decoder expects one page")
        pixmap = document[0].get_pixmap(dpi=220, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        x0, y0, x1, y1 = HybridPageWatermark.ASSIGNED_PHOTO_FRACTIONS
        crop = image.crop((
            int(x0 * image.width), int(y0 * image.height),
            int(x1 * image.width), int(y1 * image.height),
        ))
        return read_photo_tag(crop)

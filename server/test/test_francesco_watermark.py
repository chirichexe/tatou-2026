"""Security and image round-trip checks for the rasterized page method."""
from __future__ import annotations

import io
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image

from watermarking_method import InvalidKeyError, SecretNotFoundError, WatermarkingError
from francesco_watermark.method import HybridPageWatermark
from francesco_watermark import trustmark_experiment as tm_experiment
from francesco_watermark.trustmark_experiment import payload_for_copy


KEY = "0123456789abcdef" * 4
OTHER_KEY = "fedcba9876543210" * 4


@pytest.fixture
def pdf_bytes():
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 72), "A document with a table and a photograph")
        return document.tobytes()


def test_roundtrip_preserves_page_but_flattens_text(pdf_bytes):
    method = HybridPageWatermark()
    result = method.add_watermark(pdf_bytes, "unique-copy-id", KEY)
    assert method.read_secret(result, KEY) == "unique-copy-id"
    assert method.visible_code("unique-copy-id", KEY) != method.visible_code("another-id", KEY)
    assert result == method.add_watermark(pdf_bytes, "unique-copy-id", KEY)
    with fitz.open(stream=result, filetype="pdf") as document:
        assert document.page_count == 1
        assert not document[0].get_text().strip()
        assert len(document[0].get_images()) == 1


def test_wrong_key_and_forged_payload_rejected(pdf_bytes):
    method = HybridPageWatermark()
    result = method.add_watermark(pdf_bytes, "unique-copy-id", KEY)
    with pytest.raises(InvalidKeyError):
        method.read_secret(result, OTHER_KEY)
    payload = method._payload("unique-copy-id", KEY)
    with pytest.raises(InvalidKeyError):
        method._unpack(payload[:-1] + ("A" if payload[-1] != "A" else "B"), KEY)
    with pytest.raises(SecretNotFoundError):
        method.read_secret(pdf_bytes, KEY)


def test_conflicting_valid_copies_rejected(pdf_bytes):
    method = HybridPageWatermark()
    first = method.add_watermark(pdf_bytes, "copy-one", KEY)
    second = method.add_watermark(pdf_bytes, "copy-two", KEY)
    with fitz.open(stream=first, filetype="pdf") as left, fitz.open(stream=second, filetype="pdf") as mixed:
        mixed.insert_pdf(left)
        candidate = mixed.tobytes()
    with pytest.raises(WatermarkingError, match="Conflicting"):
        method.read_secret(candidate, KEY)


def test_survives_jpeg_raster_roundtrip(pdf_bytes):
    method = HybridPageWatermark()
    watermarked = method.add_watermark(pdf_bytes, "copy-through-jpeg", KEY)
    with fitz.open(stream=watermarked, filetype="pdf") as source, fitz.open() as rebuilt:
        pix = source[0].get_pixmap(dpi=220, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        page = rebuilt.new_page(width=source[0].rect.width, height=source[0].rect.height)
        page.insert_image(page.rect, stream=buffer.getvalue())
        candidate = rebuilt.tobytes()
    assert method.read_secret(candidate, KEY) == "copy-through-jpeg"


def test_input_limits_and_key_format(pdf_bytes):
    method = HybridPageWatermark()
    assert not method.is_watermark_applicable(pdf_bytes, "experimental-trustmark")
    assert not method.is_watermark_applicable(b"not-a-pdf")
    with pytest.raises(ValueError, match="32-byte key"):
        method.add_watermark(pdf_bytes, "copy", "weak")
    with pytest.raises(ValueError, match="1-64"):
        method.add_watermark(pdf_bytes, "x" * 65, KEY)
    with fitz.open() as huge:
        huge.new_page(width=3000, height=3000)
        assert not method.is_watermark_applicable(huge.tobytes())


def test_experimental_tag_is_keyed_and_bounded():
    first = payload_for_copy("copy-one", b"a" * 32)
    assert len(first) == 61
    assert set(first) <= {"0", "1"}
    assert first != payload_for_copy("copy-two", b"a" * 32)
    assert first != payload_for_copy("copy-one", b"b" * 32)
    assert HybridPageWatermark.expected_photo_tag("copy-one", KEY) != first


def test_experimental_mode_requires_assigned_photo(pdf_bytes):
    method = HybridPageWatermark()
    assert not method.is_watermark_applicable(pdf_bytes, "experimental-trustmark")


def test_trustmark_adapter_passes_keyed_tag_without_real_model(monkeypatch):
    observed = {}

    class FakeEngine:
        def encode(self, image, payload, MODE):
            assert MODE == "binary"
            observed["payload"] = payload
            return image

        def decode(self, image, MODE):
            assert MODE == "binary"
            return observed["payload"], True, 1

    monkeypatch.setattr(tm_experiment, "_engine", FakeEngine)
    image = Image.new("RGB", (100, 100), "green")
    marked = tm_experiment.embed_photo(image, "copy-one", b"k" * 32)
    assert marked.size == image.size
    assert tm_experiment.read_photo_tag(marked) == payload_for_copy("copy-one", b"k" * 32)


def test_trustmark_experiment_refuses_missing_models_before_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_experiment.importlib.metadata, "version", lambda _name: "0.9.2")
    monkeypatch.setattr(
        tm_experiment.importlib.util, "find_spec",
        lambda _name: SimpleNamespace(submodule_search_locations=[str(tmp_path)]),
    )
    with pytest.raises(RuntimeError, match="not preinstalled"):
        tm_experiment._ready_model()

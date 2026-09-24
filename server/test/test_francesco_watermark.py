"""Security and image round-trip checks for the rasterized page method."""
from __future__ import annotations

import io
from types import SimpleNamespace

import fitz
import pytest
from francesco_watermark import trustmark_experiment as tm_experiment
from francesco_watermark.method import HybridPageWatermark
from francesco_watermark.trustmark_experiment import payload_for_copy
from PIL import Image
from watermarking_method import InvalidKeyError, SecretNotFoundError, WatermarkingError

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


def test_layer_toggles_and_prerequisites(pdf_bytes, monkeypatch):
    method = HybridPageWatermark()

    # Base rasterization is mandatory
    monkeypatch.setattr(HybridPageWatermark, "ENABLE_RASTER_BASE", False)
    with pytest.raises(WatermarkingError, match="Base rasterization layer"):
        method.add_watermark(pdf_bytes, "test-copy", KEY)
    monkeypatch.undo()

    # Disabling QR watermark prevents reading secret
    monkeypatch.setattr(HybridPageWatermark, "ENABLE_QR_WATERMARK", False)
    with pytest.raises(WatermarkingError, match="QR watermark layer is disabled"):
        method.read_secret(pdf_bytes, KEY)
    monkeypatch.undo()

    # Disabling visible text still produces a readable QR watermark
    monkeypatch.setattr(HybridPageWatermark, "ENABLE_VISIBLE_TEXT", False)
    watermarked_no_text = method.add_watermark(pdf_bytes, "only-qr", KEY)
    assert method.read_secret(watermarked_no_text, KEY) == "only-qr"
    monkeypatch.undo()


def test_modular_crypto_and_utils_exports():
    from francesco_watermark import utils

    # Verify that utils re-exports the modular helper functions
    assert callable(utils.parse_hex_key)
    assert callable(utils.derive_sub_key)
    assert callable(utils.compute_visible_code)
    assert callable(utils.encrypt_qr_payload)
    assert callable(utils.decrypt_qr_payload)
    assert callable(utils.rasterize_page)
    assert callable(utils.assemble_pdf_from_images)

    # Test modular crypto functions directly
    key_bytes = utils.parse_hex_key(KEY)
    assert len(key_bytes) == 32
    sub_key = utils.derive_sub_key(KEY, b"test", 16)
    assert len(sub_key) == 16
    code = utils.compute_visible_code("hello", KEY)
    assert len(code) == 16
    payload = utils.encrypt_qr_payload("secret-val", KEY)
    assert payload.startswith("TW1:")
    recovered = utils.decrypt_qr_payload(payload, KEY)
    assert recovered == "secret-val"


def test_blind_fingerprint_and_dynamic_coordinates():
    from francesco_watermark import utils

    # Blind fingerprint is one-way, 16 hex chars, independent of key
    fp1 = utils.compute_visible_code("secret-A", KEY)
    fp1_diff_key = utils.compute_visible_code("secret-A", OTHER_KEY)
    fp2 = utils.compute_visible_code("secret-B", KEY)
    assert len(fp1) == 16
    assert set(fp1) <= set("0123456789ABCDEF")
    # Same secret yields same blind fingerprint regardless of key (does not leak key)
    assert fp1 == fp1_diff_key
    # Different secrets yield different fingerprints
    assert fp1 != fp2

    # Dynamic coordinates are pseudo-random but deterministic given same seed
    coords1 = utils.compute_dynamic_qr_coordinates(b"seed-A", 1000, 1000)
    coords1_dup = utils.compute_dynamic_qr_coordinates(b"seed-A", 1000, 1000)
    coords2 = utils.compute_dynamic_qr_coordinates(b"seed-B", 1000, 1000)
    assert coords1 == coords1_dup
    assert coords1 != coords2
    # Verify non-overlapping separation between QR 1 and QR 2
    (x1, y1), (x2, y2) = coords1
    assert x1 < 0.35 and x2 > 0.50
    assert y1 < 0.45 and y2 > 0.40



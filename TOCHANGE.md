# Changes to align the single methods with `group13-watermark`

Branch `feat/group13-watermarking` stacks the three methods in one
(`server/src/watermarking_methods/group13/`). While benchmarking it against ~50 removal
attacks (`server/test_robustness/`), some bugs showed up in the single
methods. They were fixed on that branch; this file lists what Khaled and
Francesco have to port to their own branches (`feat/khaled-text-watermark`,
`feat/francesco-watermark`) so the code stays the same.

## 0. New layout (everyone, Davide included)

Every method lives in its own folder under `server/src/watermarking_methods/`,
with one public class (exported by `__init__.py`) and private helper modules;
tests are mirrored under `server/test/watermarking/<name>/`:

| Owner | Code | Tests | Public class | Registry name |
|---|---|---|---|---|
| Davide | `watermarking_methods/davide/` | `test/watermarking/davide/` | `DavideWatermark` | `davide-watermark` |
| Khaled | `watermarking_methods/khaled/` | `test/watermarking/khaled/` | `KhaledTextSpacingWatermark` | `khaled-text-spacing-watermark` |
| Francesco | `watermarking_methods/francesco/` | `test/watermarking/francesco/` | `FrancescoWatermark` | `francesco-watermark` |
| all | `watermarking_methods/group13/` | `test/watermarking/group13/` | `Group13Watermark` | `group13-watermark` |

- Imports become `from watermarking_methods.<name> import <Class>`
  (was `from <name>_watermark import ...`; Francesco's tests were in
  `test/francesco_watermark_test/`).
- All four are registered in `watermarking_utils.METHODS`, so each one can be
  used alone in `create-watermark`, `read-watermark` and
  `RMAP_WATERMARK_METHOD`, or all together with `group13-watermark`.
- `group13` contains no watermarking code: it calls the public methods of
  the other three in a commented pipeline, so **a change in your package is
  picked up by group13 automatically**. Keep the public interface
  (`add_watermark`, `read_secret`, `is_watermark_applicable`, `get_usage`)
  and the error types (`WatermarkingError` subclasses or `ValueError`).
- `test/watermarking/test_interchangeable.py` and
  `test/rmap/test_rmap_every_method.py` check every method alone and together:
  run them after any change.

The simplest way to align is to work on `feat/group13-watermarking`, or to
take your folders from it:

```bash
git checkout origin/feat/group13-watermarking -- server/src/watermarking_methods/khaled server/test/watermarking/khaled        # Khaled
git checkout origin/feat/group13-watermarking -- server/src/watermarking_methods/francesco server/test/watermarking/francesco  # Francesco
```

Davide's algorithm was not changed (only moved).

---

## Khaled: `watermarking_methods/khaled/`

All the fixes are in `pdf_text.py::collect_runs`. They matter for the
method used alone too: before them a plain resave broke the watermark.

### 1. Merged content streams must not make the page unreadable
A resave with garbage collection (`garbage=4`, also `qpdf`) merges identical
streams, e.g. the `q`/`Q` wrappers of images drawn on top. The parser raised
`UnsupportedText("shared page content streams are unsupported")` and the
whole document became unreadable.

Now: a stream seen before still counts towards the ordinals, but only its
first occurrence yields carriers.

```python
shared = xref in seen_xrefs
seen_xrefs.add(xref)
...
if len(chars) >= 3 and not shared:
    runs.append(TextRun(...))
```

### 2. The font is graphics state: do not reset it at `BT`
`font_name = None` at every `BT` is wrong: in PDF `Tf` stays in effect
across text objects and streams, and only `Q` restores the previous one.
Text that inherited its font was skipped, and after `clean=True` (which
writes `Tf` in every text object) new carriers appeared, the keyed order
shifted and decoding failed. It happened on the real test PDF: 2 new
carriers were enough.

Now `font_name`/`font_size` are initialised once per page, saved on `q`,
restored on `Q`, and `Tf` is accepted outside `BT ... ET` too.

### 3. `cm` of images and QR codes must not disqualify the text
The old check excluded a stream if it contained *any* non-translation `cm`,
including the one that places an image (`q 409 0 0 420 0 0 cm /Im Do Q`).
After a resave merged the overlay streams into the text stream, the whole
page lost its carriers.

Now `_page_transformed(doc, page)` looks at the whole page and ignores a
scaling `cm` that is inside a `q ... Q` group containing no text. A scaling
`cm` at top level, or in a group with text, still excludes the page.

### 4. Number carriers by text page
`Carrier.identity` used the page index: removing a cover (or any page
without text) changed every identity. Now `page_no` counts only pages that
yield runs.

### 5. Format label `v2`
Fixes 2–4 change which carriers exist, so the AAD and the HKDF info moved
from `tatou:text-gap:v1` to `tatou:text-gap:v2`. Copies made with the old
code cannot be read by the new one (none were deployed).

### 6. `KhaledTextImageWatermark` removed
It is replaced by `group13-watermark`. `KhaledTextSpacingWatermark` is
registered on its own as `khaled-text-spacing-watermark`.

### Tests
- `test_text_and_image_methods_survive_both_orders` removed (combined class gone).
- New `test_text_inheriting_its_font_is_used_and_survives_clean`.

### Known limits (not changed)
Rounding or jittering the TJ numbers, retyping the text, `convert_to_pdf`,
Ghostscript and rasterisation still remove the layer, and removing a page
that carries text breaks it (the keyed order loses carriers). The real
confidential PDF (`Group_13.pdf`) has 164 usable carriers of the 640 needed,
so the layer is skipped there: the document needs more simple text.

---

## Francesco: `watermarking_methods/francesco/`

### 1. QR codes with whole pixels per module (`rendering.py`)
`build_opaque_qr_bytes` resized the code to 240 px with `NEAREST`, which
makes modules uneven. With the random salt, about half of the QR codes did
not decode and the read fell back to OCR (or failed without tesseract).

```python
modules = barcode.to_image(scale=1).shape[0]
raw_img = barcode.to_image(scale=max(1, target_pixel_size // modules))
qr_mask = Image.fromarray(raw_img).convert("L")   # no resize
```

### 2. Bigger codes, read at 300 dpi
At 6% of the page a code has fewer than 2 pixels per module at 220 dpi.
- `rendering.py`: `DEFAULT_QR_FRACTION = 0.10` (about 2 cm on A4)
- `pdf.py`: `READ_DPI = 300`

### 3. Dense pages: smaller codes, or labels only (`method.py`)
If one page had no free border, `add_watermark` failed for the whole
document (it happened on the 2-page test PDF). Now:
- `QR_FRACTIONS = (0.10, 0.08, 0.06)`: `_place_qr_codes` tries them in
  order on each page, passing a copy of `content_boxes`;
- a page without room gets only the visible labels;
- `ValueError("No text-free border position ...")` only if no page got a QR.

### 4. Bound the OCR (`visible.py`)
Any user can call `read-watermark` on any PDF, and each tesseract call can
take up to 15 s on the single gunicorn worker.
`MAX_OCR_IMAGES = 4` label images and `MAX_OCR_PAGES = 2` rendered pages
per read.

### 5. `validate_secret_string`
It is defined only in `watermarking_method.py` (the copy in
`watermarking_utils.py` is not needed).

### Tests
- The three tests that need OCR are marked `@needs_ocr` (skipped when
  tesseract is missing); CI now installs `tesseract-ocr`.
- New `test_francesco_qr_reliability.py`: every QR decodes over 6 random
  salts without OCR, a dense page gets labels only, a document with no room
  anywhere is rejected.

### Known limits (not changed)
The layer is visible and anyone can remove it without the key by deleting
the images with a transparency mask (the benchmark's `strip_overlays`).
It survives screenshots and print-scan, which is its value in the stack.

---

## Shared changes (already on the branch, nothing to port)
- `pyproject.toml`: `PyMuPDF>=1.24.0`, `reedsolo==1.7.0`, `zxing-cpp==3.1.1`.
- `Dockerfile`: `fonts-dejavu-core tesseract-ocr`.
- `.github/workflows/tests.yml`: installs `tesseract-ocr`.
- `watermarking_utils.METHODS`: all four methods (see section 0).

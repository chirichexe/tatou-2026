# Group 13 attack evaluation

## Setup and result notation

On 2026-09-26, I marked the local two-page `Gruppo13_text.pdf` with
`group13-watermark` using the throwaway key and recipient in `bench.py`.
The input contains a photograph and selectable text. Davide (D), Khaled
(K), and Francesco (F) could all read the marked copy. The tested
application code was `main` at `eced3d5`, mounted into a Python 3.12
Docker image with PyMuPDF 1.28.2 and Pillow 12.3.0. The PDF, marked
copies, previews, and raw results stayed outside the repository.

Every row below transforms the **same combined copy**. `G` is
`Group13Watermark.read_secret`; D/K/F are its component secret readers.
`✓` means the correct recipient was recovered; `·` means no recipient
was returned; `—` means not tested. No reader returned a wrong recipient.
`FP` is Davide's image fingerprint fallback, compared with the true
recipient and one innocent decoy. Its threshold is 6; scores appear for
successful attributions. Because this scoring is slow, I ran it on the
baseline and every case where G failed.

**Result:** G recovered the secret after 47 of 53 transformations;
the individual readers recovered it in 31 (D), 29 (K), and 35 (F).
Fingerprint scoring attributed three of the six G failures. Of the other
three, the strong blur left both pages visually readable, whereas the
10° rotation and 25% downsample damaged visible content. These counts
describe this one document and one key, not a general success rate.

## Exact attacks and outcomes

### PDF structure

`resave` performs garbage collection and cleaning; `clean_contents`
normalizes page content streams; `copy_pages` inserts all pages into a
new PDF; `metadata` clears document and XML metadata; `convert_to_pdf`
uses PyMuPDF's conversion path.

| Attack | G | D | K | F | FP |
|---|:---:|:---:|:---:|:---:|:---:|
| none | ✓ | ✓ | ✓ | ✓ | ✓ (221) |
| resave | ✓ | ✓ | ✓ | ✓ | — |
| clean_contents | ✓ | ✓ | ✓ | ✓ | — |
| copy_pages | ✓ | ✓ | ✓ | ✓ | — |
| metadata | ✓ | ✓ | ✓ | ✓ | — |
| convert_to_pdf | ✓ | ✓ | · | ✓ | — |

### Pages, overlays, and borders

`drop_first_page` and `drop_last_page` delete a page and its content.
`strip_overlays` deletes masked image objects, which on this marked PDF
removes the QR and visible labels. `strip_all_images` also removes the
source photograph. `white_borders` paints over the outer 12% of each
page; `cropbox` crops 12% off each edge. Border attacks may hide content.

| Attack | G | D | K | F | FP |
|---|:---:|:---:|:---:|:---:|:---:|
| drop_first_page | ✓ | · | · | ✓ | — |
| drop_last_page | ✓ | ✓ | · | ✓ | — |
| strip_overlays | ✓ | ✓ | ✓ | · | — |
| strip_all_images | ✓ | · | ✓ | · | — |
| white_borders | ✓ | ✓ | ✓ | · | — |
| cropbox | ✓ | ✓ | ✓ | · | — |

### Image edits

`jpegN` re-encodes eligible RGB images at quality N; `resizeN` scales
image pixels to N%; `down_up50` shrinks to 50% and enlarges again;
`crop10` removes 10% from every image edge; `blur1.5` applies Gaussian
radius 1.5; `noise8` adds seeded Gaussian noise with sigma 8;
`grayscale`, `rotate2`, and `flip_image` make the named changes. The
image mapping includes QR images where the image mode permits it.

| Attack | G | D | K | F | FP |
|---|:---:|:---:|:---:|:---:|:---:|
| jpeg75 | ✓ | ✓ | ✓ | ✓ | — |
| jpeg40 | ✓ | ✓ | ✓ | ✓ | — |
| resize50 | ✓ | · | ✓ | ✓ | — |
| resize75 | ✓ | · | ✓ | ✓ | — |
| down_up50 | ✓ | · | ✓ | ✓ | — |
| crop10 | ✓ | · | ✓ | ✓ | — |
| blur1.5 | ✓ | · | ✓ | · | — |
| noise8 | ✓ | ✓ | ✓ | ✓ | — |
| grayscale | ✓ | ✓ | ✓ | ✓ | — |
| rotate2 | ✓ | · | ✓ | ✓ | — |
| flip_image | ✓ | · | ✓ | ✓ | — |

### Selectable text edits

`tj_round10` rounds PDF `TJ` spacing numbers to multiples of 10;
`tj_zero` deletes those adjustments; `tj_jitter5/20` adds seeded
uniform noise up to 5/20 text units. `retype_text` redacts text spans
and writes their extracted text back in a standard font. Text extraction
matched the source after rounding, jitter, and retyping; `tj_zero`
reduced its extracted-text similarity to 0.438.

| Attack | G | D | K | F | FP |
|---|:---:|:---:|:---:|:---:|:---:|
| tj_round10 | ✓ | ✓ | · | ✓ | — |
| tj_zero | ✓ | ✓ | · | ✓ | — |
| tj_jitter5 | ✓ | ✓ | · | ✓ | — |
| tj_jitter20 | ✓ | ✓ | · | ✓ | — |
| retype_text | ✓ | ✓ | · | ✓ | — |

### Rasterization and targeted combinations

`raster150` rebuilds pages from 150 DPI PNG renders. `raster100_q70`
uses 100 DPI JPEG at quality 70. `raster200_q75_print` also rotates
each raster by 0.7° and adds noise with sigma 6. `raster72_q35` uses
72 DPI JPEG at quality 35. These remove selectable text, although the
pages remain visible. Combined attacks execute their named steps from
left to right. `blur3` means Gaussian radius 3; `rotate10` means 10°;
`down_up25` shrinks and enlarges image objects to 25%.

| Attack | G | D | K | F | FP |
|---|:---:|:---:|:---:|:---:|:---:|
| raster150 | ✓ | · | · | ✓ | — |
| raster100_q70 | · | · | · | · | ✓ (54) |
| raster200_q75_print | ✓ | · | · | ✓ | — |
| strip_overlays+raster150 | · | · | · | · | ✓ (57) |
| strip_overlays+retype_text+jpeg60 | ✓ | ✓ | · | · | — |
| strip_overlays+retype_text+blur3 | · | · | · | · | · |
| strip_overlays+retype_text+rotate10 | · | · | · | · | · |
| strip_overlays+retype_text+down_up25 | · | · | · | · | · |
| raster72_q35 | · | · | · | · | ✓ (19) |

The first two G failures are still attributable by fingerprint. The
72 DPI case is visibly degraded as well. The `blur3` combination
removed QR/labels, rewrote text, and blurred the photograph: both pages
remained readable in rendered previews, though the photo became softer.
The `rotate10` combination obscured part of the first page's table.
The `down_up25` combination rendered the second page blank despite
intact extracted text, so it is not a useful content-preserving attack.

### Seeded chains and repeated edits

Each `chainN` applies eight mild operations selected deterministically
by seed N. The exact order for each run is below. The operations are
implemented in `attacks.py`.

| Attack | Eight operations | G | D | K | F |
|---|---|:---:|:---:|:---:|:---:|
| chain0 | jpeg85, tj_round5, jpeg85, resave, convert_to_pdf, resize80, jpeg70, jpeg85 | ✓ | · | · | ✓ |
| chain1 | copy_pages, noise4, tj_round5, tj_round5, clean_contents, convert_to_pdf, clean_contents, jpeg70 | ✓ | ✓ | · | ✓ |
| chain2 | resave, clean_contents, clean_contents, strip_overlays, copy_pages, tj_jitter3, tj_round5, blur0.8 | ✓ | ✓ | ✓ | · |
| chain3 | metadata, noise4, resize80, copy_pages, strip_overlays, noise4, jpeg70, blur0.8 | ✓ | · | ✓ | · |
| chain4 | metadata, convert_to_pdf, clean_contents, tj_jitter3, jpeg85, jpeg70, copy_pages, clean_contents | ✓ | ✓ | · | ✓ |
| chain5 | noise4, convert_to_pdf, tj_jitter3, strip_overlays, tj_round5, tj_jitter3, tj_jitter3, blur0.8 | ✓ | ✓ | · | · |
| chain6 | tj_round5, noise4, clean_contents, jpeg70, tj_round5, convert_to_pdf, resave, resave | ✓ | ✓ | · | ✓ |
| chain7 | strip_overlays, copy_pages, jpeg85, blur0.8, resave, clean_contents, resize80, clean_contents | ✓ | · | ✓ | · |
| chain8 | metadata, strip_overlays, jpeg85, copy_pages, metadata, tj_jitter3, resave, clean_contents | ✓ | ✓ | ✓ | · |
| chain9 | jpeg70, noise4, strip_overlays, convert_to_pdf, copy_pages, copy_pages, blur0.8, resave | ✓ | ✓ | · | · |
| chain10 | noise4, resave, jpeg85, jpeg70, noise4, resave, metadata, jpeg70 | ✓ | ✓ | ✓ | ✓ |
| chain11 | jpeg70, resize80, tj_round5, jpeg70, jpeg70, resize80, noise4, metadata | ✓ | · | ✓ | ✓ |

`jpeg75x10`, `resavex10`, `tj_jitter3x10`, and `noise4x10` apply the
named operation ten times:

| Attack | G | D | K | F |
|---|:---:|:---:|:---:|:---:|
| jpeg75x10 | ✓ | ✓ | ✓ | ✓ |
| resavex10 | ✓ | ✓ | ✓ | ✓ |
| tj_jitter3x10 | ✓ | ✓ | · | ✓ |
| noise4x10 | ✓ | · | ✓ | ✓ |

## Reproduction and limits

Build the current server image, mount the source PDF read-only, and put
the result JSON outside the checkout. From `server/` on a Unix-like shell:

```bash
docker build -t tatou-group13-probe .
mkdir -p /tmp/tatou-group13-attacks
docker run --rm --entrypoint python \
  -e PYTHONPATH=/work/src:/work/test_robustness -e JOBS=4 \
  -v "$PWD:/work:ro" -v "/absolute/path/source.pdf:/input/source.pdf:ro" \
  -v "/tmp/tatou-group13-attacks:/out" -w /work tatou-group13-probe \
  test_robustness/bench.py /input/source.pdf /out/results.json G
docker run --rm --entrypoint python -v "$PWD:/work:ro" \
  -v "/tmp/tatou-group13-attacks:/out:ro" -w /work tatou-group13-probe \
  test_robustness/summary.py /out/results.json
```

Set `FINGERPRINT=1` and `ATTACKS=none,raster100_q70` for a focused
fingerprint run; `JOBS` sets the worker count. Never place a private
watermark key or confidential PDF in the repository or public CI.

This is a method-level test, not an HTTP/RMAP or Docker Compose server
test. One document, one recipient, and one decoy cannot establish a
general detection or false-attribution rate. The benchmark reports
reader recovery and extracted-text similarity; the visual assessment
above comes from rendering source and attacked pages at 96 DPI.

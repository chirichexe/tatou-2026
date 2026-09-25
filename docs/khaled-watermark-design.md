# Khaled's PDF text watermark

Status: implemented for review. The standalone `khaled-text-spacing-watermark` and the combined `khaled-text-image-watermark` are registered methods. The example PDF stays outside this repository.

## Purpose and scope

The standalone method embeds an authenticated copy identifier into selectable PDF text without changing the letters, words, Unicode mappings, or line endings. It changes the position of a middle glyph within short groups of existing letters. The combined method applies Davide's image watermark and Khaled's text watermark with the same secret and key, then verifies that both can be read from the final PDF.

The public implementations are `KhaledTextSpacingWatermark` and `KhaledTextImageWatermark` in `server/src/khaled_watermark/method.py`. They implement Tatou's `WatermarkingMethod` interface. RMAP can select either registered method through `RMAP_WATERMARK_METHOD`; the resulting method name and unique copy secret are stored with the issued version.

## Text carrier

1. Parse supported PDF page content streams and identify horizontal, one-byte text runs whose font maps ASCII letters. A carrier uses three consecutive letters inside one text-showing operation.
2. Select the middle glyph's two neighboring gaps. The encoded bit changes their PDF `TJ` adjustments in opposite directions, moving that glyph by at most 0.08 pt while keeping subsequent glyph positions fixed.
3. Derive a secret-dependent carrier order and quantization dither from the supplied key. Each selected carrier stores one bit. Carrier IDs depend on the page, text-showing operation, and glyph index, so they remain stable after the gap values change.
4. Rewrite only the selected text-showing operands, save the PDF, and confirm that extracted text and carrier IDs are unchanged and that the saved PDF decodes to the supplied secret.

The method checks structural capacity before use. It supports secrets of 1 to 48 UTF-8 bytes; the maximum packet needs 640 carriers. A 41-byte `Group_13:<32-character-link>` secret needs 584 carriers. The example PDF had 1,716 available carriers.

## Payload and recovery

AES-SIV encrypts and authenticates the secret with a key derived through HKDF-SHA512. Sixteen Reed-Solomon parity bytes permit some bit errors. The reader reconstructs bits from the keyed carrier order and tries supported payload lengths until error correction and authenticated decryption succeed. It needs the marked PDF and the key, not the original PDF or the recipient list.

The internal encryption and derivation label remains `tatou:text-gap:v1` despite the public method rename. Keeping that label allows the reader to decode PDFs produced before the naming change. A future change to the carrier or payload format should use a new internal version label.

## Combining with Davide's mark

`KhaledTextImageWatermark` embeds Davide's image mark first and Khaled's text mark second. Before returning, it checks both standalone readers on the final bytes. Its reader can recover one surviving authenticated layer and rejects conflicting secrets. Image fingerprint attribution is delegated to Davide's method. A PDF needs both suitable live text and a suitable image to use the combined method.

## Evidence and limits

Synthetic tests cover deterministic output, full-secret round trips, wrong-key rejection, unchanged extracted text and image bytes, one damaged carrier, clean PDF resaving, both application orders, conflict detection, image fingerprint scoring, CLI registration, and two distinct RMAP-issued versions. The full server suite passed with 298 tests passed, 11 skipped, and 2 expected failures before this commit.

The example two-page PDF supported the full RMAP secret, and its extracted text stayed identical after watermarking. Its maximum measured glyph displacement was approximately 0.08 pt. This is a digital PDF text-layer technique: copying text into a new document, reflowing it, or printing and scanning removes the geometric signal. The parser deliberately accepts only a restricted set of simple fonts and text transforms; applicability must be checked on each source PDF. Robustness against broad PDF editing and print-scan attacks remains open for evaluation.

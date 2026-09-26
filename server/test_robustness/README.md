# Robustness benchmark

Marks a PDF once with each layer and with `group13-watermark`, runs every
attack in `attacks.py` on each copy, and reads the secret back. It is not
part of the pytest run: it takes several minutes and needs a real PDF.

Attacks: resaving and page copying, metadata stripping, page removal,
overlay removal, white borders and crop box, image recompression, resizing,
cropping, blur, noise, rotation, flipping, TJ normalisation and jitter,
retyping the text, rasterisation (screenshot and simulated print-scan),
combinations that target every layer at once, 12 random chains of 8 mild
attacks and 10 repetitions of the same attack.

```bash
cd server
docker run --rm -v "$PWD":/b -v /path/to/pdfs:/n -w /b -e HOME=/tmp -u "$(id -u):$(id -g)" \
  -e PYTHONPATH=/b/src:/b/test_robustness <image with the server deps + tesseract-ocr> \
  python test_robustness/bench.py /n/source.pdf /n/result.json D,K,F,G
python test_robustness/summary.py /path/to/pdfs/result.json
```

`D`, `K`, `F` are the single layers, `G` is `group13-watermark` (the combined
read, each layer inside it, and the fingerprint `fp`). In the table ✓ means
the right secret was read, · nothing was read, ✗ a wrong recipient.
The key and secrets are throwaway values; never pass the production key or
commit the confidential PDF or its results.

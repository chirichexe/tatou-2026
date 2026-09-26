"""Write one attacked copy of a PDF per attack: <name>_<attack>.pdf

usage: python attack_pdf.py INPUT.pdf OUTPUT_DIR

The attacks are the ones of server/test_robustness/attacks.py (PyMuPDF and
Pillow only), with readable file names. Called by attack.sh.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "test_robustness"))

import attacks as A  # noqa: E402

ATTACKS = {
    # structure: the page looks the same
    "resaved": A.resave,
    "cleaned": A.clean_contents,
    "pages_copied": A.copy_pages,
    "metadata_stripped": A.metadata,
    "converted": A.convert_to_pdf,
    "first_page_removed": A.drop_first_page,
    "last_page_removed": A.drop_last_page,
    # overlays (QR codes and visible labels)
    "overlays_removed": A.strip_overlays,
    "images_removed": A.strip_all_images,
    "white_borders": A.white_borders,
    "cropbox": A.cropbox,
    # images
    "jpeg75": A.jpeg(75),
    "jpeg40": A.jpeg(40),
    "resized50": A.resize(0.5),
    "resized75": A.resize(0.75),
    "down_up50": A.down_up(0.5),
    "cropped10": A.crop_image(0.10),
    "blurred": A.blur(1.5),
    "noisy": A.noise(8),
    "grayscale": A.grayscale,
    "rotated2": A.rotate_image(2),
    "flipped": A.flip_image,
    # text spacing
    "text_rounded": A.tj_round(10),
    "text_kerning_removed": A.tj_zero,
    "text_jitter5": A.tj_jitter(5),
    "text_jitter20": A.tj_jitter(20),
    "text_retyped": A.retype_text,
    # whole page
    "screenshot150": A.rasterize(150),
    "screenshot100": A.rasterize(100, q=70),
    "printscan": A.rasterize(200, q=75, rot=0.7, sigma=6),
    # an attacker who knows every layer
    "overlays_removed_screenshot150": A.then(A.strip_overlays, A.rasterize(150)),
    "overlays_removed_text_retyped_jpeg60": A.then(A.strip_overlays, A.retype_text, A.jpeg(60)),
    # repeated and chained mild attacks
    "jpeg75_x10": A.repeat(A.jpeg(75), 10),
    "resaved_x10": A.repeat(A.resave, 10),
    "noisy4_x10": A.repeat(A.noise(4), 10),
    **{f"chain{seed}": A.chain(seed, 8) for seed in range(4)},
}


def main() -> int:
    source, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    data = source.read_bytes()
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for label, attack in ATTACKS.items():
        target = out_dir / f"{source.stem}_{label}.pdf"
        try:
            target.write_bytes(attack(data))
        except Exception as error:  # noqa: BLE001 - report and go on
            failed += 1
            print(f"  FAILED  {label}: {type(error).__name__}: {error}")
            continue
        steps = getattr(attack, "steps", None)
        print(f"  {target.name}" + (f"  ({' > '.join(steps)})" if steps else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

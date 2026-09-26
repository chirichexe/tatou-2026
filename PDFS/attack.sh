#!/usr/bin/env bash
# Make attacked copies of a (watermarked) PDF, to check end to end which
# layers of the watermark survive: <name>_blurred.pdf, <name>_jpeg75.pdf ...
#
# usage: PDFS/attack.sh FILE.pdf [OUTPUT_DIR]
#
# OUTPUT_DIR defaults to PDFS/output/<name>/. The PyMuPDF attacks run with
# a local python3 if it has pymupdf, numpy and Pillow, otherwise inside the
# server image (TATOU_IMAGE, default tatou-2026-server). Ghostscript, qpdf
# and ImageMagick attacks run too when those tools are installed.
# Never commit the output: it is a copy of the confidential document.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"

if [[ $# -lt 1 || $# -gt 2 || "$1" == -h || "$1" == --help ]]; then
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi
[[ -f "$1" ]] || { echo "error: $1 is not a file" >&2; exit 1; }

input="$(realpath "$1")"
name="$(basename "${input%.*}")"
out="$(realpath -m "${2:-$here/output/$name}")"
mkdir -p "$out"
echo "Attacking $input"
echo "Output in $out"

# ---------------------------------------------------------------- PyMuPDF
if python3 -c 'import pymupdf, numpy, PIL' 2>/dev/null; then
    python3 "$here/attack_pdf.py" "$input" "$out"
else
    image="${TATOU_IMAGE:-tatou-2026-server}"
    docker image inspect "$image" >/dev/null 2>&1 || {
        echo "error: no python3 with pymupdf and no docker image $image (build the server first)" >&2
        exit 1
    }
    docker run --rm --entrypoint python -u "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$repo:/repo:ro" -v "$(dirname "$input"):/in:ro" -v "$out:/out" \
        "$image" /repo/PDFS/attack_pdf.py "/in/$(basename "$input")" /out
fi

# ---------------------------------------------------------------- external tools
run() {  # run LABEL COMMAND...: the command writes $target
    local label="$1"; shift
    target="$out/${name}_${label}.pdf"
    if "$@" >/dev/null 2>&1 && [[ -s "$target" ]]; then
        echo "  $(basename "$target")"
    else
        rm -f "$target"
        echo "  FAILED  $label"
    fi
}

if command -v gs >/dev/null; then
    # re-distill: every stream rewritten, images downsampled and recompressed
    run gs_rewritten  gs -q -dNOPAUSE -dBATCH -dSAFER -sDEVICE=pdfwrite -sOutputFile="$out/${name}_gs_rewritten.pdf" "$input"
    run gs_ebook      gs -q -dNOPAUSE -dBATCH -dSAFER -sDEVICE=pdfwrite -dPDFSETTINGS=/ebook -sOutputFile="$out/${name}_gs_ebook.pdf" "$input"
    run gs_screen     gs -q -dNOPAUSE -dBATCH -dSAFER -sDEVICE=pdfwrite -dPDFSETTINGS=/screen -sOutputFile="$out/${name}_gs_screen.pdf" "$input"
    run gs_no_text    gs -q -dNOPAUSE -dBATCH -dSAFER -sDEVICE=pdfwrite -dNoOutputFonts -sOutputFile="$out/${name}_gs_no_text.pdf" "$input"
fi
if command -v qpdf >/dev/null; then
    run qpdf_qdf        qpdf --qdf --object-streams=disable "$input" "$out/${name}_qpdf_qdf.pdf"
    run qpdf_linearized qpdf --linearize "$input" "$out/${name}_qpdf_linearized.pdf"
fi
if command -v magick >/dev/null && command -v gs >/dev/null; then
    # screenshot of every page through ImageMagick (uses Ghostscript to render)
    run magick_screenshot magick -density 150 "$input" -quality 85 "$out/${name}_magick_screenshot.pdf"
fi

echo "$(find "$out" -name "${name}_*.pdf" | wc -l) files in $out"

"""Robustness benchmark: mark once per method, run every attack, read back.

usage (from server/, Python 3.12):
    PYTHONPATH=src:test_robustness python test_robustness/bench.py SOURCE.pdf OUT.json [D,K,F,G]

D = davide-watermark alone, K = khaled text layer alone, F = francesco
overlay alone, G = group13-watermark (all layers). The key and secrets are
throwaway test values; never pass the production key.
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from difflib import SequenceMatcher

import attacks as A
import pymupdf as fitz

from watermarking_methods.davide.method import DavideWatermark
from watermarking_methods.francesco import FrancescoWatermark
from watermarking_methods.group13 import Group13Watermark
from watermarking_methods.khaled import KhaledTextSpacingWatermark

D, K, F, G = DavideWatermark(), KhaledTextSpacingWatermark(), FrancescoWatermark(), Group13Watermark()
METHODS = {"D": D, "K": K, "F": F, "G": G}
SECRET = "Group_07:da0bb583c432fbfd078959ecc9b62902"  # noqa: S105 - test-only identifier
# Fingerprint scoring is expensive. The broad matrix checks blind readers;
# set FINGERPRINT=1 to additionally compare the true recipient to one decoy.
DECOYS = ["Group_09:0123456789abcdef0123456789abcdef"]
KEY = "bench-only-key-3f9c1e0a7d"


def _read(method, pdf) -> str:
    try:
        return "OK" if method.read_secret(pdf, KEY) == SECRET else "WRONG"
    except Exception:  # any failure means "not read"
        return "-"


def _fingerprint(pdf, original) -> str:
    try:
        scores = D.score_recipients(pdf, original, KEY, [SECRET, *DECOYS])
    except Exception:
        return "-"
    best = max(scores, key=scores.get)
    if scores[best] < D.ATTRIBUTION_THRESHOLD:
        return "-"
    return f"{'OK' if best == SECRET else 'WRONG'}({scores[best]:.0f})"


def _text(pdf: bytes) -> tuple[int, str]:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count, "".join(page.get_text() for page in doc)


def run_one(job):
    index, kind, marked, original, fingerprint = job
    attack = A.ALL[index]
    try:
        leak = attack(marked)
    except Exception as e:
        return attack.__name__, kind, {"attack_error": f"{type(e).__name__}: {e}"}
    t = time.time()
    original_pages, original_text = _text(original)
    leak_pages, leak_text = _text(leak)
    if kind == "G":
        r = {"combined": _read(G, leak), "D": _read(D, leak), "K": _read(K, leak),
             "F": _read(F, leak)}
    else:
        r = {"blind": _read(METHODS[kind], leak)}
    if fingerprint and kind in ("D", "G"):
        r["fp"] = _fingerprint(leak, original)
    r["pages"] = leak_pages
    r["source_pages"] = original_pages
    r["text_ratio"] = round(SequenceMatcher(None, original_text, leak_text).ratio(), 3)
    r["searchable_secret"] = SECRET in leak_text
    r["t"] = round(time.time() - t, 1)
    return attack.__name__, kind, r


def main():
    source, out = sys.argv[1], sys.argv[2]
    kinds = sys.argv[3].split(",") if len(sys.argv) > 3 else list(METHODS)
    with open(source, "rb") as source_file:
        original = source_file.read()
    marked = {}
    for kind in kinds:
        try:
            marked[kind] = METHODS[kind].add_watermark(original, SECRET, KEY)
        except Exception as e:
            print(kind, "not applicable:", e, flush=True)
            continue
        extra = f" layers={G.embedded_layers(marked[kind], KEY, SECRET)}" if kind == "G" else ""
        print(kind, "marked", len(marked[kind]) // 1024, "KB" + extra, flush=True)

    names = set(os.environ.get("ATTACKS", "").split(",")) - {""}
    selected = [i for i, attack in enumerate(A.ALL) if not names or attack.__name__ in names]
    if names - {A.ALL[i].__name__ for i in selected}:
        raise ValueError(f"Unknown attacks: {sorted(names - {A.ALL[i].__name__ for i in selected})}")
    fingerprint = os.environ.get("FINGERPRINT") == "1"
    jobs = [(i, kind, marked[kind], original, fingerprint) for i in selected for kind in marked]
    results: dict = {}
    with ProcessPoolExecutor(int(os.environ.get("JOBS", "8"))) as ex:
        for name, kind, r in ex.map(run_one, jobs):
            results.setdefault(name, {})[kind] = r
            print(f"{name:32s} {kind} {r}", flush=True)
    chains = {a.__name__: a.steps for a in A.ALL if hasattr(a, "steps")}
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"source_pages": _text(original)[0], "initial_layers":
                   {kind: G.embedded_layers(pdf, KEY, SECRET) for kind, pdf in marked.items()},
                   "results": results, "chains": chains}, f, indent=1)


if __name__ == "__main__":
    main()

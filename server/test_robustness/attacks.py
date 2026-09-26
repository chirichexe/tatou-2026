"""Watermark-removal attacks: every function maps PDF bytes to PDF bytes.

They model what another group can do with PyMuPDF/PIL after studying our
public code: resaving, image processing, text normalisation, overlay
removal, rasterisation (screenshot / print-scan).
"""

from __future__ import annotations

import io
import random
import re

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageFilter


def _open(b: bytes) -> fitz.Document:
    return fitz.open(stream=b, filetype="pdf")


def _save(doc: fitz.Document, **kw) -> bytes:
    kw.setdefault("garbage", 3)
    kw.setdefault("deflate", True)
    out = doc.tobytes(**kw)
    doc.close()
    return out


def _named(fn, name):
    fn.__name__ = name
    return fn


# ---------------------------------------------------------------- structure

def resave(b):
    return _save(_open(b), garbage=4, clean=True)


def clean_contents(b):
    doc = _open(b)
    for p in doc:
        p.clean_contents()
    return _save(doc)


def copy_pages(b):
    src, dst = _open(b), fitz.open()
    dst.insert_pdf(src)
    src.close()
    return _save(dst)


def metadata(b):
    doc = _open(b)
    doc.set_metadata({})
    doc.del_xml_metadata()
    return _save(doc, garbage=4)


def convert_to_pdf(b):
    doc = _open(b)
    out = doc.convert_to_pdf()
    doc.close()
    return _save(_open(out))


def drop_first_page(b):
    doc = _open(b)
    if doc.page_count > 1:
        doc.delete_page(0)
    return _save(doc)


def drop_last_page(b):
    doc = _open(b)
    if doc.page_count > 1:
        doc.delete_page(-1)
    return _save(doc)


# ---------------------------------------------------------------- overlays

def strip_overlays(b):
    """Delete every image with a transparency mask: the QR codes and labels
    drawn on top of the page (anyone who reads the code knows them)"""
    doc = _open(b)
    for p in doc:
        for xref in {i[0] for i in p.get_images(full=True) if i[1]}:
            p.delete_image(xref)
    return _save(doc, garbage=4)


def strip_all_images(b):
    doc = _open(b)
    for p in doc:
        for xref in {i[0] for i in p.get_images(full=True)}:
            p.delete_image(xref)
    return _save(doc, garbage=4)


def white_borders(b, frac=0.12):
    doc = _open(b)
    for p in doc:
        r = p.rect
        dx, dy = r.width * frac, r.height * frac
        for box in (fitz.Rect(r.x0, r.y0, r.x1, r.y0 + dy), fitz.Rect(r.x0, r.y1 - dy, r.x1, r.y1),
                    fitz.Rect(r.x0, r.y0, r.x0 + dx, r.y1), fitz.Rect(r.x1 - dx, r.y0, r.x1, r.y1)):
            p.draw_rect(box, color=None, fill=(1, 1, 1), overlay=True)
    return _save(doc)


def cropbox(b, frac=0.12):
    doc = _open(b)
    for p in doc:
        r = p.mediabox
        dx, dy = r.width * frac, r.height * frac
        p.set_cropbox(fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 - dx, r.y1 - dy))
    return _save(doc)


# ---------------------------------------------------------------- images

def _map_images(b, fn, fmt="PNG", quality=95):
    doc = _open(b)
    done = set()
    for p in doc:
        for info in p.get_images(full=True):
            xref, smask = info[0], info[1]
            if xref in done:
                continue
            done.add(xref)
            pix = fitz.Pixmap(doc, xref)
            if smask:
                pix = fitz.Pixmap(pix, fitz.Pixmap(doc, smask))
            if pix.n - pix.alpha != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            alpha = img.mode == "RGBA"
            out = fn(img.convert("RGBA" if alpha else "RGB"))
            buf = io.BytesIO()
            if alpha or fmt == "PNG":
                out.save(buf, "PNG")
            else:
                out.convert("RGB").save(buf, "JPEG", quality=quality)
            p.replace_image(xref, stream=buf.getvalue())
    return _save(doc, garbage=4)


def jpeg(q):
    return _named(lambda b: _map_images(b, lambda im: im, fmt="JPEG", quality=q), f"jpeg{q}")


def resize(f):
    return _named(lambda b: _map_images(b, lambda im: im.resize(
        (max(1, int(im.width * f)), max(1, int(im.height * f))), Image.Resampling.LANCZOS)),
        f"resize{int(f * 100)}")


def down_up(f):
    def fn(im):
        w, h = im.size
        return im.resize((int(w * f), int(h * f)), Image.Resampling.BILINEAR).resize((w, h), Image.Resampling.BICUBIC)
    return _named(lambda b: _map_images(b, fn), f"down_up{int(f * 100)}")


def crop_image(frac):
    return _named(lambda b: _map_images(b, lambda im: im.crop(
        (int(im.width * frac), int(im.height * frac), int(im.width * (1 - frac)), int(im.height * (1 - frac))))),
        f"crop{int(frac * 100)}")


def blur(r):
    return _named(lambda b: _map_images(b, lambda im: im.filter(ImageFilter.GaussianBlur(r))), f"blur{r}")


def noise(sigma, seed=0):
    def attack(b):
        rng = np.random.default_rng(seed)

        def fn(im):
            a = np.asarray(im).astype(np.float64)
            a[..., :3] += rng.normal(0, sigma, a[..., :3].shape)
            return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), im.mode)
        return _map_images(b, fn)
    return _named(attack, f"noise{sigma}")


def grayscale(b):
    return _map_images(b, lambda im: im.convert("L").convert(im.mode))


def rotate_image(deg):
    return _named(lambda b: _map_images(b, lambda im: im.rotate(
        deg, resample=Image.Resampling.BICUBIC, fillcolor=(255,) * len(im.mode))), f"rotate{deg}")


def flip_image(b):
    return _map_images(b, lambda im: im.transpose(Image.Transpose.FLIP_LEFT_RIGHT))


# ---------------------------------------------------------------- text

_TJ = re.compile(rb"\[((?:\((?:\\.|[^\\)])*\)|<[^>]*>|[^\]])*)\]\s*TJ", re.S)
_TOK = re.compile(rb"\((?:\\.|[^\\)])*\)|<[^>]*>|[+-]?(?:\d+\.?\d*|\.\d+)")


def _map_tj_numbers(b, fn):
    doc = _open(b)
    for p in doc:
        for xref in p.get_contents():
            def array(m):
                def token(t):
                    s = t.group(0)
                    if s[:1] in (b"(", b"<"):
                        return s
                    v = fn(float(s))
                    return b"" if v is None else b"%.3f" % v
                return b"[" + _TOK.sub(token, m.group(1)) + b"] TJ"
            doc.update_stream(xref, _TJ.sub(array, doc.xref_stream(xref)))
    return _save(doc)


def tj_round(step):
    return _named(lambda b: _map_tj_numbers(b, lambda v: round(v / step) * step), f"tj_round{step}")


def tj_zero(b):
    return _map_tj_numbers(b, lambda v: None)


def tj_jitter(a, seed=0):
    def attack(b):
        rng = random.Random(seed)
        return _map_tj_numbers(b, lambda v: v + rng.uniform(-a, a))
    return _named(attack, f"tj_jitter{a}")


def retype_text(b):
    """Redact every text span and write it again with a standard font"""
    doc = _open(b)
    for p in doc:
        spans = [s for blk in p.get_text("dict")["blocks"] if blk["type"] == 0
                 for line in blk["lines"] for s in line["spans"] if s["text"].strip()]
        for s in spans:
            p.add_redact_annot(fitz.Rect(s["bbox"]))
        p.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        for s in spans:
            p.insert_text(s["origin"], s["text"], fontsize=s["size"], fontname="helv")
    return _save(doc, garbage=4)


# ---------------------------------------------------------------- whole page

def rasterize(dpi, q=None, rot=0.0, sigma=0.0, seed=0):
    def attack(b):
        rng = np.random.default_rng(seed)
        src, dst = _open(b), fitz.open()
        for p in src:
            pix = p.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if rot:
                img = img.rotate(rot, resample=Image.Resampling.BICUBIC, fillcolor="white")
            if sigma:
                a = np.asarray(img).astype(np.float64) + rng.normal(0, sigma, (img.height, img.width, 3))
                img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=q) if q else img.save(buf, "PNG")
            page = dst.new_page(width=p.rect.width, height=p.rect.height)
            page.insert_image(page.rect, stream=buf.getvalue())
        src.close()
        return _save(dst)
    return _named(attack, f"raster{dpi}" + (f"_q{q}" if q else "") + ("_print" if rot or sigma else ""))


def then(*steps):
    def attack(b):
        for s in steps:
            b = s(b)
        return b
    return _named(attack, "+".join(s.__name__ for s in steps))


SINGLE = [
    resave, clean_contents, copy_pages, metadata, convert_to_pdf,
    drop_first_page, drop_last_page,
    strip_overlays, strip_all_images, white_borders, cropbox,
    jpeg(75), jpeg(40), resize(0.5), resize(0.75), down_up(0.5), crop_image(0.10),
    blur(1.5), noise(8), grayscale, rotate_image(2), flip_image,
    tj_round(10), tj_zero, tj_jitter(5), tj_jitter(20), retype_text,
    rasterize(150), rasterize(100, q=70), rasterize(200, q=75, rot=0.7, sigma=6),
    # an attacker who knows every layer: remove the overlays, then screenshot
    then(strip_overlays, rasterize(150)),
    then(strip_overlays, retype_text, jpeg(60)),
]

# mild attacks that keep the document looking the same: used for long chains
MILD = [
    resave, clean_contents, copy_pages, metadata, convert_to_pdf, strip_overlays,
    jpeg(85), jpeg(70), resize(0.8), noise(4), blur(0.8), tj_jitter(3), tj_round(5),
]


def chain(seed: int, length: int):
    rng = random.Random(seed)
    steps = [rng.choice(MILD) for _ in range(length)]
    attack = _named(then(*steps), f"chain{seed}")
    attack.steps = [s.__name__ for s in steps]
    return attack


def repeat(fn, n):
    return _named(then(*[fn] * n), f"{fn.__name__}x{n}")


ALL = ([_named(lambda b: b, "none")] + SINGLE
       + [chain(s, 8) for s in range(12)]
       + [repeat(jpeg(75), 10), repeat(resave, 10), repeat(tj_jitter(3), 10), repeat(noise(4), 10)])

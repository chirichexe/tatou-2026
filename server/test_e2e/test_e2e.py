"""End-to-end tests of RMAP delivery and leak attribution against the real stack.

Run with:  pytest test_e2e    (needs Docker; see test_e2e/README.md)
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess

import numpy as np
import pymupdf as fitz
import pytest
import requests
from PIL import Image, ImageFilter
from rmap import RMAPClient

from conftest import BASE, CSRF, GROUPS, METHOD, PORT, STRANGER, confidential_pdf


# ---------------------------------------------------------------- helpers

def handshake(stack, identity: str) -> tuple[RMAPClient, str]:
    """Full RMAP exchange over HTTP; returns the client and the decrypted link."""
    client = RMAPClient(identity, stack.keys / f"{identity}_private.asc", stack.keys / "server_public.asc")
    r = requests.post(f"{BASE}/rmap-initiate", json=client.build_msg1())
    assert r.status_code == 200, r.text
    client.process_resp1(r.json())
    r = requests.post(f"{BASE}/rmap-get-link", json=client.build_msg2())
    assert r.status_code == 200, r.text
    return client, client.process_resp2(r.json())


def download(link: str) -> bytes:
    r = requests.get(f"{BASE}/get-version/{link}")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    return r.content


def photo(pdf: bytes) -> Image.Image:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return Image.open(io.BytesIO(doc.extract_image(doc[0].get_images()[0][0])["image"])).convert("RGB")


def with_photo(pdf: bytes, image: Image.Image, quality: int = 85) -> bytes:
    """Replace the photo where it is drawn, as a leaker editing the file would."""
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        xref = page.get_images()[0][0]
        rect = page.get_image_rects(xref)[0]
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=quality)
        page.insert_image(rect, stream=buf.getvalue())
        page.delete_image(xref)
        return doc.tobytes(garbage=4, deflate=True)


def screenshot(pdf: bytes) -> bytes:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        png = page.get_pixmap(dpi=96).tobytes("png")
        with fitz.open() as out:
            out.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, stream=png)
            return out.tobytes()


def ghostscript_screen(pdf: bytes, tmp_path) -> bytes:
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    src.write_bytes(pdf)
    subprocess.run(["gs", "-q", "-o", str(dst), "-sDEVICE=pdfwrite", "-dPDFSETTINGS=/screen", str(src)], check=True)
    return dst.read_bytes()


def jpeg(image: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality)
    return Image.open(buf).convert("RGB")


def attribution(stack, pdf: bytes, name: str) -> requests.Response:
    """Upload a leak as the RMAP service account and ask for attribution."""
    return stack.read_watermark(stack.service_token, stack.upload(stack.service_token, pdf, name))


# ---------------------------------------------------------------- delivery

@pytest.fixture(scope="session")
def copies(stack) -> dict[str, tuple[str, str, bytes]]:
    """Three real deliveries: Group_A twice, Group_B once. Maps label -> (group, link, pdf)."""
    result = {}
    for label, group in (("A1", GROUPS[0]), ("A2", GROUPS[0]), ("B1", GROUPS[1])):
        client, link = handshake(stack, group)
        assert link == client.expected_link
        assert link == f"{client.nonceClient:016x}{client.nonceServer:016x}"
        result[label] = (group, link, download(link))
    return result


def test_stack_is_up_and_method_is_registered(stack):
    health = requests.get(f"http://127.0.0.1:{PORT}/healthz").json()
    assert health["db_connected"] is True
    methods = requests.get(f"{BASE}/get-watermarking-methods").json()["methods"]
    assert METHOD in {m["name"] for m in methods}


def test_each_handshake_delivers_a_distinct_watermarked_copy(stack, copies):
    links = [link for _, link, _ in copies.values()]
    assert all(re.fullmatch(r"[0-9a-f]{32}", link) for link in links)
    assert len(set(links)) == 3

    pdfs = [pdf for _, _, pdf in copies.values()]
    assert len({bytes(p) for p in pdfs}) == 3
    assert all(p != stack.source_pdf for p in pdfs)
    for pdf in pdfs:
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            assert "Group E2E" in doc[0].get_text()
            assert len(doc[0].get_images()) == 1  # no unmarked copy left inside


def test_each_delivery_is_recorded_as_a_version(stack, copies):
    # The listing API (owner only) deliberately omits secrets: check the database.
    rows = {row[0]: row[1:] for row in stack.sql(
        "SELECT link, documentid, intended_for, method, secret FROM Versions"
    )}
    for group, link, _ in copies.values():
        assert rows[link] == [str(stack.source_id), group, METHOD, f"{group}:{link}"]

    listed = requests.get(f"{BASE}/list-all-versions", headers=stack.auth(stack.service_token))
    assert listed.status_code == 200
    assert {link for _, link, _ in copies.values()} <= {v["link"] for v in listed.json()["versions"]}


def test_blind_read_returns_the_secret_and_attribution(stack, copies):
    group, link, pdf = copies["A1"]
    r = attribution(stack, pdf, "intact.pdf")
    assert r.status_code == 201
    assert r.json()["secret"] == f"{group}:{link}"
    assert r.json()["attribution"] == {"intended_for": group, "link": link}


# ---------------------------------------------------------------- attribution

ATTACKS = {
    "crop-and-jpeg40": lambda pdf, tmp: with_photo(pdf, photo(pdf).crop((20, 20, 780, 800)), quality=40),
    "resize-50%": lambda pdf, tmp: with_photo(pdf, photo(pdf).resize((400, 411))),
    "blur": lambda pdf, tmp: with_photo(pdf, photo(pdf).filter(ImageFilter.GaussianBlur(1.5))),
    "jpeg-q20": lambda pdf, tmp: with_photo(pdf, jpeg(photo(pdf), 20)),
    "page-screenshot": lambda pdf, tmp: screenshot(pdf),
    "ghostscript-screen": pytest.param(
        ghostscript_screen,
        marks=pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed"),
    ),
}


@pytest.mark.parametrize("attack", list(ATTACKS.values()), ids=list(ATTACKS))
def test_attacked_copy_is_attributed_to_the_exact_delivery(stack, copies, tmp_path, attack):
    group, link, pdf = copies["A1"]
    r = attribution(stack, attack(pdf, tmp_path), "leak.pdf")
    assert r.status_code == 201, r.text
    # A2 was delivered to the same group: the link proves the right copy was found.
    assert r.json()["attribution"] == {"intended_for": group, "link": link}


def test_averaged_copies_are_attributed_to_a_colluder(stack, copies):
    a, b = copies["A1"], copies["B1"]
    mix = (np.asarray(photo(a[2]), dtype=np.float64) + np.asarray(photo(b[2]), dtype=np.float64)) / 2
    r = attribution(stack, with_photo(a[2], Image.fromarray(mix.round().astype(np.uint8))), "averaged.pdf")
    assert r.status_code == 201
    assert r.json()["attribution"] in (
        {"intended_for": a[0], "link": a[1]},
        {"intended_for": b[0], "link": b[1]},
    )


@pytest.mark.parametrize("name", ["unmarked-original", "unrelated"])
def test_nobody_is_accused_without_a_fingerprint(stack, copies, name):
    pdf = stack.source_pdf if name == "unmarked-original" else confidential_pdf(seed=99)
    r = attribution(stack, pdf, f"{name}.pdf")
    assert r.status_code == 400
    assert r.json() == {"error": "could not read watermark"}


def test_wrong_key_gives_no_secret_and_no_attribution(stack, copies):
    _, _, pdf = copies["A1"]
    doc_id = stack.upload(stack.service_token, screenshot(pdf), "leak.pdf")
    for key in ("wrong-key", stack.watermark_key[:-1] + "0"):
        r = stack.read_watermark(stack.service_token, doc_id, key=key)
        assert r.status_code == 400
        assert "attribution" not in r.json()


# ---------------------------------------------------------------- access control

def test_normal_user_gets_no_attribution_oracle(stack, copies):
    _, _, pdf = copies["A1"]
    token = stack.create_user("e2e_normal_user")
    leak_id = stack.upload(token, with_photo(pdf, photo(pdf).crop((20, 20, 780, 800))), "leak.pdf")

    # Even with the real watermark key, a normal user gets no fingerprint result.
    r = stack.read_watermark(token, leak_id)
    assert r.status_code == 400
    assert "attribution" not in r.json()

    # And cannot touch the service account's documents.
    assert stack.read_watermark(token, stack.source_id).status_code == 404
    assert requests.get(f"{BASE}/get-document/{stack.source_id}", headers=stack.auth(token)).status_code == 404
    r = requests.get(f"{BASE}/list-versions/{stack.source_id}", headers=stack.auth(token))
    assert r.status_code == 200 and r.json() == {"versions": []}


def test_missing_credentials(stack, copies):
    _, _, pdf = copies["A1"]
    doc_id = stack.upload(stack.service_token, pdf, "copy.pdf")
    body = {"method": METHOD, "key": stack.watermark_key}
    assert requests.post(f"{BASE}/read-watermark/{doc_id}", headers=CSRF, json=body).status_code == 401
    no_csrf = {"Authorization": f"Bearer {stack.service_token}"}
    assert requests.post(f"{BASE}/read-watermark/{doc_id}", headers=no_csrf, json=body).status_code == 403


# ---------------------------------------------------------------- RMAP protocol

def test_unregistered_identity_is_rejected(stack):
    client = RMAPClient(STRANGER, stack.keys / f"{STRANGER}_private.asc", stack.keys / "server_public.asc")
    r = requests.post(f"{BASE}/rmap-initiate", json=client.build_msg1())
    assert r.status_code == 400
    assert r.json() == {"error": "invalid RMAP message"}


@pytest.mark.parametrize("body", [{"payload": "not base64 !"}, {"payload": 42}, {}, ["payload"], "text"])
@pytest.mark.parametrize("route", ["rmap-initiate", "rmap-get-link"])
def test_malformed_messages_are_rejected(stack, route, body):
    r = requests.post(f"{BASE}/{route}", json=body)
    assert r.status_code == 400
    assert "payload" not in r.json()


def test_replayed_message_2_gets_no_second_link(stack):
    before = len(requests.get(f"{BASE}/list-all-versions", headers=stack.auth(stack.service_token)).json()["versions"])
    client, _ = handshake(stack, GROUPS[1])
    replay = requests.post(f"{BASE}/rmap-get-link", json=client.build_msg2())
    assert replay.status_code == 409
    assert "payload" not in replay.json()
    after = len(requests.get(f"{BASE}/list-all-versions", headers=stack.auth(stack.service_token)).json()["versions"])
    assert after == before + 1


def test_wrong_nonce_server_is_rejected(stack):
    client = RMAPClient(GROUPS[0], stack.keys / f"{GROUPS[0]}_private.asc", stack.keys / "server_public.asc")
    r = requests.post(f"{BASE}/rmap-initiate", json=client.build_msg1())
    client.process_resp1(r.json())
    client.nonceServer = (client.nonceServer + 1) % 2 ** 64
    r = requests.post(f"{BASE}/rmap-get-link", json=client.build_msg2())
    assert r.status_code == 400
    assert "payload" not in r.json()


def test_unknown_link_is_not_found(stack):
    assert requests.get(f"{BASE}/get-version/{'0' * 32}").status_code == 404

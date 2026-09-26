#!/usr/bin/env python3
"""End-to-end check: upload every PDF of a folder to Tatou and read its watermark.

usage:
    TATOU_EMAIL=... TATOU_PASSWORD=... WM_KEY_FILE=path/to/key \\
        python3 PDFS/check.py PDFS/output/<name> [--url http://127.0.0.1:5000] [--method group13-watermark]

It logs in once, and for each PDF calls upload-document, read-watermark and
delete-document (the uploaded copy is removed again). Logged in as the owner
of RMAP_DOCUMENT_ID, the answer also has `attribution` (fingerprint fallback
included); with any other account only the blind secret is shown.
Only the standard library is used. The key is read from a file so it never
appears in the shell history or the process list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from urllib import error, request


def call(url: str, method: str = "GET", token: str | None = None, body: bytes | None = None,
         content_type: str | None = None) -> tuple[int, dict]:
    headers = {"X-CSRF-Protection": "1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = request.Request(url, data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=600) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def upload(base: str, token: str, pdf: Path) -> tuple[int, dict]:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{pdf.name}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + pdf.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return call(f"{base}/api/upload-document", "POST", token, body, f"multipart/form-data; boundary={boundary}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--url", default=os.environ.get("TATOU_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--method", default="group13-watermark")
    parser.add_argument("--expect", help="expected secret, e.g. Group_13:<link> (default: the most common one)")
    args = parser.parse_args()

    try:
        email, password = os.environ["TATOU_EMAIL"], os.environ["TATOU_PASSWORD"]
        key = Path(os.environ["WM_KEY_FILE"]).read_text().strip()
    except (KeyError, OSError) as e:
        sys.exit(f"set TATOU_EMAIL, TATOU_PASSWORD and WM_KEY_FILE ({e})")
    pdfs = sorted(args.folder.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"no PDF in {args.folder}")

    status, login = call(f"{args.url}/api/login", "POST",
                         body=json.dumps({"email": email, "password": password}).encode(),
                         content_type="application/json")
    if status != 200:
        sys.exit(f"login failed: {status} {login}")
    token = login["token"]

    rows = []
    for pdf in pdfs:
        status, doc = upload(args.url, token, pdf)
        if status != 201:
            rows.append((pdf.name, f"upload {status}", None, None))
            continue
        status, read = call(f"{args.url}/api/read-watermark/{doc['id']}", "POST", token,
                            json.dumps({"method": args.method, "key": key}).encode(), "application/json")
        call(f"{args.url}/api/delete-document/{doc['id']}", "DELETE", token)
        attribution = read.get("attribution") if status == 201 else None
        rows.append((pdf.name, status, read.get("secret") if status == 201 else None, attribution))
        print(f"  {pdf.name}: {status}", file=sys.stderr, flush=True)

    secrets = [s for _, _, s, _ in rows if s]
    expected = args.expect or (max(set(secrets), key=secrets.count) if secrets else None)
    width = max(len(name) for name, *_ in rows)
    print(f"\n{'file':{width}s}  result          secret / attribution")
    counts = {"secret": 0, "fingerprint": 0, "lost": 0, "WRONG": 0}
    for name, status, secret, attribution in rows:
        if secret:
            result = "secret" if secret == expected else "WRONG"
            detail = secret
        elif attribution:
            result, detail = "fingerprint", f"-> {attribution['intended_for']} {attribution['link']}"
        else:
            result, detail = "lost", f"HTTP {status}"
        counts[result] += 1
        print(f"{name:{width}s}  {result:14s}  {detail}")
    print(f"\n{len(rows)} files: {counts['secret']} secret read, {counts['fingerprint']} attributed by "
          f"fingerprint, {counts['lost']} lost, {counts['WRONG']} wrong (expected {expected})")
    return 0 if counts["WRONG"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

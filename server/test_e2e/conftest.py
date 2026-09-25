"""Session fixture: a real Tatou stack (server image + MariaDB) in Docker.

Nothing is mocked. The fixture follows the production setup steps:
1. start the stack with RMAP disabled;
2. create the RMAP service account and upload the confidential PDF via the API;
3. restart the server with RMAP enabled on that document.

Only test material is used: generated OpenPGP keys, random secrets, a fake
flag and a synthetic PDF. `docker compose down -v` removes everything.
"""

from __future__ import annotations

import io
import os
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf as fitz
import pytest
import requests
from PIL import Image
from rmap.keygen import generate_keypair

HERE = Path(__file__).parent
PORT = int(os.environ.get("E2E_PORT", "5055"))
BASE = f"http://127.0.0.1:{PORT}/api"
CSRF = {"X-CSRF-Protection": "1"}
METHOD = "davide-watermark"
GROUPS = ("Group_A", "Group_B")  # registered with the server
STRANGER = "Group_X"             # has a keypair, but is not registered


def confidential_pdf(seed: int = 13) -> bytes:
    """A page like the course PDF: a title, a photo, and a caption table."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:823, 0:800]
    sky = np.stack([90 + 0.1 * y, 140 + 0.05 * y, 200 - 0.05 * y], axis=-1)
    grass = 60 + 50 * np.sin(x / 13.0 + rng.normal(0, 1, (823, 800))) * np.cos(y / 7.0)
    pixels = np.where((y > 300)[..., None], np.stack([grass * 0.5, grass + 40, grass * 0.3], -1), sky)
    pixels = np.clip(pixels + rng.normal(0, 12, pixels.shape), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buf, "JPEG", quality=90)

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((250, 60), "Group E2E", fontsize=20)
    page.insert_image(fitz.Rect(93, 100, 502, 521), stream=buf.getvalue())
    for i, (k, v) in enumerate([("Title", "Synthetic landscape"), ("Author", "Test suite")]):
        page.insert_text((62, 560 + 18 * i), f"{k}: {v}", fontsize=10)
    return doc.tobytes()


@dataclass
class Stack:
    keys: Path
    watermark_key: str
    source_pdf: bytes
    service_token: str
    source_id: int
    env: dict = field(repr=False)

    @staticmethod
    def auth(token: str) -> dict:
        return {"Authorization": f"Bearer {token}", **CSRF}

    def create_user(self, login: str) -> str:
        """Register a user through the API and return a bearer token."""
        password = secrets.token_hex(16)
        email = f"{login}@e2e.test"
        r = requests.post(f"{BASE}/create-user", headers=CSRF,
                          json={"login": login, "email": email, "password": password})
        assert r.status_code == 201, r.text
        r = requests.post(f"{BASE}/login", headers=CSRF, json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def upload(self, token: str, pdf: bytes, name: str) -> int:
        r = requests.post(f"{BASE}/upload-document", headers=self.auth(token),
                          files={"file": (name, pdf, "application/pdf")}, data={"name": name})
        assert r.status_code in (200, 201), r.text
        return int(r.json()["id"])

    def sql(self, query: str) -> list[list[str]]:
        """Run a read-only query in the stack's MariaDB (tab-separated rows)."""
        out = _compose(self.env, "exec", "-T", "db", "sh", "-c",
                       'mariadb -N -u tatou -p"$MARIADB_PASSWORD" tatou -e "$0"', query)
        return [line.split("\t") for line in out.splitlines()]

    def read_watermark(self, token: str, doc_id: int, key: str | None = None, method: str = METHOD):
        return requests.post(f"{BASE}/read-watermark/{doc_id}", headers=self.auth(token),
                             json={"method": method, "key": self.watermark_key if key is None else key})


def _compose(env: dict, *args: str) -> str:
    result = subprocess.run(["docker", "compose", "-f", str(HERE / "compose.yml"), *args],
                            env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{result.stderr[-2000:]}")
    return result.stdout


def _wait_healthy(timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(f"http://127.0.0.1:{PORT}/healthz", timeout=2).json().get("db_connected"):
                return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1)
    raise TimeoutError("Tatou E2E stack did not become healthy")


@pytest.fixture(scope="session")
def stack(tmp_path_factory) -> Stack:
    keys = tmp_path_factory.mktemp("rmap-keys")
    (keys / "clients").mkdir()
    server = generate_keypair("Tatou E2E server", "server@e2e.test")
    (keys / "server_private.asc").write_text(str(server))
    (keys / "server_public.asc").write_text(str(server.pubkey))
    for group in (*GROUPS, STRANGER):
        pair = generate_keypair(group, f"{group.lower()}@e2e.test")
        (keys / f"{group}_private.asc").write_text(str(pair))
        if group in GROUPS:
            (keys / "clients" / f"{group}.asc").write_text(str(pair.pubkey))
    keys.chmod(0o755)

    env = {
        **os.environ,
        "E2E_PORT": str(PORT),
        "E2E_KEYS_DIR": str(keys),
        "E2E_DB_PASSWORD": secrets.token_hex(16),
        "E2E_FLAG": secrets.token_hex(20),      # fake 40-hex flag, never a real one
        "E2E_SECRET_KEY": secrets.token_hex(32),
    }
    watermark_key = secrets.token_hex(32)
    source_pdf = confidential_pdf()

    _compose(env, "down", "-v", "--remove-orphans")
    try:
        # 1. RMAP disabled: the service account and document do not exist yet.
        _compose(env, "up", "-d", "--build")
        _wait_healthy()

        # 2. Service account + confidential PDF, through the real API.
        stack = Stack(keys, watermark_key, source_pdf, "", 0, env)
        stack.service_token = stack.create_user("rmap_service")
        stack.source_id = stack.upload(stack.service_token, source_pdf, "confidential.pdf")

        # 3. Enable RMAP on that document and restart the server only.
        env.update({
            "RMAP_SERVER_PUBLIC_KEY_PATH": "/app/rmap-keys/server_public.asc",
            "RMAP_SERVER_PRIVATE_KEY_PATH": "/app/rmap-keys/server_private.asc",
            "RMAP_CLIENT_KEYS_DIR": "/app/rmap-keys/clients",
            "RMAP_DOCUMENT_ID": str(stack.source_id),
            "RMAP_WATERMARK_METHOD": METHOD,
            "RMAP_WATERMARK_KEY": watermark_key,
        })
        _compose(env, "up", "-d", "--no-deps", "--force-recreate", "server")
        _wait_healthy()

        yield stack
    finally:
        if os.environ.get("E2E_KEEP") != "1":
            _compose(env, "down", "-v", "--remove-orphans")

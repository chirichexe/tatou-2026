# HOWTO: local RMAP test

How to run the RMAP flow locally, from getting a watermarked copy to attributing a leak.
Never commit `.env`, keys, passphrases, flags or distributed PDFs.

## 1. Requirements

- Docker + docker compose
- [uv](https://docs.astral.sh/uv/) and **Python 3.12**. `pgpy`, which `rmap` depends on,
  imports `imghdr`, and that module was removed in Python 3.13.

```sh
uv venv -p 3.12 rmap-venv            # keep it outside the repo
source rmap-venv/bin/activate
uv pip install "rmap @ git+https://github.com/nharrand/RMAP.git@v1.0.2" requests
# to run the server test suite as well:  uv pip install -e 'tatou-2026/server[dev]'
```

Prefix commands with `PYTHONWARNINGS=ignore` to hide pgpy's `CryptographyDeprecationWarning`s.

## 2. Server setup

Keys live outside the repo (`../secrets`, mounted read-only at `/app/rmap-keys`).
Add these to `.env`:

```sh
RMAP_KEYS_HOST_DIR=/abs/path/to/secrets
RMAP_SERVER_PUBLIC_KEY_PATH=/app/rmap-keys/public_key.asc
RMAP_SERVER_PRIVATE_KEY_PATH=/app/rmap-keys/private_key.asc
RMAP_SERVER_KEY_PASSPHRASE_FILE=         # only if the private key is protected (file must be mode 600)
RMAP_CLIENT_KEYS_DIR=/app/rmap-keys/clients
RMAP_WATERMARK_METHOD=toy-eof            # the add-after-eof method's registered name
RMAP_WATERMARK_KEY=<random value>
RMAP_DOCUMENT_ID=<id of the source PDF>
```

The server only reads RMAP config at startup, and it won't start with RMAP enabled
unless `RMAP_DOCUMENT_ID` is set. So the first time:

1. Start the server with RMAP disabled:
   `RMAP_SERVER_PUBLIC_KEY_PATH= RMAP_SERVER_PRIVATE_KEY_PATH= RMAP_CLIENT_KEYS_DIR= docker compose up -d --build`
2. Create a service account (for example `rmap_service`) and upload the source PDF with it
   (`create-user`, `login`, `upload-document`).
3. Put the returned id in `RMAP_DOCUMENT_ID`, then run `docker compose up -d --force-recreate server`.

Check that it's running: `curl http://127.0.0.1:5000/healthz`

## 3. Client: get a watermarked copy

A group is accepted if `clients/<Identity>.asc` exists. It must also hold that key's
**private** key, because the server encrypts its replies to it.

```sh
rmap-client --url http://127.0.0.1:5000 --identity Group_13 \
  --client-private-key secrets/private_key.asc \
  --server-public-key secrets/public_key.asc \
  --no-passphrase-prompt \
  --msg1-path /api/rmap-initiate --msg2-path /api/rmap-get-link
# → Link returned by server: <32-hex>   OK: link matches expected value.

curl -o copy.pdf http://127.0.0.1:5000/api/get-version/<LINK>
```

Expected results:

| Identity | Result |
|---|---|
| `Group_13` (our key) | link + PDF |
| `Group_14` (registered, but we don't have its private key) | `Cannot decrypt the provided message with this key` |
| `Group_99` (not registered) | `400 invalid RMAP message` |
| Replaying message 2 | `409` |

## 4. Read the secret / attribute a leak

**Official route.** Log in as `rmap_service`, upload the leaked PDF, then call:

```sh
curl -X POST http://127.0.0.1:5000/api/read-watermark/<DOC_ID> \
  -H "Authorization: Bearer <TOKEN>" -H "X-CSRF-Protection: 1" \
  -H "Content-Type: application/json" \
  -d '{"method": "toy-eof", "key": "<RMAP_WATERMARK_KEY>"}'
# → {"secret": "Group_13:<link>", "attribution": {"intended_for": "Group_13", "link": "<link>"}, ...}
```

Only the owner of `RMAP_DOCUMENT_ID` gets the `attribution` field. Other users get the plain response.

**Raw read (toy-eof only).** The watermark is a base64 JSON record after `%%EOF`.
The HMAC authenticates it but does not hide it:

```sh
tail -n 1 copy.pdf | tr -- '-_' '+/' | base64 -d | python3 -c \
 'import sys,json,base64;print(base64.urlsafe_b64decode(json.load(sys.stdin)["secret"]).decode())'
# → Group_13:<link>
```

## 5. Other checks

- DB row for each handshake (one row per link, `intended_for` = identity):
  ```sh
  docker compose exec -T db sh -c 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" tatou \
    -e "SELECT documentid, intended_for, link, method FROM Versions WHERE documentid = <RMAP_DOCUMENT_ID>"'
  ```
- Two handshakes as the same group give two different links and files (`sha256sum`).
- A normal user who runs `read-watermark` on their own upload gets
  `documentid, secret, method, position` and **no** `attribution` field.
- If RMAP fails, check `docker compose logs server`. `PassphraseRequiredException` means the
  passphrase is missing or wrong. Errors sent to clients are always generic.
- Unit tests: `cd server && SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))') pytest`

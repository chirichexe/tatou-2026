# Manual E2E: official RMAP + `davide-watermark`, step by step

These are copy-paste commands that run the full RMAP + `davide-watermark`
workflow by hand against an isolated stack.

- **Stack:** an isolated one (`server/test_e2e/compose.yml`), built from a clean state, so it never touches your development data or real keys.
- **Secrets:** every secret is generated into `$WORK`, which is mode 700, and is never typed or printed.
- **RMAP payloads:** they're built with the official library's own helpers, `rmap.crypto.encrypt_json` and `decrypt_json` from `nharrand/RMAP` v1.0.2. They're sent with curl.
- **Requirements:** Python 3.12 with `pip install -e 'server[dev]'`, plus Docker, curl, jq, openssl and Ghostscript (`gs`).

## 0. Variables and keys

The server key directory is mounted into the container. It holds the server keypair and the **public** keys of the registered clients; registering an identity means adding its public key there. Client keypairs stay in `$WORK/client` and never reach the server.

```bash
cd tatou-2026                                  # repository root
PY=/path/to/python3.12                         # the Python with the server deps
BIN=$(dirname "$(command -v "$PY")")
export E2E_PORT=5055
BASE_URL=http://127.0.0.1:$E2E_PORT
API=$BASE_URL/api
COMPOSE="docker compose -f server/test_e2e/compose.yml"
SOURCE_PDF=../Group_13.pdf                     # the confidential PDF (never commit it)
WORK=$(mktemp -d); chmod 700 "$WORK"; mkdir -p "$WORK/keys/clients" "$WORK/client"

"$BIN/rmap-keygen" --name server --email server@e2e.test \
  --out-private "$WORK/keys/server_private.asc" --out-public "$WORK/keys/server_public.asc"
for id in Group_A Group_B Group_X; do
  "$BIN/rmap-keygen" --name $id --email $id@e2e.test \
    --out-private "$WORK/client/${id}_private.asc" --out-public "$WORK/client/${id}_public.asc"
done
cp "$WORK/client/Group_A_public.asc" "$WORK/keys/clients/Group_A.asc"   # register A
cp "$WORK/client/Group_B_public.asc" "$WORK/keys/clients/Group_B.asc"   # register B (X stays unregistered)
chmod 755 "$WORK/keys" "$WORK/keys/clients"
```

## 1. Secrets and a clean stack (RMAP disabled for bootstrap)

The four values are different things. `E2E_SECRET_KEY` is Tatou's Flask and bearer-token secret, **not RMAP**, and `wm.key` is the `davide-watermark` key, also **not RMAP**.

```bash
export E2E_KEYS_DIR="$WORK/keys"
export E2E_DB_PASSWORD=$(openssl rand -hex 16)
export E2E_FLAG=$(openssl rand -hex 20)            # fake flag, required by the entrypoint
export E2E_SECRET_KEY=$(openssl rand -hex 32)      # Tatou SECRET_KEY
openssl rand -hex 32 | tr -d '\n' > "$WORK/wm.key" # RMAP_WATERMARK_KEY

$COMPOSE down -v --remove-orphans
$COMPOSE up -d --build
until curl -sf $BASE_URL/healthz | jq -e .db_connected >/dev/null; do sleep 1; done
curl -s $BASE_URL/healthz
# 200 {"db_connected":true,"message":"The server is up and running."}
```

## 2. Service account, confidential PDF, enable RMAP

`POST /api/create-user` returns `201 {"id","login","email"}`.
`POST /api/login` returns `200 {"token","token_type":"bearer","expires_in"}`: keep `token`.
`POST /api/upload-document` returns `201 {"id","name","creation","sha256","size"}`: keep `id`.

```bash
SVC_PW=$(openssl rand -hex 16)
curl -s -w ' %{http_code}\n' -X POST $API/create-user \
  -H 'X-CSRF-Protection: 1' -H 'Content-Type: application/json' \
  -d "{\"login\":\"rmap_service\",\"email\":\"rmap_service@e2e.test\",\"password\":\"$SVC_PW\"}"
# 201

SVC_TOKEN=$(curl -s -X POST $API/login -H 'X-CSRF-Protection: 1' -H 'Content-Type: application/json' \
  -d "{\"email\":\"rmap_service@e2e.test\",\"password\":\"$SVC_PW\"}" | jq -r .token)

DOCUMENT_ID=$(curl -s -X POST $API/upload-document \
  -H 'X-CSRF-Protection: 1' -H "Authorization: Bearer $SVC_TOKEN" \
  -F "file=@$SOURCE_PDF;type=application/pdf" -F name=confidential.pdf | jq -r .id)
echo "DOCUMENT_ID=$DOCUMENT_ID"

export RMAP_SERVER_PUBLIC_KEY_PATH=/app/rmap-keys/server_public.asc
export RMAP_SERVER_PRIVATE_KEY_PATH=/app/rmap-keys/server_private.asc
export RMAP_CLIENT_KEYS_DIR=/app/rmap-keys/clients
export RMAP_DOCUMENT_ID=$DOCUMENT_ID
export RMAP_WATERMARK_METHOD=davide-watermark
export RMAP_WATERMARK_KEY=$(cat "$WORK/wm.key")
$COMPOSE up -d --no-deps --force-recreate server
until curl -sf $BASE_URL/healthz | jq -e .db_connected >/dev/null; do sleep 1; done
```

## 3. RMAP handshake for Group_A (raw curl, official payload helpers)

Message 1 is `{"identity","nonceClient"}`, encrypted to the **server** public key. The reply is encrypted to **Group_A**'s key and must echo `nonceClient`.

`POST /api/rmap-initiate` returns `200 {"payload"}` with `{"nonceClient","nonceServer"}` inside.

```bash
enc() { "$PY" -c 'import json,sys; from rmap.crypto import encrypt_json, load_key
print(json.dumps(encrypt_json(json.loads(sys.argv[1]), load_key(sys.argv[2]))))' "$1" "$2"; }
dec() { "$PY" -c 'import json,sys; from rmap.crypto import decrypt_json, load_key
print(json.dumps(decrypt_json(json.load(open(sys.argv[1])), load_key(sys.argv[2]))))' "$1" "$2"; }

NONCE_C=$("$PY" -c 'import secrets; print(secrets.randbits(64))')
enc "{\"identity\":\"Group_A\",\"nonceClient\":$NONCE_C}" "$WORK/keys/server_public.asc" > "$WORK/msg1.json"
curl -s -o "$WORK/resp1.json" -w '%{http_code}\n' -X POST $API/rmap-initiate \
  -H 'Content-Type: application/json' -d @"$WORK/msg1.json"
# 200
dec "$WORK/resp1.json" "$WORK/client/Group_A_private.asc" > "$WORK/resp1.plain"
jq -e --argjson n "$NONCE_C" '.nonceClient == $n' "$WORK/resp1.plain"   # true: the server holds its private key
NONCE_S=$(jq -r .nonceServer "$WORK/resp1.plain")
```

Message 2 is `{"nonceServer"}`, encrypted to the server. `POST /api/rmap-get-link` returns `200 {"payload"}` with `{"result":"<32 hex>"}` inside. Before replying, the server has watermarked a copy for Group_A and stored its version row.

```bash
enc "{\"nonceServer\":$NONCE_S}" "$WORK/keys/server_public.asc" > "$WORK/msg2.json"
curl -s -o "$WORK/resp2.json" -w '%{http_code}\n' -X POST $API/rmap-get-link \
  -H 'Content-Type: application/json' -d @"$WORK/msg2.json"
# 200
LINK_A=$(dec "$WORK/resp2.json" "$WORK/client/Group_A_private.asc" | jq -r .result)
[ "$LINK_A" = "$(printf '%016x%016x' "$NONCE_C" "$NONCE_S")" ] && echo "link = hex16(nonceClient)||hex16(nonceServer)"

curl -s -w ' %{http_code}\n' -X POST $API/rmap-get-link -H 'Content-Type: application/json' -d @"$WORK/msg2.json"
# 409 {"error":"RMAP session already completed"}   (replay)
```

The same handshake with the official CLI, for Group_B:

```bash
LINK_B=$("$BIN/rmap-client" --url $BASE_URL --identity Group_B \
  --client-private-key "$WORK/client/Group_B_private.asc" --server-public-key "$WORK/keys/server_public.asc" \
  --no-passphrase-prompt --msg1-path /api/rmap-initiate --msg2-path /api/rmap-get-link \
  | awk '/Link returned by server/ {print $NF}')
echo "LINK_B=${LINK_B:0:6}…"
```

An unregistered identity, a wrong nonce and a malformed body are all rejected:

```bash
NX=$("$PY" -c 'import secrets; print(secrets.randbits(64))')
enc "{\"identity\":\"Group_X\",\"nonceClient\":$NX}" "$WORK/keys/server_public.asc" > "$WORK/msgX.json"
curl -s -w ' %{http_code}\n' -X POST $API/rmap-initiate -H 'Content-Type: application/json' -d @"$WORK/msgX.json"
# 400 {"error":"invalid RMAP message"}

enc '{"nonceServer":12345}' "$WORK/keys/server_public.asc" > "$WORK/wrong.json"
curl -s -w ' %{http_code}\n' -X POST $API/rmap-get-link -H 'Content-Type: application/json' -d @"$WORK/wrong.json"
# 400 {"error":"invalid RMAP message"}

curl -s -w ' %{http_code}\n' -X POST $API/rmap-initiate -H 'Content-Type: application/json' -d '{"payload":"not base64"}'
# 400
```

## 4. Retrieve the watermarked copies

`GET /api/get-version/<link>` returns `200 application/pdf`. It's public: the link itself is the bearer secret.

```bash
curl -s -o "$WORK/copy_A.pdf" -w '%{http_code} %{content_type}\n' $API/get-version/$LINK_A
# 200 application/pdf
curl -s -o "$WORK/copy_B.pdf" -w '%{http_code} %{content_type}\n' $API/get-version/$LINK_B
# 200 application/pdf
sha256sum "$WORK/copy_A.pdf" "$WORK/copy_B.pdf" "$SOURCE_PDF" | cut -c1-16   # three different hashes
```

## 5. Blind watermark and attribution (service account)

`POST /api/read-watermark/<id>` with `{"method","key"}` returns `201 {"documentid","secret","method","position","attribution"}`. The `attribution` field is returned **only** to the owner of `RMAP_DOCUMENT_ID`.

```bash
upload() { curl -s -X POST $API/upload-document -H 'X-CSRF-Protection: 1' -H "Authorization: Bearer $1" \
  -F "file=@$2;type=application/pdf" -F "name=$(basename "$2")" | jq -r .id; }
read_wm() { curl -s -w ' %{http_code}\n' -X POST $API/read-watermark/$2 \
  -H 'X-CSRF-Protection: 1' -H "Authorization: Bearer $1" -H 'Content-Type: application/json' \
  -d "$(jq -n --rawfile k "$3" '{method:"davide-watermark", key:$k}')"; }

COPY_A_ID=$(upload $SVC_TOKEN "$WORK/copy_A.pdf")
read_wm $SVC_TOKEN $COPY_A_ID "$WORK/wm.key"
# 201 {"secret":"Group_A:<LINK_A>","attribution":{"intended_for":"Group_A","link":"<LINK_A>"},...}
```

## 6. Leaks: fingerprint attribution

After a lossy rewrite the blind layer is gone, so the fingerprint names the exact copy: the response has `secret: null` and an `attribution`.

```bash
gs -q -o "$WORK/leak_A.pdf" -sDEVICE=pdfwrite -dPDFSETTINGS=/screen "$WORK/copy_A.pdf"
LEAK_A_ID=$(upload $SVC_TOKEN "$WORK/leak_A.pdf")
read_wm $SVC_TOKEN $LEAK_A_ID "$WORK/wm.key"
# 201 {"secret":null,"attribution":{"intended_for":"Group_A","link":"<LINK_A>"},...}

gs -q -o "$WORK/leak_B.pdf" -sDEVICE=pdfwrite -dPDFSETTINGS=/screen "$WORK/copy_B.pdf"
read_wm $SVC_TOKEN $(upload $SVC_TOKEN "$WORK/leak_B.pdf") "$WORK/wm.key"
# 201 {"secret":null,"attribution":{"intended_for":"Group_B","link":"<LINK_B>"},...}   (B, not A)
```

## 7. No false positives

A wrong key, a recipient's PGP key, the unmarked original and an unrelated PDF all return `400 {"error":"could not read watermark"}` with no `attribution`.

```bash
openssl rand -hex 32 | tr -d '\n' > "$WORK/random.key"
read_wm $SVC_TOKEN $LEAK_A_ID "$WORK/random.key"                     # 400
read_wm $SVC_TOKEN $COPY_A_ID "$WORK/client/Group_B_private.asc"     # 400: another recipient's key
read_wm $SVC_TOKEN $COPY_A_ID "$WORK/client/Group_A_private.asc"     # 400: even its own PGP key
read_wm $SVC_TOKEN $(upload $SVC_TOKEN "$SOURCE_PDF") "$WORK/wm.key" # 400: unmarked original
```

## 8. Normal user: `create-watermark` flow and access control

`POST /api/create-watermark/<id>` with `{"method","key","secret","intended_for"}` returns `201 {"id","documentid","link",...}`: keep `id` and `link`.

```bash
U_PW=$(openssl rand -hex 16)
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/create-user -H 'X-CSRF-Protection: 1' \
  -H 'Content-Type: application/json' -d "{\"login\":\"alice\",\"email\":\"alice@e2e.test\",\"password\":\"$U_PW\"}"
# 201
TOKEN=$(curl -s -X POST $API/login -H 'X-CSRF-Protection: 1' -H 'Content-Type: application/json' \
  -d "{\"email\":\"alice@e2e.test\",\"password\":\"$U_PW\"}" | jq -r .token)

USER_DOC=$(upload $TOKEN "$SOURCE_PDF")
printf 'alice-key-%s' "$(openssl rand -hex 8)" > "$WORK/alice.key"
curl -s -o "$WORK/cw.json" -w '%{http_code}\n' -X POST $API/create-watermark/$USER_DOC \
  -H 'X-CSRF-Protection: 1' -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "$(jq -n --rawfile k "$WORK/alice.key" '{method:"davide-watermark", key:$k, secret:"for-bob", intended_for:"bob"}')"
# 201
VERSION_ID=$(jq -r .id "$WORK/cw.json"); VERSION_LINK=$(jq -r .link "$WORK/cw.json")
curl -s -o "$WORK/bob.pdf" -w '%{http_code}\n' $API/get-version/$VERSION_LINK           # 200
read_wm $TOKEN $(upload $TOKEN "$WORK/bob.pdf") "$WORK/alice.key"
# 201 {"secret":"for-bob",...}   (no "attribution" field for normal users)
curl -s $API/list-versions/$USER_DOC -H "Authorization: Bearer $TOKEN"
# 200 {"versions":[{"id":<VERSION_ID>,"documentid":<int>,"link":...,"intended_for":"bob","secret":"for-bob","method":"davide-watermark"}]}
# (list-versions shows the owner the secret; list-all-versions does not)
```

Access control:

```bash
read_wm $TOKEN $(upload $TOKEN "$WORK/leak_A.pdf") "$WORK/wm.key"
# 400, no "attribution": even with the real key a normal user gets no fingerprint result
curl -s -o /dev/null -w '%{http_code}\n' $API/get-document/$DOCUMENT_ID -H "Authorization: Bearer $TOKEN"                 # 404
read_wm $TOKEN $DOCUMENT_ID "$WORK/wm.key"                                                                         # 404
curl -s -o /dev/null -w '%{http_code}\n' $API/get-document/$USER_DOC -H "Authorization: Bearer $SVC_TOKEN"             # 404
curl -s -o /dev/null -w '%{http_code}\n' $API/list-documents                                                          # 401: no token
curl -s -o /dev/null -w '%{http_code}\n' $API/list-documents -H 'Authorization: Bearer not-a-token'                   # 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/read-watermark/$USER_DOC -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{}'                                                                       # 403: no CSRF header
curl -s -o /dev/null -w '%{http_code}\n' $API/get-version/$(openssl rand -hex 16)                                    # 404: guessed link
curl -s -o /dev/null -w '%{http_code}\n' $API/get-version/not-a-link                                                  # 404
```

## 9. Restart and rebuild

Versions, files and accounts are persistent. Handshakes between message 1 and message 2 live in memory (the official `RMAPServer`), so a handshake started before a restart must be redone.

```bash
NONCE_P=$("$PY" -c 'import secrets; print(secrets.randbits(64))')
enc "{\"identity\":\"Group_A\",\"nonceClient\":$NONCE_P}" "$WORK/keys/server_public.asc" > "$WORK/p1.json"
curl -s -o "$WORK/p1r.json" -X POST $API/rmap-initiate -H 'Content-Type: application/json' -d @"$WORK/p1.json"
enc "{\"nonceServer\":$(dec "$WORK/p1r.json" "$WORK/client/Group_A_private.asc" | jq .nonceServer)}" \
  "$WORK/keys/server_public.asc" > "$WORK/p2.json"          # message 2 prepared, not sent

$COMPOSE restart db server                                   # or: $COMPOSE up -d --build --no-deps --force-recreate server
until curl -sf $BASE_URL/healthz | jq -e .db_connected >/dev/null; do sleep 1; done

curl -s -o /dev/null -w '%{http_code}\n' $API/get-version/$LINK_A                           # 200: version persisted
SVC_TOKEN=$(curl -s -X POST $API/login -H 'X-CSRF-Protection: 1' -H 'Content-Type: application/json' \
  -d "{\"email\":\"rmap_service@e2e.test\",\"password\":\"$SVC_PW\"}" | jq -r .token)
read_wm $SVC_TOKEN $LEAK_A_ID "$WORK/wm.key"                                                 # 201: still link A
curl -s -w ' %{http_code}\n' -X POST $API/rmap-get-link -H 'Content-Type: application/json' -d @"$WORK/p2.json"
# 400: the pending session was in memory; start a new handshake (section 3)
```

## 10. Clean up

```bash
$COMPOSE down -v --remove-orphans
rm -rf "$WORK"
```

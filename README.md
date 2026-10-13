# tatou
A web platform for PDF watermarking. This project is intended for pedagogical use and may contain security vulnerabilities. Do not deploy on an open network.

## Instructions

The following instructions are meant for a bash terminal on a Linux machine. If you are using something else, you will need to adapt them.

To clone the repo, you can simply run:

```bash
git clone https://github.com/nharrand/tatou-2026.git
```

Note that you should probably fork the repo and clone your own repo.


### Run python unit tests

```bash
cd tatou-2026/server

# Create a Python 3.12 virtual environment (the CI/container version)
python3.12 -m venv .venv

# Activate your virtual environment
. .venv/bin/activate

# Install the necessary dependencies
python -m pip install -r requirements.txt -e ".[dev]"

# Run the unit tests
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python -m pytest

# Run lint
python -m ruff check .
```

API tests are in `test/api/`, RMAP integration tests in `test/rmap/`, and
watermarking tests in `test/watermarking/`. There is no `test_e2e/` suite in this
checkout. Install Tesseract for visible-label OCR tests; Ghostscript is used by
optional PDF transformation tests. Record skips and expected failures separately.

### Mutation testing

From `server/`, using the same Python 3.12 environment:

```bash
source .venv/bin/activate
python -m pip install "mutmut==3.8.0"
../mutation help                  # Also accepts --help or -h
../mutation                       # Same as all
../mutation pause                 # Safely stop the active campaign
../mutation resume                # Continue the last recorded campaign
```

The helper starts a fresh campaign, generates a temporary test key and uses the
`[tool.mutmut]` configuration in `server/pyproject.toml`.

| Command argument | Mutants selected for execution |
|---|---|
| `all` (default) | All configured source modules |
| `backend` | Flask backend and login rate limiter |
| `francesco`, `davide`, `khaled` | The selected watermarking package |
| `common` | Group13, Toy EOF, registry, base contract, utilities and CLI |

From the repository root, use `./mutation`; from `server/`, use `../mutation`.
Activation is not required when running the launcher: it directly uses Python and
mutmut from `server/.venv`. That environment must already contain the project
dependencies and mutmut. For example, `./mutation backend` works from the root
without an active virtual environment.
The launcher resolves the project location automatically, without a machine-specific
path. For example, run `../mutation davide` from `server/`. All tests remain available;
mutmut selects associated tests. These execution filters do not restrict initial
generation or statistics collection to the selected package. Mutmut 3.8 focuses
on exercised functions; including `src/` does not guarantee every statement is mutated.

Inspect the native output from `server/`:

```bash
mutmut results
mutmut browse
mutmut browse --show-killed
```

Run one campaign at a time and invoke session controls sequentially. Fresh campaigns
ask for confirmation before replacing the same ignored `mutants/` directory, and
are refused while a campaign for this checkout is active. Preserve baseline results
privately before starting another fresh campaign.

`pause` sends SIGINT to the campaign coordinator, using mutmut's worker cleanup.
Completed results are preserved; unfinished work can run again on resume. If
shutdown takes more than ten seconds, the helper reports that it is still pending;
wait for it to finish before resuming. Without an active campaign, pause does nothing.
`resume` keeps saved results and the original scope filters. It does nothing if
already running, if that scope is complete, or if session data cannot be recovered.
The ignored `mutants/.mutation-session.json` records arguments only, never the test
key. To adopt a campaign started before these controls existed, invoke resume while
it is still active; an already stopped legacy campaign with unknown filters cannot
be recovered automatically. Changes to sources, tests or configuration may invalidate
mutmut's cached results. Process discovery uses `ps` and requires permission to
read the process list. Help never starts or modifies a campaign.

Analyze surviving mutants for missing assertions,
untested cases or equivalent behavior; the mutation score alone is insufficient.

### Add a watermarking method

Put each concrete method in its own module under `server/src/watermarking_methods/`
and implement the `WatermarkingMethod` interface in `server/src/watermarking_method.py`.
Register the method explicitly in `server/src/watermarking_utils.py`; the server and
CLI use that registry, and no modules are loaded dynamically. Keep method-specific
tests under `server/test/watermarking/` (one folder per larger method, e.g.
`davide/`), next to the shared contract and CLI tests. HTTP tests live in
`server/test/api/` and RMAP tests in `server/test/rmap/`. `toy-eof` is not
registered: `server/test/conftest.py` registers it only for the tests that
need a fast method.

### Deploy

From the root of the directory:

```bash
# Create a file to set environment variables like passwords.
cp sample.env .env

# Edit .env and pick the passwords you want

# Generate a signing key, then paste it into SECRET_KEY in .env only.
python3 -c 'import secrets; print(secrets.token_hex(32))'

# Rebuild the docker image and deploy the containers
docker compose up --build -d

# Monitor logs in realtime 
docker compose logs -f

# Test if the API is up
http -v :5000/healthz

# Open your browser at 127.0.0.1:5000 to check if the website is up.
```

### Monitoring Stack

The project includes a monitoring and logging stack based on Prometheus, Grafana, Loki, Promtail, and cAdvisor:

- **Grafana**: [http://127.0.0.1:3000](http://127.0.0.1:3000) (pre-configured with Prometheus & Loki datasources and the *Tatou Monitoring & Logs Overview* dashboard)
- **Prometheus**: [http://127.0.0.1:9090](http://127.0.0.1:9090)
- **Loki**: [http://127.0.0.1:3100](http://127.0.0.1:3100)
- **cAdvisor**: [http://127.0.0.1:8081](http://127.0.0.1:8081)

To browse logs, open Grafana at [http://127.0.0.1:3000](http://127.0.0.1:3000) and visit **Dashboards** > **Tatou Monitoring & Logs Overview**, or use **Explore** with the Loki data source. For more details, see [monitoring/README.md](monitoring/README.md).


The standard deployment starts the application and MariaDB. MariaDB is
available only to the application through Docker's internal backend network;
it does not publish a port on the host.

phpMyAdmin is commented out in the current Compose file; no active admin profile
is provided.

For direct database administration without publishing MariaDB, use
`docker compose exec db mariadb -u root -p` and enter the root password from
the local `.env` file when prompted.

`SECRET_KEY` is required: Compose and the application reject an unset or empty
key, and the application rejects the former public development key. Keep
`sample.env` empty for this setting and never commit the real `.env` file.
Each developer should generate a separate local key. Generate the university
deployment's key once, store it privately on that server, and reuse it across
restarts and all server workers. Changing it invalidates existing login tokens,
so users must log in again.

PDF uploads are limited to 64 MiB by default. Set `MAX_UPLOAD_SIZE_BYTES` in
the local `.env` file to a different positive byte value when the deployment
requires another limit. Requests or documents above the configured limit are
rejected with HTTP `413` before PDF processing or database insertion.

For direct Python runs, export `SECRET_KEY` in the process environment; the
application does not load `.env` itself. The test command above creates a
temporary test key and does not need the deployment key.

Login allows five failed attempts per account and per connection IP in a rolling
60-second window. The next attempt returns HTTP 429 and `Retry-After` (at most
60 seconds); blocked retries never extend the cooldown. Successful logins do not
consume the failure budget, and existing tokens continue to work.

The counters use Python's built-in SQLite in
`STORAGE_DIR/.auth/login-attempts.sqlite3`. The existing storage volume shares
them across Gunicorn workers and preserves them across container recreation.
Expired entries are removed on subsequent attempts. Account/IP keys are HMAC
digests, and passwords are never stored in the counter database. Storage errors
return a generic 503 rather than disabling throttling. No MariaDB migration or
additional service is required. Multiple replicas must share this local storage;
this implementation is intended for the single-host course deployment.

IP limits use the direct connection address. Do not enable trust in arbitrary
`X-Forwarded-For` headers. If a reverse proxy is introduced, configure trusted
proxy handling explicitly; otherwise all clients behind it share one IP budget.

### Enable RMAP

Tatou uses the upstream [RMAP v1.0.2](https://github.com/nharrand/RMAP) package
for the PGP challenge/response protocol. The feature stays disabled until its
key paths are configured. Create the server keypair locally, then place the
course-provided client public keys in `rmap-keys/clients`, named after their
identities (for example, `Group_01.asc`). Keep private keys outside Git. For a
server deployment, keep the entire key directory outside the checkout and set
`RMAP_KEYS_HOST_DIR` to that absolute host path; Compose mounts it read-only at
`/app/rmap-keys`.

```bash
mkdir -p rmap-keys/clients
rmap-keygen --name "Tatou server" --email server@example.test \
  --out-private rmap-keys/server_private.asc \
  --out-public rmap-keys/server_public.asc
```

Set these values in `.env` for Compose:

```dotenv
RMAP_SERVER_PUBLIC_KEY_PATH=/app/rmap-keys/server_public.asc
RMAP_SERVER_PRIVATE_KEY_PATH=/app/rmap-keys/server_private.asc
RMAP_CLIENT_KEYS_DIR=/app/rmap-keys/clients
RMAP_DOCUMENT_ID=42
RMAP_WATERMARK_METHOD=group13-watermark
RMAP_WATERMARK_KEY=<private-watermark-key>
RMAP_SERVER_KEY_PASSPHRASE_FILE=/app/rmap-keys/server_passphrase
```

For example, a server keeping its keys in `/home/softsec/secrets` can use:

```dotenv
RMAP_KEYS_HOST_DIR=/home/softsec/secrets
RMAP_SERVER_PUBLIC_KEY_PATH=/app/rmap-keys/public_key.asc
RMAP_SERVER_PRIVATE_KEY_PATH=/app/rmap-keys/private_key.asc
RMAP_CLIENT_KEYS_DIR=/app/rmap-keys/clients
RMAP_SERVER_KEY_PASSPHRASE_FILE=/app/rmap-keys/server_passphrase
```

`RMAP_DOCUMENT_ID` is the confidential source document already stored in Tatou.
Every completed handshake produces a new version, watermarked with the peer's
session link as its sole watermark secret, records the peer identity separately
as version metadata, and returns the generated link. RMAP defaults to
`group13-watermark` for newly issued copies. The manual watermark interface offers four choices: each of the three
component methods individually, or the combined method. Operators can still
override `RMAP_WATERMARK_METHOD` for RMAP. The three component methods are
also used by the combined pipeline:

| Method | Carrier |
|---|---|
| `davide-watermark` | PDF images |
| `francesco-watermark` | encrypted QR code and visible labels with the secret |
| `khaled-text-spacing-watermark` | spacing between selectable text glyphs |
| `group13-watermark` | all three component methods |

Layers that do not fit the PDF are skipped, and the combined reader can still
read copies carrying a compatible component layer. The fingerprint attributes
cropped, rescaled or screenshotted leaks through `read-watermark`.
`toy-eof` is easily stripped and kept only for the test suite.
Removal-attack tests are included under `server/test/watermarking/`; there is no
separate `server/test_robustness/` directory in this checkout.

When the server private key has a passphrase, create
`rmap-keys/server_passphrase` locally with mode `600`, place the passphrase in
that file, and keep `RMAP_SERVER_KEY_PASSPHRASE` empty. The container receives
the key directory read-only and the passphrase is never included in the Compose
environment.

The upstream CLI can exercise the complete flow with the client keypair:

```bash
rmap-client --url http://localhost:5000 --identity Group_01 \
  --client-private-key path/to/group01_private.asc \
  --server-public-key rmap-keys/server_public.asc \
  --msg1-path /api/rmap-initiate --msg2-path /api/rmap-get-link \
  --get-link-path /api/get-version --fetch-link
```

The reference `RMAPServer` keeps pending nonces in process memory. The
provided Gunicorn command uses one worker; if you add workers or replicas,
route both handshake messages to the same process or provide shared session
state.

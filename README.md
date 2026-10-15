# tatou
A web platform for PDF watermarking. This project is intended for pedagogical use and may contain security vulnerabilities. Do not deploy on an open network.

## Contents

- [Setup](#prerequisites-and-python-setup)
- [Tests and lint](#run-tests)
- [Coverage](#coverage-analysis)
- [Reports](#reports)
- [Mutation testing](#mutation-testing)
- [Watermarking methods](#add-a-watermarking-method)
- [Deployment](#deploy)
- [Monitoring](#monitoring-stack)
- [RMAP](#enable-rmap)
- [Monitoring configuration](#monitoring-configuration)

## Instructions

The following instructions are meant for a bash terminal on a Linux machine. If you are using something else, you will need to adapt them.

To clone the repo, you can simply run:

```bash
git clone https://github.com/nharrand/tatou-2026.git
```

Note that you should probably fork the repo and clone your own repo.


### Prerequisites and Python setup

Use Python 3.12, Git, and pip for local checks. Install Tesseract OCR for
visible-label extraction tests; Ghostscript is optional for PDF transformation
tests. Deployment requires Docker Engine and Docker Compose v2 with support
for the `include` directive.

From the repository root:

```bash
make setup
make help
```

`make setup` creates `server/.venv` with Python 3.12 and installs runtime dependencies
and development tools, including coverage.py, pytest-cov and mutmut. Subsequent Make commands use that environment
without requiring activation. Coverage is a local analysis tool, not a runtime
dependency. Tesseract and optional Ghostscript must be installed separately.

### Run tests

From the repository root:

```bash
make test  # Complete suite with a freshly generated temporary test key
make lint  # Separate Ruff checks
```

`make test` always runs the complete suite; it takes no test-selection arguments.
For manual targeted runs, activate `server/.venv`, work from `server/`, and
export a temporary `SECRET_KEY` before calling pytest directly.

Under `server/`, API tests are in `test/api/`, RMAP integration tests in
`test/rmap/`, and watermarking tests in `test/watermarking/`. There is no
`test_e2e/` suite in this checkout. Install Tesseract for visible-label OCR tests; Ghostscript is used by
optional PDF transformation tests. Record skips and expected failures separately.

API tests use Flask's in-process test client and isolated temporary storage and
SQLite fixtures. They do not require a running deployment and do not establish
end-to-end behavior against MariaDB. The watermarking directory includes both
unit tests and integration tests; it is not a unit-only suite.

### Coverage analysis

From the repository root, after `make setup`:

```bash
make coverage         # Run the suite and save coverage measurements
make coverage-report  # Print the saved summary and missing lines
make coverage-html    # Generate the HTML report from saved measurements
```

`make coverage` uses pytest-cov to run the complete suite with line and branch
coverage of `server/src/` and a fresh temporary test key. It saves measurements
in `server/.coverage` without generating reports. `make coverage-report` and
`make coverage-html` read those measurements without rerunning tests; HTML is
written to `server/htmlcov/index.html`. Run measurement first; reports describe
that execution, not later source changes. Generated coverage files are ignored
by Git. Coverage does not prove that assertions are effective. An optional XML
export is available with `cd server && .venv/bin/python -m coverage xml`.

### Reports

```bash
make report             # report/report.pdf
make report-francesco   # artifacts/francesco/mutation-testing-report.pdf
```

Both commands require `pdflatex`, run two compilation passes when sources change,
and keep intermediate files in each report's `.build/` directory. The main source
is `report/report.tex`; section files are build dependencies. There are no watch
targets. Report workspaces are local and ignored by Git.

### Mutation testing

From the repository root, run `make setup` once, then use:

| Command | Action |
|---|---|
| `make mutation` | Fresh campaign for all configured modules |
| `make mutation-backend` | Flask backend and login rate limiter |
| `make mutation-francesco` | Francesco watermarking package |
| `make mutation-davide` | Davide watermarking package |
| `make mutation-khaled` | Khaled watermarking package |
| `make mutation-common` | Group13, Toy EOF, registry, base contract, utilities and CLI |
| `make mutation-pause` | Safely stop the active campaign, preserving saved results |
| `make mutation-resume` | Resume the recorded campaign with its original filters |
| `make mutation-results` | Print saved native results |
| `make mutation-browse` | Open the interactive mutant browser |

All commands use `server/.venv` without activation. `make help` lists the commands.
The old `./mutation` launcher has been removed. Make delegates campaign lifecycle
to `scripts/run-mutation.sh` and `scripts/mutation-session.py`; their safety
checks and confirmation remain in place. A fresh campaign generates a temporary
test key and uses `[tool.mutmut]` in `server/pyproject.toml`.

Scopes filter mutant execution, not initial generation or statistics collection.
All tests remain available and mutmut selects associated tests. Mutmut 3.8 focuses
on exercised functions; including `src/` does not guarantee every statement is mutated.
To include killed mutants in the browser, use the underlying tool from the root:
`cd server && .venv/bin/mutmut browse --show-killed`.

Run one campaign at a time and invoke session controls sequentially. Fresh campaigns
ask for confirmation before replacing the same ignored `mutants/` directory, and
are refused while a campaign for this checkout is active. Preserve baseline results
privately before starting another fresh campaign.

`make mutation-pause` sends SIGINT to the campaign coordinator, using mutmut's worker cleanup.
Completed results are preserved; unfinished work can run again on resume. If
shutdown takes more than ten seconds, the helper reports that it is still pending;
wait for it to finish before resuming. Without an active campaign, pause does nothing.
`make mutation-resume` keeps saved results and the original scope filters.
For scoped campaigns it passes only unfinished mutant IDs to mutmut, because
explicit scope filters would otherwise rerun completed mutants. It does nothing if
already running, if that scope is complete, or if session data cannot be recovered.
The ignored `mutants/.mutation-session.json` records arguments only, never the test
key. To adopt a campaign started before these controls existed, invoke `make mutation-resume` while
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

Configure MariaDB and Grafana credentials in `.env`, and replace `FLAG_1` with
exactly 40 hexadecimal characters. For an isolated local deployment, generate
a fresh value with `python3 -c 'import secrets; print(secrets.token_hex(20))'`;
the university deployment must use its assigned flag. Keep all real values
private. Configure RMAP separately as described below.

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
curl --fail http://127.0.0.1:5000/healthz

# Open your browser at 127.0.0.1:5000 to check if the website is up.
```

### Monitoring Stack

The project includes a monitoring and logging stack based on Prometheus, Grafana, Loki, Promtail, and cAdvisor:

- **Grafana**: [http://127.0.0.1:3000](http://127.0.0.1:3000) (pre-configured with Prometheus & Loki datasources and the *Tatou Monitoring & Logs Overview* dashboard)
- **Prometheus**: [http://127.0.0.1:9090](http://127.0.0.1:9090)
- **Loki**: [http://127.0.0.1:3100](http://127.0.0.1:3100)
- **cAdvisor**: [http://127.0.0.1:8081](http://127.0.0.1:8081)

To browse logs, open Grafana at [http://127.0.0.1:3000](http://127.0.0.1:3000) and visit **Dashboards** > **Tatou Monitoring & Logs Overview**, or use **Explore** with the Loki data source. For more details, see [monitoring configuration](#monitoring-configuration).


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

### Monitoring configuration

Monitoring configuration and operations:

The `monitoring/` directory provides the monitoring, alerting, and log aggregation stack for Tatou:

- **Prometheus** (Port `9090`): Time-series database scraping metrics from containers and evaluating alert rules.
- **Alertmanager** (Port `9093`): Alert routing engine dispatching notifications for attacks and system errors.
- **Alert Logger Webhook** (Port `9095`): Lightweight receiver logging all alerts to `monitoring/alertmanager/alerts.log`.
- **Grafana** (Port `3000`): Web dashboard UI pre-configured with Loki, Prometheus, and Alertmanager datasources.
- **Loki** (Port `3100`): Log aggregation engine receiving log streams.
- **Promtail**: Daemon reading Docker container logs via Docker engine socket and forwarding them with labels to Loki and extracting alert metrics.
- **cAdvisor** (Port `8081`): Container metrics exporter providing CPU, memory, filesystem, and network statistics per container.

---

#### Access URLs

| Service | URL | Default Credentials |
| :--- | :--- | :--- |
| **Grafana** | [http://localhost:3000](http://localhost:3000) | Configured via `GF_USERNAME` and `GF_PASSWORD` in `.env` |
| **Prometheus** | [http://localhost:9090](http://localhost:9090) | No auth required |
| **Alertmanager** | [http://localhost:9093](http://localhost:9093) | No auth required |
| **Loki** | [http://localhost:3100](http://localhost:3100) | No auth required |
| **cAdvisor** | [http://localhost:8081](http://localhost:8081) | No auth required |

---

#### Resource Limits (CPU & RAM)

All monitoring services have strict resource limits configured in [docker-compose.monitoring.yml](docker-compose.monitoring.yml):

| Service | CPU Limit | RAM Limit | CPU Reservation | RAM Reservation |
| :--- | :--- | :--- | :--- | :--- |
| **prometheus** | `0.50` (50%) | `512MB` | `0.10` | `128MB` |
| **loki** | `0.50` (50%) | `512MB` | `0.10` | `128MB` |
| **grafana** | `0.50` (50%) | `512MB` | `0.10` | `128MB` |
| **cadvisor** | `0.30` (30%) | `256MB` | `0.05` | `64MB` |
| **promtail** | `0.25` (25%) | `256MB` | `0.05` | `64MB` |
| **alertmanager** | `0.25` (25%) | `128MB` | `0.05` | `32MB` |
| **alert-logger** | `0.10` (10%) | `64MB` | `0.02` | `16MB` |

---

#### Alerting: Attacchi ed Errori

Alertmanager riceve gli alert generati da Prometheus in base alle regole definite in [monitoring/prometheus/alert.rules.yml](monitoring/prometheus/alert.rules.yml):

##### 1. Alert per Rilevamento Attacchi
- **BruteForceOrAuthAttack**: Rileva picchi di tentativi di autenticazione falliti o risposte HTTP `429 Too Many Requests` dal rate limiter.
- **PotentialDoS_HighCpu**: Utilizzo prolungato della CPU oltre l'80% su un container (possibile attacco DoS, loop o abuso computazionale).
- **PotentialDDoS_NetworkFlood**: Traffico di rete in ingresso anomalo superiore a 10 MB/s su un container.
- **HighMemoryExhaustion**: Consumo elevato di RAM (> 450MB) indicativo di tentativi di memory exhaustion (es. decompressione zip-bomb o upload massivi).

##### 2. Alert per Errori di Sistema
- **ServiceDown**: Uno dei servizi essenziali (server, database, prometheus, loki, promtail, cadvisor) è irraggiungibile (`up == 0`).
- **ContainerCrashOrRestart**: Riavvio anomalo o crash improvviso di un container.
- **HighServerErrorRate**: Picco di errori HTTP 5xx o eccezioni non gestite dal server Flask.
- **DatabaseErrors**: Errori o malfunzionamenti generati dal database MariaDB.

---

#### Notifiche su File (`alerts.log`)

Alertmanager invia le notifiche al microservizio `alert-logger` (webhook locale su porta 9095) che le scrive formattate e con timestamp in:

```bash
# Monitora gli alert in tempo reale
tail -f monitoring/alertmanager/alerts.log
```

Esempio di riga registrata:
```text
[2026-09-21 11:45:00 UTC] [FIRING] [CRITICAL] Alert: BruteForceOrAuthAttack | Target: tatou-2026-server-1 | Summary: Potential Brute-Force / Credential Stuffing Attack | Description: Detected spike in authentication failures or 429 rate limit triggers on Tatou server.
```

---

#### Come Collegare un Canale Telegram

Le credenziali di Telegram vengono lette direttamente dal file `.env`:

1. Apri il file `.env` e imposta:
   ```bash
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
   TELEGRAM_CHAT_ID=-1001234567890
   ```
2. Applica le modifiche:
   ```bash
   docker compose up -d alert-logger
   ```
   *(Il servizio `alert-logger` registra automaticamente gli alert sia su file che sul canale Telegram con formattazione HTML ricca di dettagli).*

---

#### How to Start & Stop

##### Option 1: Together with Tatou (Default)

```bash
# Start application and monitoring stack
docker compose up -d

# View status of all containers
docker compose ps

# Stop all containers
docker compose down
```

##### Option 2: Run Monitoring Stack Separately

```bash
# Start only monitoring services
docker compose -f docker-compose.monitoring.yml up -d

# Stop only monitoring services
docker compose -f docker-compose.monitoring.yml down
```

---

#### Configuration Files

```text
monitoring/
├── alertmanager/
│   ├── alertmanager.yml                      # Alertmanager routes, webhooks & Telegram configs
│   ├── webhook.py                            # Webhook script logging alerts to file
│   └── alerts.log                            # Destination log file for alerts
├── prometheus/
│   ├── prometheus.yml                        # Scrape jobs & alerting configuration
│   └── alert.rules.yml                       # Alerting rules for attacks and errors
├── loki/
│   └── loki-config.yaml                      # Loki TSDB & storage configuration
├── promtail/
│   └── promtail-config.yaml                  # Promtail Docker discovery and log forwarding config
└── grafana/
    ├── provisioning/
    │   ├── datasources/
    │   │   └── datasources.yaml              # Auto-provisions Prometheus, Loki, Alertmanager
    │   └── dashboards/
    │       └── dashboards.yaml               # Auto-provisions dashboards
    └── dashboards/
        └── tatou-overview.json               # Pre-built monitoring & logs dashboard
```

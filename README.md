# tatou
A web platform for pdf watermarking. This project is intended for pedagogical use, and contain security vulnerabilities. Do not deploy on an open network.

## Instructions

The following instructions are meant for a bash terminal on a Linux machine. If you are using something else, you will need to adapt them.

To clone the repo, you can simply run:

```bash
git clone https://github.com/nharrand/tatou-2026.git
```

Note that you should probably fork the repo and clone your own repo.


### Run python unit tests

```bash
cd tatou/server

# Create a python virtual environement
python3 -m venv .venv

# Activate your virtual environement
. .venv/bin/activate

# Install the necessary dependencies
python -m pip install -e ".[dev]"

# Run the unit tests
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python -m pytest
```

### Deploy

From the root of the directory:

```bash
# Create a file to set environement variables like passwords.
cp sample.env .env

# Edit .env and pick the passwords you want

# Generate a signing key, then paste it into SECRET_KEY in .env only.
python3 -c 'import secrets; print(secrets.token_hex(32))'

# When upgrading an existing deployment, stop and remove the old phpMyAdmin
# container before applying this configuration.
docker compose stop phpmyadmin
docker compose rm -f phpmyadmin

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

phpMyAdmin is an optional local administration tool. Start the local
administration profile explicitly with:

```bash
docker compose --profile admin up -d phpmyadmin
```

phpMyAdmin is then available only at `http://127.0.0.1:8080`.

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

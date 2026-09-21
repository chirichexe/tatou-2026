# Monitoring Stack (Prometheus + Grafana + Loki + Promtail + cAdvisor + Alertmanager)

This directory provides a lightweight monitoring, alerting, and log aggregation stack for Tatou:

- **Prometheus** (Port `9090`): Time-series database scraping metrics from containers and evaluating alert rules.
- **Alertmanager** (Port `9093`): Alert routing engine dispatching notifications for attacks and system errors.
- **Alert Logger Webhook** (Port `9095`): Lightweight receiver logging all alerts to `monitoring/alertmanager/alerts.log`.
- **Grafana** (Port `3000`): Web dashboard UI pre-configured with Loki, Prometheus, and Alertmanager datasources.
- **Loki** (Port `3100`): Log aggregation engine receiving log streams.
- **Promtail**: Daemon reading Docker container logs via Docker engine socket and forwarding them with labels to Loki and extracting alert metrics.
- **cAdvisor** (Port `8081`): Container metrics exporter providing CPU, memory, filesystem, and network statistics per container.

---

## Access URLs

| Service | URL | Default Credentials |
| :--- | :--- | :--- |
| **Grafana** | [http://localhost:3000](http://localhost:3000) | Configured via `GF_USERNAME` and `GF_PASSWORD` in `.env` |
| **Prometheus** | [http://localhost:9090](http://localhost:9090) | No auth required |
| **Alertmanager** | [http://localhost:9093](http://localhost:9093) | No auth required |
| **Loki** | [http://localhost:3100](http://localhost:3100) | No auth required |
| **cAdvisor** | [http://localhost:8081](http://localhost:8081) | No auth required |

---

## Resource Limits (CPU & RAM)

All monitoring services have strict resource limits configured in [docker-compose.monitoring.yml](file:///home/davide/uni/softsec/tatou-2026/docker-compose.monitoring.yml):

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

## Alerting: Attacchi ed Errori

Alertmanager riceve gli alert generati da Prometheus in base alle regole definite in [monitoring/prometheus/alert.rules.yml](file:///home/davide/uni/softsec/tatou-2026/monitoring/prometheus/alert.rules.yml):

### 1. Alert per Rilevamento Attacchi
- **BruteForceOrAuthAttack**: Rileva picchi di tentativi di autenticazione falliti o risposte HTTP `429 Too Many Requests` dal rate limiter.
- **PotentialDoS_HighCpu**: Utilizzo prolungato della CPU oltre l'80% su un container (possibile attacco DoS, loop o abuso computazionale).
- **PotentialDDoS_NetworkFlood**: Traffico di rete in ingresso anomalo superiore a 10 MB/s su un container.
- **HighMemoryExhaustion**: Consumo elevato di RAM (> 450MB) indicativo di tentativi di memory exhaustion (es. decompressione zip-bomb o upload massivi).

### 2. Alert per Errori di Sistema
- **ServiceDown**: Uno dei servizi essenziali (server, database, prometheus, loki, promtail, cadvisor) è irraggiungibile (`up == 0`).
- **ContainerCrashOrRestart**: Riavvio anomalo o crash improvviso di un container.
- **HighServerErrorRate**: Picco di errori HTTP 5xx o eccezioni non gestite dal server Flask.
- **DatabaseErrors**: Errori o malfunzionamenti generati dal database MariaDB.

---

## Notifiche su File (`alerts.log`)

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

## Come Collegare un Canale Telegram

Per inviare le notifiche anche su Telegram:

1. Apri [monitoring/alertmanager/alertmanager.yml](file:///home/davide/uni/softsec/tatou-2026/monitoring/alertmanager/alertmanager.yml).
2. Decommenta il blocco `telegram_configs` all'interno del receiver `file-and-telegram`:
   ```yaml
   receivers:
     - name: 'file-and-telegram'
       webhook_configs:
         - url: 'http://alert-logger:9095/alert'
           send_resolved: true
       telegram_configs:
         - bot_token: '<IL_TUO_BOT_TOKEN>'
           chat_id: <IL_TUO_CHAT_ID>
           parse_mode: 'HTML'
           send_resolved: true
           message: |
             <b>[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}</b>
             <b>Severity:</b> {{ .CommonLabels.severity }}
             <b>Target:</b> {{ or .CommonLabels.name .CommonLabels.instance .CommonLabels.compose_service "tatou" }}
             <b>Summary:</b> {{ .CommonAnnotations.summary }}
             <b>Description:</b> {{ .CommonAnnotations.description }}
   ```
3. Ricarica la configurazione di Alertmanager senza fermare i container:
   ```bash
   docker exec tatou-alertmanager kill -HUP 1
   ```

---

## How to Start & Stop

### Option 1: Together with Tatou (Default)

```bash
# Start application and monitoring stack
docker compose up -d

# View status of all containers
docker compose ps

# Stop all containers
docker compose down
```

### Option 2: Run Monitoring Stack Separately

```bash
# Start only monitoring services
docker compose -f docker-compose.monitoring.yml up -d

# Stop only monitoring services
docker compose -f docker-compose.monitoring.yml down
```

---

## Configuration Files

```text
monitoring/
├── README.md                                 # This guide
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

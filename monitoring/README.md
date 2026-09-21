# Monitoring Stack (Prometheus + Grafana + Loki + Promtail + cAdvisor)

This directory provides a lightweight monitoring and log aggregation stack for Tatou:

- **Prometheus** (Port `9090`): Time-series database scraping metrics from all containers and services.
- **Grafana** (Port `3000`): Web dashboard UI pre-configured with Loki and Prometheus data sources and an overview dashboard.
- **Loki** (Port `3100`): Log aggregation engine receiving log streams.
- **Promtail**: Daemon reading Docker container logs via the Docker engine socket and forwarding them with labels to Loki.
- **cAdvisor** (Port `8081`): Container metrics exporter providing CPU, memory, filesystem, and network statistics per container.

---

## Access URLs

| Service | URL | Default Credentials |
| :--- | :--- | :--- |
| **Grafana** | [http://localhost:3000](http://localhost:3000) | Configured via `GF_USERNAME` and `GF_PASSWORD` in `.env` |
| **Prometheus** | [http://localhost:9090](http://localhost:9090) | No auth required |
| **Loki** | [http://localhost:3100](http://localhost:3100) | No auth required |
| **cAdvisor** | [http://localhost:8081](http://localhost:8081) | No auth required |

---

## How to Start & Stop

### Option 1: Together with Tatou (Default)

`docker-compose.yml` automatically includes `docker-compose.monitoring.yml`. Starting the compose stack runs the application and monitoring together:

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

## Browsing Logs in Grafana

### 1. Pre-configured Overview Dashboard
Open [http://localhost:3000](http://localhost:3000) and go to:
- **Dashboards** > **Tatou Monitoring & Logs Overview**
  - **Live Logs Browser**: Real-time log streamer for all containers.
  - **Filter by Service**: Use the dropdown filter (`server`, `db`, `cadvisor`, `loki`, `prometheus`, etc.).
  - **Search filter**: Type any keyword (e.g. `error`, `POST`, `watermark`) to filter logs live.
  - **Container CPU & Memory**: Real-time resource usage graphs per container.

### 2. Grafana Explore (LogQL)
Open [http://localhost:3000/explore](http://localhost:3000/explore) and select the **Loki** datasource.

Useful LogQL query examples:
```logql
# Stream logs from the tatou server
{compose_service="server"}

# Stream logs from MariaDB database
{compose_service="db"}

# Search for errors across all services
{compose_service=~".+"} |= "error"

# Search for 404 or 500 status codes in server logs
{compose_service="server"} |~ "(404|500)"

# Count log rate over time
sum by (compose_service) (rate({compose_service=~".+"}[1m]))
```

---

## Configuration Files

```text
monitoring/
├── README.md                                 # This guide
├── prometheus/
│   └── prometheus.yml                        # Prometheus scrape jobs (prometheus, cadvisor, loki, promtail)
├── loki/
│   └── loki-config.yaml                      # Loki TSDB & storage configuration
├── promtail/
│   └── promtail-config.yaml                  # Promtail Docker discovery and log forwarding config
└── grafana/
    ├── provisioning/
    │   ├── datasources/
    │   │   └── datasources.yaml              # Auto-provisions Prometheus and Loki datasources
    │   └── dashboards/
    │       └── dashboards.yaml               # Auto-provisions dashboards
    └── dashboards/
        └── tatou-overview.json               # Pre-built monitoring & logs dashboard
```

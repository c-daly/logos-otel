# Claude Code Observability Stack

OTEL Collector + Prometheus + Loki + Grafana, with Grafana Cloud sync for multi-machine visibility.

```
Claude Code → OTEL Collector → Prometheus (metrics) + Loki (logs) → Grafana (local)
                             → Grafana Cloud (central, all machines)
```

## Quick Start

```bash
# 1. Set up Grafana Cloud credentials
cp ~/.claude/infra/otel/.env.example ~/.claude/infra/otel/.env
# Edit .env with your OTLP endpoint and base64-encoded auth token

# 2. Start stack and launch Claude Code
~/.claude/infra/otel/start.sh
```

## View Data

| URL | What |
|-----|------|
| http://localhost:3000 | Grafana (local dashboards, no login required) |
| http://localhost:9090 | Prometheus (raw metric queries) |
| Grafana Cloud | Central view across all machines |

A pre-built Claude Code dashboard is auto-provisioned in Grafana covering cost, tokens, tool usage, errors, and productivity.

## Ports

| Port | Service | Protocol |
|------|---------|----------|
| 4317 | OTEL Collector | gRPC (OTLP) |
| 4318 | OTEL Collector | HTTP (OTLP) |
| 8889 | OTEL Collector | Prometheus scrape |
| 9090 | Prometheus | HTTP |
| 3100 | Loki | HTTP |
| 3000 | Grafana | HTTP |

## Stop

```bash
~/.claude/infra/otel/stop.sh
```

## Data

Metrics and logs persist in Docker volumes (`prometheus-data`, `loki-data`, `grafana-data`).

Reset everything:
```bash
docker compose -f ~/.claude/infra/otel/docker-compose.yml down -v
```

## Grafana Cloud Setup

1. Sign in to [Grafana Cloud](https://grafana.com/products/cloud/)
2. Open your stack → Configure → OpenTelemetry
3. Copy the OTLP endpoint URL
4. Generate an API key
5. Base64-encode `INSTANCE_ID:API_KEY`:
   ```bash
   echo -n "123456:glc_eyJ..." | base64
   ```
6. Put both values in `.env`

Each machine running this stack pushes to the same Grafana Cloud instance. Use `host.name` resource attribute to filter by machine.

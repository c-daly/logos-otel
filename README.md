# logos-otel

Observability stack for the [LOGOS](https://github.com/c-daly/logos) cognitive architecture. Collects traces, metrics, and logs from all LOGOS services and Claude Code, stores them locally, and optionally forwards to a central Grafana Cloud instance for multi-machine visibility.

## Architecture

```
LOGOS services (sophia, hermes, apollo, ...)
Claude Code
        │
        ▼ OTLP (gRPC :4317 / HTTP :4318)
┌───────────────────┐
│   OTel Collector  │
└───────┬───────────┘
        │
        ├──► Prometheus (metrics)
        ├──► Loki (logs)
        ├──► Tempo (traces)
        └──► Grafana Cloud (metrics + logs, all machines)
                │
                ▼
           Grafana (local dashboards)
```

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2
- A [Grafana Cloud](https://grafana.com/products/cloud/) account (free tier works for a single machine; paid or self-hosted for multi-machine aggregation)
- LOGOS services configured with `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`

## Quick Start

```bash
git clone https://github.com/c-daly/logos-otel.git
cd logos-otel
cp .env.example .env
# Edit .env — see Configuration below
./start.sh
```

Grafana will be available at http://localhost:3000 (no login required in dev mode).

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
# Grafana Cloud OTLP gateway — from: Grafana Cloud → Your Stack → Configure → OpenTelemetry
GRAFANA_CLOUD_OTLP_ENDPOINT=https://otlp-gateway-prod-us-east-2.grafana.net/otlp

# base64(INSTANCE_ID:API_KEY) — generate with:
#   echo -n "INSTANCE_ID:API_KEY" | base64
GRAFANA_CLOUD_AUTH_TOKEN=your_base64_encoded_token_here

# Paths to your Claude agent-swarm plugin data (defaults work for standard installs)
AGENT_SWARM_DATA_DIR=${HOME}/.claude/plugins/agent-swarm/data
AGENT_SWARM_DASHBOARD_DIR=${HOME}/.claude/plugins/agent-swarm/dashboard/data
```

If you don't have Grafana Cloud credentials, the stack still runs fully locally — just leave the cloud vars as placeholders. The cloud pipeline will fail silently and everything else will work.

## Included Dashboards

Dashboards are auto-provisioned into Grafana on startup.

| Dashboard | What it shows |
|-----------|--------------|
| **logos-key-signals** | Top-level health across all services |
| **sophia-otel** | Sophia (cognitive core) traces and metrics |
| **hermes-otel** | Hermes (language services) traces and metrics |
| **apollo-otel** | Apollo (client layer) traces and metrics |
| **claude-code** | Claude Code session metrics (local) |
| **claude-code-cloud** | Claude Code metrics aggregated across machines |
| **agent-swarm** | Agent swarm task metrics and token usage |

## Custom Exporters

### agent-swarm-exporter

Scrapes the agent-swarm plugin's SQLite datastore and exposes metrics on `:8098`:

- Token usage by tool, agent role, and subagent type
- Session counts and average token consumption
- Task completion rates

### tool-sequence-miner

Mines tool call sequences from agent-swarm logs and exposes pattern metrics on `:8099`. Useful for understanding how agents navigate codebases.

## Ports

| Port | Service | Protocol |
|------|---------|---------|
| 4317 | OTel Collector | gRPC (OTLP) |
| 4318 | OTel Collector | HTTP (OTLP) |
| 8889 | OTel Collector | Prometheus scrape endpoint |
| 9090 | Prometheus | HTTP |
| 3100 | Loki | HTTP |
| 3200 | Tempo | HTTP |
| 3000 | Grafana | HTTP |
| 8098 | agent-swarm-exporter | Prometheus metrics |
| 8099 | tool-sequence-miner | Prometheus metrics |

## LOGOS Service Integration

Each LOGOS service reads `OTEL_EXPORTER_OTLP_ENDPOINT` at startup. Set it in each service's `.env`:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=sophia   # or hermes, apollo, etc.
OTEL_CONSOLE_EXPORT=false
```

`.env.example` files in each LOGOS repo already have these values set correctly.

## Claude Code Integration

`start.sh` exports the environment variables Claude Code needs to send telemetry:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_RESOURCE_ATTRIBUTES=host.name=$(hostname)
```

For these to persist across shell sessions, add them to your shell profile or run `source start.sh` instead of `./start.sh`.

## Multi-Machine Setup

All machines point `OTEL_EXPORTER_OTLP_ENDPOINT` at their local collector. Each collector forwards to the same Grafana Cloud endpoint. Data is tagged with `host.name` so you can filter per machine in Grafana Cloud dashboards.

## Stop / Reset

```bash
# Stop (preserves data volumes)
./stop.sh

# Stop and wipe all stored data
docker compose down -v
```

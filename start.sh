#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check .env exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
  echo "ERROR: $SCRIPT_DIR/.env not found."
  echo "Copy .env.example to .env and fill in your Grafana Cloud credentials:"
  echo "  cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
  exit 1
fi

echo "Starting OTEL stack (collector, prometheus, loki, grafana)..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d

echo ""
echo "Local Grafana:  http://localhost:3000"
echo "Prometheus:     http://localhost:9090"
echo ""

export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
export OTEL_EXPORTER_OTLP_PROTOCOL="grpc"
export OTEL_METRICS_EXPORTER="otlp"
export OTEL_LOGS_EXPORTER="otlp"
export OTEL_RESOURCE_ATTRIBUTES="host.name=$(hostname)"

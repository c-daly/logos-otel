#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Stopping OTEL stack..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" down

echo "Done. OTEL env vars are not affected in other shells."

#!/usr/bin/env bash
# Stops everything and, if -v/--volumes is passed, wipes the Ollama model
# cache and LocalStack state too.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "-v" || "${1:-}" == "--volumes" ]]; then
  echo "==> Stopping the stack and deleting volumes (Ollama models, LocalStack state)..."
  docker compose down -v
else
  echo "==> Stopping the stack (models/state preserved -- pass -v to wipe them too)..."
  docker compose down
fi

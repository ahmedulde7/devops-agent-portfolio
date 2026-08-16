#!/usr/bin/env bash
# Brings up Ollama + LocalStack + the agent via docker-compose, waits for
# everything to be healthy, and pulls the Ollama model.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || { echo "docker is required"; exit 1; }
docker compose version >/dev/null || { echo "docker compose v2 is required"; exit 1; }

echo "==> Building the agent image and starting the stack..."
docker compose up -d --build ollama localstack

echo "==> Waiting for Ollama and LocalStack to report healthy..."
for svc in ollama localstack; do
  for _ in $(seq 1 60); do
    status=$(docker compose ps -q "$svc" | xargs -r docker inspect -f '{{.State.Health.Status}}' 2>/dev/null || echo "starting")
    [ "$status" = "healthy" ] && { echo "  $svc: healthy"; break; }
    sleep 3
  done
done

echo "==> Pulling the Ollama model (this can take a while the first time)..."
docker compose run --rm ollama-pull

echo "==> Starting the agent..."
docker compose up -d agent

echo
echo "Ready. Chat UI:   http://localhost:8000"
echo "        LocalStack: http://localhost:4566"
echo "        Ollama:     http://localhost:11434"
echo
echo "Try:  ./scripts/demo.sh"

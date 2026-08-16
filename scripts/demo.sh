#!/usr/bin/env bash
# Sends the example "deploy an S3 bucket" request straight to the running
# agent API and pretty-prints the result. Requires ./scripts/setup.sh to
# have been run first (or the stack to already be up).
set -euo pipefail

REQUEST="${1:-I want to deploy an S3 bucket for storing build artifacts}"
AGENT_URL="${AGENT_URL:-http://localhost:8000}"

echo "==> Request: $REQUEST"
echo "==> Sending to $AGENT_URL/api/chat ..."
echo

curl -sf -X POST "$AGENT_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"message": sys.argv[1]}))' "$REQUEST")" \
  | python3 -c '
import json, sys
data = json.load(sys.stdin)
print(f"Status: {data[\"status\"]}\n")
print(data["response"])
'

#!/usr/bin/env bash
# Deploys the same stack to a local k3s/k3d cluster instead of docker-compose.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v kubectl >/dev/null || { echo "kubectl is required"; exit 1; }

echo "==> Building the agent image..."
docker build -t devops-agent:local .

if command -v k3d >/dev/null; then
  echo "==> Importing the image into the k3d cluster..."
  k3d image import devops-agent:local
else
  echo "==> k3d not found -- if you're on a different k3s setup, make sure"
  echo "    devops-agent:local is reachable by your cluster's container runtime"
  echo "    (e.g. 'ctr image import' for containerd, or push to a registry"
  echo "    and update the image field in k3s/agent.yaml)."
fi

echo "==> Applying manifests..."
kubectl apply -f k3s/namespace.yaml
kubectl apply -f k3s/ollama.yaml
kubectl apply -f k3s/localstack.yaml
kubectl apply -f k3s/agent.yaml

echo "==> Waiting for ollama and localstack to be ready..."
kubectl -n devops-agent rollout status deployment/ollama --timeout=180s
kubectl -n devops-agent rollout status deployment/localstack --timeout=180s

echo "==> Pulling the Ollama model (job)..."
kubectl -n devops-agent delete job ollama-pull --ignore-not-found
kubectl apply -f k3s/ollama.yaml
kubectl -n devops-agent wait --for=condition=complete job/ollama-pull --timeout=600s

echo "==> Waiting for the agent to be ready..."
kubectl -n devops-agent rollout status deployment/agent --timeout=180s

echo
echo "Ready. Chat UI: http://localhost:30080 (or 'kubectl -n devops-agent port-forward svc/agent 8000:8000')"

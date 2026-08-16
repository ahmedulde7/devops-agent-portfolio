FROM python:3.11-slim

# Real terraform binary (not the fake_terraform.py test double) via
# HashiCorp's official apt repo.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip gnupg ca-certificates && \
    curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(. /etc/os-release && echo \"$VERSION_CODENAME\") main" \
        | tee /etc/apt/sources.list.d/hashicorp.list && \
    apt-get update && apt-get install -y terraform && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/
COPY mcp_server/ ./mcp_server/

RUN mkdir -p /app/terraform/workspaces

ENV TERRAFORM_WORKSPACES_DIR=/app/terraform/workspaces \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "agent.web.app:app", "--host", "0.0.0.0", "--port", "8000"]

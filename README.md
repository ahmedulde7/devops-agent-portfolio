# DevOps Agent -- LangGraph + Ollama + Terraform + AWS MCP + Checkov CIS gate

A fully local, offline-capable agent that turns a natural-language infra
request ("I want to deploy an S3 bucket") into real, CIS-hardened Terraform,
applies it to LocalStack, and verifies the result live -- self-remediating
any compliance gaps it finds along the way, with zero cloud spend and zero
outbound network dependency once the local model is pulled.

```
"I want to deploy an S3 bucket"
        │
        ▼
 ┌─────────────┐   local LLM, JSON-only     ┌────────────────────┐
 │ parse_intent│ ─────────────────────────► │ Ollama (llama3.1)  │
 └──────┬──────┘                            └────────────────────┘
        ▼
 ┌────────────────────┐
 │ generate_terraform  │  Jinja2 → CIS-aligned HCL (S3 + logging bucket)
 └──────────┬──────────┘
        ▼
 ┌────────────┐   MCP tool call      ┌───────────────────────────┐
 │  cis_scan   │ ───────────────────►│ MCP server: checkov_cis_scan│
 └──────┬─────┘                      └───────────────────────────┘
    fail│  pass
        ▼    └───────────────────────────────┐
 ┌────────────┐                               ▼
 │ remediate  │                     ┌───────────────────┐   MCP tool call
 │ (rule-based│                     │  terraform_apply    │ ───────────► terraform → LocalStack
 │  fix, loop │                     └──────────┬─────────┘
 │  back)     │                                ▼
 └──────┬─────┘                     ┌───────────────────┐   MCP tool call
        └─── back to generate_terraform    │  verify_live        │ ───────────► s3_get_bucket_details
                                    └──────────┬─────────┘
                                               ▼
                                          final report
```

## Why this exists

This is a portfolio project built to demonstrate an "agentic DevOps"
workflow end to end, not just describe one: a real LangGraph state machine,
a real [MCP](https://modelcontextprotocol.io) server exposing Terraform and
AWS tools, a real static CIS-aligned compliance gate (via
[Checkov](https://www.checkov.io)), and a real live-verification step after
apply -- all runnable entirely on a laptop against
[LocalStack](https://localstack.cloud), with no AWS account and no API
costs (the LLM is [Ollama](https://ollama.com), running locally).

## Architecture

| Component | Role |
|---|---|
| **LangGraph** (`agent/graph.py`) | The state machine: parse intent → generate Terraform → CIS scan → remediate (loop) → apply → live-verify → respond. |
| **Ollama** (`agent/llm.py`) | Local LLM. Its *only* job is turning free text into a structured `{resource_type, bucket_name, region}` intent -- it never decides security posture. |
| **MCP server** (`mcp_server/server.py`) | A real [MCP](https://modelcontextprotocol.io) server (stdio transport, official `mcp` Python SDK) exposing `terraform_init/apply/destroy`, `checkov_cis_scan`, and `s3_list_buckets` / `s3_get_bucket_details`. Every infrastructure-touching action goes through this one process. |
| **Terraform** (`agent/templates/*.tf.j2`) | Jinja2-rendered HCL for an S3 bucket + hardened log-target bucket, pointed at LocalStack via provider endpoint overrides. |
| **Checkov** (`agent/cis_rules.py`, `agent/cis_mapping.yaml`) | Static, offline CIS-aligned compliance gate that runs *before* apply. The mapping file curates which Checkov check IDs correspond to which CIS AWS Foundations Benchmark control, and which Terraform field to flip to fix each one. |
| **LocalStack** | Emulates the AWS S3 API locally -- no AWS account, no cost. |
| **FastAPI + vanilla JS UI** (`agent/web/`) | A small chat UI to drive the agent from a browser (`/`), plus `POST /api/chat`. |
| **CLI** (`agent/cli.py`) | `python -m agent.cli "..."` for terminal use. |

### Design choices worth calling out

- **The LLM never decides security settings.** `parse_intent` extracts *what* resource and *what name/region* -- `versioning`, `encryption`, `block_public_access`, `logging`, `enforce_ssl` are deterministic policy defaults in code. The remediation loop is a lookup (`agent/cis_mapping.yaml`: Checkov ID → Terraform field → value), not a free-form LLM edit of security-relevant infrastructure code.
- **Two independent correctness signals.** Checkov statically scans the rendered HCL *before* `terraform apply` (shift-left). After apply, `verify_live` makes a *separate* live `boto3` call through the MCP server to confirm LocalStack actually has what was asked for. A static assumption and a live API response disagreeing would itself be worth surfacing.
- **Everything infrastructure-touching goes through MCP.** The graph never shells out to `terraform` or calls `boto3` directly -- it only ever calls MCP tools. That's what makes the tool surface reusable by other agents/clients and inspectable independently of the graph.
- **The remediation loop is bounded and auditable** (`max_remediation_attempts` in `agent/cis_mapping.yaml`, default 3) and every pass is recorded in the state's `events` timeline, shown in the final report.

## Quickstart (docker-compose)

Requires Docker with Compose v2. Pulls the Ollama model on first run, which
can take a few minutes.

```bash
./scripts/setup.sh          # brings up ollama + localstack + agent, pulls the model
./scripts/demo.sh           # sends the example "deploy an S3 bucket" request
# or open http://localhost:8000 for the chat UI
./scripts/teardown.sh       # stop everything (add -v to also wipe the model cache)
```

Or with `make`: `make up`, `make demo`, `make down`.

## Quickstart (k3s)

```bash
./scripts/run_k3s.sh
kubectl -n devops-agent port-forward svc/agent 8000:8000   # if NodePort 30080 isn't reachable
```

Manifests live in `k3s/`: `namespace.yaml`, `ollama.yaml` (Deployment + PVC
+ a one-shot model-pull Job), `localstack.yaml`, `agent.yaml`.

## Quickstart (CLI, no UI)

```bash
pip install -r requirements.txt
python -m agent.cli "I want to deploy an S3 bucket for storing build artifacts"
```

(Needs `terraform`, a running LocalStack on `:4566`, and Ollama on `:11434`
-- or run inside the docker-compose network.)

## Example: "I want to deploy an S3 bucket"

See [`examples/example_transcript.md`](examples/example_transcript.md) for a
full, real, captured run -- intent parsing, the CIS gate catching two real
gaps (no versioning, no access logging) on the first draft, a
self-remediation pass, a passing re-scan, `terraform apply`, and live
post-apply verification. The generated Terraform for that run is saved
alongside it as `generated_main.tf.sample`.

## Testing

```bash
pip install -r requirements.txt
python -m playwright install chromium
pytest tests/ -v --browser chromium
```

18 tests, all runnable with **no Docker, no Ollama, and no `terraform`
binary** -- which matters because that's exactly the constraint this
project was developed under (see "What was and wasn't tested where" below):

| File | What it proves | How, without real infra |
|---|---|---|
| `test_terraform_gen.py` | Jinja2 templates render syntactically valid HCL | Real `python-hcl2` parser, no infra needed |
| `test_cis_scan.py` | The CIS gate correctly fails an insecure bucket and passes a hardened one | Real Checkov binary (genuinely offline-capable) |
| `test_intent.py` | JSON extraction and bucket-name sanitization are robust to messy LLM output | Pure functions, no LLM needed |
| `test_mcp_server.py` | The real MCP server (stdio JSON-RPC) correctly dispatches Terraform + AWS + Checkov tools, and a full apply→verify→destroy cycle works | Real MCP transport; `TERRAFORM_BIN` points at `tests/fixtures/fake_terraform.py` (see below); AWS calls hit a real local `moto` S3 server |
| `test_graph_flow.py` | The full LangGraph state machine, **including the remediation loop**, works end to end | LangChain's own `FakeListChatModel` in place of Ollama; same Terraform/AWS doubles as above |
| `test_web_ui_playwright.py` | The actual FastAPI + HTML/JS chat UI works in a real browser: sending a message, rendering the compliance table, showing the right status badge | Real Chromium via Playwright, driving a real `uvicorn` subprocess; same doubles |

### What was and wasn't tested where

This project was authored in a sandboxed environment with **no Docker
image pulls, no `terraform` binary download, and no LocalStack/Ollama
daemons available** (all blocked by network egress rules there). Rather
than skip testing the orchestration logic, every piece that doesn't
strictly require those binaries is exercised for real:

- **Checkov** is a genuinely offline-capable static analyzer -- the CIS gate
  tests run the real thing, not a mock.
- **The MCP server** runs for real over real stdio JSON-RPC in every test
  that touches it.
- **AWS calls** go to a real local `moto` server (`python -m moto.server`),
  which speaks the same S3 API LocalStack does -- same `boto3` client code
  path, different backend.
- **Terraform** itself is the one binary this sandbox couldn't obtain.
  `tests/fixtures/fake_terraform.py` is a terraform-CLI-compatible stand-in:
  it parses the *same* rendered HCL with the *same* `python-hcl2` parser
  the rest of the project uses, and really creates/configures the bucket
  against the `moto` endpoint via `boto3` -- so the post-apply live
  verification step observes genuine, freshly-created state, not a canned
  fixture. Swapping `TERRAFORM_BIN=terraform` is the only difference
  between this and production; nothing in `agent/` or `mcp_server/`
  branches on which one is running.
- **Ollama** is swapped for LangChain's own `FakeListChatModel` in tests --
  a first-party testing utility, not a hand-rolled mock -- so intent parsing
  is deterministic while everything downstream of it (routing, remediation,
  apply, verification) runs for real.

`.github/workflows/ci.yml` includes a `localstack-e2e` job that runs the
real MCP server against a real `localstack/localstack` service container
with the real `terraform` binary (via `hashicorp/setup-terraform`) -- this
closes the one gap the dev sandbox couldn't, in an environment that isn't
network-restricted the same way.

## Project layout

```
agent/
  graph.py            LangGraph state machine + routing
  llm.py               Ollama / FakeListChatModel backend selection
  intent.py            JSON extraction, bucket-name sanitization, the intent-parsing prompt
  terraform_gen.py     Jinja2 → HCL rendering
  cis_rules.py          Checkov invocation + CIS mapping evaluation
  cis_mapping.yaml     Checkov check ID -> CIS control -> remediation field (edit this to add controls)
  mcp_client.py         Async MCP client the graph uses to reach the tool server
  report.py             Final markdown report renderer
  cli.py                Terminal entry point
  templates/            provider.tf.j2, s3_bucket.tf.j2
  web/                   FastAPI app + static chat UI
mcp_server/
  server.py             The MCP server: terraform / checkov / AWS tools
terraform/workspaces/    Generated per-request Terraform (gitignored)
k3s/                     Kubernetes manifests (namespace, ollama, localstack, agent)
scripts/                 setup.sh / demo.sh / teardown.sh / run_k3s.sh
tests/                   18 tests, see table above
tests/fixtures/          fake_terraform.py -- the test-only terraform stand-in
examples/                A real captured example run + generated Terraform sample
.github/workflows/ci.yml Lint, unit+MCP+Playwright tests, terraform validate, LocalStack e2e
```

## Extending it

The agent currently supports exactly one resource type (S3 bucket) by
design -- narrow scope, but every layer done properly, rather than broad
and shallow. To add another resource type:

1. Add a Jinja2 template under `agent/templates/`.
2. Add a `render_<resource>()` function in `agent/terraform_gen.py`.
3. Add the relevant Checkov check IDs + CIS control refs + remediation
   fields to `agent/cis_mapping.yaml`.
4. Add an `elif` branch in `generate_terraform` (`agent/graph.py`) keyed off
   `intent["resource_type"]`, and extend the `parse_intent` system prompt in
   `agent/intent.py` to recognize the new resource type.
5. Add an AWS introspection tool to `mcp_server/server.py` for the live
   verification step.

## License

MIT -- see `LICENSE`.

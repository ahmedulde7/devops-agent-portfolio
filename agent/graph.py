"""The LangGraph state machine.

    parse_intent --(unsupported)--> respond_unsupported --> END
        |
        v (supported)
    generate_terraform --> cis_scan --(failed, attempts left)--> remediate -+
        ^                     |                                            |
        |                     +--(passed OR attempts exhausted)--+         |
        +-------------------------------------------------------(loop)-----+
                                                                  v
                                                          terraform_apply
                                                          /              \\
                                                  (apply failed)     (apply ok)
                                                       |                  |
                                                       v                  v
                                                    respond          verify_live
                                                       ^                  |
                                                       +------------------+

Every node returns a *partial* state update; LangGraph merges it back into
the running AgentState. Nodes that need the LLM or the MCP tool client pull
them from `config["configurable"]` rather than module-level globals, which
is what makes the whole graph safe to reuse across concurrent requests (the
compiled graph object is stateless; only the per-run config carries live
dependencies).
"""

from __future__ import annotations

import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from agent.cis_rules import load_mapping
from agent.intent import (
    INTENT_SYSTEM_PROMPT,
    extract_json_object,
    new_request_id,
    sanitize_bucket_name,
)
from agent.state import AgentState, ResourceIntent
from agent.terraform_gen import render_s3_bucket, write_workspace


def _workspaces_dir() -> str:
    # Read lazily (not at import time) so tests/CLI/web app can override
    # TERRAFORM_WORKSPACES_DIR per-process without needing to reload the module.
    return os.environ.get(
        "TERRAFORM_WORKSPACES_DIR",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "terraform",
            "workspaces",
        ),
    )


def _log(state: AgentState, step: str, message: str) -> list:
    events = list(state.get("events", []))
    events.append({"step": step, "message": message})
    return events


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


async def parse_intent(state: AgentState, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]
    messages = [
        SystemMessage(content=INTENT_SYSTEM_PROMPT),
        HumanMessage(content=state["user_request"]),
    ]
    response = await llm.ainvoke(messages)
    parsed = extract_json_object(response.content)

    if not parsed or not parsed.get("is_supported"):
        events = _log(
            state,
            "parse_intent",
            f"Request not recognized as a supported resource. Model said: {response.content!r}",
        )
        return {"status": "unsupported_request", "events": events}

    bucket_name = sanitize_bucket_name(
        parsed.get("bucket_name") or "devops-agent-bucket", state["request_id"]
    )
    intent: ResourceIntent = {
        "resource_type": "s3_bucket",
        "bucket_name": bucket_name,
        "region": parsed.get("region") or "us-east-1",
        # Deliberately conservative-but-incomplete starting point: public
        # access is closed and encryption is on from the first draft (never
        # ship an open or unencrypted bucket, even briefly). Versioning,
        # access logging and SSL-enforcement start off so the CIS gate below
        # has something real to catch and the remediation loop has
        # something real to do -- note that Checkov's default-encryption
        # check (CKV_AWS_19) passes even with encryption="" here, since AWS
        # applies SSE-S3 by default; we set it explicitly anyway so the
        # *live* post-apply verification (which talks to the real endpoint,
        # not a static assumption) reports a concrete algorithm too.
        "versioning": False,
        "encryption": "AES256",
        "block_public_access": True,
        "logging": False,
        "enforce_ssl": False,
        "tags": {"Project": "devops-agent", "RequestId": state["request_id"]},
    }
    events = _log(
        state,
        "parse_intent",
        f"Parsed intent -> bucket '{bucket_name}' in {intent['region']}. {parsed.get('notes', '')}".strip(),
    )
    return {"intent": intent, "events": events, "status": "in_progress"}


async def generate_terraform(state: AgentState, config: RunnableConfig) -> dict:
    intent = state["intent"]
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
    files = render_s3_bucket(
        intent,
        request_id=state["request_id"],
        user_request=state["user_request"],
        localstack_endpoint=endpoint,
    )
    workspace_dir = write_workspace(_workspaces_dir(), state["request_id"], files)
    attempt = state.get("remediation_attempts", 0) + 1
    events = _log(
        state,
        "generate_terraform",
        f"Rendered Terraform to {workspace_dir} (pass {attempt}).",
    )
    return {"workspace_dir": workspace_dir, "terraform_files": files, "events": events}


async def cis_scan(state: AgentState, config: RunnableConfig) -> dict:
    client = config["configurable"]["client"]
    result = await client.checkov_cis_scan(state["workspace_dir"])
    findings = result["findings"]
    passed = result["passed"]
    failed = [f for f in findings if not f["passed"]]

    if passed:
        message = f"CIS gate PASSED ({len(findings)} controls evaluated)."
    else:
        summary = "; ".join(f"{f['checkov_id']} {f['title']}" for f in failed)
        message = f"CIS gate found {len(failed)} failing control(s): {summary}"

    events = _log(state, "cis_scan", message)
    return {
        "compliance_findings": findings,
        "compliance_passed": passed,
        "events": events,
    }


async def remediate(state: AgentState, config: RunnableConfig) -> dict:
    intent = dict(state["intent"])
    changed: set[str] = set()
    for finding in state.get("compliance_findings", []):
        if finding["passed"]:
            continue
        field = finding.get("remediation_field")
        if field:
            intent[field] = finding.get("remediation_value")
            changed.add(field)

    attempts = state.get("remediation_attempts", 0) + 1
    events = _log(
        state,
        "remediate",
        f"Auto-remediation pass {attempts}: updated {sorted(changed) or 'nothing (no fixable field found)'}.",
    )
    return {"intent": intent, "remediation_attempts": attempts, "events": events}


async def terraform_apply(state: AgentState, config: RunnableConfig) -> dict:
    client = config["configurable"]["client"]
    result = await client.terraform_apply(state["workspace_dir"])

    if result.get("success"):
        events = _log(
            state,
            "terraform_apply",
            f"terraform apply succeeded. Outputs: {result.get('outputs')}",
        )
        return {"apply_result": result, "events": events}

    events = _log(
        state,
        "terraform_apply",
        f"terraform apply FAILED in phase '{result.get('phase')}': {str(result.get('stderr', ''))[:500]}",
    )
    return {"apply_result": result, "events": events, "status": "failed"}


async def verify_live(state: AgentState, config: RunnableConfig) -> dict:
    client = config["configurable"]["client"]
    bucket_name = state["intent"]["bucket_name"]
    details = await client.s3_get_bucket_details(bucket_name)
    events = _log(state, "verify_live", f"Live AWS-endpoint verification: {details}")
    return {"verification": details, "events": events}


async def respond(state: AgentState, config: RunnableConfig) -> dict:
    from agent.report import (
        render_summary,
    )  # local import avoids a cycle at module load

    status = "completed" if state.get("apply_result", {}).get("success") else "failed"
    summary = render_summary(state, status)
    events = _log(state, "respond", "Composed final summary.")
    return {"final_response": summary, "status": status, "events": events}


async def respond_unsupported(state: AgentState, config: RunnableConfig) -> dict:
    message = (
        "I can currently only provision one kind of resource -- an S3 bucket on LocalStack "
        "via Terraform -- and I didn't recognize this request as that. Try something like "
        '"deploy an S3 bucket for build artifacts".'
    )
    events = _log(state, "respond_unsupported", "Returned unsupported-request message.")
    return {
        "final_response": message,
        "status": "unsupported_request",
        "events": events,
    }


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def _route_after_intent(state: AgentState) -> str:
    return (
        "respond_unsupported"
        if state.get("status") == "unsupported_request"
        else "generate_terraform"
    )


def _route_after_scan(state: AgentState) -> str:
    if state.get("compliance_passed"):
        return "terraform_apply"
    max_attempts = state.get("max_remediation_attempts", 3)
    if state.get("remediation_attempts", 0) >= max_attempts:
        return "terraform_apply"  # proceed best-effort; unresolved findings are reported
    return "remediate"


def _route_after_apply(state: AgentState) -> str:
    return "respond" if state.get("status") == "failed" else "verify_live"


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("parse_intent", parse_intent)
    graph.add_node("generate_terraform", generate_terraform)
    graph.add_node("cis_scan", cis_scan)
    graph.add_node("remediate", remediate)
    graph.add_node("terraform_apply", terraform_apply)
    graph.add_node("verify_live", verify_live)
    graph.add_node("respond", respond)
    graph.add_node("respond_unsupported", respond_unsupported)

    graph.set_entry_point("parse_intent")
    graph.add_conditional_edges(
        "parse_intent",
        _route_after_intent,
        {
            "generate_terraform": "generate_terraform",
            "respond_unsupported": "respond_unsupported",
        },
    )
    graph.add_edge("generate_terraform", "cis_scan")
    graph.add_conditional_edges(
        "cis_scan",
        _route_after_scan,
        {"remediate": "remediate", "terraform_apply": "terraform_apply"},
    )
    graph.add_edge("remediate", "generate_terraform")
    graph.add_conditional_edges(
        "terraform_apply",
        _route_after_apply,
        {"verify_live": "verify_live", "respond": "respond"},
    )
    graph.add_edge("verify_live", "respond")
    graph.add_edge("respond", END)
    graph.add_edge("respond_unsupported", END)

    return graph.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


async def run_agent(user_request: str, llm, mcp_env: dict | None = None) -> AgentState:
    """Entry point used by both the CLI and the FastAPI web app."""
    from agent.mcp_client import McpToolClient

    mapping = load_mapping()
    init_state: AgentState = {
        "user_request": user_request,
        "request_id": new_request_id(),
        "remediation_attempts": 0,
        "max_remediation_attempts": mapping.get("max_remediation_attempts", 3),
        "events": [],
        "status": "in_progress",
    }

    async with McpToolClient(env=mcp_env) as client:
        config: RunnableConfig = {"configurable": {"llm": llm, "client": client}}
        final_state = await get_graph().ainvoke(init_state, config=config)

    return final_state

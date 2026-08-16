"""Renders the final human-readable report shown in the chat UI / CLI."""

from __future__ import annotations

from agent.state import AgentState


def _severity_icon(severity: str) -> str:
    return {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}.get(severity, "⚪")


def render_summary(state: AgentState, status: str) -> str:
    lines: list[str] = []
    intent = state.get("intent", {})
    bucket_name = intent.get("bucket_name", "unknown")

    if status == "completed":
        lines.append(f"## ✅ Deployed `{bucket_name}` on LocalStack\n")
    else:
        lines.append(f"## ❌ Deployment failed for `{bucket_name}`\n")

    lines.append(f"**Request:** \"{state.get('user_request', '')}\"  ")
    lines.append(f"**Region:** {intent.get('region', 'us-east-1')}  ")
    lines.append(f"**Remediation passes:** {state.get('remediation_attempts', 0)}\n")

    findings = state.get("compliance_findings", [])
    if findings:
        lines.append("### CIS-aligned compliance gate (Checkov)\n")
        lines.append(
            "Each control is evaluated per-resource, so the primary bucket and its log-target bucket each get their own row.\n"
        )
        lines.append("| Control | CIS ref | Resource | Check | Result |")
        lines.append("|---|---|---|---|---|")
        for f in findings:
            result = "✅ PASS" if f["passed"] else f"{_severity_icon(f['severity'])} FAIL"
            lines.append(
                f"| `{f['checkov_id']}` | {f['cis_control']} | `{f['resource']}` | {f['title']} | {result} |"
            )
        lines.append("")

    apply_result = state.get("apply_result", {})
    if apply_result:
        lines.append("### Terraform apply\n")
        if apply_result.get("success"):
            outputs = apply_result.get("outputs", {})
            lines.append("- **Status:** success")
            for k, v in outputs.items():
                lines.append(f"- **{k}:** `{v}`")
        else:
            lines.append(f"- **Status:** failed in phase `{apply_result.get('phase')}`")
            stderr = str(apply_result.get("stderr", ""))[:800]
            if stderr:
                lines.append(f"- **Error:**\n```\n{stderr}\n```")
        lines.append("")

    verification = state.get("verification")
    if verification:
        lines.append("### Live verification (AWS MCP tool, post-apply)\n")
        lines.append(f"- **Exists on endpoint:** {verification.get('exists')}")
        lines.append(f"- **Versioning:** {verification.get('versioning')}")
        lines.append(f"- **Encryption:** {verification.get('encryption')}")
        pab = verification.get("public_access_block") or {}
        lines.append(f"- **Public access blocked:** {bool(pab.get('BlockPublicAcls'))}")
        lines.append(f"- **Access logging enabled:** {verification.get('logging_enabled')}")
        lines.append("")

    events = state.get("events", [])
    if events:
        lines.append("### Agent timeline\n")
        for e in events:
            lines.append(f"- **{e['step']}:** {e['message']}")

    return "\n".join(lines)

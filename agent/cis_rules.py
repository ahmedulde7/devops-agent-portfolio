"""CIS-aligned compliance gate for generated Terraform, backed by Checkov.

Design choice: run Checkov as a subprocess against the rendered `.tf` files
*before* anything is applied (a "shift-left" gate), rather than only
inspecting the resource after the fact. A second, live check against the
actual LocalStack resource happens post-apply via the AWS MCP tool
(agent/tool_client.py) to catch drift between "what we asked for" and "what
actually exists" -- static analysis and live verification are deliberately
two separate signals.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from agent.state import ComplianceFinding

MAPPING_PATH = Path(__file__).parent / "cis_mapping.yaml"


def load_mapping() -> dict[str, Any]:
    with open(MAPPING_PATH) as fh:
        return yaml.safe_load(fh)


def _checkov_ids(mapping: dict[str, Any]) -> list[str]:
    return [c["checkov_id"] for c in mapping["checks"]]


def run_checkov(workspace_dir: str, mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run checkov against workspace_dir and return the raw parsed JSON report."""
    mapping = mapping or load_mapping()
    check_ids = ",".join(_checkov_ids(mapping))

    cmd = [
        sys.executable,
        "-m",
        "checkov.main",
        "-d",
        workspace_dir,
        "--framework",
        "terraform",
        "-o",
        "json",
        "--compact",
        "--skip-download",
        "-c",
        check_ids,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # checkov exits non-zero when it finds failed checks -- that's expected,
    # not a tool failure. A tool failure is when stdout isn't valid JSON.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"checkov did not return valid JSON (exit={proc.returncode}): "
            f"{proc.stdout[:2000]}\nstderr: {proc.stderr[:2000]}"
        ) from exc
    return report


def evaluate(workspace_dir: str, mapping: dict[str, Any] | None = None) -> list[ComplianceFinding]:
    """Run checkov and translate the report into CIS-flavored findings."""
    mapping = mapping or load_mapping()
    by_id = {c["checkov_id"]: c for c in mapping["checks"]}

    report = run_checkov(workspace_dir, mapping)
    results = report.get("results", {}) if isinstance(report, dict) else {}

    findings: list[ComplianceFinding] = []

    for bucket, passed in (("passed_checks", True), ("failed_checks", False)):
        for item in results.get(bucket, []) or []:
            control = by_id.get(item["check_id"])
            if not control:
                continue
            findings.append(
                ComplianceFinding(
                    checkov_id=item["check_id"],
                    cis_control=control["cis_control"],
                    title=control["title"],
                    severity=control["severity"],
                    passed=passed,
                    resource=item.get("resource", "unknown"),
                    remediation_field=control.get("remediation_field"),
                    remediation_value=control.get("remediation_value"),
                )
            )

    # A curated check that never fired (e.g. resource type not present yet)
    # is neither a pass nor a fail worth reporting -- checkov simply has
    # nothing to evaluate it against on this pass.
    return findings


def all_passed(findings: list[ComplianceFinding]) -> bool:
    return all(f["passed"] for f in findings) if findings else False

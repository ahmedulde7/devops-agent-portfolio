"""The 'AWS MCP' + Terraform MCP server the agent talks to.

This is a real Model Context Protocol server (using the official `mcp`
Python SDK, stdio transport) -- not a Python function call dressed up as a
tool. The LangGraph agent never shells out to `terraform` or calls `boto3`
directly; every infrastructure-touching action goes through one of the tools
below, which gives us a single, auditable choke point for anything that
mutates real (or LocalStack-emulated) AWS state.

Run standalone for manual testing:
    python -m mcp_server.server

Environment variables:
    TERRAFORM_BIN        path to a terraform-compatible binary (default: "terraform")
    AWS_ENDPOINT_URL      LocalStack (or moto server) endpoint (default: http://localhost:4566)
    AWS_ACCESS_KEY_ID      dummy creds are fine against LocalStack/moto (default: "test")
    AWS_SECRET_ACCESS_KEY  (default: "test")
    AWS_DEFAULT_REGION     (default: "us-east-1")
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Make the sibling `agent` package importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.exceptions import ClientError
from mcp.server.fastmcp import FastMCP

from agent.cis_rules import all_passed, evaluate

mcp = FastMCP("devops-agent-tools")

TERRAFORM_BIN = os.environ.get("TERRAFORM_BIN", "terraform")
TERRAFORM_TIMEOUT = int(os.environ.get("TERRAFORM_TIMEOUT", "300"))


def _run(cmd: list[str], cwd: str, timeout: int = TERRAFORM_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _tail(text: str, n: int = 4000) -> str:
    return text[-n:] if text else text


# --------------------------------------------------------------------------
# Terraform tools
# --------------------------------------------------------------------------


@mcp.tool()
def terraform_init(workspace_dir: str) -> dict[str, Any]:
    """Run `terraform init` in workspace_dir."""
    proc = _run([TERRAFORM_BIN, "init", "-input=false"], cwd=workspace_dir)
    return {
        "success": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": _tail(proc.stdout),
        "stderr": _tail(proc.stderr),
    }


@mcp.tool()
def terraform_plan(workspace_dir: str) -> dict[str, Any]:
    """Run `terraform init` then `terraform plan` in workspace_dir."""
    init = terraform_init(workspace_dir)
    if not init["success"]:
        return {"success": False, "phase": "init", **init}
    proc = _run([TERRAFORM_BIN, "plan", "-input=false", "-no-color"], cwd=workspace_dir)
    return {
        "success": proc.returncode == 0,
        "phase": "plan",
        "stdout": _tail(proc.stdout),
        "stderr": _tail(proc.stderr),
    }


@mcp.tool()
def terraform_apply(workspace_dir: str) -> dict[str, Any]:
    """Run `terraform init` + `terraform apply -auto-approve` in workspace_dir.

    Returns parsed `terraform output -json` on success so callers get the
    real bucket name/ARN LocalStack assigned rather than guessing them.
    """
    init = terraform_init(workspace_dir)
    if not init["success"]:
        return {"success": False, "phase": "init", **init}

    apply = _run(
        [TERRAFORM_BIN, "apply", "-auto-approve", "-input=false", "-no-color"],
        cwd=workspace_dir,
    )
    if apply.returncode != 0:
        return {
            "success": False,
            "phase": "apply",
            "stdout": _tail(apply.stdout),
            "stderr": _tail(apply.stderr),
        }

    outputs: dict[str, Any] = {}
    out_proc = _run([TERRAFORM_BIN, "output", "-json"], cwd=workspace_dir)
    if out_proc.returncode == 0:
        try:
            raw = json.loads(out_proc.stdout)
            outputs = {k: v.get("value") for k, v in raw.items()}
        except (json.JSONDecodeError, AttributeError):
            outputs = {}

    return {
        "success": True,
        "phase": "apply",
        "outputs": outputs,
        "stdout": _tail(apply.stdout),
    }


@mcp.tool()
def terraform_destroy(workspace_dir: str) -> dict[str, Any]:
    """Run `terraform destroy -auto-approve` in workspace_dir (teardown helper)."""
    proc = _run(
        [TERRAFORM_BIN, "destroy", "-auto-approve", "-input=false", "-no-color"],
        cwd=workspace_dir,
    )
    return {
        "success": proc.returncode == 0,
        "stdout": _tail(proc.stdout),
        "stderr": _tail(proc.stderr),
    }


# --------------------------------------------------------------------------
# CIS / Checkov tool
# --------------------------------------------------------------------------


@mcp.tool()
def checkov_cis_scan(workspace_dir: str) -> dict[str, Any]:
    """Static CIS-aligned compliance scan of the rendered Terraform (pre-apply gate)."""
    findings = evaluate(workspace_dir)
    return {"passed": all_passed(findings), "findings": findings}


# --------------------------------------------------------------------------
# AWS (LocalStack) introspection tools -- the "AWS MCP" surface
# --------------------------------------------------------------------------


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


@mcp.tool()
def s3_list_buckets() -> list[str]:
    """List every bucket currently visible on the configured AWS endpoint."""
    client = _s3_client()
    return [b["Name"] for b in client.list_buckets().get("Buckets", [])]


@mcp.tool()
def s3_get_bucket_details(bucket_name: str) -> dict[str, Any]:
    """Live post-apply verification: what does the endpoint actually report for this bucket?

    This is deliberately separate from the pre-apply Checkov scan -- it is
    the second, independent signal that closes the loop between "what
    Terraform was told to build" and "what LocalStack actually has".
    """
    client = _s3_client()
    details: dict[str, Any] = {"bucket_name": bucket_name, "exists": False}

    try:
        client.head_bucket(Bucket=bucket_name)
        details["exists"] = True
    except ClientError:
        return details

    try:
        versioning = client.get_bucket_versioning(Bucket=bucket_name)
        details["versioning"] = versioning.get("Status", "Disabled")
    except ClientError as exc:
        details["versioning"] = f"error: {exc}"

    try:
        encryption = client.get_bucket_encryption(Bucket=bucket_name)
        rule = encryption["ServerSideEncryptionConfiguration"]["Rules"][0]
        details["encryption"] = rule["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
    except ClientError:
        details["encryption"] = None

    try:
        pab = client.get_public_access_block(Bucket=bucket_name)
        details["public_access_block"] = pab["PublicAccessBlockConfiguration"]
    except ClientError:
        details["public_access_block"] = None

    try:
        logging_cfg = client.get_bucket_logging(Bucket=bucket_name)
        details["logging_enabled"] = "LoggingEnabled" in logging_cfg
    except ClientError:
        details["logging_enabled"] = False

    return details


if __name__ == "__main__":
    mcp.run()

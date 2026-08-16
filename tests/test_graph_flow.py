"""Exercises the full LangGraph state machine end to end.

The LLM is LangChain's own `FakeListChatModel` (a first-party testing
utility) instead of a live Ollama call, and Terraform is the
tests/fixtures/fake_terraform.py stand-in described in test_mcp_server.py.
Every other moving part -- the graph routing, the remediation loop, the
real MCP server over stdio, the real Checkov scan, the real boto3 calls
against a real (moto) S3 endpoint -- is exercised for real.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent.graph import run_agent

FAKE_TERRAFORM = str(Path(__file__).parent / "fixtures" / "fake_terraform.py")


@pytest.fixture(autouse=True)
def _workspaces_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TERRAFORM_WORKSPACES_DIR", str(tmp_path / "workspaces"))
    os.chmod(FAKE_TERRAFORM, 0o755)
    yield


@pytest.mark.asyncio
async def test_happy_path_deploys_and_self_remediates(moto_endpoint):
    llm = FakeListChatModel(
        responses=[
            json.dumps(
                {
                    "is_supported": True,
                    "bucket_name": "build-artifacts",
                    "region": "us-east-1",
                    "notes": "user wants an S3 bucket for build artifacts",
                }
            )
        ]
    )
    mcp_env = {"TERRAFORM_BIN": FAKE_TERRAFORM, "AWS_ENDPOINT_URL": moto_endpoint}

    final_state = await run_agent("I want to deploy an S3 bucket", llm, mcp_env=mcp_env)

    assert final_state["status"] == "completed"
    assert final_state["intent"]["bucket_name"].startswith("build-artifacts")

    # The starting intent is intentionally non-compliant (no encryption/
    # versioning/logging/SSL-enforcement yet) so the CIS gate should have
    # caught real issues and the remediation loop should have fired.
    assert final_state["remediation_attempts"] >= 1

    # The LAST cis_scan (the one that gated the apply) must be fully green.
    assert final_state["compliance_passed"] is True
    assert all(f["passed"] for f in final_state["compliance_findings"])

    assert final_state["apply_result"]["success"] is True
    assert (
        final_state["apply_result"]["outputs"]["bucket_name"]
        == final_state["intent"]["bucket_name"]
    )

    assert final_state["verification"]["exists"] is True
    assert final_state["verification"]["versioning"] == "Enabled"
    assert final_state["verification"]["logging_enabled"] is True

    assert "Deployed" in final_state["final_response"]
    assert "CIS-aligned compliance gate" in final_state["final_response"]


@pytest.mark.asyncio
async def test_unsupported_request_short_circuits(moto_endpoint):
    llm = FakeListChatModel(
        responses=[
            json.dumps(
                {
                    "is_supported": False,
                    "bucket_name": "",
                    "region": "",
                    "notes": "not a bucket request",
                }
            )
        ]
    )
    mcp_env = {"TERRAFORM_BIN": FAKE_TERRAFORM, "AWS_ENDPOINT_URL": moto_endpoint}

    final_state = await run_agent("please launch me an RDS database", llm, mcp_env=mcp_env)

    assert final_state["status"] == "unsupported_request"
    assert "S3 bucket" in final_state["final_response"]
    assert "intent" not in final_state
    assert "apply_result" not in final_state

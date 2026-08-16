"""End-to-end test of the real MCP server over real stdio JSON-RPC.

Uses the fake-terraform test double (see tests/fixtures/fake_terraform.py)
in place of the HashiCorp binary -- everything else (the MCP transport, the
tool dispatch, the Checkov scan, the boto3 calls) is the genuine code path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.mcp_client import McpToolClient
from agent.terraform_gen import render_s3_bucket, write_workspace

FAKE_TERRAFORM = str(Path(__file__).parent / "fixtures" / "fake_terraform.py")


# TERRAFORM_BIN needs to be a single executable, not "<python> <script>", so
# we make the fixture directly executable and point TERRAFORM_BIN at it.
def _make_executable(path: str) -> str:
    os.chmod(path, 0o755)
    return path


@pytest.mark.asyncio
async def test_mcp_tools_list_includes_expected_tools(moto_endpoint):
    _make_executable(FAKE_TERRAFORM)
    env = {
        "TERRAFORM_BIN": FAKE_TERRAFORM,
        "AWS_ENDPOINT_URL": moto_endpoint,
    }
    async with McpToolClient(env=env) as client:
        tools = await client.session.list_tools()
        names = {t.name for t in tools.tools}
        assert {
            "terraform_init",
            "terraform_apply",
            "terraform_destroy",
            "checkov_cis_scan",
            "s3_list_buckets",
            "s3_get_bucket_details",
        } <= names


@pytest.mark.asyncio
async def test_full_deploy_and_verify_cycle(tmp_path, moto_endpoint):
    """Renders a hardened bucket, applies it via the fake-terraform double
    through the real MCP server, then verifies live state via the AWS MCP
    tool -- proving the whole terraform+MCP+CIS pipeline is wired correctly.
    """
    _make_executable(FAKE_TERRAFORM)

    intent = {
        "bucket_name": "mcp-e2e-test-bucket",
        "region": "us-east-1",
        "versioning": True,
        "encryption": "AES256",
        "block_public_access": True,
        "logging": True,
        "enforce_ssl": True,
        "tags": {},
    }
    files = render_s3_bucket(intent, request_id="mcp-e2e", user_request="deploy an s3 bucket")
    workspace = write_workspace(str(tmp_path), "mcp-e2e", files)

    env = {"TERRAFORM_BIN": FAKE_TERRAFORM, "AWS_ENDPOINT_URL": moto_endpoint}
    async with McpToolClient(env=env) as client:
        scan = await client.checkov_cis_scan(workspace)
        assert scan["passed"] is True, scan["findings"]

        apply_result = await client.terraform_apply(workspace)
        assert apply_result["success"] is True, apply_result
        assert apply_result["outputs"]["bucket_name"] == "mcp-e2e-test-bucket"

        details = await client.s3_get_bucket_details("mcp-e2e-test-bucket")
        assert details["exists"] is True
        assert details["versioning"] == "Enabled"
        assert details["encryption"] == "AES256"
        assert details["public_access_block"]["BlockPublicAcls"] is True
        assert details["logging_enabled"] is True

        buckets = await client.s3_list_buckets()
        assert "mcp-e2e-test-bucket" in buckets

        destroy_result = await client.terraform_destroy(workspace)
        assert destroy_result["success"] is True

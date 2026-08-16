"""Renders CIS-aligned Terraform HCL for a resource intent.

This module owns *what infrastructure code looks like*; it never talks to
Terraform or AWS directly -- that's the MCP server's job. Keeping generation
and execution in separate modules means the templates can be unit tested
(render + parse) with zero network/subprocess dependencies.
"""

from __future__ import annotations

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from agent.state import ResourceIntent

TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_s3_bucket(
    intent: ResourceIntent,
    request_id: str,
    user_request: str,
    localstack_endpoint: str = "http://localhost:4566",
) -> dict[str, str]:
    """Render the provider + s3 bucket templates into {filename: contents}."""
    provider_tpl = _env.get_template("provider.tf.j2")
    bucket_tpl = _env.get_template("s3_bucket.tf.j2")

    provider_tf = provider_tpl.render(
        region=intent.get("region", "us-east-1"),
        localstack_endpoint=localstack_endpoint,
    )

    bucket_tf = bucket_tpl.render(
        request_id=request_id,
        user_request=user_request,
        bucket_name=intent["bucket_name"],
        versioning=intent.get("versioning", False),
        encryption=intent.get("encryption") or "",
        block_public_access=intent.get("block_public_access", False),
        logging=intent.get("logging", False),
        enforce_ssl=intent.get("enforce_ssl", False),
        tags=intent.get("tags", {}),
    )

    return {
        "provider.tf": provider_tf,
        "main.tf": bucket_tf,
    }


def write_workspace(base_dir: str, request_id: str, files: dict[str, str]) -> str:
    """Write rendered files to terraform/workspaces/<request_id>/ and return the path."""
    workspace_dir = os.path.join(base_dir, request_id)
    os.makedirs(workspace_dir, exist_ok=True)
    for filename, contents in files.items():
        with open(os.path.join(workspace_dir, filename), "w") as fh:
            fh.write(contents)
    return workspace_dir

"""Typed state shared across every LangGraph node.

Keeping this as an explicit, serializable TypedDict (rather than passing
loose kwargs between nodes) is what makes the graph resumable, loggable and
unit-testable -- every node reads a subset of this dict and returns a partial
update that LangGraph merges back in.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class ComplianceFinding(TypedDict):
    checkov_id: str
    cis_control: str
    title: str
    severity: str
    passed: bool
    resource: str
    remediation_field: str | None
    remediation_value: Any


class ResourceIntent(TypedDict, total=False):
    resource_type: Literal["s3_bucket"]
    bucket_name: str
    region: str
    versioning: bool
    encryption: str
    block_public_access: bool
    logging: bool
    enforce_ssl: bool
    tags: dict[str, str]


class AgentEvent(TypedDict):
    step: str
    message: str


class AgentState(TypedDict, total=False):
    # input
    user_request: str
    request_id: str

    # working data
    intent: ResourceIntent
    workspace_dir: str
    terraform_files: dict[str, str]

    compliance_findings: list[ComplianceFinding]
    compliance_passed: bool
    remediation_attempts: int
    max_remediation_attempts: int

    apply_result: dict[str, Any]
    verification: dict[str, Any]

    events: list[AgentEvent]
    status: Literal[
        "in_progress",
        "unsupported_request",
        "completed",
        "failed",
    ]
    final_response: str

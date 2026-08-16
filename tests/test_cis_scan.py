"""Exercises the real Checkov binary against generated Terraform.

This is intentionally NOT mocked -- Checkov is a real, offline-capable
static analysis tool, so we get a genuine CIS-flavored compliance signal
without needing LocalStack, Docker or a terraform binary.
"""

from agent.cis_rules import all_passed, evaluate, load_mapping
from agent.terraform_gen import render_s3_bucket, write_workspace


def _intent(**overrides):
    base = {
        "bucket_name": "agent-demo-bucket",
        "region": "us-east-1",
        "versioning": False,
        "encryption": "",
        "block_public_access": False,
        "logging": False,
        "enforce_ssl": False,
        "tags": {},
    }
    base.update(overrides)
    return base


def test_mapping_loads_and_has_expected_controls():
    mapping = load_mapping()
    ids = {c["checkov_id"] for c in mapping["checks"]}
    assert "CKV2_AWS_6" in ids  # public access block
    assert "CKV_AWS_21" in ids  # versioning
    assert "CKV_AWS_18" in ids  # logging


def test_insecure_bucket_fails_cis_gate(tmp_path):
    files = render_s3_bucket(_intent(), request_id="insecure", user_request="deploy an s3 bucket")
    workspace = write_workspace(str(tmp_path), "insecure", files)

    findings = evaluate(workspace)
    failed_ids = {f["checkov_id"] for f in findings if not f["passed"]}

    assert "CKV2_AWS_6" in failed_ids  # no public access block requested
    assert "CKV_AWS_21" in failed_ids  # versioning off
    assert "CKV_AWS_18" in failed_ids  # logging off
    assert not all_passed(findings)


def test_hardened_bucket_passes_cis_gate(tmp_path):
    intent = _intent(
        versioning=True,
        encryption="AES256",
        block_public_access=True,
        logging=True,
        enforce_ssl=True,
    )
    files = render_s3_bucket(
        intent, request_id="hardened", user_request="deploy a compliant s3 bucket"
    )
    workspace = write_workspace(str(tmp_path), "hardened", files)

    findings = evaluate(workspace)
    failed = [f for f in findings if not f["passed"]]

    assert failed == []
    assert all_passed(findings)

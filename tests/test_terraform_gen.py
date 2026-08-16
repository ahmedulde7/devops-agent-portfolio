"""Template rendering must produce syntactically valid HCL.

No network, no docker, no terraform binary needed -- just Jinja2 + a real
HCL parser (python-hcl2), so this runs anywhere including plain CI.
"""

import hcl2

from agent.terraform_gen import render_s3_bucket, write_workspace


def _intent(**overrides):
    base = {
        "resource_type": "s3_bucket",
        "bucket_name": "agent-demo-bucket",
        "region": "us-east-1",
        "versioning": False,
        "encryption": "",
        "block_public_access": False,
        "logging": False,
        "enforce_ssl": False,
        "tags": {"Project": "devops-agent-demo"},
    }
    base.update(overrides)
    return base


def test_minimal_bucket_renders_valid_hcl(tmp_path):
    files = render_s3_bucket(_intent(), request_id="req-1", user_request="deploy an s3 bucket")
    assert "main.tf" in files and "provider.tf" in files

    workspace = write_workspace(str(tmp_path), "req-1", files)
    with open(f"{workspace}/main.tf") as fh:
        parsed = hcl2.load(fh)

    assert "resource" in parsed
    resource_types = {list(r.keys())[0] for r in parsed["resource"]}
    assert "aws_s3_bucket" in resource_types
    # nothing else should be present when every flag is off
    assert "aws_s3_bucket_versioning" not in resource_types
    assert "aws_s3_bucket_server_side_encryption_configuration" not in resource_types


def test_fully_hardened_bucket_renders_all_resources(tmp_path):
    intent = _intent(
        versioning=True,
        encryption="AES256",
        block_public_access=True,
        logging=True,
        enforce_ssl=True,
    )
    files = render_s3_bucket(
        intent, request_id="req-2", user_request="deploy a compliant s3 bucket"
    )
    workspace = write_workspace(str(tmp_path), "req-2", files)

    with open(f"{workspace}/main.tf") as fh:
        parsed = hcl2.load(fh)

    resource_types = {list(r.keys())[0] for r in parsed["resource"]}
    assert resource_types >= {
        "aws_s3_bucket",
        "aws_s3_bucket_versioning",
        "aws_s3_bucket_server_side_encryption_configuration",
        "aws_s3_bucket_public_access_block",
        "aws_s3_bucket_logging",
        "aws_s3_bucket_policy",
    }

    # provider.tf must also parse and point at localstack by default
    with open(f"{workspace}/provider.tf") as fh:
        provider_parsed = hcl2.load(fh)
    assert provider_parsed["provider"][0]["aws"]["endpoints"][0]["s3"] == ["http://localhost:4566"]

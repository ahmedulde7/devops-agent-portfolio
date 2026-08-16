#!/usr/bin/env python3
"""A terraform-CLI-compatible stand-in used ONLY by the test suite.

Why this exists: the real `terraform` binary can't be downloaded in the
sandboxed environment this project was authored in (egress to
releases.hashicorp.com is blocked there), so the automated tests can't shell
out to the genuine binary. Real usage -- the docker-compose / k3s stack the
user runs locally -- uses the actual HashiCorp `terraform` binary; nothing
about the agent or the MCP server changes between the two, only the
`TERRAFORM_BIN` environment variable.

This stub reads the *actual* rendered HCL with the same parser the rest of
the project uses (python-hcl2), and on `apply` really creates/configures the
bucket against whatever S3-compatible endpoint AWS_ENDPOINT_URL points at
(LocalStack in production, a local moto server in CI/tests) -- so the
post-apply live-verification MCP tool observes genuine, freshly-created
state rather than a canned fixture.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
import hcl2
from botocore.exceptions import ClientError

OUTPUTS_FILE = ".agent_fake_outputs.json"


def _endpoint_kwargs() -> dict:
    return {
        "endpoint_url": os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        "region_name": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    }


def _unwrap(value):
    """python-hcl2 wraps single attribute values in a 1-element list."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _load_resources(cwd: Path) -> dict:
    resources: dict[str, dict] = {}
    for tf_file in cwd.glob("*.tf"):
        with open(tf_file) as fh:
            parsed = hcl2.load(fh)
        for block in parsed.get("resource", []):
            for rtype, instances in block.items():
                for name, body in instances.items():
                    resources[f"{rtype}.{name}"] = body
    return resources


def _apply(cwd: Path) -> int:
    resources = _load_resources(cwd)
    s3 = boto3.client("s3", **_endpoint_kwargs())

    created = []

    def ensure_bucket(address: str) -> str | None:
        body = resources.get(address)
        if not body:
            return None
        bucket_name = _unwrap(body["bucket"])
        try:
            s3.create_bucket(Bucket=bucket_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise
        created.append(bucket_name)
        return bucket_name

    bucket_name = ensure_bucket("aws_s3_bucket.this")
    log_bucket_name = ensure_bucket("aws_s3_bucket.log_bucket")

    if bucket_name and "aws_s3_bucket_versioning.this" in resources:
        s3.put_bucket_versioning(Bucket=bucket_name, VersioningConfiguration={"Status": "Enabled"})

    if log_bucket_name and "aws_s3_bucket_versioning.log_bucket" in resources:
        s3.put_bucket_versioning(
            Bucket=log_bucket_name, VersioningConfiguration={"Status": "Enabled"}
        )

    if bucket_name and "aws_s3_bucket_server_side_encryption_configuration.this" in resources:
        algo = _unwrap(
            resources["aws_s3_bucket_server_side_encryption_configuration.this"]["rule"][0][
                "apply_server_side_encryption_by_default"
            ][0]["sse_algorithm"]
        )
        s3.put_bucket_encryption(
            Bucket=bucket_name,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": algo}}]
            },
        )

    if bucket_name and "aws_s3_bucket_public_access_block.this" in resources:
        s3.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )

    if bucket_name and log_bucket_name and "aws_s3_bucket_logging.this" in resources:
        if "aws_s3_bucket_policy.log_bucket_policy" in resources:
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "S3ServerAccessLogsPolicy",
                        "Effect": "Allow",
                        "Principal": {"Service": "logging.s3.amazonaws.com"},
                        "Action": "s3:PutObject",
                        "Resource": f"arn:aws:s3:::{log_bucket_name}/log/*",
                        "Condition": {"ArnLike": {"aws:SourceArn": f"arn:aws:s3:::{bucket_name}"}},
                    }
                ],
            }
            s3.put_bucket_policy(Bucket=log_bucket_name, Policy=json.dumps(policy))

        s3.put_bucket_logging(
            Bucket=bucket_name,
            BucketLoggingStatus={
                "LoggingEnabled": {
                    "TargetBucket": log_bucket_name,
                    "TargetPrefix": "log/",
                }
            },
        )

    bucket_arn = f"arn:aws:s3:::{bucket_name}" if bucket_name else None

    outputs = {
        "bucket_name": {"value": bucket_name, "type": "string"},
        "bucket_arn": {"value": bucket_arn, "type": "string"},
    }
    with open(cwd / OUTPUTS_FILE, "w") as fh:
        json.dump(outputs, fh)

    print(f"Apply complete! Resources: {len(created)} added, 0 changed, 0 destroyed.")
    return 0


def _destroy(cwd: Path) -> int:
    resources = _load_resources(cwd)
    s3 = boto3.client("s3", **_endpoint_kwargs())

    for address in ("aws_s3_bucket.this", "aws_s3_bucket.log_bucket"):
        body = resources.get(address)
        if not body:
            continue
        bucket_name = _unwrap(body["bucket"])
        try:
            objects = s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])
            for obj in objects:
                s3.delete_object(Bucket=bucket_name, Key=obj["Key"])
            s3.delete_bucket(Bucket=bucket_name)
        except ClientError:
            pass

    outputs_path = cwd / OUTPUTS_FILE
    if outputs_path.exists():
        outputs_path.unlink()

    print("Destroy complete! Resources: 2 destroyed.")
    return 0


def _output(cwd: Path) -> int:
    outputs_path = cwd / OUTPUTS_FILE
    if outputs_path.exists():
        with open(outputs_path) as fh:
            print(fh.read())
    else:
        print("{}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: fake_terraform.py <init|plan|apply|destroy|output> [flags...]",
            file=sys.stderr,
        )
        return 1

    command = sys.argv[1]
    cwd = Path.cwd()

    if command == "init":
        print("Terraform has been successfully initialized!")
        return 0
    if command == "plan":
        print("Plan: (fake) no changes computed by the test stub.")
        return 0
    if command == "apply":
        return _apply(cwd)
    if command == "destroy":
        return _destroy(cwd)
    if command == "output":
        return _output(cwd)

    print(f"fake_terraform.py: unknown command {command!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

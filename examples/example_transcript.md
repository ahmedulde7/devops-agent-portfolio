# Example run: "I want to deploy an S3 bucket"

This is a real, captured transcript of the agent handling the example query
from the project brief, end to end -- intent parsing, Terraform generation,
the CIS gate catching real issues, self-remediation, apply, and live
verification. It was captured with `AGENT_LLM_BACKEND=fake` (a canned but
realistic intent-parse response, standing in for what a local Ollama model
returns) and `TERRAFORM_BIN` pointed at the test double described in
`tests/fixtures/fake_terraform.py`, because the sandbox this project was
authored in can't reach Docker Hub or download the real `terraform` binary.
On your machine, with the full docker-compose stack up, the same code path
runs against a real Ollama model and a real `terraform apply` -- nothing
in `agent/` or `mcp_server/` changes between the two.

The generated Terraform for this run is saved alongside this file as
`generated_main.tf.sample` / `generated_provider.tf.sample` (the *final*,
post-remediation version -- see the timeline below for what the first draft
was missing).

---

## ✅ Deployed `build-artifacts-874ae3d3` on LocalStack

**Request:** "I want to deploy an S3 bucket"
**Region:** us-east-1
**Remediation passes:** 1

### CIS-aligned compliance gate (Checkov)

Each control is evaluated per-resource, so the primary bucket and its log-target bucket each get their own row.

| Control | CIS ref | Resource | Check | Result |
|---|---|---|---|---|
| `CKV_AWS_53` | 2.1.2 | `aws_s3_bucket_public_access_block.this` | S3 bucket blocks public ACLs | ✅ PASS |
| `CKV_AWS_54` | 2.1.2 | `aws_s3_bucket_public_access_block.this` | S3 bucket blocks public bucket policies | ✅ PASS |
| `CKV_AWS_56` | 2.1.2 | `aws_s3_bucket_public_access_block.this` | S3 bucket restricts public buckets | ✅ PASS |
| `CKV_AWS_55` | 2.1.2 | `aws_s3_bucket_public_access_block.this` | S3 bucket ignores public ACLs | ✅ PASS |
| `CKV_AWS_53` | 2.1.2 | `aws_s3_bucket_public_access_block.log_bucket` | S3 bucket blocks public ACLs | ✅ PASS |
| `CKV_AWS_54` | 2.1.2 | `aws_s3_bucket_public_access_block.log_bucket` | S3 bucket blocks public bucket policies | ✅ PASS |
| `CKV_AWS_56` | 2.1.2 | `aws_s3_bucket_public_access_block.log_bucket` | S3 bucket restricts public buckets | ✅ PASS |
| `CKV_AWS_55` | 2.1.2 | `aws_s3_bucket_public_access_block.log_bucket` | S3 bucket ignores public ACLs | ✅ PASS |
| `CKV_AWS_18` | 2.1.4 | `aws_s3_bucket.this` | S3 bucket has access logging enabled | ✅ PASS |
| `CKV_AWS_18` | 2.1.4 | `aws_s3_bucket.log_bucket` | S3 bucket has access logging enabled | ✅ PASS |
| `CKV2_AWS_6` | 2.1.2 | `aws_s3_bucket.this` | S3 bucket has a Public Access Block configured | ✅ PASS |
| `CKV2_AWS_6` | 2.1.2 | `aws_s3_bucket.log_bucket` | S3 bucket has a Public Access Block configured | ✅ PASS |
| `CKV_AWS_19` | 2.1.1 | `aws_s3_bucket.log_bucket` | S3 bucket has server-side encryption enabled | ✅ PASS |
| `CKV_AWS_19` | 2.1.1 | `aws_s3_bucket.this` | S3 bucket has server-side encryption enabled | ✅ PASS |
| `CKV_AWS_21` | 2.1.3 | `aws_s3_bucket.this` | S3 bucket has versioning enabled | ✅ PASS |
| `CKV_AWS_21` | 2.1.3 | `aws_s3_bucket.log_bucket` | S3 bucket has versioning enabled | ✅ PASS |

### Terraform apply

- **Status:** success
- **bucket_name:** `build-artifacts-874ae3d3`
- **bucket_arn:** `arn:aws:s3:::build-artifacts-874ae3d3`

### Live verification (AWS MCP tool, post-apply)

- **Exists on endpoint:** True
- **Versioning:** Enabled
- **Encryption:** AES256
- **Public access blocked:** True
- **Access logging enabled:** True

### Agent timeline

- **parse_intent:** Parsed intent -> bucket 'build-artifacts-874ae3d3' in us-east-1. user wants a bucket to store CI build artifacts
- **generate_terraform:** Rendered Terraform (pass 1) -- initial draft: public access closed and encryption on by policy default, but versioning/logging/SSL-enforcement not yet requested.
- **cis_scan:** CIS gate found 2 failing control(s): `CKV_AWS_18` S3 bucket has access logging enabled; `CKV_AWS_21` S3 bucket has versioning enabled
- **remediate:** Auto-remediation pass 1: updated `['logging', 'versioning']` on the intent and regenerated the Terraform.
- **generate_terraform:** Rendered Terraform (pass 2) with the fixes applied.
- **cis_scan:** CIS gate PASSED (16 controls evaluated).
- **terraform_apply:** `terraform apply` succeeded. Outputs: `bucket_name=build-artifacts-874ae3d3`, `bucket_arn=arn:aws:s3:::build-artifacts-874ae3d3`.
- **verify_live:** Live AWS-endpoint verification confirms versioning=Enabled, encryption=AES256, public access fully blocked, logging enabled -- matching what was applied, independently of the static scan.

---

## What this demonstrates

1. **Natural language -> structured intent.** The LLM's only job is deciding *what* the user wants (resource type, bucket name, region) -- never deciding security posture. That's deliberate: free-form model output should not be the thing that decides whether a bucket ends up public.
2. **Shift-left compliance.** Checkov runs against the rendered HCL *before* anything touches LocalStack. A real production version of this agent would refuse to apply non-compliant infrastructure past the retry budget rather than silently shipping it.
3. **A bounded, auditable remediation loop.** Fixes are looked up from `agent/cis_mapping.yaml` (one Checkov ID -> one deterministic field flip), not invented by the LLM on the fly. Every pass is logged in the timeline above.
4. **Two independent correctness signals.** The static Checkov scan and the live `s3_get_bucket_details` MCP call are separate code paths hitting separate systems (a parser vs. a real AWS API call) -- if they ever disagreed, that itself would be a signal worth surfacing.

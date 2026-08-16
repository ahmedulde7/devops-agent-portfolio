"""Small, dependency-free helpers used by the parse_intent node.

Split out from graph.py so they're trivially unit-testable without touching
LangGraph or any LLM at all.
"""

from __future__ import annotations

import json
import re
import uuid

INTENT_SYSTEM_PROMPT = """You are the intent-parsing stage of a DevOps automation agent.

The agent currently knows how to provision exactly ONE kind of resource: an
AWS S3 bucket, deployed with Terraform against LocalStack.

Given the user's request, respond with ONLY a single JSON object (no
markdown code fences, no commentary before or after it) matching this exact
schema:

{"is_supported": <bool>, "bucket_name": <string>, "region": <string>, "notes": <string>}

Rules:
- is_supported is true only if the user is asking to create/provision/deploy
  a storage bucket (S3 or generically "a bucket"/"object storage"). For
  anything else (databases, compute, networking, or unrelated requests),
  is_supported is false.
- bucket_name: invent a short, lowercase, DNS-safe name (letters, digits,
  hyphens only) that reflects the request. If the user gave an explicit
  name, normalize it instead of inventing one.
- region: an AWS region code. Default to "us-east-1" if the user did not
  specify one.
- notes: one short sentence explaining what you inferred.
"""

_BUCKET_NAME_RE = re.compile(r"[^a-z0-9-]+")


def sanitize_bucket_name(raw: str, request_id: str) -> str:
    name = raw.strip().lower()
    name = _BUCKET_NAME_RE.sub("-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    if not name:
        name = "devops-agent-bucket"
    # keep it short and append a short, request-scoped suffix so repeated
    # demo runs don't collide against the same LocalStack instance.
    suffix = request_id.replace("req-", "")[:8]
    name = name[:40].rstrip("-")
    if not name.endswith(suffix):
        name = f"{name}-{suffix}"
    return name[:63]  # S3 bucket name hard limit


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:10]}"


def extract_json_object(text: str) -> dict | None:
    """Tolerantly pull a JSON object out of an LLM response.

    Local models routinely wrap JSON in ```json fences or add a stray
    sentence despite instructions not to -- this handles the common cases
    instead of hard-failing the whole request on a formatting slip.
    """
    text = text.strip()
    # strip a fenced code block if present
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            return None
    return None

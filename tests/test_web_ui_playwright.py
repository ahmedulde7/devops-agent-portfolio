"""Real Playwright browser test against the real FastAPI chat UI.

Runs the actual `uvicorn` server as a subprocess (LLM backend swapped to
LangChain's FakeListChatModel and Terraform swapped to the test double, both
via environment variables the app reads at request time -- no code changes
between this and a production run) and drives it with a real Chromium
instance via pytest-playwright.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

FAKE_TERRAFORM = str(Path(__file__).parent / "fixtures" / "fake_terraform.py")
REPO_ROOT = str(Path(__file__).parent.parent)

SUPPORTED_RESPONSE = json.dumps(
    {
        "is_supported": True,
        "bucket_name": "build-artifacts",
        "region": "us-east-1",
        "notes": "user wants an S3 bucket for build artifacts",
    }
)
UNSUPPORTED_RESPONSE = json.dumps(
    {
        "is_supported": False,
        "bucket_name": "",
        "region": "",
        "notes": "not a storage request",
    }
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _running_app(tmp_path, moto_endpoint, fake_responses: list[str]):
    os.chmod(FAKE_TERRAFORM, 0o755)
    port = _free_port()

    env = {
        **os.environ,
        "PYTHONPATH": REPO_ROOT,
        "AGENT_LLM_BACKEND": "fake",
        "AGENT_FAKE_LLM_RESPONSES": json.dumps(fake_responses),
        "TERRAFORM_BIN": FAKE_TERRAFORM,
        "AWS_ENDPOINT_URL": moto_endpoint,
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "TERRAFORM_WORKSPACES_DIR": str(tmp_path / "workspaces"),
    }

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "agent.web.app:app",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    last_err = None
    try:
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{base_url}/api/health", timeout=1)
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(0.3)
        else:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise RuntimeError(f"app server did not start in time: {last_err}\n{out}")

        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_deploy_s3_bucket_via_chat_ui(tmp_path, moto_endpoint, page):
    with _running_app(tmp_path, moto_endpoint, [SUPPORTED_RESPONSE]) as base_url:
        page.goto(base_url)
        page.get_by_test_id("message-input").fill(
            "I want to deploy an S3 bucket for storing build artifacts"
        )
        page.get_by_test_id("send-button").click()

        # The agent runs a real (test-doubled) terraform apply + CIS scan +
        # live verification cycle; give it a generous timeout.
        agent_message = page.get_by_test_id("agent-message")
        agent_message.wait_for(timeout=30_000)

        assert agent_message.get_attribute("data-status") == "completed"

        response_text = page.get_by_test_id("agent-response").inner_text()
        assert "build-artifacts" in response_text
        assert "CIS-aligned compliance gate" in response_text

        timeline = page.get_by_test_id("agent-timeline")
        assert timeline.count() == 1


def test_example_button_fills_input(tmp_path, moto_endpoint, page):
    with _running_app(tmp_path, moto_endpoint, [SUPPORTED_RESPONSE]) as base_url:
        page.goto(base_url)
        page.locator(".examples button", has_text="deploy an S3 bucket").click()
        assert "S3 bucket" in page.get_by_test_id("message-input").input_value()


def test_unsupported_request_shows_warning_badge(tmp_path, moto_endpoint, page):
    with _running_app(tmp_path, moto_endpoint, [UNSUPPORTED_RESPONSE]) as base_url:
        page.goto(base_url)
        page.get_by_test_id("message-input").fill("please launch me an RDS database")
        page.get_by_test_id("send-button").click()

        agent_message = page.get_by_test_id("agent-message")
        agent_message.wait_for(timeout=30_000)
        assert agent_message.get_attribute("data-status") == "unsupported_request"

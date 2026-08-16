"""Shared fixtures.

`moto_endpoint` spins up a real moto S3 server (a lightweight, pip-only
stand-in for LocalStack's S3 API) for the duration of the test session, so
the MCP server's AWS tools and the fake-terraform test double can make real
HTTP calls against a real S3-compatible API without Docker or network
egress. Point the same code at LocalStack (AWS_ENDPOINT_URL=http://localhost:4566)
and it behaves identically -- that's the point of testing against the same
boto3 client the production code path uses.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def moto_endpoint():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "moto.server", "-p", str(port), "-H", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    endpoint = f"http://127.0.0.1:{port}"

    deadline = time.time() + 15
    last_err = None
    while time.time() < deadline:
        try:
            import urllib.request

            urllib.request.urlopen(f"{endpoint}/moto-api/", timeout=1)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError(f"moto server did not start in time: {last_err}")

    yield endpoint

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

"""FastAPI wrapper around the LangGraph agent.

Deliberately thin: this module owns HTTP concerns only. Every request builds
a fresh LLM client (`get_llm()`) and runs the compiled graph via
`run_agent()` -- the graph itself and the MCP tool client are what do the
actual work, and both are exercised directly (no HTTP involved) by the
non-UI test suite. This file exists so there's something for the Playwright
tests -- and a human -- to click on.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.graph import run_agent
from agent.llm import get_llm

app = FastAPI(title="DevOps Agent")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    status: str
    response: str
    events: list[dict]


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "llm_backend": os.environ.get("AGENT_LLM_BACKEND", "ollama"),
        "aws_endpoint": os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    llm = get_llm()
    final_state = await run_agent(payload.message, llm)
    return ChatResponse(
        status=final_state.get("status", "failed"),
        response=final_state.get("final_response", "(no response produced)"),
        events=final_state.get("events", []),
    )

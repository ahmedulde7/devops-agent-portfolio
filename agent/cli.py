"""Terminal entry point: `python -m agent.cli "I want to deploy an S3 bucket"`."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.markdown import Markdown

from agent.graph import run_agent
from agent.llm import get_llm

app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def deploy(
    request: str = typer.Argument(..., help="Natural-language infrastructure request.")
) -> None:
    """Run one request through the agent and print the report."""
    llm = get_llm()

    with console.status(
        "[bold blue]Agent working: parsing intent, generating Terraform, running the CIS gate..."
    ):
        final_state = asyncio.run(run_agent(request, llm))

    console.print(Markdown(final_state.get("final_response", "(no response)")))
    raise typer.Exit(code=0 if final_state.get("status") == "completed" else 1)


if __name__ == "__main__":
    app()

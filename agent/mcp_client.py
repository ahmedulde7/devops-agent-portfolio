"""Async client the LangGraph nodes use to reach the MCP tool server.

Every method here is a thin, typed wrapper around a real MCP `call_tool`
round trip over stdio -- there is no direct-import shortcut back into
`mcp_server`, on purpose: the whole point of routing infrastructure actions
through MCP is that the agent process and the tool-execution process are
separate, so the tool surface is the same whether the caller is this graph,
a different agent, or a human poking at the server with an MCP inspector.
"""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpToolClient:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        merged_env.setdefault("PYTHONPATH", repo_root)

        self._params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=repo_root,
            env=merged_env,
        )
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> McpToolClient:
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(self._params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    async def _call(self, name: str, **arguments: Any) -> Any:
        assert self.session is not None, "McpToolClient used outside `async with`"
        result = await self.session.call_tool(name, arguments=arguments)
        if result.isError:
            text = result.content[0].text if result.content else "unknown MCP error"
            raise RuntimeError(f"MCP tool '{name}' failed: {text}")
        return result.structuredContent

    async def terraform_init(self, workspace_dir: str) -> dict:
        return await self._call("terraform_init", workspace_dir=workspace_dir)

    async def terraform_apply(self, workspace_dir: str) -> dict:
        return await self._call("terraform_apply", workspace_dir=workspace_dir)

    async def terraform_destroy(self, workspace_dir: str) -> dict:
        return await self._call("terraform_destroy", workspace_dir=workspace_dir)

    async def checkov_cis_scan(self, workspace_dir: str) -> dict:
        return await self._call("checkov_cis_scan", workspace_dir=workspace_dir)

    async def s3_list_buckets(self) -> list[str]:
        # FastMCP wraps bare list/scalar tool results as {"result": ...};
        # dict-returning tools come back unwrapped. Handle both shapes.
        result = await self._call("s3_list_buckets")
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return result

    async def s3_get_bucket_details(self, bucket_name: str) -> dict:
        return await self._call("s3_get_bucket_details", bucket_name=bucket_name)

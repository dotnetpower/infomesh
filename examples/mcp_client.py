#!/usr/bin/env python3
"""Example: Connect to InfoMesh MCP server as a client.

Demonstrates how to programmatically connect to the InfoMesh
MCP server and call its tools (search, crawl_url, etc.)
from Python code.

Requires: mcp>=1.0
Usage:
    # First, in another terminal: uv run infomesh mcp
    # Then run this client:
    uv run python examples/mcp_client.py
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main() -> None:
    # Launch the InfoMesh MCP server as a subprocess
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "infomesh", "mcp"],
    )

    async with stdio_client(server_params) as (read, write), \
            ClientSession(read, write) as session:
        await session.initialize()

        # List available tools
        tools = await session.list_tools()
        print("Available MCP tools:")
        for tool in tools.tools:
            print(f"  - {tool.name}: {tool.description[:60]}...")
        print()

        # Search
        print("─" * 60)
        print("Calling search('python asyncio', limit=3)...")
        result = await session.call_tool(
            "search",
            {"query": "python asyncio", "limit": 3},
        )
        for content in result.content:
            print(content.text)

        # Network stats
        print("\n" + "─" * 60)
        print("Calling network_stats()...")
        stats = await session.call_tool("network_stats", {})
        for content in stats.content:
            print(content.text)


if __name__ == "__main__":
    asyncio.run(main())

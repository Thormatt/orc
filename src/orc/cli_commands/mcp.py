"""`orc mcp serve` — start the MCP stdio server."""

from __future__ import annotations

import click


@click.group("mcp")
def mcp() -> None:
    """MCP stdio server for invoking Orc skills from Claude Code, Codex, etc."""


@mcp.command("serve")
def serve_command() -> None:
    """Start the MCP server on stdio."""
    from orc.mcp.server import serve_stdio

    serve_stdio()

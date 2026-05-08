# MCP server

Orc exposes a Model Context Protocol stdio server via `orc mcp serve`. Use it to
let Claude Code or another MCP client invoke Orc skills directly.

## Tools

The server exposes four tools:

- `orc_verify_claim(claim, workspace="default")`
- `orc_research_topic(topic, workspace="default")`
- `orc_search_evidence(query, workspace="default", k=10)`
- `orc_get_trace(run_id)`

Each tool resolves a workspace, opens a Run, dispatches through the directive
registry, and returns the same payload shape as the corresponding CLI command.

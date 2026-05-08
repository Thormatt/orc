# Command-line interface

The Orc CLI uses click. Every command resolves a workspace, opens a Run, dispatches
to a skill via the directive registry, and renders the result.

## Commands

- `orc workspace create <name>` — create a workspace
- `orc workspace list` — list workspaces
- `orc ingest <path-or-url> [-w <name>]` — add evidence to the corpus
- `orc search "<query>" [-w <name>]` — surface BM25 retrieval
- `orc verify "<claim>" [-w <name>]` — verify a claim against the corpus
- `orc mcp serve` — start the MCP stdio server

`orc verify` defaults to model `claude-sonnet-4-6`. Override with `--model` or the
environment variable `ORC_VERIFY_MODEL`.

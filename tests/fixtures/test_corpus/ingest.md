# Ingestion

`orc ingest` parses files or URLs and writes evidence + chunks into the workspace.

## Supported inputs

Markdown, plain text, RST, JSON, and URLs that return any of the above. PDF
support is deferred to v1.1.

## Chunking

The chunker is token-bounded with a default target of 800 tokens and a 100-token
overlap. It is heading-aware: code-fenced regions never count as headings, and
the heading hierarchy is preserved as `headings_path` on each chunk.

## Idempotency

Ingestion is idempotent on the SHA-256 of the source bytes. Re-ingesting an
unchanged file is a no-op.

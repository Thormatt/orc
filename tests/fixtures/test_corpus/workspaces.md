# Workspaces

Orc workspaces hold evidence, chunks, runs, and traces. Each workspace is a separate
SQLite database stored under `~/.orc/workspaces/<name>/orc.db`.

## Isolation

Workspaces never share evidence. A workspace can be packaged as a tarball and moved
between machines without any cross-references.

## Schema

The canonical schema lives in `src/orc/storage/schema.sql`. The current schema version is 1.

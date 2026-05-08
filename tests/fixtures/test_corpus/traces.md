# Traces

Every Orc run writes a trace. The trace has two halves: an indexed `run` row in
the workspace SQLite database, and a full-payload JSON file under
`traces/<YYYY>/<MM>/<run_id>.json`.

## Trace contents

The JSON includes inputs, retrieval results (which chunks were considered, in what
order, and at what scores), every LLM call (model, tokens, cache hits, elapsed
milliseconds, the request shape and response stop reason), the structured output,
and the timeline of recorded events.

## Replay

`orc replay <run_id>` re-executes the recorded inputs against the corpus snapshot
referenced by `run.corpus_version`. `--live` re-runs against the current corpus,
which can show whether a corpus update changed the verdict.

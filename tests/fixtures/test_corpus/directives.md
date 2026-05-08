# Directives

A directive in Orc combines persistent context, allowed skills, tools, policies,
triggers, verification rules, and output contracts.

## v1 directives

Orc v1 ships exactly one directive: `research`. The research directive exposes
four skills: `verify_claim`, `research_topic`, `search_evidence`, and
`extract_claims`.

## Adding a new directive

Drop a new package under `src/orc/directives/<name>/` whose `__init__.py` calls
`register(DirectiveSpec(...))`. CLI and MCP surfaces look up skills via the
registry and never by hard-coded import.

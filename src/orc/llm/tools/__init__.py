"""Tools that verify_claim can chain into during multi-turn LLM loops.

Each tool exports:
  - <NAME>_TOOL_SCHEMA: the Anthropic tool-use schema dict
  - execute(input: dict) -> str: pure, side-effect-free executor that the
    agentic loop calls when the model emits a tool_use block.

Adding a new tool means adding a module here, registering its schema in
the verify_claim arithmetic mode (or wherever it's used), and writing a
unit test for the executor. The agentic loop in `orc.llm.agentic` is
tool-agnostic — it just dispatches by tool name.
"""

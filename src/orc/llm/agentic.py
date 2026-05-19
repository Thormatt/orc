"""Multi-turn tool-use loop for skills that need the model to call tools and
react to the results.

The flow per turn:
  1. Send the current message stack + the tool registry.
  2. Inspect the response.
     - If stop_reason == "tool_use" and the response contains tool_use blocks
       that are not the terminal tool (e.g. `record_binary_verdict`), execute
       each one, append the assistant message and a user message containing
       tool_result blocks, then loop.
     - Otherwise return the response (the caller extracts the terminal verdict).
  3. Hard cap on iterations so a misbehaving model can't run unbounded.

Tool-agnostic: callers pass a `tools` list and an `executors` dict keyed by
tool name. The terminal tool (the one that ends the loop) is named via
`terminal_tool` — the loop stops as soon as it appears, even if other
tool_use blocks accompany it.

`on_tool_call` is invoked per non-terminal tool call so callers (verify_claim)
can record each invocation into the run trace.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class AgenticLoopError(RuntimeError):
    """Raised when the loop exceeds max_turns without reaching the terminal tool."""


def run_tool_loop(
    client: Any,
    *,
    model: str,
    system: list[dict[str, Any]] | str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    executors: dict[str, Callable[[dict[str, Any]], str]],
    terminal_tool: str,
    max_tokens: int = 2048,
    max_turns: int = 6,
    on_tool_call: Callable[[str, dict[str, Any], str, int], None] | None = None,
    on_llm_call: Callable[[Any, int, int], None] | None = None,
) -> tuple[Any, list[dict[str, Any]], list[Any]]:
    """Run a multi-turn tool-use loop and return
    (final_response, full_messages, all_responses).

    `messages` is the initial user-message stack; the function returns the
    fully-extended conversation including all assistant tool_use blocks and
    user tool_result responses so the caller can record the full sequence.

    `executors` must have an entry for every tool name in `tools` except the
    terminal one (which is parsed by the caller after the loop returns).

    `on_tool_call(name, input, result_str, elapsed_ms)` fires per non-terminal
    tool invocation. `on_llm_call(response, turn_idx, elapsed_ms)` fires per
    LLM call. Use these to stream events into a Run trace.
    """
    from orc.llm.client import messages_create

    conversation: list[dict[str, Any]] = list(messages)
    responses: list[Any] = []

    for turn_idx in range(max_turns):
        llm_start = time.monotonic()
        response = messages_create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=conversation,
        )
        llm_elapsed_ms = int((time.monotonic() - llm_start) * 1000)
        responses.append(response)
        if on_llm_call is not None:
            on_llm_call(response, turn_idx, llm_elapsed_ms)

        tool_uses = [
            b
            for b in response.content
            if getattr(b, "type", None) == "tool_use"
        ]

        # Terminal tool present → loop ends. The caller parses it from response.
        if any(getattr(t, "name", None) == terminal_tool for t in tool_uses):
            return response, conversation, responses

        # No tool_use at all → model gave up without finalizing. Bail out
        # rather than spin: returning the response lets the caller decide.
        if not tool_uses:
            return response, conversation, responses

        # Execute every non-terminal tool, append assistant + tool_result.
        conversation.append({"role": "assistant", "content": response.content})
        tool_results: list[dict[str, Any]] = []
        for use in tool_uses:
            name = getattr(use, "name", None)
            input_data = dict(getattr(use, "input", {}) or {})
            if name == terminal_tool:
                continue
            executor = executors.get(name)
            if executor is None:
                result_str = f"ERROR: no executor registered for tool {name!r}"
            else:
                start = time.monotonic()
                try:
                    result_str = executor(input_data)
                except Exception as exc:  # noqa: BLE001 — funnel into tool_result
                    result_str = f"ERROR: {type(exc).__name__}: {exc}"
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if on_tool_call is not None:
                    on_tool_call(name, input_data, result_str, elapsed_ms)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(use, "id", ""),
                    "content": result_str,
                }
            )
        conversation.append({"role": "user", "content": tool_results})

    raise AgenticLoopError(
        f"agentic loop exceeded max_turns={max_turns} without emitting "
        f"terminal tool {terminal_tool!r}"
    )

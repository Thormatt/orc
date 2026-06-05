"""Multi-turn tool-use loop tests.

Uses FakeAnthropic to drive the loop without spending money. The key
property under test: the loop correctly dispatches non-terminal tools,
appends tool_result messages, and stops as soon as the terminal tool
appears in the response.
"""

from __future__ import annotations

from typing import Any

import pytest

from orc.llm import client as client_module
from orc.llm.agentic import AgenticLoopError, run_tool_loop
from tests._fake_llm import FakeAnthropic, FakeContentBlock, FakeResponse


def _setup(monkeypatch: pytest.MonkeyPatch, fake: FakeAnthropic) -> None:
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)


def _tool_use_block(name: str, *, tool_use_id: str, input_data: dict[str, Any]) -> FakeContentBlock:
    block = FakeContentBlock(type="tool_use", name=name, input=input_data)
    block.id = tool_use_id  # type: ignore[attr-defined]
    return block


def test_agentic_loop_stops_when_terminal_tool_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """One LLM call, model emits terminal tool, loop returns immediately."""
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    _tool_use_block(
                        "record_verdict",
                        tool_use_id="t1",
                        input_data={"label": "supported"},
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    _setup(monkeypatch, fake)

    final, _convo, all_responses = run_tool_loop(
        fake,
        model="m",
        system="sys",
        messages=[{"role": "user", "content": "x"}],
        tools=[{"name": "calc"}, {"name": "record_verdict"}],
        executors={"calc": lambda i: "should not run"},
        terminal_tool="record_verdict",
    )
    assert len(all_responses) == 1
    assert final is all_responses[0]


def test_agentic_loop_dispatches_tools_and_appends_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool call → execute → tool_result message → next turn → terminal verdict."""
    fake = FakeAnthropic(
        responses=[
            # Turn 0: model calls calc
            FakeResponse(
                content=[
                    _tool_use_block(
                        "calc",
                        tool_use_id="call_1",
                        input_data={"expression": "1+2"},
                    )
                ],
                stop_reason="tool_use",
            ),
            # Turn 1: model commits verdict
            FakeResponse(
                content=[
                    _tool_use_block(
                        "record_verdict",
                        tool_use_id="v1",
                        input_data={"label": "supported", "confidence": 0.9},
                    )
                ],
                stop_reason="tool_use",
            ),
        ]
    )
    _setup(monkeypatch, fake)

    tool_calls: list[tuple[str, dict[str, Any], str]] = []

    def _record(name: str, input_data: dict[str, Any], result: str, elapsed_ms: int) -> None:
        tool_calls.append((name, input_data, result))

    final, convo, all_responses = run_tool_loop(
        fake,
        model="m",
        system="sys",
        messages=[{"role": "user", "content": "verify"}],
        tools=[{"name": "calc"}, {"name": "record_verdict"}],
        executors={"calc": lambda i: f"={1+2}"},
        terminal_tool="record_verdict",
        on_tool_call=_record,
    )
    assert len(all_responses) == 2
    assert tool_calls == [("calc", {"expression": "1+2"}, "=3")]
    # Conversation should include the assistant tool_use message + the
    # tool_result user message. The user message's content is a list of dicts.
    roles = [m["role"] for m in convo]
    assert "assistant" in roles
    user_tool_result_msgs = [
        m
        for m in convo
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and isinstance(m["content"][0], dict)
        and m["content"][0].get("type") == "tool_result"
    ]
    assert len(user_tool_result_msgs) == 1
    assert user_tool_result_msgs[0]["content"][0]["tool_use_id"] == "call_1"


def test_agentic_loop_raises_when_max_turns_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model keeps calling the tool, never emits terminal — loop must abort."""
    # 4 turns of nothing-but-calc, with max_turns=3 → AgenticLoopError.
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    _tool_use_block(
                        "calc",
                        tool_use_id=f"c{i}",
                        input_data={"expression": "1+1"},
                    )
                ],
                stop_reason="tool_use",
            )
            for i in range(5)
        ]
    )
    _setup(monkeypatch, fake)

    with pytest.raises(AgenticLoopError):
        run_tool_loop(
            fake,
            model="m",
            system="sys",
            messages=[{"role": "user", "content": "x"}],
            tools=[{"name": "calc"}, {"name": "record_verdict"}],
            executors={"calc": lambda i: "2"},
            terminal_tool="record_verdict",
            max_turns=3,
        )


def test_agentic_loop_tool_executor_error_is_in_band(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool executor raising should surface in tool_result as ERROR: ..., not crash."""
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    _tool_use_block(
                        "boom",
                        tool_use_id="b1",
                        input_data={},
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeResponse(
                content=[
                    _tool_use_block(
                        "record_verdict",
                        tool_use_id="v1",
                        input_data={"label": "not_found"},
                    )
                ],
                stop_reason="tool_use",
            ),
        ]
    )
    _setup(monkeypatch, fake)

    def _boom(input_data: dict[str, Any]) -> str:
        raise RuntimeError("intentional")

    seen: list[str] = []

    def _record(name: str, input_data: dict[str, Any], result: str, elapsed_ms: int) -> None:
        seen.append(result)

    run_tool_loop(
        fake,
        model="m",
        system="sys",
        messages=[{"role": "user", "content": "x"}],
        tools=[{"name": "boom"}, {"name": "record_verdict"}],
        executors={"boom": _boom},
        terminal_tool="record_verdict",
        on_tool_call=_record,
    )
    assert any(s.startswith("ERROR") for s in seen)

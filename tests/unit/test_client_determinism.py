"""Determinism guarantees for the LLM call chokepoint.

The replay invariant ("re-executes the exact decision") requires that every LLM
call is issued with deterministic sampling. `messages_create` is the single
chokepoint all skills route through, so it must pin temperature=0 by default.
"""

from __future__ import annotations

from orc.llm.client import messages_create
from tests._fake_llm import FakeAnthropic, FakeResponse


def test_messages_create_pins_temperature_zero_by_default() -> None:
    client = FakeAnthropic(responses=[FakeResponse()])

    messages_create(client, model="claude-sonnet-4-6", max_tokens=10, messages=[])

    assert client.calls[-1]["temperature"] == 0


def test_messages_create_respects_explicit_temperature() -> None:
    client = FakeAnthropic(responses=[FakeResponse()])

    messages_create(
        client, model="claude-sonnet-4-6", max_tokens=10, messages=[], temperature=0.7
    )

    assert client.calls[-1]["temperature"] == 0.7

"""Fake Anthropic client for tests. Mirrors the parts of the SDK that orc relies on."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeContentBlock:
    type: str = "tool_use"
    name: str = "record_verdict"
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    content: list[FakeContentBlock] = field(default_factory=list)
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "tool_use"


class FakeAnthropic:
    """A drop-in fake of the Anthropic client.

    Either preload a list of FakeResponse objects, or pass a `responder` callable that
    receives the kwargs of `messages.create(...)` and returns a FakeResponse.
    """

    def __init__(
        self,
        responses: list[FakeResponse] | None = None,
        responder: Callable[[dict[str, Any]], FakeResponse] | None = None,
    ) -> None:
        self.queue: list[FakeResponse] = list(responses or [])
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    @property
    def messages(self) -> FakeAnthropic:
        return self

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if self.responder is not None:
            return self.responder(kwargs)
        if self.queue:
            return self.queue.pop(0)
        raise RuntimeError(
            "FakeAnthropic exhausted: provide more responses or a responder callable"
        )


def make_verdict_response(
    *,
    label: str,
    confidence: float,
    reasoning: str = "synthetic test reasoning",
    supporting_chunk_ids: list[str] | None = None,
    contradicting_chunk_ids: list[str] | None = None,
    missing_information: str = "",
    input_tokens: int = 1000,
    output_tokens: int = 80,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 1000,
) -> FakeResponse:
    return FakeResponse(
        content=[
            FakeContentBlock(
                type="tool_use",
                name="record_verdict",
                input={
                    "label": label,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "supporting_chunk_ids": supporting_chunk_ids or [],
                    "contradicting_chunk_ids": contradicting_chunk_ids or [],
                    "missing_information": missing_information,
                },
            ),
        ],
        usage=FakeUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
        ),
        stop_reason="tool_use",
    )

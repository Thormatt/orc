"""gmail.send_draft executor — proves the external-credential path.

The credential (GMAIL_TOKEN) lives only in the effect plane. We never hit the real
Gmail API in tests: httpx.post is faked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orc import effects
from orc.effects.action import Action, ActionValidationError
from orc.effects.base import MissingCredentialError
from orc.effects.builtin import gmail as gmail_mod
from orc.storage import workspace as ws_module


def _allow(orc_home: Path, workspace: str, executor_ids: list[str]) -> None:
    quoted = ", ".join(f'"{e}"' for e in executor_ids)
    (orc_home / "config.toml").write_text(
        f"[workspace.{workspace}.effects]\nallowed = [{quoted}]\n"
    )


class _FakeResp:
    def __init__(self, status: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


def _send_action(draft_id: str = "draft-1") -> Action:
    return Action(
        executor="gmail.send_draft",
        version=1,
        params={"draft_id": draft_id},
        idempotency_key="k-send-1",
    )


def test_gmail_send_draft_registered() -> None:
    ex = effects.get("gmail.send_draft")
    assert ex.required_credential == "GMAIL_TOKEN"


def test_send_draft_posts_with_bearer_and_returns_ids(
    orc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_module.create("research")
    _allow(orc_home, "research", ["gmail.send_draft"])
    monkeypatch.setenv("GMAIL_TOKEN", "ya29.fake")

    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResp:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResp(payload={"id": "msg-99", "threadId": "thr-7"})

    monkeypatch.setattr(gmail_mod.httpx, "post", fake_post)

    result = effects.run_action("research", _send_action("draft-42"))

    assert captured["headers"]["Authorization"] == "Bearer ya29.fake"
    assert captured["json"] == {"id": "draft-42"}
    assert "gmail.googleapis.com" in captured["url"]
    assert result["message_id"] == "msg-99"
    assert result["thread_id"] == "thr-7"


def test_send_draft_requires_credential(orc_home: Path) -> None:
    ws_module.create("research")
    _allow(orc_home, "research", ["gmail.send_draft"])
    # GMAIL_TOKEN not set -> the analysis-plane-style env cannot execute.
    with pytest.raises(MissingCredentialError):
        effects.run_action("research", _send_action())


def test_send_draft_validates_params(orc_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws_module.create("research")
    _allow(orc_home, "research", ["gmail.send_draft"])
    monkeypatch.setenv("GMAIL_TOKEN", "ya29.fake")
    bad = Action(
        executor="gmail.send_draft",
        version=1,
        params={},  # missing draft_id
        idempotency_key="k",
    )
    with pytest.raises(ActionValidationError):
        effects.run_action("research", bad)


def test_send_draft_raises_on_http_error(
    orc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_module.create("research")
    _allow(orc_home, "research", ["gmail.send_draft"])
    monkeypatch.setenv("GMAIL_TOKEN", "ya29.fake")

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResp:
        return _FakeResp(status=500)

    monkeypatch.setattr(gmail_mod.httpx, "post", fake_post)

    with pytest.raises(RuntimeError):
        effects.run_action("research", _send_action())

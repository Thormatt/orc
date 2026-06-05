"""Effect-plane worker: drain approved actions and execute them.

`drain_once` processes every currently-leasable approved action in a single pass;
failures back off (via mark_failed's next_retry_at) so they are retried on a later
pass rather than spun on within the same one. `run_worker` is the polling loop.

Run this where the write credentials live — never alongside the analysis plane.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from typing import Any

from orc import effects
from orc.effects.action import Action
from orc.queue import approval


def _lease_owner() -> str:
    return f"worker:{socket.gethostname()}:{os.getpid()}"


def drain_once(
    workspace: str,
    *,
    lease_owner: str | None = None,
    max_attempts: int = 3,
    backoff_seconds: float = 30,
) -> dict[str, int]:
    owner = lease_owner or _lease_owner()
    succeeded = 0
    failed = 0
    while True:
        appr = approval.lease_one(workspace, lease_owner=owner)
        if appr is None:
            break
        action = Action.from_dict(appr.proposed_action or {})
        try:
            result = effects.run_action(workspace, action)
        except Exception as exc:  # noqa: BLE001 — any executor failure is recorded
            approval.mark_failed(
                workspace,
                appr.approval_id,
                error=str(exc),
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
            )
            failed += 1
            continue
        approval.mark_executed(workspace, appr.approval_id, result=result)
        succeeded += 1
    return {"succeeded": succeeded, "failed": failed}


def run_worker(
    workspace: str,
    *,
    poll_interval: float = 2.0,
    once: bool = False,
    max_attempts: int = 3,
    on_pass: Callable[[dict[str, int]], Any] | None = None,
) -> dict[str, int]:
    """Drain in a loop. With `once=True`, drains a single pass and returns its summary."""
    owner = _lease_owner()
    while True:
        summary = drain_once(workspace, lease_owner=owner, max_attempts=max_attempts)
        if on_pass is not None:
            on_pass(summary)
        if once:
            return summary
        time.sleep(poll_interval)

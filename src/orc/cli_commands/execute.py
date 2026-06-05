"""`orc execute <approval_id>` — the effect plane.

Run this as a separate process holding only the write credentials its executors
need (and no LLM key). It refuses anything not human-approved, runs the action
through the guarded executor path, and records the outcome.
"""

from __future__ import annotations

import getpass

import click
from rich.console import Console

from orc import effects
from orc.effects.action import Action
from orc.effects.base import MissingCredentialError
from orc.errors import WorkspaceNotFoundError
from orc.queue import approval as approval_module
from orc.queue.approval import (
    AlreadyExecutedError,
    ApprovalNotFoundError,
    NotApprovedError,
)
from orc.storage import workspace as ws_module

console = Console()


@click.command("execute")
@click.argument("approval_id")
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
def execute_command(approval_id: str, workspace: str | None) -> None:
    """Execute an approved action by approval_id."""
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    existing = approval_module.get_execution(ws.name, approval_id)
    if existing is not None and existing["exec_status"] == "succeeded":
        console.print(f"[dim]Approval {approval_id} was already executed — no-op.[/dim]")
        return

    lease_owner = f"cli:{getpass.getuser()}"
    try:
        appr = approval_module.begin_execution(ws.name, approval_id, lease_owner=lease_owner)
    except AlreadyExecutedError:
        console.print(f"[dim]Approval {approval_id} was already executed — no-op.[/dim]")
        return
    except (ApprovalNotFoundError, NotApprovedError) as exc:
        raise click.ClickException(str(exc)) from exc

    action = Action.from_dict(appr.proposed_action or {})
    try:
        result = effects.run_action(ws.name, action)
    except MissingCredentialError as exc:
        approval_module.mark_failed(ws.name, approval_id, error=str(exc))
        raise click.ClickException(f"Missing credential: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — record any executor failure, then surface
        status = approval_module.mark_failed(ws.name, approval_id, error=str(exc))
        raise click.ClickException(f"Execution failed ({status}): {exc}") from exc

    approval_module.mark_executed(ws.name, approval_id, result=result)
    console.print(f"[green]Executed[/green] {approval_id} via {action.executor}: {result}")

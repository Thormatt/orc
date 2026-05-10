"""`orc approve ...` commands — manage the approval queue."""

from __future__ import annotations

import json as json_lib

import click
from rich.console import Console
from rich.table import Table

from orc.errors import WorkspaceNotFoundError
from orc.queue import approval as approval_module
from orc.queue.approval import ApprovalAlreadyDecidedError, ApprovalNotFoundError
from orc.storage import workspace as ws_module

console = Console()

_STATUS_STYLE = {
    "pending": "yellow",
    "approved": "green",
    "rejected": "red",
    "expired": "dim",
}


@click.group("approve")
def approve_group() -> None:
    """Manage the approval queue."""


@approve_group.command("list")
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
@click.option(
    "--status",
    type=click.Choice(["pending", "approved", "rejected", "expired", "all"]),
    default="pending",
    help="Filter by status (default: pending)",
)
@click.option("--limit", type=int, default=20)
def list_command(workspace: str | None, status: str, limit: int) -> None:
    """List approvals."""
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    items = approval_module.list_approvals(
        ws.name, status=None if status == "all" else status, limit=limit
    )
    if not items:
        console.print(f"[dim]No approvals with status={status} in {ws.name}[/dim]")
        return
    table = Table(title=f"Approvals in {ws.name}")
    table.add_column("approval_id", style="bold")
    table.add_column("status")
    table.add_column("directive")
    table.add_column("skill")
    table.add_column("created")
    table.add_column("summary")
    for a in items:
        style = _STATUS_STYLE.get(a.status, "white")
        table.add_row(
            a.approval_id,
            f"[{style}]{a.status}[/{style}]",
            a.directive,
            a.skill,
            a.created_at,
            a.summary[:80],
        )
    console.print(table)


@approve_group.command("show")
@click.argument("approval_id")
@click.option("--workspace", "-w", default=None)
def show_command(approval_id: str, workspace: str | None) -> None:
    """Print full payload for an approval."""
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        a = approval_module.get(ws.name, approval_id)
    except ApprovalNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        json_lib.dumps(
            {
                "approval_id": a.approval_id,
                "workspace": a.workspace,
                "directive": a.directive,
                "skill": a.skill,
                "source_run_id": a.source_run_id,
                "status": a.status,
                "summary": a.summary,
                "created_at": a.created_at,
                "decided_at": a.decided_at,
                "decided_by": a.decided_by,
                "decision_note": a.decision_note,
                "payload": a.payload,
                "proposed_action": a.proposed_action,
            },
            indent=2,
            default=str,
        )
    )


@approve_group.command("accept")
@click.argument("approval_id")
@click.option("--workspace", "-w", default=None)
@click.option("--note", default=None, help="Optional decision note")
@click.option("--by", "decided_by", default=None, help="Who decided (defaults to $USER)")
def accept_command(
    approval_id: str, workspace: str | None, note: str | None, decided_by: str | None
) -> None:
    """Accept a pending approval."""
    _decide(approval_id, workspace, note, decided_by, accept=True)


@approve_group.command("reject")
@click.argument("approval_id")
@click.option("--workspace", "-w", default=None)
@click.option("--note", default=None, help="Optional decision note")
@click.option("--by", "decided_by", default=None)
def reject_command(
    approval_id: str, workspace: str | None, note: str | None, decided_by: str | None
) -> None:
    """Reject a pending approval."""
    _decide(approval_id, workspace, note, decided_by, accept=False)


def _decide(
    approval_id: str,
    workspace: str | None,
    note: str | None,
    decided_by: str | None,
    *,
    accept: bool,
) -> None:
    import os

    if decided_by is None:
        decided_by = os.environ.get("USER") or "user"
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        if accept:
            a = approval_module.accept(ws.name, approval_id, decided_by=decided_by, note=note)
        else:
            a = approval_module.reject(ws.name, approval_id, decided_by=decided_by, note=note)
    except (ApprovalNotFoundError, ApprovalAlreadyDecidedError) as exc:
        raise click.ClickException(str(exc)) from exc
    style = _STATUS_STYLE.get(a.status, "white")
    console.print(f"[{style}]{a.status}[/{style}]  {a.approval_id}")
    if a.decision_note:
        console.print(f"  note: {a.decision_note}")
    if a.decided_by:
        console.print(f"  by:   {a.decided_by}")

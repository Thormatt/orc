"""`orc approve ...` commands — manage the approval queue."""

from __future__ import annotations

import json as json_lib

import click
from rich.console import Console
from rich.table import Table

from orc.cli_commands._shared import resolve_workspace
from orc.queue import approval as approval_module
from orc.queue.approval import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    DuplicateApproverError,
)

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
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output")
def list_command(workspace: str | None, status: str, limit: int, as_json: bool) -> None:
    """List approvals."""
    ws = resolve_workspace(workspace)
    items = approval_module.list_approvals(
        ws.name, status=None if status == "all" else status, limit=limit
    )
    if as_json:
        # Plain echo, never rich: scripts (and baton) parse this.
        click.echo(
            json_lib.dumps(
                [
                    {
                        "approval_id": a.approval_id,
                        "status": a.status,
                        "approvers_required": a.approvers_required,
                        "accept_count": a.accept_count,
                        "reject_count": a.reject_count,
                        "directive": a.directive,
                        "skill": a.skill,
                        "summary": a.summary,
                        "source_run_id": a.source_run_id,
                        "created_at": a.created_at,
                    }
                    for a in items
                ],
                indent=2,
            )
        )
        return
    if not items:
        console.print(f"[dim]No approvals with status={status} in {ws.name}[/dim]")
        return
    table = Table(title=f"Approvals in {ws.name}")
    table.add_column("approval_id", style="bold")
    table.add_column("status")
    table.add_column("approvers", justify="right")
    table.add_column("directive")
    table.add_column("skill")
    table.add_column("created")
    table.add_column("summary")
    for a in items:
        style = _STATUS_STYLE.get(a.status, "white")
        approvers_cell = a.progress
        if a.reject_count:
            approvers_cell = f"[red]{approvers_cell}  · {a.reject_count}✗[/red]"
        elif a.status == "pending" and a.approvers_required > 1:
            approvers_cell = f"[yellow]{approvers_cell}[/yellow]"
        table.add_row(
            a.approval_id,
            f"[{style}]{a.status}[/{style}]",
            approvers_cell,
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
    ws = resolve_workspace(workspace)
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
                "approvers_required": a.approvers_required,
                "approvers_progress": a.progress,
                "accept_count": a.accept_count,
                "reject_count": a.reject_count,
                "created_at": a.created_at,
                "decided_at": a.decided_at,
                "decided_by": a.decided_by,
                "decision_note": a.decision_note,
                "decisions": [
                    {
                        "decision_id": d.decision_id,
                        "decision": d.decision,
                        "decided_by": d.decided_by,
                        "decided_at": d.decided_at,
                        "note": d.note,
                    }
                    for d in a.decisions
                ],
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
@click.option(
    "--by",
    "decided_by",
    default=None,
    help="Who decided (defaults to $USER). Self-reported and unauthenticated: "
    "anyone with shell access can pass any name, so multi-approver gates are "
    "honor-system unless an authenticated layer supplies this value.",
)
def accept_command(
    approval_id: str, workspace: str | None, note: str | None, decided_by: str | None
) -> None:
    """Accept a pending approval.

    The recorded approver name comes from --by (or $USER) and is not
    authenticated by orc. Deployments using approvers_required > 1 as a
    compliance control (e.g. EU AI Act Article 14(5)) must ensure decisions
    are submitted through an authenticated surface that pins --by to a
    verified identity.
    """
    _decide(approval_id, workspace, note, decided_by, accept=True)


@approve_group.command("reject")
@click.argument("approval_id")
@click.option("--workspace", "-w", default=None)
@click.option("--note", default=None, help="Optional decision note")
@click.option(
    "--by",
    "decided_by",
    default=None,
    help="Who decided (defaults to $USER). Self-reported and unauthenticated; "
    "see `orc approve accept --help`.",
)
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
    ws = resolve_workspace(workspace)
    try:
        if accept:
            a = approval_module.accept(ws.name, approval_id, decided_by=decided_by, note=note)
        else:
            a = approval_module.reject(ws.name, approval_id, decided_by=decided_by, note=note)
    except (ApprovalNotFoundError, ApprovalAlreadyDecidedError, DuplicateApproverError) as exc:
        raise click.ClickException(str(exc)) from exc
    style = _STATUS_STYLE.get(a.status, "white")
    verb = "accepted" if accept else "rejected"
    console.print(
        f"[{style}]{a.status}[/{style}]  {a.approval_id}  "
        f"[dim]· {verb} by {decided_by} · progress {a.progress}[/dim]"
    )
    if note:
        console.print(f"  note: {note}")
    if a.status == "pending" and a.approvers_required > 1:
        remaining = a.approvers_required - a.accept_count
        console.print(
            f"  [yellow]still pending: {remaining} more approver(s) required[/yellow]"
        )

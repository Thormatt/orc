"""`orc worker` — drain approved actions automatically (effect plane).

Run this where the write credentials live. With --once it drains a single pass and
exits (useful for cron); otherwise it polls until interrupted.
"""

from __future__ import annotations

import click
from rich.console import Console

from orc.effects.worker import drain_once, run_worker
from orc.errors import WorkspaceNotFoundError
from orc.storage import workspace as ws_module

console = Console()


@click.command("worker")
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
@click.option("--once", is_flag=True, help="Drain a single pass and exit.")
@click.option("--poll-interval", type=float, default=2.0, help="Seconds between passes.")
@click.option("--max-attempts", type=int, default=3, help="Retries before an action is marked dead.")
def worker_command(
    workspace: str | None, once: bool, poll_interval: float, max_attempts: int
) -> None:
    """Execute approved actions from the queue."""
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    if once:
        summary = drain_once(ws.name, max_attempts=max_attempts)
        console.print(
            f"[green]drained[/green] {ws.name}: "
            f"{summary['succeeded']} succeeded, {summary['failed']} failed"
        )
        return

    console.print(f"[dim]draining {ws.name} every {poll_interval}s — Ctrl-C to stop[/dim]")

    def _report(summary: dict[str, int]) -> None:
        if summary["succeeded"] or summary["failed"]:
            console.print(
                f"  {summary['succeeded']} succeeded, {summary['failed']} failed"
            )

    try:
        run_worker(
            ws.name, poll_interval=poll_interval, max_attempts=max_attempts, on_pass=_report
        )
    except KeyboardInterrupt:
        console.print("[dim]worker stopped[/dim]")

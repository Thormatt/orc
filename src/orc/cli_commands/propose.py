"""`orc propose <executor>` — the approval queue's front door.

Stages an action from the command line through the same guarded path skills use
(`Run.propose`): executor existence, params schema, and the workspace allow-list
are all checked *before* anything is enqueued. The CLI never executes — it only
proposes; a human decision plus `orc execute`/`orc worker` carries it out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from orc import effects
from orc.errors import WorkspaceNotFoundError
from orc.paths import config_path
from orc.runs import open_run
from orc.storage import workspace as ws_module


def _load_params(raw: str) -> dict[str, Any]:
    # curl-style convention: @ means "read the JSON from this file".
    if raw.startswith("@"):
        path = Path(raw[1:])
        if not path.is_file():
            raise click.ClickException(f"Params file not found: {path}")
        raw = path.read_text()
    try:
        params = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"--params is not valid JSON: {exc}") from exc
    if not isinstance(params, dict):
        raise click.ClickException("--params must be a JSON object, not a list or scalar")
    return params


@click.command("propose")
@click.argument("executor_id")
@click.option("--params", "params_raw", required=True, help="JSON object, or @/path/to/file.json")
@click.option("--summary", required=True, help="One-line summary shown to approvers")
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
@click.option("--approvers", type=int, default=1, show_default=True, help="Accepts required")
@click.option("--idempotency-key", default=None, help="Dedupe key for execution")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output")
def propose_command(
    executor_id: str,
    params_raw: str,
    summary: str,
    workspace: str | None,
    approvers: int,
    idempotency_key: str | None,
    as_json: bool,
) -> None:
    """Propose an action for human approval.

    Note: `orc replay` of a propose-run is unsupported — the "effects" directive
    is not in the directive registry, and replaying a proposal is semantically
    wrong (it would silently stage a duplicate effect).
    """
    params = _load_params(params_raw)
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    with open_run(
        ws,
        directive="effects",
        skill="cli.propose",
        inputs={"executor": executor_id, "summary": summary, "params": params},
    ) as run:
        try:
            approval_id = run.propose(
                executor=executor_id,
                params=params,
                summary=summary,
                idempotency_key=idempotency_key,
                approvers_required=approvers,
            )
        except effects.ExecutorNotFoundError as exc:
            known = ", ".join(sorted(e.id for e in effects.all_executors()))
            raise click.ClickException(f"{exc}. Known executors: {known}") from exc
        except effects.ExecutorNotAllowedError as exc:
            raise click.ClickException(
                f"{exc}\n"
                f"Enable it by adding to {config_path()}:\n"
                f"[workspace.{ws.name}.effects]\n"
                f'allowed = ["{executor_id}"]'
            ) from exc
        except effects.ActionValidationError as exc:
            schema = json.dumps(effects.get(executor_id).params_schema, indent=2)
            raise click.ClickException(
                f"Invalid params for {executor_id}: {exc}\nSchema:\n{schema}"
            ) from exc
        run.close(output={"approval_id": approval_id})
        run_id = run.run_id

    if as_json:
        click.echo(
            json.dumps(
                {
                    "approval_id": approval_id,
                    "run_id": run_id,
                    "workspace": ws.name,
                    "executor": executor_id,
                    "status": "pending",
                },
                indent=2,
            )
        )
    else:
        click.echo(f"Proposed {executor_id}: approval {approval_id} pending")
        click.echo("Next steps:")
        click.echo(f"  orc approve show {approval_id} -w {ws.name}")
        click.echo(f"  orc approve accept {approval_id} -w {ws.name}")
        click.echo(f"  orc execute {approval_id} -w {ws.name}")

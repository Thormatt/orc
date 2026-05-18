"""`orc audit export` — bundle traces + manifest for regulator handoff."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from orc.audit.export import AuditExportError, export_workspace

console = Console()


@click.group("audit")
def audit_group() -> None:
    """Audit-export commands. See `orc audit export --help`."""


@audit_group.command("export")
@click.option(
    "--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)"
)
@click.option(
    "--out",
    "output_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="Output path for the tarball (default: ./audit-<workspace>-<ts>.tar.gz)",
)
@click.option(
    "--from",
    "range_from",
    default=None,
    help="Lower bound on Run.started_at (ISO 8601, inclusive)",
)
@click.option(
    "--to",
    "range_to",
    default=None,
    help="Upper bound on Run.started_at (ISO 8601, inclusive)",
)
@click.option("--json", "as_json", is_flag=True, help="Emit raw manifest JSON.")
def export_command(
    workspace: str | None,
    output_path: str | None,
    range_from: str | None,
    range_to: str | None,
    as_json: bool,
) -> None:
    """Bundle a workspace's traces, run rows, evidence manifest, approvals,
    and runtime metadata into a single tar.gz for handoff to a regulator,
    auditor, or customer."""
    from orc.storage import workspace as ws_module

    try:
        ws = ws_module.resolve(workspace)
    except Exception as exc:  # noqa: BLE001 — surface as ClickException
        raise click.ClickException(str(exc)) from exc

    if output_path is None:
        from orc.core.clock import now_iso

        stamp = now_iso().replace(":", "").replace("-", "").replace("T", "-")[:15]
        output_path = f"./audit-{ws.name}-{stamp}.tar.gz"
    out = Path(output_path).expanduser().resolve()

    try:
        manifest = export_workspace(
            ws.name,
            output_path=out,
            range_from=range_from,
            range_to=range_to,
        )
    except AuditExportError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(manifest.to_json())
        return

    console.print(f"[bold]Audit export written:[/bold] {out}")
    console.print(f"  workspace                  = {manifest.workspace}")
    console.print(f"  exported_at                = {manifest.exported_at}")
    console.print(f"  orc_version                = {manifest.orc_version}")
    console.print(
        f"  range                      = "
        f"{manifest.range_from or '—'} … {manifest.range_to or '—'}"
    )
    console.print(
        f"  trace_schemas_supported    = {manifest.trace_schema_versions_supported}"
    )
    console.print(
        f"  trace_schemas_seen         = {manifest.trace_schema_versions_seen}"
    )
    for k, v in sorted(manifest.counts.items()):
        console.print(f"  {k:<26} = {v}")
    console.print(f"  files in bundle            = {len(manifest.files)}")
    console.print(
        "[dim]integrity: `manifest.json` carries sha256 for every file in the bundle.[/dim]"
    )

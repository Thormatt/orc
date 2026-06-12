"""`orc report RUN_ID...` — render traces as a self-contained HTML artifact."""

from __future__ import annotations

from pathlib import Path

import click

from orc.errors import TraceNotFoundError
from orc.rendering.trace_html import build_report_html
from orc.storage.trace_store import load_trace


@click.command("report")
@click.argument("run_ids", nargs=-1, required=True)
@click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Write the report to PATH instead of stdout.",
)
@click.option(
    "--open",
    "open_after",
    is_flag=True,
    help="Open the written report in the default browser (requires -o).",
)
def report_command(
    run_ids: tuple[str, ...],
    output_path: Path | None,
    open_after: bool,
) -> None:
    """Render one or more run traces as a self-contained HTML report."""
    # Fail before rendering: there is no file to open when writing to stdout,
    # and silently ignoring the flag would hide a typo in the invocation.
    if open_after and output_path is None:
        raise click.ClickException("--open requires -o/--output (stdout cannot be opened)")
    try:
        traces = [load_trace(run_id) for run_id in run_ids]
    except TraceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    html_doc = build_report_html(traces)
    if output_path is None:
        click.echo(html_doc)
        return
    output_path.write_text(html_doc, encoding="utf-8")
    click.echo(str(output_path))
    if open_after:
        click.launch(str(output_path))

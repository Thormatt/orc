"""`orc verify "<claim>"`, `orc verify --file <path>`, `orc verify --url <url>`."""

from __future__ import annotations

import json as json_lib
from pathlib import Path
from typing import Any

import click
from rich.console import Console

from orc import directives
from orc.directives.research.routing import UnknownDomainError
from orc.errors import WorkspaceNotFoundError
from orc.ingest.loaders import load_file, load_url
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.workspace import Workspace

console = Console()

_LABEL_STYLE = {
    "supported": "green",
    "contradicted": "red",
    "partial": "yellow",
    "not_found": "magenta",
}


@click.command("verify")
@click.argument("claim", required=False)
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
@click.option("--model", default=None, help="Override the verify model")
@click.option("--k", type=int, default=None, help="Number of chunks to retrieve (default 10)")
@click.option(
    "--file",
    "from_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Extract and verify all claims from a file",
)
@click.option("--url", "from_url", default=None, help="Fetch URL and verify all claims in it")
@click.option(
    "--domain",
    default=None,
    help="Route mode by domain hint (e.g. 'financial', 'clinical', 'legal')",
)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt for batch verify")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON instead of formatted output")
def verify_command(
    claim: str | None,
    workspace: str | None,
    model: str | None,
    k: int | None,
    from_file: str | None,
    from_url: str | None,
    domain: str | None,
    yes: bool,
    as_json: bool,
) -> None:
    """Verify a claim against the workspace's evidence corpus."""
    if not claim and not from_file and not from_url:
        raise click.UsageError("Provide CLAIM, --file <path>, or --url <url>.")

    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    if from_file or from_url:
        _verify_from_document(
            ws,
            file_path=from_file,
            url=from_url,
            model=model,
            k=k,
            domain=domain,
            yes=yes,
            as_json=as_json,
        )
        return

    _verify_one(ws, claim=claim, model=model, k=k, domain=domain, as_json=as_json)


def _verify_one(
    ws: Workspace,
    *,
    claim: str,
    model: str | None,
    k: int | None,
    domain: str | None,
    as_json: bool,
) -> None:
    spec = directives.get("research")
    skill = spec.skills["verify_claim"]
    kwargs: dict[str, Any] = {**spec.kwargs_for("verify_claim"), "claim": claim}
    if model is not None:
        kwargs["model"] = model
    if k is not None:
        kwargs["k"] = k
    if domain is not None:
        kwargs["domain"] = domain

    with open_run(ws, directive="research", skill="verify_claim", inputs=dict(kwargs)) as run:
        run.record_effective_kwargs(kwargs)
        try:
            result = skill.run(workspace=ws, run=run, **kwargs)
        except UnknownDomainError as exc:
            raise click.ClickException(str(exc)) from exc
        run.close(output=result)

    if as_json:
        click.echo(json_lib.dumps(result, indent=2, default=str))
        return
    _render_verdict(result, run_id=run.run_id)


def _verify_from_document(
    ws: Workspace,
    *,
    file_path: str | None,
    url: str | None,
    model: str | None,
    k: int | None,
    domain: str | None,
    yes: bool,
    as_json: bool,
) -> None:
    if file_path:
        loaded = load_file(Path(file_path))
        source_label = file_path
    else:
        loaded = load_url(url)
        source_label = url

    spec = directives.get("research")
    extract_skill = spec.skills["extract_claims"]
    extract_kwargs = {**spec.kwargs_for("extract_claims"), "document": loaded.text}

    with open_run(
        ws,
        directive="research",
        skill="extract_claims",
        inputs={
            "source": source_label,
            "doc_chars": len(loaded.text),
            "document": loaded.text,
        },
    ) as run:
        run.record_effective_kwargs(extract_kwargs)
        extract_result = extract_skill.run(workspace=ws, run=run, **extract_kwargs)
        run.close(output=extract_result)
    raw_claims = list(extract_result.get("claims", []))
    claim_texts = [c["text"] for c in raw_claims if c.get("text")]

    if not claim_texts:
        console.print("[yellow]No claims extracted from document.[/yellow]")
        return

    if not as_json:
        console.print(f"\n[bold]{len(claim_texts)} claim(s) extracted from[/bold] {source_label}")
        for i, t in enumerate(claim_texts, 1):
            console.print(f"  {i}. {t}")

    if not yes and not as_json and not click.confirm("\nVerify all claims?", default=True):
        return

    results: list[dict[str, Any]] = []
    verify_skill = spec.skills["verify_claim"]
    for c in claim_texts:
        kwargs = {**spec.kwargs_for("verify_claim"), "claim": c}
        if model is not None:
            kwargs["model"] = model
        if k is not None:
            kwargs["k"] = k
        if domain is not None:
            kwargs["domain"] = domain
        with open_run(ws, directive="research", skill="verify_claim", inputs=dict(kwargs)) as run:
            run.record_effective_kwargs(kwargs)
            try:
                result = verify_skill.run(workspace=ws, run=run, **kwargs)
            except UnknownDomainError as exc:
                raise click.ClickException(str(exc)) from exc
            run.close(output=result)
        results.append({**result, "_run_id": run.run_id})

    if as_json:
        click.echo(
            json_lib.dumps({"source": source_label, "results": results}, indent=2, default=str)
        )
        return

    _render_summary(source_label, results)


def _render_verdict(r: dict[str, Any], *, run_id: str) -> None:
    label = r["label"]
    style = _LABEL_STYLE.get(label, "white")
    console.print()
    console.print(f"[bold {style}]{label.upper()}[/bold {style}]  confidence={r['confidence']:.2f}")
    console.print(f"[dim]claim:[/dim] {r['claim']}")
    console.print(f"[bold]reasoning:[/bold] {r['reasoning']}")

    if r["supporting_chunks"]:
        console.print(f"\n[green]supporting evidence ({len(r['supporting_chunks'])}):[/green]")
        for c in r["supporting_chunks"]:
            _render_chunk(c)

    if r["contradicting_chunks"]:
        console.print(f"\n[red]contradicting evidence ({len(r['contradicting_chunks'])}):[/red]")
        for c in r["contradicting_chunks"]:
            _render_chunk(c)

    if r.get("missing_information"):
        console.print(f"\n[bold]missing information:[/bold] {r['missing_information']}")

    console.print(f"\n[dim]model={r['model']}  run_id={run_id}[/dim]")


def _render_chunk(c: dict[str, Any]) -> None:
    title = c.get("evidence_title") or "[dim]?[/dim]"
    headings = c.get("headings_path") or "[dim]?[/dim]"
    preview = c["text"].strip().replace("\n", " ")[:160]
    console.print(f"  [bold]{title}[/bold]  [dim]{headings}[/dim]")
    console.print(f"  {preview}")
    console.print(f"  [dim]chunk_id={c['chunk_id']}[/dim]")


def _render_summary(source: str, results: list[dict[str, Any]]) -> None:
    console.print(f"\n[bold]Verification summary for {source}[/bold]")
    counts: dict[str, int] = {}
    for r in results:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    parts = [
        f"[{_LABEL_STYLE.get(k, 'white')}]{k}={v}[/{_LABEL_STYLE.get(k, 'white')}]"
        for k, v in sorted(counts.items())
    ]
    console.print("  " + "  ".join(parts))
    for i, r in enumerate(results, 1):
        label = r["label"]
        style = _LABEL_STYLE.get(label, "white")
        console.print(
            f"  {i}. [bold {style}]{label}[/bold {style}]  ({r['confidence']:.2f})  "
            f"{r['claim'][:80]}"
        )

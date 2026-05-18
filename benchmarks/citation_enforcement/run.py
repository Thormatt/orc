"""Citation-enforcement benchmark.

Measures the fraction of fabricated chunk_ids that reach the caller across N
`verify_claim` invocations driven by an adversarial fake LLM. The fake LLM is
configured to return `supporting_chunk_ids` containing a mix of real
retrieval-set IDs and fabricated IDs; the runtime's chunk_id-validation step
(verify_claim.py lines 147-155) must drop the fabricated IDs before the
verdict is returned.

Run:
    uv run python -m benchmarks.citation_enforcement.run [--n 100] [--out PATH]

Output:
    results/<timestamp>/results.json   per-case + aggregate numbers
    results/<timestamp>/audit.tar.gz   the benchmark run's own audit-export
    results/<timestamp>/README.md      human-readable summary

The benchmark itself runs through Orc — every verify_claim invocation produces
a real trace. The audit-export of those traces is the artifact a reviewer
re-runs to validate the published number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = Path(__file__).parent / "dataset.yaml"
RESULTS_ROOT = Path(__file__).parent / "results"

# A small corpus the benchmark ingests so BM25 retrieval returns at least a
# couple of chunks for each claim in dataset.yaml. Kept tiny on purpose —
# this benchmark is about the citation-validation invariant, not retrieval.
CORPUS_DOCS: dict[str, str] = {
    "workspaces.md": (
        "# Workspaces\n\n"
        "Orc workspaces are stored under ~/.orc/workspaces/<name>/. Each\n"
        "workspace owns its own SQLite database and evidence directory.\n"
    ),
    "traces.md": (
        "# Trace and replay\n\n"
        "Every orc verify run writes a full trace JSON to disk under\n"
        "traces/<YYYY>/<MM>/<run_id>.json. Replay can run in frozen mode\n"
        "against a corpus version recorded in the original run.\n"
    ),
    "retrieval.md": (
        "# Retrieval\n\n"
        "BM25 retrieval is the default in orc. The candidate pool is\n"
        "filtered down to K chunks before the LLM call.\n"
    ),
    "verify.md": (
        "# Verify claim\n\n"
        "verify_claim returns a structured verdict with confidence and\n"
        "supporting / contradicting chunk IDs. Hallucinated chunk_ids\n"
        "returned by the model are dropped before the verdict reaches\n"
        "the caller.\n"
    ),
    "mcp.md": (
        "# MCP\n\n"
        "Orc supports MCP via a stdio server. The four read tools are\n"
        "orc_verify_claim, orc_search_evidence, orc_research_topic, and\n"
        "orc_get_trace.\n"
    ),
    "directives.md": (
        "# Directives\n\n"
        "The directive registry maps names to skill specs. Adding a new\n"
        "directive is dropping a package under src/orc/directives/<name>/.\n"
    ),
    "ingest.md": (
        "# Ingestion\n\n"
        "Markdown, text, and URL ingestion are supported in 0.1.x. PDF is\n"
        "planned for 0.2.\n"
    ),
    "approval.md": (
        "# Approvals\n\n"
        "An approval queue gates external actions in orc. Multi-approver\n"
        "workflows satisfy EU AI Act Article 14 §5.\n"
    ),
}


@dataclass
class CaseResult:
    case_index: int
    claim: str
    retrieval_count: int
    real_ids_used: int
    fake_ids_injected: int
    supporting_returned: list[str]
    contradicting_returned: list[str]
    fake_ids_leaked: list[str]
    run_id: str

    @property
    def leaked(self) -> int:
        return len(self.fake_ids_leaked)


@dataclass
class Aggregate:
    n_cases: int
    total_fakes_injected: int
    total_fakes_leaked: int
    leak_rate: float
    real_ids_passed_through: int


def _generate_fake_ids(seed_prefix: str, n: int) -> list[str]:
    """Make ULID-shaped fake IDs that won't collide with real chunk_ids.

    Real ULIDs start with `01K...` (May 2026 timestamp). These are also 26
    chars and start with `01K` so they look plausible, but they're rejected
    against the live retrieval set by definition (no chunk has them)."""
    out: list[str] = []
    for i in range(n):
        suffix = secrets.token_hex(8).upper()[:18]
        out.append(f"01KFAKE{seed_prefix}{i:02d}{suffix}"[:26])
    return out


def _setup_workspace(orc_home: Path) -> str:
    """Create the benchmark workspace and ingest the corpus."""
    os.environ["ORC_HOME"] = str(orc_home)
    # Re-import paths so the env var takes effect for the rest of this run.
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.storage import workspace as ws_module

    corpus_dir = orc_home / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for name, body in CORPUS_DOCS.items():
        (corpus_dir / name).write_text(body)
    ws = ws_module.create("bench-citation")
    do_ingest(ws, str(corpus_dir))
    return ws.name


def _run_one_case(
    workspace_name: str, case_index: int, claim: str, fake_ids: list[str]
) -> CaseResult:
    """Run a single verify_claim with a fake LLM that injects `fake_ids`
    alongside up-to-2 real chunk_ids from the retrieval set."""
    from tests._fake_llm import FakeAnthropic, make_verdict_response

    from orc import directives
    from orc.runs import open_run
    from orc.storage import workspace as ws_module

    ws = ws_module.resolve(workspace_name)

    # Closure over fake_ids — generated per-case by the orchestrator, captured
    # here so the responder sees a fresh set on each call.
    real_ids_seen: list[str] = []

    def responder(kwargs: dict[str, Any]):
        # The system blocks include the corpus dump where chunk_ids appear as
        # <chunk id="..."> markers. Pull two so we have realistic supporting IDs.
        sysblocks = kwargs.get("system") or []
        corpus_text = ""
        for b in sysblocks:
            if isinstance(b, dict) and b.get("type") == "text":
                corpus_text += b.get("text", "")
        import re

        # Match only ULID-shaped IDs (26 chars starting with 01) to avoid
        # capturing the `chunk id="..."` example inside the system prompt.
        real_in_corpus = re.findall(r'chunk id="(01[A-Z0-9]{24})"', corpus_text)
        chosen_real = real_in_corpus[:2]
        real_ids_seen.extend(chosen_real)
        return make_verdict_response(
            label="supported",
            confidence=0.9,
            reasoning="benchmark synthetic — should not appear in production.",
            supporting_chunk_ids=[*chosen_real, *fake_ids],
        )

    fake_client = FakeAnthropic(responder=responder)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(
        ws, directive="research", skill="verify_claim", inputs={"claim": claim}
    ) as run:
        run.record_effective_kwargs({"claim": claim, "model": "benchmark-fake"})
        out = skill.run(workspace=ws, run=run, claim=claim, client=fake_client)
        run.close(output=out)

    supporting_returned = [c["chunk_id"] for c in out["supporting_chunks"]]
    contradicting_returned = [c["chunk_id"] for c in out["contradicting_chunks"]]
    leaked = [cid for cid in fake_ids if cid in supporting_returned or cid in contradicting_returned]
    return CaseResult(
        case_index=case_index,
        claim=claim,
        retrieval_count=len(out["retrieval_chunk_ids"]),
        real_ids_used=len(set(real_ids_seen)),
        fake_ids_injected=len(fake_ids),
        supporting_returned=supporting_returned,
        contradicting_returned=contradicting_returned,
        fake_ids_leaked=leaked,
        run_id=run.run_id,
    )


def _export_bundle(workspace_name: str, out_path: Path) -> None:
    from orc.audit.export import export_workspace

    export_workspace(workspace_name, output_path=out_path)


def _summarize(cases: list[CaseResult]) -> Aggregate:
    total_inj = sum(c.fake_ids_injected for c in cases)
    total_leak = sum(c.leaked for c in cases)
    real_through = sum(len(c.supporting_returned) for c in cases) - total_leak
    return Aggregate(
        n_cases=len(cases),
        total_fakes_injected=total_inj,
        total_fakes_leaked=total_leak,
        leak_rate=(total_leak / total_inj) if total_inj else 0.0,
        real_ids_passed_through=real_through,
    )


def _render_readme(agg: Aggregate, manifest_path: Path) -> str:
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    return (
        "# Citation-enforcement benchmark — results\n"
        "\n"
        "This run measures the fraction of fabricated chunk_ids that reach the\n"
        "caller across `verify_claim` invocations driven by an adversarial fake\n"
        "LLM. The runtime's chunk_id-validation step (verify_claim.py:147-155)\n"
        "must drop fabricated IDs before the verdict is returned.\n"
        "\n"
        "## Aggregate\n"
        f"- cases run: **{agg.n_cases}**\n"
        f"- fake chunk_ids injected: **{agg.total_fakes_injected}**\n"
        f"- fake chunk_ids leaked: **{agg.total_fakes_leaked}**\n"
        f"- leak rate: **{agg.leak_rate:.4f}**\n"
        f"- real chunk_ids preserved: **{agg.real_ids_passed_through}**\n"
        "\n"
        "## Provenance\n"
        f"- orc version: `{manifest.get('orc_version', 'unknown')}`\n"
        f"- exported_at: `{manifest.get('exported_at', 'unknown')}`\n"
        f"- trace schemas seen: `{manifest.get('trace_schema_versions_seen', '?')}`\n"
        "\n"
        "## Reproducing\n"
        "Clone the orc repo, then:\n"
        "```\n"
        "uv sync\n"
        "uv run python -m benchmarks.citation_enforcement.run --n {N}\n"
        "```\n"
        "Each run produces a new directory under `benchmarks/citation_enforcement/results/`\n"
        "containing `results.json` (per-case detail + aggregate), an `audit.tar.gz`\n"
        "of the benchmark's own runs, and this README.\n"
        "\n"
        "## Interpretation\n"
        "A leak rate of 0 demonstrates a runtime invariant: a fabricated chunk_id\n"
        "cannot reach the caller through `verify_claim`, regardless of what the\n"
        "upstream LLM returns. This is a *system property*, not a faithfulness\n"
        "score — comparable judges (Lynx, HHEM, RAGAS) operate after generation\n"
        "and answer a different question. See `docs/benchmarks/plan.md`.\n"
    ).replace("{N}", str(agg.n_cases))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=100, help="Total cases to run (default 100)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default: benchmarks/citation_enforcement/results/<ts>/)",
    )
    args = parser.parse_args(argv)

    # Ensure repo src + tests are importable for tests._fake_llm and src.orc.
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "src"))

    from orc.core.clock import now_iso

    ts = now_iso().replace(":", "").replace("-", "").replace("T", "-")[:15]
    out_dir = args.out or (RESULTS_ROOT / ts)
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_home = Path(tempfile.mkdtemp(prefix="orc-bench-cite-"))
    try:
        cases_raw = yaml.safe_load(DATASET_PATH.read_text())["cases"]
        # Expand the dataset to args.n by cycling templates and re-rolling fake IDs.
        expanded: list[tuple[str, list[str]]] = []
        for i in range(args.n):
            tpl = cases_raw[i % len(cases_raw)]
            expanded.append(
                (tpl["claim"], _generate_fake_ids(seed_prefix=f"{i:03d}", n=3))
            )

        workspace_name = _setup_workspace(tmp_home)

        case_results: list[CaseResult] = []
        for i, (claim, fake_ids) in enumerate(expanded):
            r = _run_one_case(workspace_name, i, claim, fake_ids)
            case_results.append(r)

        bundle_path = out_dir / "audit.tar.gz"
        _export_bundle(workspace_name, bundle_path)

        agg = _summarize(case_results)
        (out_dir / "results.json").write_text(
            json.dumps(
                {
                    "aggregate": asdict(agg),
                    "cases": [asdict(c) for c in case_results],
                    "audit_bundle_sha256": hashlib.sha256(
                        bundle_path.read_bytes()
                    ).hexdigest(),
                },
                indent=2,
            )
        )
        # Extract manifest from the audit bundle for the README header.
        import tarfile

        manifest_path = out_dir / "manifest.json"
        with tarfile.open(bundle_path) as tar:
            mf = tar.extractfile("manifest.json")
            if mf:
                manifest_path.write_bytes(mf.read())
        (out_dir / "README.md").write_text(_render_readme(agg, manifest_path))

        print(f"\n== citation_enforcement: n={agg.n_cases} ==")
        print(f"  fakes injected     : {agg.total_fakes_injected}")
        print(f"  fakes leaked       : {agg.total_fakes_leaked}")
        print(f"  leak rate          : {agg.leak_rate:.4f}")
        print(f"  real ids preserved : {agg.real_ids_passed_through}")
        print(f"  results dir        : {out_dir}")
        return 0 if agg.total_fakes_leaked == 0 else 1
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

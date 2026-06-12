"""Render trace JSON into a self-contained HTML report.

The report is a single file with inlined CSS/JS so it can be attached to an
email, filed with a compliance package, or archived — no server, no CDN, no
external requests. Structure mirrors site/index.html (the designed mockup);
verdict pills, ticks, counters, and the sticky ledger are resolved
client-side by the same trace.js the site uses.
"""

from __future__ import annotations

import html
from importlib.resources import files
from typing import Any

# Verdict labels come from the verify_claim output contract; CSS classes come
# from site/trace.css. Unknown labels fall back to "nf" — the neutral verdict —
# rather than failing the whole report over one odd trace.
_LABEL_TO_VERDICT = {
    "supported": "ok",
    "partial": "warn",
    "contradicted": "bad",
    "not_found": "nf",
}


def _asset(name: str) -> str:
    """Read a packaged asset (works from wheels and editable installs alike)."""
    return files("orc.rendering.assets").joinpath(name).read_text(encoding="utf-8")


def _esc(value: Any) -> str:
    """Escape a trace-derived value for HTML.

    Evidence text is untrusted corpus content and claims are caller input —
    everything read from a trace goes through here before hitting the page.
    """
    return html.escape(str(value), quote=True)


def build_report_html(traces: list[dict[str, Any]]) -> str:
    """Render one or more trace dicts as a self-contained HTML document."""
    articles = [
        _claim_article(trace, index=i) for i, trace in enumerate(traces, start=1)
    ]
    run_ids = [str(t.get("run_id", "?")) for t in traces]
    title = _esc("orc — trace " + ", ".join(run_ids))
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            f"<title>{title}</title>",
            f"<style>{_asset('trace.css')}</style>",
            "</head>",
            "<body>",
            _topbar(traces, run_ids=run_ids),
            '<div class="wrap grid">',
            '<main class="main">',
            _thead(traces, run_ids=run_ids),
            *articles,
            _footer(traces),
            "</main>",
            _ledger_aside(),
            "</div>",
            # At the bottom so the claim DOM exists when trace.js boots and
            # builds the ticks, counters, and ledger from it.
            f"<script>{_asset('trace.js')}</script>",
            "</body>",
            "</html>",
        ]
    )


def _uniq(values: list[str]) -> str:
    """Join distinct non-empty values, preserving order — multi-trace reports
    can span workspaces or models and the header must not pretend otherwise."""
    seen = [v for i, v in enumerate(values) if v and v not in values[:i]]
    return ", ".join(seen) if seen else "?"


def _topbar(traces: list[dict[str, Any]], *, run_ids: list[str]) -> str:
    workspaces = _uniq([str(t.get("workspace", "")) for t in traces])
    models = _uniq([str(t.get("model") or "") for t in traces])
    corpora = _uniq([str(t.get("corpus_version", "")) for t in traces])
    sep = '<span class="sep">·</span>'
    meta = sep.join(
        [
            f"<b>trace {_esc(', '.join(run_ids))}</b>",
            f"workspace={_esc(workspaces)}",
            f"model={_esc(models)}",
            f"corpus v{_esc(corpora)}",
        ]
    )
    return "\n".join(
        [
            '<header class="topbar">',
            '<div class="wrap topbar-inner">',
            '<a class="tb-mark" href="#">orc</a>',
            f'<div class="tb-meta">{meta}</div>',
            '<div class="tb-counters" id="tb-counters">',
            '<span class="tb-count ok"><span id="cnt-ok">0</span> supported</span>',
            '<span class="tb-count warn"><span id="cnt-warn">0</span> partial</span>',
            '<span class="tb-count bad"><span id="cnt-bad">0</span> contradicted</span>',
            "</div>",
            "</div>",
            "</header>",
        ]
    )


def _thead(traces: list[dict[str, Any]], *, run_ids: list[str]) -> str:
    first = traces[0] if traces else {}
    started = first.get("started_at") or "?"
    ended = first.get("ended_at") or "?"
    cmd_args = " ".join(f'<span class="arg">{_esc(rid)}</span>' for rid in run_ids)
    return "\n".join(
        [
            '<section class="thead">',
            '<div class="cmd-line">',
            '<span class="prompt">$</span>',
            f'<span class="cmd">orc report</span> {cmd_args}',
            "</div>",
            '<dl class="thead-meta">',
            f"<dt>runs</dt><dd>{_esc(', '.join(run_ids))}</dd>",
            f"<dt>started</dt><dd>{_esc(started)} "
            f'<span class="dim">· ended {_esc(ended)}</span></dd>',
            "</dl>",
            '<div class="summary">',
            '<div class="label">claim-by-claim summary</div>',
            '<div class="ticks" id="ticks"></div>',
            "</div>",
            "</section>",
        ]
    )


def _ledger_aside() -> str:
    # Empty containers by design: trace.js builds the ledger rows from the
    # .claim DOM, exactly as the public site does.
    return "\n".join(
        [
            '<aside class="ledger-wrap">',
            '<div class="ledger" id="ledger">',
            '<div class="ledger-head">',
            "<span>trace ledger</span>",
            '<span class="progress"><span id="led-progress">0</span>'
            '/<span id="led-total">0</span></span>',
            "</div>",
            '<ul id="ledger-list"></ul>',
            '<div class="ledger-foot">click row to jump</div>',
            "</div>",
            "</aside>",
        ]
    )


def _footer(traces: list[dict[str, Any]]) -> str:
    calls = [call for t in traces for call in (t.get("llm_calls") or [])]
    total_in = sum(int(c.get("input_tokens") or 0) for c in calls)
    total_out = sum(int(c.get("output_tokens") or 0) for c in calls)
    rows = [
        "<dt>tokens</dt>"
        f"<dd>{total_in:,} in · {total_out:,} out "
        f'<span class="dim">· across {len(calls)} llm call(s)</span></dd>'
    ]
    for trace in traces:
        replay_of = (trace.get("inputs") or {}).get("_replay_of")
        if replay_of:
            rows.append(
                "<dt>lineage</dt>"
                f"<dd>run {_esc(trace.get('run_id', '?'))} is a replay of "
                f"{_esc(replay_of)}</dd>"
            )
    return "\n".join(
        [
            '<footer class="tfoot">',
            '<div class="tfoot-rule">━━━ end of trace ━━━</div>',
            '<dl class="tfoot-grid">',
            *rows,
            "</dl>",
            '<div class="tfoot-mark">',
            "<span><b>orc.</b> · the verification runtime</span>",
            '<span class="tfoot-meta">— generated by orc report —</span>',
            "</div>",
            "</footer>",
        ]
    )


def _claim_article(trace: dict[str, Any], *, index: int) -> str:
    output = trace.get("output") or {}
    verdict = _LABEL_TO_VERDICT.get(output.get("label"), "nf")
    confidence = output.get("confidence")
    score = f' data-score="{float(confidence):.2f}"' if confidence is not None else ""
    claim = output.get("claim") or trace.get("inputs", {}).get("claim") or "(no claim recorded)"
    n = f"{index:02d}"
    run_id = trace.get("run_id", "?")
    return "\n".join(
        [
            f'<article class="claim" id="claim-{n}" data-claim="{n}" '
            f'data-verdict="{verdict}"{score}>',
            '<header class="claim-head">',
            f'<span class="claim-id">claim_{n} · run {_esc(run_id)}</span>',
            '<span class="claim-rule"></span>',
            '<span class="verdict pending"><span class="vt">verifying</span></span>',
            "</header>",
            f'<h2 class="claim-title quoted">{_esc(claim)}</h2>',
            *_reasoning_block(output),
            *_chunks_block(output),
            "</article>",
        ]
    )


def _reasoning_block(output: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    reasoning = output.get("reasoning")
    if reasoning:
        lines.append(
            '<div class="reasoning"><span class="k">reasoning</span>'
            f'<span class="v">{_esc(reasoning)}</span></div>'
        )
    missing = output.get("missing_information")
    if missing:
        lines.append(
            '<div class="reasoning"><span class="k">missing</span>'
            f'<span class="v">{_esc(missing)}</span></div>'
        )
    return lines


def _chunks_block(output: dict[str, Any]) -> list[str]:
    supporting = output.get("supporting_chunks") or []
    contradicting = output.get("contradicting_chunks") or []
    if not supporting and not contradicting:
        return []
    lines = ['<div class="chunks">']
    lines.extend(_chunk_div(c, role="supporting") for c in supporting)
    lines.extend(_chunk_div(c, role="contradicting") for c in contradicting)
    lines.append("</div>")
    return lines


def _chunk_div(chunk: dict[str, Any], *, role: str) -> str:
    # `.chunk.bad` carries the contradicted border color in trace.css.
    css = "chunk bad" if role == "contradicting" else "chunk"
    source = chunk.get("evidence_source_path") or chunk.get("evidence_title") or ""
    return "\n".join(
        [
            f'<div class="{css}">',
            '<div class="chunk-head">',
            f'<span class="cid">{_esc(chunk.get("chunk_id", "?"))}</span>',
            f'<span class="src">{_esc(source)}</span>',
            "</div>",
            f'<div class="chunk-quote">{_esc(chunk.get("text", ""))}</div>',
            "</div>",
        ]
    )

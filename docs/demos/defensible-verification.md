# A raw LLM call vs. orc, on one claim

> **The thesis in one line:** with a frontier model, orc usually reaches the
> *same verdict* as a bare API call — its value is not a smarter answer, it is a
> **defensible** one: a structured label, a calibrated confidence, a chunk-level
> citation validated against the retrieval set, and a replayable trace. Plus one
> thing a raw call structurally cannot do — refuse to ship a fabricated citation.

Aggregate benchmark numbers (F1 0.864 on HaluBench) are abstract. This page runs
the *same model* two ways on a single, human-legible claim so the difference is
concrete. Everything below is real captured output from
[`demos/orc_vs_raw.py`](../../demos/orc_vs_raw.py); reproduce it yourself with the
commands at the end.

---

## Example 1 — a subtle, readable hallucination

A HaluBench `halueval` item. The claimed answer names an award that *sounds*
right but appears nowhere in the source. Ground-truth label: **FAIL**.

```
ITEM halueval-803  ·  source: halueval
────────────────────────────────────────────────────────────────────────
Question      : What major Albanian musical event did Aurela Gaçe win?
Claimed answer: Aurela Gaçe won the Albanian Music Festival.
Ground truth  : HALLUCINATED (FAIL)

① RAW LLM  (one plain call, same model, no pipeline)
────────────────────────────────────────────────────────────────────────
model       : claude-sonnet-4-6
answer      : No, the claimed answer is not faithful to the passage. The
              passage states that Aurela Gaçe won Kënga Magjike (meaning
              "Magical Song" in English), not the "Albanian Music Festival,"
              which is not mentioned anywhere in the passage.
structured? : no   citation? : no   confidence? : no   trace/replay? : no

② ORC  (verify_claim, evidence mode — what `orc verify` runs)
────────────────────────────────────────────────────────────────────────
verdict     : CONTRADICTED
confidence  : 0.95
reasoning   : ...she is a three-time Kënga Magjike winner, and Kënga Magjike is
              a major musical event in Albania. The claim states she won the
              "Albanian Music Festival," which is not the name of the event
              mentioned in the corpus...
contradicting cite: [01KV4FJPD0A6…] 'She is a three-time Festivali i Këngës
              winner, a three-time Kënga Magjike winner and a two-time Balkan
              Music Award winner...'
trace       : traces/.../01KV4FJPDJRA4S14JH2H0T01NY.json
              (replay with `orc replay 01KV4FJPDJRA4S14JH2H0T01NY`)
```

Both **catch the hallucination**. The model is a strong judge when you hand it
the passage. The difference is everything to the *right* of the verdict: orc
returns a label you can route on, a confidence you can threshold, the exact
chunk it relied on, and a trace a reviewer re-runs six months later. The raw
call returns a paragraph you have to read and trust.

---

## Example 2 — a numeric claim (where models are supposed to slip)

A FinanceBench item: AMD's 2-year revenue CAGR. The arithmetic is the whole
game — `√(9763 / 6475) − 1 = 22.8%`, so the claimed **24.5%** is wrong.
Ground-truth label: **FAIL**. orc routes financial claims to **arithmetic
mode**, where the model invokes a real calculator mid-verification and every
call lands in the trace.

```
ITEM financebench_id_02747  ·  source: FinanceBench
────────────────────────────────────────────────────────────────────────
Question      : What is AMD's 2 year total revenue CAGR from FY2018 to FY2020?
Claimed answer: 24.5%
Ground truth  : HALLUCINATED (FAIL)

① RAW LLM
────────────────────────────────────────────────────────────────────────
answer      : ...(9,763/6,475)^(1/2) - 1 = 22.8%. The claimed answer of 24.5%
              does not match this calculation, making it not faithful...
structured? : no   citation? : no   confidence? : no   trace/replay? : no

② ORC  (verify_claim, arithmetic mode)
────────────────────────────────────────────────────────────────────────
verdict     : NOT_FOUND
confidence  : 0.97
reasoning   : Using the passage's figures (FY2018 = $6,475M, FY2020 = $9,763M),
              the 2-year CAGR computes to (9763/6475)^(1/2) - 1 ≈ 22.8%, not
              24.5%. The discrepancy of ~1.7 pp exceeds rounding tolerance.
citations   : n/a in arithmetic mode (every input chunk still recorded in trace)
trace       : (replay with `orc replay 01KV4FJYPQ3GK6HK2WRESD2QD9`)
```

Again, **both get it right** — Sonnet 4.6 does the CAGR cleanly. We are not
going to pretend otherwise on a cherry-picked example. What changes is that
orc's calculation is captured as a tool call in the trace, so an auditor sees
*the math*, not just the conclusion — and on weaker or cheaper models, where the
mental arithmetic does break down, that calculator is the difference between a
right and a wrong verdict (FinanceBench F1 climbs 0.736 → 0.916 with arithmetic
mode; see [the benchmark](../benchmarks/results-2026-05-19-phase2-arithmetic.md)).

---

## So what does orc actually buy you?

On these items, verdict correctness is a **tie**. That is the honest result, and
it is the right framing: orc is not sold as a smarter judge than a frontier
model. It is the layer that turns a model's opinion into a defensible record.

| | Raw LLM call | orc `verify_claim` |
|---|---|---|
| Verdict | prose, free-form | one of `supported / partial / contradicted / not_found` |
| Confidence | none | calibrated scalar you can threshold |
| Citation | none (or whatever the model types) | chunk IDs **validated against the retrieval set** |
| Fabricated citation | shipped to you | **dropped before you see it** (runtime invariant) |
| Trace | the chat log, if you kept it | schema-versioned JSON on disk |
| Replay | re-prompt and hope | `orc replay <id>` against the frozen corpus snapshot |
| Audit handoff | copy-paste | hashed `orc audit export` tar.gz a third party verifies |

---

## The one thing a raw call structurally cannot do

A faithfulness judge — and a bare LLM call — can be *wrong* about a citation. orc
**cannot emit one that isn't real.** Chunk IDs the model invents, that don't
appear in the retrieval set, are dropped before the verdict reaches the caller.
This is a runtime invariant, not a prompt and not a post-hoc filter.

It is measurable. [`benchmarks/citation_enforcement/`](../../benchmarks/citation_enforcement/)
drives `verify_claim` with an adversarial fake LLM that injects fabricated chunk
IDs into every response (this benchmark uses no real API — it is free to run):

```
== citation_enforcement: n=100 ==
  fakes injected     : 300
  fakes leaked       : 0          ← 0.0000 leak rate
  real ids preserved : 200
```

**0 of 300 fabricated citations reached the caller.** A raw call has no such
guard: if the model writes "per [doc-47]," you ship "per [doc-47]," whether or
not doc-47 exists.

---

## At scale, and vs. the competition

The single example shows the *shape* of the difference; the aggregate shows it
holds up. orc's `verify_claim` on the stratified 504-item HaluBench subsample,
against the published faithfulness-judge field:

| System | What it is | HaluBench F1 | Citations | Replay | Audit bundle |
|---|---|---:|:---:|:---:|:---:|
| **orc** | verification runtime (general Claude Sonnet 4.6) | **0.864** | ✅ validated | ✅ | ✅ |
| Patronus **Lynx-70B** | fine-tuned faithfulness classifier | 0.85¹ | ❌ | ❌ | ❌ |
| Vectara **HHEM-2.1** | open-weight consistency scorer | —² | ❌ | ❌ | ❌ |
| Raw LLM call | one prompt | n/a³ | ❌ | ❌ | ❌ |

¹ Lynx's own paper, full HaluBench ([arXiv:2407.08488](https://arxiv.org/abs/2407.08488)) — a 70B model dedicated to this one task. orc matches it with a general-purpose call and ships the artifacts Lynx doesn't.
² A live head-to-head HHEM run on the same 504-item subsample is the honest next step; the self-hosted harness is wired (`benchmarks/faithfulness/run.py --hhem`) but not yet run end-to-end. We cite published positioning, not our own HHEM number, until then.
³ Not a like-for-like — a raw call produces no structured label to score at scale without wrapping it in… essentially orc.

Full per-source breakdown and reproduction:
[`docs/benchmarks/results-2026-05-19-phase2-arithmetic.md`](../benchmarks/results-2026-05-19-phase2-arithmetic.md).
Category positioning: [`docs/positioning/competitive.md`](../positioning/competitive.md).

---

## Reproduce this

```bash
# The two worked examples above (live; a few cents on Sonnet 4.6):
uv run python -m demos.orc_vs_raw --live --item halueval-803
uv run python -m demos.orc_vs_raw --live --item financebench_id_02747

# The citation invariant (free — adversarial fake LLM, no API spend):
uv run python -m benchmarks.citation_enforcement.run --n 100

# The aggregate faithfulness number (live, full spend — gated):
uv run python -m benchmarks.faithfulness.bootstrap          # one-time dataset fetch
ORC_BENCHMARK_ALLOW_LIVE_LLM=1 uv run python -m benchmarks.faithfulness.run --n 504
```

## Honest limits

- **This is two items.** They illustrate the *shape* of the difference; the
  aggregate F1 is the evidence it generalizes.
- **A frontier model is a strong judge.** orc's correctness edge shows up on
  harder items, weaker/cheaper models, and numeric claims — not on every easy
  one. The artifact edge (citation, confidence, trace, replay, audit) is present
  on *every* call, easy or hard.
- **orc verifies against your corpus, not the world.** A faithfully-cited but
  wrong/stale/poisoned source is not caught — by orc or by any post-hoc judge.
  See the "faithful-but-wrong" row in
  [`competitive.md`](../positioning/competitive.md).
- **The HHEM head-to-head is not yet run.** The table cites published numbers;
  the live comparison is wired but pending.

# A raw LLM call vs. orc, on one claim

> **The thesis in one line:** when the whole source fits in the prompt, a
> frontier model is already a strong judge — orc's value there is a *defensible*
> verdict (structured label, calibrated confidence, validated citation,
> replayable trace), not a smarter one. But the moment the source is a *large
> private corpus* the model has never seen, a raw call can't verify at all — and
> that is the case orc is actually built for (Example 4).

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
*the math*, not just the conclusion.

---

## Example 3 — where the raw verdict actually breaks

The tie above is not universal. We scanned the two hardest HaluBench categories
(DROP tabular reasoning, FinanceBench) for items where the **production-shaped
raw call gets the verdict wrong and orc gets it right** — and they exist. This
is a CVS Health ROA claim: net income ÷ average total assets. The claimed
**0.04** is wrong; the figures give **0.03**. Ground truth: **FAIL**.

The distinction that matters: in a real pipeline you need a *quick, parseable*
verdict, not a paragraph. So we asked the raw model the way a pipeline would —
Lynx's binary `YES/NO` faithfulness prompt:

```
ITEM financebench_id_07081  ·  source: FinanceBench
────────────────────────────────────────────────────────────────────────
Question      : FY2021 return on assets (ROA) for CVS Health?
                (net income / average total assets, FY2020–FY2021)
Claimed answer: 0.04
Ground truth  : HALLUCINATED (FAIL)

① RAW LLM  (binary YES/NO faithfulness prompt — pipeline-shaped)
────────────────────────────────────────────────────────────────────────
raw answer  : "YES"          ← says the 0.04 claim IS faithful.  WRONG.

② ORC  (verify_claim, arithmetic mode)
────────────────────────────────────────────────────────────────────────
verdict     : NOT_FOUND
confidence  : 0.95
reasoning   : Using the passage figures — net income $7,898M, FY2021 total
              assets $232,999M, FY2020 $230,715M — average assets $231,857M,
              ROA = 7,898 / 231,857 ≈ 0.034 → 0.03, not 0.04 as claimed.
```

The quick raw call **answers before it computes** and rubber-stamps the wrong
number. (Asked for a *paragraph* instead, the same model rambles its way to the
right conclusion — but it opens with "Yes, the claimed answer is faithful…" and
then contradicts itself, which is exactly the unparseable mush you can't put
behind an automated gate.) orc's arithmetic mode forces compute-then-verdict
with a real calculator, so the structured label is right.

This is the at-scale pattern, not a fluke: with the calculator, FinanceBench F1
climbs **0.736 → 0.916** ([benchmark](../benchmarks/results-2026-05-19-phase2-arithmetic.md)).
The weaker or cheaper the model, the wider this gap gets.

---

## Example 4 — the case single passages can't show: a large private corpus

The three examples above hand the model the source passage inline. That is the
*best* case for a raw call. The real world is the opposite: a knowledge base of
many documents the model has never seen, where the answer to a claim lives in
one specific file and a reviewer needs to know *which* one.

[`demos/orc_large_corpus.py`](../../demos/orc_large_corpus.py) ingests a small,
deliberately fictional internal knowledge base (`demos/corpus/` — 11 documents
for a made-up logistics company) and verifies two claims about a single incident
buried in one postmortem. Fictional on purpose: the model cannot lean on world
knowledge, so this isolates the value of grounding + citation.

```
CORPUS: 11 private documents → 18 retrievable chunks
(a fictional internal knowledge base the model has never seen)

CLAIM: The 2026-03-14 Helix Freight checkout outage was caused by an
       expired TLS certificate.                          (truth: FALSE)
──────────────────────────────────────────────────────────────────────────
① RAW LLM (no corpus access — the 'ask the chatbot' baseline)
   "I cannot verify or refute this claim. I don't have access to any internal
    Helix Freight Systems documents... I will not fabricate a source document
    or render a true/false verdict without a legitimate evidentiary basis."

② ORC (retrieves across the corpus, cites the exact source)
   verdict     : CONTRADICTED   confidence: 0.99
   citation    : incident-2026-03-14-postmortem.md · chunk [01KV4JCC81RY…]
   reasoning   : ...the root cause was "database connection-pool exhaustion"
                 and directly rules out a TLS/certificate problem...

CLAIM: The 2026-03-14 Helix Freight checkout outage lasted 73 minutes.
                                                          (truth: TRUE)
──────────────────────────────────────────────────────────────────────────
① RAW LLM
   "I cannot verify or refute this claim... Verdict: Unable to determine —
    I have no source document to cite."

② ORC
   verdict     : SUPPORTED   confidence: 0.99
   citation    : incident-2026-03-14-postmortem.md · chunk [01KV4JCC81RY…]
   reasoning   : ...explicitly states "unavailable or degraded for 73 minutes,
                 from 14:02 to 15:15 UTC." A near-direct quotation.
```

This is the whole pitch in one screen. A **well-aligned** model does the
responsible thing — it *refuses* rather than confabulating — but the
consequence is the same: **a raw call cannot verify a claim against documents
you own.** It is blind to your corpus, and a less careful model (or a more
leading prompt) fabricates a citation instead of refusing. orc retrieves the one
relevant chunk out of 18, returns the right verdict, and names the exact file an
auditor can open. The citation isn't decoration — it's the difference between
"the model says it's false" and "it's false, see
`incident-2026-03-14-postmortem.md`."

This is also where the artifacts compound: that verdict carries a replayable
trace, and `orc audit export` bundles the corpus, the retrieval, and the verdict
into one hashed tar.gz a regulator re-runs. You cannot get there from a chat box.

---

## So what does orc actually buy you?

Two regimes, both honest:

- **On easy items (Examples 1–2), verdict correctness is a tie.** orc is not a
  smarter judge than a frontier model handed a short passage. Its value there is
  the *defensible record*.
- **On hard items (Example 3), the production-shaped raw verdict breaks** and
  orc holds — because orc forces the model through retrieval and tool use
  instead of trusting a snap judgment.

Either way, the table below is what you get on **every** call, easy or hard:

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
| Vectara **HHEM-2.1** | open-weight consistency scorer | 0.643² | ❌ | ❌ | ❌ |
| Raw LLM call | one prompt | n/a³ | ❌ | ❌ | ❌ |

¹ Lynx's own paper, full HaluBench ([arXiv:2407.08488](https://arxiv.org/abs/2407.08488)) — a 70B model dedicated to this one task. orc matches it with a general-purpose call and ships the artifacts Lynx doesn't.
² **Now a real same-set number.** HHEM-2.1-Open scored on the *identical* 503 items: F1 0.643 vs orc's 0.864 (threshold 0.5, standard input). Not a truncation artifact (0.623 even on items inside HHEM's 512-token window); HHEM is competitive on RAG passages (RAGTruth 0.80) but collapses on numeric/tabular reasoning (FinanceBench 0.30). This stratified subsample equal-weights the hard categories, so it's tougher for HHEM than its ~0.75 full-HaluBench headline — and orc's 0.864 is on the same harder set. Full accounting: [results-2026-06-15-hhem-head-to-head.md](../benchmarks/results-2026-06-15-hhem-head-to-head.md).
³ Not a like-for-like — a raw call produces no structured label to score at scale without wrapping it in… essentially orc.

Full per-source breakdown and reproduction:
[`docs/benchmarks/results-2026-05-19-phase2-arithmetic.md`](../benchmarks/results-2026-05-19-phase2-arithmetic.md).
Category positioning: [`docs/positioning/competitive.md`](../positioning/competitive.md).

---

## Reproduce this

```bash
# The worked examples above (live; a few cents each on Sonnet 4.6):
uv run python -m demos.orc_vs_raw --live --item halueval-803
uv run python -m demos.orc_vs_raw --live --item financebench_id_02747
uv run python -m demos.orc_vs_raw --live --item financebench_id_07081

# The large private-corpus demo — orc's actual moat (live):
uv run python -m demos.orc_large_corpus --live

# The citation invariant (free — adversarial fake LLM, no API spend):
uv run python -m benchmarks.citation_enforcement.run --n 100

# The aggregate faithfulness number (live, full spend — gated):
uv run python -m benchmarks.faithfulness.bootstrap          # one-time dataset fetch
ORC_BENCHMARK_ALLOW_LIVE_LLM=1 uv run python -m benchmarks.faithfulness.run --n 504
```

## Honest limits

- **This is two items.** They illustrate the *shape* of the difference; the
  aggregate F1 is the evidence it generalizes.
- **A frontier model is a strong judge** on short single passages. orc's
  *correctness* edge shows up on harder items, weaker/cheaper models, and numeric
  claims (Example 3) — not on every easy one. The *artifact* edge (citation,
  confidence, trace, replay, audit) is present on **every** call, easy or hard.
- **Example 3 was found by scanning, not invented.** Across DROP + FinanceBench,
  most verdicts still agree with Sonnet 4.6; the raw-wrong/orc-right items are a
  minority, and orc has its own misses too (it is ~0.86 F1, not 1.0). The point
  is that the failure mode exists and orc's structure removes a real slice of it.
- **orc verifies against your corpus, not the world.** A faithfully-cited but
  wrong/stale/poisoned source is not caught — by orc or by any post-hoc judge.
  See the "faithful-but-wrong" row in
  [`competitive.md`](../positioning/competitive.md).
- **The HHEM head-to-head is now run** (same 503 items: orc 0.864 vs HHEM
  0.643). Lynx remains a published-number comparison — self-hosting a 70B judge
  is the one piece still outstanding.

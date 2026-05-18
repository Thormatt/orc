# Orc · EU AI Act Compliance Mapping

> **What this is.** A claim-by-claim mapping of `orc` v0.1.0 features to the EU AI Act
> requirements most relevant to operators of high-risk AI systems (Annex III).
>
> **What this isn't.** A legal opinion, a conformity assessment, or a certification.
> Orc is a *tool* used by deployers and providers of AI systems. The compliance
> obligations themselves fall on the deployer/provider, not on Orc as a product.
> What Orc provides is the **machinery to produce the evidence** those obligations
> require — automatic logging, audit-grade traces, human-oversight gates, replay.
>
> **What to do with this doc.** If you're a compliance officer, DPO, or counsel
> evaluating whether Orc helps you meet your obligations under Articles 9–15, 26,
> and 72 ahead of the 2 August 2026 enforcement deadline, read on. If you're a
> developer wiring Orc into a regulated workflow, the "Runbook" section at the end
> is for you.

---

## Scope of this mapping

- **EU AI Act (Regulation (EU) 2024/1689)**: Articles 9, 10, 11, 12, 13, 14, 15, 26, 72.
- **Orc version**: 0.1.0, released 2026-05-13. Source: `github.com/Thormatt/orc`.
- **Deadline**: 2 August 2026 (Annex III), 2 December 2027 (biometrics, critical
  infrastructure, education, employment, migration, asylum, border control).
- **Penalty**: up to €15M or 3% of worldwide annual turnover.

This document is itself an artifact your auditors can review. Every "How Orc
satisfies this" claim below points at specific source files and live behaviors that
can be inspected.

---

## Article 12 — Record-keeping

**The requirement.** High-risk AI systems must technically allow for the automatic
recording of events ("logs") over the lifetime of the system. Logging must enable
identifying risk situations, supporting post-market monitoring, and monitoring system
operation. Logs must be retained for a minimum of six months under Article 26(6).

**How Orc satisfies this — by default, not as a feature flag:**

| §        | Requirement                                          | Orc                                                                                                                                                              | Source                                                       |
|----------|------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| §1       | "Automatic recording of events over lifetime"        | Every CLI or MCP invocation opens a `Run`, which writes a row to the `run` table and a JSON file under `traces/<YYYY>/<MM>/<run_id>.json`. Cannot be disabled.   | `src/orc/runs/runner.py`                                     |
| §2(a)    | Identify risk situations (Article 79(1))             | Verdict labels `contradicted`, `not_found`, and `partial` explicitly mark unreliable outputs. Errors recorded with `status="error"` and `error_message`.         | `src/orc/storage/schema.sql`, `src/orc/storage/trace_store.py` |
| §2(b)    | Facilitate post-market monitoring (Article 72)       | `orc trace list` enables longitudinal queries. `orc replay <run_id>` re-executes against the recorded `corpus_version` to detect drift.                          | `src/orc/cli_commands/trace.py`, `src/orc/runs/replay.py`    |
| §2(c)    | Monitor operation (Article 26(5))                    | Per-run `total_input_tokens`, `total_output_tokens`, `total_cache_read`, `total_cache_creation`, model identifier, and elapsed time all recorded.                | `src/orc/runs/runner.py:close()`                             |
| Art. 26(6) | Minimum six-month retention                        | Logs are append-only by design. Trace JSON files live under `~/.orc/workspaces/<name>/traces/`; SQLite rows in the `run` table are never deleted by `orc`.       | Filesystem + SQLite                                          |

**What's still on you (the deployer):**

- Configuring a backup / retention policy that exceeds 6 months for your jurisdiction.
- Ensuring `~/.orc/` (or `$ORC_HOME`) is on a durable, backed-up volume.
- Setting access controls on the workspace directory at the OS level.
- Documenting your retention policy in your operator manual.

---

## Article 13 — Transparency and provision of information to deployers

**The requirement.** Operations of high-risk systems must be sufficiently transparent
that deployers can interpret and appropriately use the output. Instructions for use
must document capabilities, limitations, accuracy, robustness, cybersecurity,
intended purpose, and logging.

**How Orc satisfies this:**

| §       | Requirement                                                | Orc                                                                                                                                          | Source                                          |
|---------|------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------|
| §1      | Sufficient transparency to interpret output                 | Every verdict ships with: structured label, confidence score, reasoning text, chunk-level citations to source evidence, and the full retrieval set.| `src/orc/directives/research/skills/verify_claim.py` |
| §3(b)(ii) | Capabilities and limitations of performance               | Documented in `README.md` and in this file. Limitations include: BM25-only retrieval today, no PDF ingestion, no embedding-based retrieval.    | `README.md`, `docs/compliance/eu-ai-act.md`       |
| §3(b)(iii) | Foreseeable circumstances likely to lead to harm         | The runtime structurally prevents hallucinated citations (chunk IDs validated against retrieval). `not_found` is preferred over confabulation.| `src/orc/directives/research/skills/verify_claim.py:run()` |
| §3(b)(iv) | Performance regarding specific groups                     | Out of scope for the v0.1.0 `research` directive (no demographic features). Future directives operating on personal data must add this.       | n/a today                                       |
| §3(b)(v)| Input data specs and training data characteristics          | The workspace **is** the input-data spec. Corpus contents are inspectable via `orc search`. Corpus version is recorded with every run.       | `src/orc/storage/workspace.py`, `evidence` table  |
| §3(d)   | Human oversight measures (links to Article 14)              | `orc approve` queue + multi-approver workflow (planned, see Article 14 below).                                                                 | `src/orc/queue/approval.py`                       |
| §3(e)   | Computational and hardware resources, expected lifetime     | Python 3.11+, runs on any machine that can run CPython. No GPU required.                                                                       | `README.md` / `pyproject.toml`                    |
| §3(f)   | Logging mechanisms                                          | This document. Plus `docs/compliance/eu-ai-act.md` §"Article 12".                                                                              | here                                            |

**What's still on you (the deployer):**

- Writing the operator manual / SOP for users of your specific high-risk system.
- Documenting how your corpus was assembled (provenance) and what it does/doesn't cover.
- Disclosing limits of accuracy to end users in your application.

---

## Article 14 — Human oversight

**The requirement.** Effective oversight by natural persons. For some Annex III
systems (notably biometric identification under §1(a)), no action may be taken on the
basis of the system's output unless verified and confirmed by **at least two natural
persons** with appropriate competence, training, and authority.

**How Orc satisfies this:**

| §       | Requirement                                                | Orc                                                                                                                                          | Source                                            |
|---------|------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| §4(a)   | Human can understand capabilities and limitations          | Every run output is structured + cited. Reasoning is in plain language. Traces are inspectable.                                                | verdict outputs                                   |
| §4(b)   | Stay aware of automation bias                              | Verdicts include explicit confidence scores; `partial` and `not_found` are first-class outputs, not buried.                                  | verdict labels                                     |
| §4(c)   | Correctly interpret the output                             | Every claim is grounded in a chunk citation that can be opened and read.                                                                       | chunk citations                                   |
| §4(d)   | Decide not to use the output, or override it               | `orc approve reject <id>` records the override with a free-text reason. The rejection is preserved in the trace.                              | `src/orc/cli_commands/approve.py`                  |
| §4(e)   | Intervene or interrupt                                     | Approval queue blocks any action-taking output from leaving Orc until a human accepts. Rejection drops the proposal cleanly.                  | `src/orc/queue/approval.py`                        |
| §5      | Two natural persons verify (some Annex III systems)        | **Planned for v0.2 — multi-approver workflow.** Track at github.com/Thormatt/orc issue #TBD.                                                  | Phase 2 roadmap                                   |

**What's still on you (the deployer):**

- Identifying who the "natural persons with competence, training and authority" are
  inside your organization, in writing.
- Training them on Orc's verdict semantics (especially: `partial` and `not_found`
  must not be treated as "supported").
- Documenting your override / escalation policy.

---

## Article 9 — Risk management system

**The requirement.** A continuous, iterative risk management process across the system
lifecycle. Identification, estimation, evaluation, and mitigation of known and
foreseeable risks.

**How Orc supports this:**

- Trace + replay enables incident reconstruction. If a downstream action causes harm,
  `orc trace show <run_id>` produces the full reasoning chain.
- `corpus_version` and `cache_creation_input_tokens` / `cache_read_input_tokens` are
  exposed so cost and freshness regressions are detectable.
- The `not_found` verdict is the runtime's first-line risk-mitigation: when the corpus
  doesn't contain evidence for a claim, the system refuses to confabulate.

**What's still on you:** the risk-management process itself is a documented procedure,
not a product. Orc supplies evidence; you supply the process.

---

## Article 10 — Data governance

**The requirement.** Training, validation, and testing data sets shall be subject to
data governance and management practices: relevance, representativeness, quality,
bias detection.

**How Orc supports this:**

- The "data" in Orc is your evidence corpus. Each evidence item carries a SHA-256
  hash, ingestion timestamp, source path, and corpus version. Provenance is intact.
- `orc ingest` is the single audited entry point; nothing slips into the corpus
  without a row in the `evidence` table.
- Deduplication is by sha256 — the same source file ingested twice does not
  produce two rows.

**What's still on you:** representativeness, bias detection, and fairness analysis
of your corpus. Orc records what's in the corpus; it doesn't yet evaluate it.

---

## Article 11 — Technical documentation

**The requirement.** Technical documentation drawn up before the system is placed on
the market, kept up-to-date, covering Annex IV elements (general description,
detailed description, monitoring, performance, risk management, lifecycle, etc.).

**How Orc supports this:**

- This document is part of yours.
- `CHANGELOG.md` covers the "lifecycle" requirement (Annex IV §1(g)).
- `README.md` covers the general description.
- The trace database itself is the source-of-truth for performance and monitoring.
- A `conformity-assessment-helper` directive is planned (Phase 3) that generates
  Annex IV scaffolding from a system spec.

---

## Article 15 — Accuracy, robustness, cybersecurity

**The requirement.** High-risk systems shall be designed to achieve appropriate
levels of accuracy, robustness, and cybersecurity, with consistent performance over
their lifecycle.

**Status today:**

- **Accuracy**: tracked per-run via confidence scores; golden tests pass against a
  fixed corpus (`tests/golden/`).
- **Robustness**: `orc eval consistency / perturb / regression` is planned (Phase 3)
  to provide R(k, ε, λ) reliability instrumentation — repeated runs, ε-perturbed
  inputs, frozen-corpus regression. Will produce auditor-ready metrics.
- **Cybersecurity**: dependencies pinned in `pyproject.toml`; local-first by
  default (no telemetry, no outbound calls beyond the configured LLM provider);
  approval queue prevents autonomous mutation. SOC 2 Type II and ISO/IEC 42001
  conformance are on the Phase 4 roadmap.

---

## Article 26 — Obligations of deployers

**Relevant clauses:**

- §5 — monitor operation: `orc trace list` + `orc replay`.
- §6 — retain logs ≥ 6 months: traces are append-only on disk by default. For
  point-in-time handoff, `orc audit export -w <workspace>` bundles every Run
  row, full trace JSON, evidence manifest with sha256, approval queue with
  per-approver decisions, workspace metadata, and runtime version info into a
  single tar.gz. Each file is hashed in a top-level `manifest.json`; trace
  schema versions are validated on the way out so the bundle cannot mix
  supported and unsupported formats.
- §7 — inform workers using the system: deployer's responsibility; this doc is for
  procurement evaluation.

---

## Article 72 — Post-market monitoring

**The requirement.** Active and systematic collection of data on system performance
during use.

**How Orc supports this:**

- The trace database **is** the post-market monitoring substrate. Every production
  call writes to it.
- Planned eval commands (`orc eval consistency`, `orc eval perturb`) will produce
  drift metrics against a baseline.
- `orc audit export` packages the trace database, evidence manifest, and
  approval log into a hashed, schema-validated tarball for regulators.

---

## What Orc is NOT

Honest framing matters here.

1. **Orc itself is not a "high-risk AI system" per Annex III.** It is a *tool*
   used by deployers of high-risk systems. The Article 12/13/14/26 obligations
   strictly fall on the deployer. Orc supplies the machinery to meet those
   obligations; it does not absolve the deployer of them.

2. **Orc is not a substitute for a conformity assessment.** Conformity assessment
   under Article 43 is a procedural obligation on the provider of the high-risk
   system. Orc can produce most of the evidence Annex IV expects, but the
   assessment itself is not something a tool can perform.

3. **Orc is not certified.** As of v0.1.0 there is no SOC 2 Type II, no
   ISO/IEC 42001 attestation, no independent security audit. Those are on the
   Phase 4 roadmap. Until then, Orc is open-source, MIT-licensed, fully
   inspectable code — which is a different (and in some respects stronger) form
   of trust, but it is not the same thing as a certificate.

4. **General-purpose AI obligations (Articles 51–56) apply to the underlying
   LLM**, not to Orc. If you use Orc with Claude (Anthropic), the GPAI provider
   obligations fall on Anthropic; Orc passes through whatever transparency
   information the upstream provider supplies.

---

## Runbook for deployers

If you are operating an AI workflow that may fall under Annex III, here is how to
use Orc to meet your Article 12/13/14 obligations:

1. **Create a workspace per high-risk use case.** Workspaces are trust boundaries;
   keeping them per-use-case keeps your audit story clean.

   ```bash
   orc workspace create credit-scoring-q3-2026
   ```

2. **Ingest evidence with auditable provenance.** Use `orc ingest` for any source
   the workflow consults. SHA-256 deduplication and corpus versioning happen
   automatically.

   ```bash
   orc ingest ./policy-corpus -w credit-scoring-q3-2026
   ```

3. **Make verification calls via Orc, not directly against an LLM.** Direct LLM
   calls leave no trace; Orc calls produce structured JSON with citations.

   ```bash
   orc verify "applicant meets all underwriting criteria in §3.2" \
     -w credit-scoring-q3-2026
   ```

4. **Route any action-taking output through the approval queue.** Orc does not
   take external actions on its own. A human "natural person with competence,
   training, and authority" accepts or rejects.

   ```bash
   orc approve list  -w credit-scoring-q3-2026
   orc approve accept <id> --by "marie.dupont@acme.eu" --note "verified §3.2 maps"
   ```

5. **Retain traces.** Default behavior already satisfies Article 26(6). Back up
   `$ORC_HOME` to durable storage. Retention longer than 6 months is your call.

6. **Hand the audit packet to your assessor.** Run `orc audit export -w <name>`
   to produce a single hashed tar.gz containing the workspace's full audit
   trail. The bundle includes `manifest.json` with sha256 for every file so the
   assessor can verify integrity independently.

---

## Mapping at a glance

```
ARTICLE                   ORC v0.1.x                 v0.2 (Phase 2)      v0.3+ (Phase 3-4)
─────────                 ───────────                ───────────────     ───────────────────
Art. 9  Risk management   trace + replay             —                   anomaly detection
Art. 10 Data governance   evidence sha256, ver       —                   bias / fairness skills
Art. 11 Technical docs    README + CHANGELOG          —                   conformity helper directive
Art. 12 Record-keeping    ✓ trace + audit export     —                   —
Art. 13 Transparency      structured outputs         —                   accuracy metrics doc
Art. 14 Human oversight   ✓ multi-approver queue     —                   —
Art. 15 Robustness        golden + schema vers.      replay-safety         eval consistency/perturb
Art. 26 Deployer obs.     ✓ trace + audit export     —                   —
Art. 72 Post-market mon.  trace + audit export       —                   eval regression
```

**Cert / posture (Phase 4):** SOC 2 Type II · ISO/IEC 42001 · independent security audit · cyber/E&O insurance.

---

## Contact

For procurement, conformity-assessment, or compliance-pilot inquiries:
[thormatt@gmail.com](mailto:thormatt@gmail.com)

Source: [github.com/Thormatt/orc](https://github.com/Thormatt/orc) · Last updated:
2026-05-17. This document is part of the repository and is versioned with the
runtime it describes.

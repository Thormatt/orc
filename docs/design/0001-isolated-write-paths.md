# Design 0001 — Isolated write paths

**Status:** Draft / RFC
**Author:** (orc maintainers)
**Scope:** Turn the Approval invariant from aspiration into an enforced property.

---

## 1. Problem

The README sells four invariants. Three are enforced in code. The fourth is not:

> Anything that would mutate the outside world is routed to an approval queue first.
> Write paths run as separate processes with separate tokens. Blast radius from a
> compromised agent is zero by design.

Today:

- The approval queue (`src/orc/queue/approval.py`) is real, well-built, and is the
  *only* mutation boundary — but **nothing calls `enqueue()`**, and **nothing drains
  approved entries**. The queue's own docstring concedes it: "Write-path MCPs (when
  they exist) drain from approved entries."
- There is **no second process** (`grep subprocess|fork|spawn` → nothing) and **no
  second credential**: a single `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` is read in
  `src/orc/llm/client.py` and shared by everything in one address space.

So "separate processes with separate tokens / zero blast radius" describes a design
that does not exist. This document specifies the design that makes it true, and the
phases by which the README claim becomes literally accurate.

### Non-goals

- Replacing SQLite or going multi-region. The design is single-host first; the hosted
  shape is sketched in §9 but not specified here.
- A general workflow engine. The effect plane executes a **closed, registered set** of
  action types — not arbitrary code.

---

## 2. The core idea: two planes that never share a secret

orc splits into two cooperating processes that share **only the per-workspace SQLite
database** and never share credentials:

| | **Analysis plane** (exists today) | **Effect plane** (new) |
|---|---|---|
| Process | `orc verify`, `orc mcp serve`, skills | `orc worker` / `orc execute` |
| Reads | corpus, traces, approvals | approved actions only |
| Writes | traces, **proposed** approvals | the outside world; execution results |
| Credentials in env | LLM API key only | per-executor **write tokens** only — **no LLM key** |
| Can it mutate the outside world? | **No.** It can only *propose*. | Yes, but only schema-validated, human-approved actions |
| Can it approve? | No (humans do) | No |

The security property falls out of this structure, not out of vigilance:

> A compromised analysis skill (prompt injection, poisoned corpus, supply-chain) can at
> most **enqueue a proposal**. It cannot execute it, cannot approve it, and **does not
> have the write credential in its memory to steal**. The worst case is a malicious
> proposal that a human must read and approve before a *different* process — holding a
> *different*, capability-scoped token — carries it out under a schema allow-list.

That is the concrete meaning of "separate processes, separate tokens, blast radius
bounded by what one human approves."

```
   ┌──────────────────────── Analysis plane (LLM key only) ─────────────────────┐
   │  skill.run(...) ── proposes ──▶ approval.enqueue(proposed_action=Action)    │
   └─────────────────────────────────────┬──────────────────────────────────────┘
                                          │  (per-workspace SQLite — the ONLY shared surface)
                              status: pending → [human] → approved
                                          │
   ┌──────────────────────────────────────▼───────── Effect plane (write tokens only) ─┐
   │  lease approved+pending ▶ validate vs executor schema ▶ execute ▶ record result    │
   └────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. The contract between planes: a typed `Action` envelope

`approval.proposed_action` is already a JSON column — today it is opaque
(`dict[str, Any]`). We make it a **validated, versioned envelope**. This is the linchpin:
the analysis plane can only *describe* effects in this vocabulary, and the effect plane
will only *execute* what validates against a registered executor.

```python
# src/orc/effects/action.py  (new)
@dataclass(frozen=True)
class Action:
    executor: str            # registered executor id, e.g. "gmail.send_draft"
    version: int             # executor contract version
    params: dict[str, Any]   # validated against the executor's JSON Schema
    idempotency_key: str     # stable across retries; dedupes effectively-once
    constraints: dict        # optional caller-asserted bounds (e.g. max_recipients)
```

Rules:

- **Closed set.** `executor` must be a key in the executor registry (§4). An unknown
  executor is rejected at *enqueue* time (analysis plane) **and** at *execute* time
  (effect plane) — defense on both sides of the boundary.
- **Schema-validated params.** Each executor publishes a JSON Schema; `params` is
  validated against it on enqueue and re-validated on execute. The model never hands the
  worker free-form instructions — only schema-shaped data.
- **Idempotency key is mandatory.** It is what turns the unavoidable at-least-once drain
  (§5) into effectively-once execution.

---

## 4. Executor registry (mirrors the directive registry)

Executors are to the effect plane what skills are to the analysis plane: small, stateless,
explicitly registered units with declared I/O contracts. We reuse the exact pattern from
`src/orc/directives/__init__.py`.

```python
# src/orc/effects/base.py  (new)
class Executor(Protocol):
    id: str                          # "gmail.send_draft", "fs.write_file", "http.post"
    version: int
    params_schema: dict              # JSON Schema for Action.params
    required_credential: str         # env var name the WORKER must hold, e.g. "GMAIL_TOKEN"

    def execute(self, *, params: dict, credential: str) -> dict: ...
```

- `required_credential` is the **only** place a write secret is named. The analysis plane
  imports the registry to *validate* proposals but the executor's `execute()` is never
  called there, and the credential is never read there.
- Registry exposes `effects.get(executor_id)` and `effects.allowed_for(workspace)` —
  a per-workspace **capability allow-list** (config, §7) so a given workspace can only
  ever propose/execute the executors an operator has switched on.
- Adding an executor = one file + a `register(Executor(...))` call. Same extensibility
  story as directives.

---

## 5. The drain protocol (effect plane)

The worker turns `status="approved"` rows into real effects, exactly once each, surviving
crashes. This is the genuinely hard part; we are explicit about the guarantees.

### 5.1 New execution state (schema addition)

Add an execution lifecycle alongside the existing approval lifecycle. New table keeps the
approval table's decision semantics untouched:

```sql
CREATE TABLE IF NOT EXISTS approval_execution (
    approval_id      TEXT PRIMARY KEY REFERENCES approval(approval_id) ON DELETE CASCADE,
    exec_status      TEXT NOT NULL DEFAULT 'pending',  -- pending|leased|succeeded|failed|dead
    lease_owner      TEXT,
    lease_expires_at TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    idempotency_key  TEXT NOT NULL,
    result           TEXT,            -- JSON, on success
    last_error       TEXT,
    executed_at      TEXT,
    UNIQUE (idempotency_key)          -- effectively-once guard at the DB layer
);
```

The `UNIQUE (idempotency_key)` constraint is the backstop: even a buggy double-lease
cannot produce two `succeeded` rows for the same logical action.

### 5.2 Atomic lease (no double-execution across workers)

Reuses the existing `BEGIN IMMEDIATE` write-lock discipline (`storage/db.py`):

```
BEGIN IMMEDIATE
  SELECT a.approval_id FROM approval a
    LEFT JOIN approval_execution e USING (approval_id)
   WHERE a.status = 'approved'
     AND (e.exec_status IS NULL OR e.exec_status = 'pending'
          OR (e.exec_status = 'leased' AND e.lease_expires_at < now))   -- reclaim stale lease
   ORDER BY a.created_at LIMIT 1
  UPSERT approval_execution SET exec_status='leased', lease_owner=?, lease_expires_at=now+TTL
COMMIT
```

`BEGIN IMMEDIATE` serializes writers on the per-workspace DB, so two workers cannot lease
the same row. Stale leases (crashed worker) are reclaimable after TTL.

### 5.3 Execute → record (the only place a write token is used)

```
action = Action.from_json(approval.proposed_action)
assert action.executor in effects.allowed_for(workspace)      # re-check allow-list
validate(action.params, effects.get(action.executor).params_schema)   # re-validate
credential = os.environ[executor.required_credential]         # ONLY the worker has this
result = executor.execute(params=action.params, credential=credential)
BEGIN IMMEDIATE
  UPDATE approval_execution SET exec_status='succeeded', result=?, executed_at=now
COMMIT
```

On failure: increment `attempts`, set `last_error`, set back to `pending` with backoff;
after `max_attempts` → `dead` (requires human re-trigger). Every transition is written to
the run/trace model so the audit record covers **execution**, not just the decision —
closing the loop "proposed → approved-by-whom → executed-when → with-what-result".

### 5.4 Delivery guarantee, stated honestly

This is **at-least-once delivery with idempotency keys ⇒ effectively-once execution**. We
do not claim distributed exactly-once (impossible without executor cooperation). Executors
that can be made idempotent (drafts, PUT-by-key, dedupe tokens) get effectively-once;
executors that genuinely can't (a raw "send") are documented as at-least-once and should
be modeled as two-step (create draft → approve send).

---

## 6. Where skills plug in (analysis plane)

Skills stay stateless and side-effect-free w.r.t. the outside world. The *only* new
capability is "propose": a thin helper on the existing `Run`, so the trace automatically
records that a proposal was made.

```python
# skill body — illustrative
run.propose(
    executor="gmail.send_draft",
    params={"to": [...], "subject": ..., "body": ...},
    summary="Draft reply to reviewer",
    approvers_required=2,            # Article 14 §5 multi-person, already supported
)
# → internally: approval.enqueue(..., proposed_action=Action(...).to_json())
```

`run.propose()` validates the executor + params schema **at enqueue time** and refuses
unknown/disallowed executors — so a compromised skill cannot even stage an out-of-policy
proposal. It returns the `approval_id`; the skill's verdict dict references it. No skill
ever touches a write credential, by construction (the credential env var is simply not
present in the analysis process).

---

## 7. Configuration & credential placement

- **Analysis process** env: `ORC_LLM_API_KEY` (or existing `OPENROUTER_API_KEY` /
  `ANTHROPIC_API_KEY`). **No write tokens.**
- **Worker process** env: the write tokens named by enabled executors' `required_credential`
  (e.g. `GMAIL_TOKEN`). **No LLM key** — the worker does no inference.
- Per-workspace capability allow-list in `config.toml` (extends existing `config_path()`):

```toml
[workspace.research.effects]
allowed = ["gmail.send_draft", "fs.write_file"]   # everything else is refused, both planes
```

Operationally this is enforced by *running the two planes as different OS users / containers
with different env files*. The separation is real because the analysis plane's process
**literally cannot read** a secret that was only ever exported into the worker's environment.

---

## 8. Threat model (before → after)

| Compromise | Today | After this design |
|---|---|---|
| Prompt injection / poisoned corpus drives a skill | Skill returns a verdict; no write path exists, so no external effect — but the claim of *isolation* is unproven | Skill can only `propose`; proposal is schema-bounded, requires human approval, executed by a process the skill cannot reach. **No write token in the skill's address space to exfiltrate.** |
| Analysis process fully popped (RCE) | Attacker has the LLM key and full DB write | Attacker has the LLM key + can write *proposals* and traces. Cannot execute effects, cannot forge `approved` (that requires a human `decided_by`), cannot read write tokens. Blast radius = "can spam the approval queue," which humans see. |
| Worker process popped | n/a | Attacker has the write tokens for *enabled executors only* and can execute *approved* actions. Cannot generate new proposals (no LLM key), cannot approve. Scope = the credentials an operator deliberately granted that one worker. |
| Malicious/buggy proposal | n/a | Caught by human review; further bounded by per-workspace allow-list + per-executor params schema + `constraints`. |

"Zero blast radius" is marketing shorthand; the honest, defensible claim this design earns
is: **"a compromised analysis agent cannot cause an external effect, and cannot obtain the
credentials that would let it — the maximum it can do is place a proposal a human must
approve."**

---

## 9. Phasing — and what the README may claim at each step

| Phase | Deliverable | README claim it earns |
|---|---|---|
| **0 (done)** | Approval queue exists; read/verify paths only | "Mutations are routed to an approval queue." (queue exists) |
| **1** | `Action` envelope + executor registry + `run.propose()` + `orc execute <approval_id>` (operator runs it **in a separate shell with write creds and no LLM key**) | **"Write paths run as a separate process with separate tokens"** — literally true, executed manually. Skills can propose; nothing self-executes. |
| **2** | `orc worker` daemon: lease + idempotency + retry/backoff drain loop; execution recorded in traces | "Approved actions drain automatically; every execution is audited." Effectively-once. |
| **3 (hosted)** | DB-as-a-service with **row-level auth per plane** (analysis plane: INSERT-proposal/SELECT only, *cannot* UPDATE status→approved or read tokens; worker: SELECT-approved/UPDATE-execution only); secrets in a vault; per-executor container isolation | "Zero-trust between planes" — the separation no longer relies on OS-user discipline but on enforced authz. |

Phase 1 is the high-leverage step: it is small, and it is the moment the headline claim
stops being aspirational. Phases 2–3 are about ergonomics and hardening.

---

## 10. Concrete work breakdown (Phase 1)

New modules:

- `src/orc/effects/__init__.py` — registry (`register`, `get`, `allowed_for`), mirrors
  `directives/__init__.py`.
- `src/orc/effects/action.py` — `Action` dataclass + `to_json`/`from_json` + validation.
- `src/orc/effects/base.py` — `Executor` protocol.
- `src/orc/effects/builtin/` — a first **reference executor** that is safe to ship, e.g.
  `fs.write_file` confined to a workspace-scoped output dir (reuses the path-validation
  hardening from `storage/workspace.py`). Proves the loop end-to-end without external creds.

Changes:

- `src/orc/runs/runner.py` — add `Run.propose(...)` → validates + `approval.enqueue(...)`,
  records a `proposal` event in the trace.
- `src/orc/queue/approval.py` — store `proposed_action` as a validated `Action`; add the
  `approval_execution` table to `_TABLE_DDL`; add `lease_one()`, `mark_executed()`,
  `mark_failed()`.
- `src/orc/cli_commands/execute.py` (new) — `orc execute <approval_id>`: refuses unless
  `status == approved`; reads the write credential from **its own** env; runs the executor;
  records result. This is the "separate process with separate token" in the simplest form.
- `config.toml` — `[workspace.<name>.effects] allowed = [...]`.

Tests (TDD, mirroring the existing suite):

- An analysis-plane process with **no write credential in env** can `propose` but a direct
  `execute` attempt in that env fails for lack of the token (proves credential separation).
- `lease_one()` under two concurrent callers yields the row to exactly one (BEGIN IMMEDIATE).
- Idempotency: replaying `execute` with the same `idempotency_key` does not double-effect
  (UNIQUE constraint + dedupe).
- Unknown / disallowed executor is refused at *both* enqueue and execute.
- A skill cannot enqueue an executor outside the workspace allow-list.

---

## 11. Open decisions (need a human call)

1. **First real executor.** `fs.write_file` (no external creds, ships immediately) vs.
   jumping straight to an MCP-backed one (`gmail.send_draft`) to prove the credential-split
   on something users care about. Recommendation: ship `fs.write_file` as the reference in
   Phase 1, add an MCP executor in Phase 2.
2. **Cross-plane integrity on a single SQLite file.** Phase 1/2 rely on OS-user/file-perm
   separation (analysis user can't read the worker's env; both can write the DB). Do we want
   an interim HMAC over `(approval_id, proposed_action, status, decided_by)` to detect a
   compromised analysis process forging an `approved` row, before Phase 3's row-level authz?
   Adds a key-management wrinkle; may not be worth it pre-hosted.
3. **Worker trigger model.** Poll (simple, a few seconds latency) vs. SQLite update hook /
   external signal. Recommend poll for Phase 2.
4. **Expiry.** The `expired` status exists but nothing sets it. Should approved-but-unexecuted
   actions expire (and should `set_expiration` apply to proposals)? Likely yes for compliance.

---

## 12. Summary

The approval queue is already the right boundary; it just has no producers and no consumers.
This design adds (a) a typed, schema-validated `Action` contract so the analysis plane can
only *describe* effects, (b) an executor registry + per-workspace allow-list so only
operator-sanctioned effects exist, and (c) a separate worker process that holds the write
credentials the analysis plane never sees and drains approved actions effectively-once.
Phase 1 — small, ~5 new files — is the point at which "separate processes with separate
tokens" becomes a true statement about the code rather than a promise about the future.

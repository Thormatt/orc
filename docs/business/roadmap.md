# Orc — business roadmap

How Orc makes money. What's open vs paid. What we ship in which order.

This is a living document. The plan it describes is opinionated but not yet
validated against real customer demand. Stage 0 is "land 3 pilots and learn
what to charge for"; everything past Stage 1 will be revised based on what
those pilots teach us.

Last updated: 2026-05-19. Code state: v0.1.4 (F1 = 0.864 on HaluBench, above
Lynx-70B home-court).

---

## Where we are today

**Engineering: strong.** v0.1.4 ships a complete verification runtime: five
verify modes, source-aware routing, multi-turn calculator tool, deterministic
replay, self-contained audit bundles, multi-approver workflow, MCP server,
CLI. 194 tests, MIT-licensed, public on GitHub.

**Distribution: zero.** No paying customers. No design partners. No PyPI
release. The benchmark validates the technical moat but nobody outside this
repo knows.

The next inflection isn't another F1 point — it's the first regulated
customer in production.

---

## The thesis

The pattern: open-source the runtime, charge for the operations companies
don't want to run themselves.

This is the GitLab / MongoDB / Hashicorp / Sentry / Posthog playbook. The
core stays free and credible. The paid product captures value where
organizations need a hosted layer, shared workspaces, compliance UX, and a
sales contract.

**What this is *not*:** a "free tier" of a SaaS. The OSS isn't a marketing
trial — it's the production runtime. The paid product solves a different
problem (running it across a team, mapping it to controls, integrating with
the rest of the org's stack), not a feature-gated version of the same one.

### One specific position on the moat

Verification *as a feature* will be commoditized within 12–18 months.
Anthropic, OpenAI, or LangChain will ship "verify your AI against your
corpus" as a built-in. Orc's wedge cannot be "verify your AI" — that
compresses too fast.

Orc's wedge is **the audit artifact a regulator will accept.** Verification
is *how*; defensibility is *what*. Compliance products live in a different
procurement cycle, a different buyer (Chief Risk Officer, not CTO), and a
different sales motion than dev tools. That's the moat — and it's exactly
what the EU AI Act, NIST AI RMF, SR 11-7, and EBA model-risk guidance are
moving the market toward.

Every business decision below leans into this.

---

## What's open-source (forever, MIT)

- CLI (`orc workspace`, `orc ingest`, `orc verify`, `orc replay`, `orc audit
  export`, `orc trace`, `orc mcp serve`).
- MCP server with the four tools.
- All five verification modes (evidence / judgment / binary / decomposed /
  arithmetic).
- The runtime invariants: citation guard, deterministic replay, hashed
  audit-export bundle, multi-approver workflow, schema-versioned traces.
- The benchmark suite + reproducible HaluBench / citation-enforcement /
  bundle-invariants runners.
- Local-file ingestion (markdown, txt, URL).
- The full SQLite + FTS5 + sqlite-vec storage layer.
- **Cryptographic signing of audit bundles** — this is part of the trust
  story, not a paywall. Gating it would weaken the open-source credibility.

The OSS has to be production-grade on its own. A single regulated team
should be able to deploy it on a laptop and produce defensible audit
artifacts without paying anyone.

---

## What's paid

The paid product solves four problems the OSS doesn't:

1. **Running this across a team** without each engineer setting up local
   workspaces. Shared projects, users, roles, retention.
2. **Mapping outputs to controls** — a compliance officer sees "every Run
   tagged with which EU AI Act article, what's missing, what's about to
   expire" in one dashboard, not in a tarball.
3. **Integrating with the rest of the stack** — pulling evidence from
   Google Drive / SharePoint / S3 / a legal DMS, posting verdicts back to
   Slack / Jira / Salesforce.
4. **Operating the audit pipeline at scale** — scheduled evals, retention
   policy enforcement, signed/timestamped bundles routed to a regulator
   contact, replay-as-a-service, etc.

### The paid SKUs

| Tier | Who | Shape | Indicative price |
|---|---|---|---:|
| **Pilot** | First 3 design partners | Free in exchange for case study + logo + feedback rights | $0 |
| **Pilot** | After the first 3 | 30-day instrumented pilot of one regulated workflow, including audit-bundle delivery | **$25k–50k** |
| **Team (hosted)** | 5–50-person teams | Shared workspaces, hosted MCP gateway, 2–3 strategic connectors, basic compliance dashboard | **$1,500–4,000 / mo** |
| **Enterprise (VPC)** | Banks, hospitals, EU-deployed AI teams | VPC or on-prem deployment, SSO, full connector list, custom directives, SLA, dedicated support | **$50k–200k / yr** |

**No individual Pro tier.** Individuals don't have audit problems. The
buyer here is always a team or org; pricing for a single seat doesn't
match the gravity of the product. (If a consultant wants hosted storage
for one-client engagements, that's a Team workspace.)

### Specific paid features

- **Team workspaces.** Shared projects, named users, role-based access,
  retention policies, org-level settings.
- **Compliance dashboard.** EU AI Act Article 12/13/14 mapping, evidence
  completeness, missing-log alerts, audit-readiness score, weekly
  regulatory reports. **The killer enterprise feature.**
- **Hosted MCP gateway.** One secure endpoint a 50-person org adds to
  Claude Code / Codex / Cursor instead of every developer installing
  Python + the CLI. Roll-out cost goes from O(engineers) to O(1).
- **Scheduled evals.** Consistency, perturbation, regression, retrieval
  drift; weekly compliance reports auto-delivered to the risk officer.
- **Strategic connectors.** Google Drive, SharePoint, S3, Slack, Notion,
  Confluence, GitHub, Jira, Salesforce, legal DMS. **Build 2–3 first** —
  Drive + SharePoint + S3 covers ~80% of regulated-industry document
  storage. Add others against direct customer demand, not speculatively.
- **Hosted retention + replay-as-a-service.** Long-term storage of
  audit bundles, on-demand replay through a web UI (not just CLI),
  timestamped/notarized handoff to a named regulator contact.
- **VPC / on-prem deployment.** The actual enterprise SKU. Same runtime,
  packaged for an air-gapped or VPC environment with SSO, audit
  retention guarantees, and a signed support contract.
- **Compliance services.** Setup, controls mapping, custom directives,
  pilot delivery. Real revenue early, real customer-discovery signal
  forever.

---

## Go-to-market — pilot-first, SaaS later

The single biggest founder mistake here would be building the SaaS before
3 customers ask for the same thing repeatedly.

Order of operations:

1. **Now.** Pitch the pilot offer to 10 regulated-industry teams. EU
   banks, US clinical-research orgs, mid-size law firms running AI
   review, HR-tech vendors deploying in the EU, pharma regulatory
   submissions teams.
2. **Land 3.** First 3 are free in exchange for case-study rights,
   logo usage, and detailed weekly feedback. Each one is roughly 4
   weeks of full-time work — that's the cost of the customer-discovery
   signal.
3. **From those pilots, identify the 1–2 features customers are willing
   to pay for repeatedly.** This will probably be: (a) the hosted MCP
   gateway, (b) the compliance dashboard, (c) one or two connectors.
4. **Build that as the paid product.** Not the full SKU list above —
   just the parts that have been proven against signal.
5. **Then scale.** Sell paid pilots at $25–50k. Use them to validate
   pricing. When 3+ paid pilots in a single segment renew or upgrade,
   that's the moment to build the hosted Team SKU.
6. **Enterprise / VPC last.** Only build the VPC SKU when an actual
   regulated buyer asks for it and is willing to anchor the deal.

The discipline is to *not* build SaaS infrastructure speculatively. Every
hour spent on it before signal is an hour not spent landing the next
pilot.

---

## The first paid offer

Don't lead with "buy Orc." Lead with:

> "Give me one AI workflow that has to be defensible. In 30 days, I'll
> instrument it with trace, replay, claim verification, approval gates,
> and an audit-export bundle your counsel or compliance team can review.
> Your counsel can email the bundle to your regulator on day one if they
> need to."

Concrete deliverables:

- One named workflow instrumented end-to-end with Orc.
- A walk-through of the audit-export bundle with the customer's
  compliance / legal team.
- A reviewed compliance mapping (which Articles / controls this workflow
  satisfies, which it doesn't, what's missing).
- A short report on what would need to change to extend this to two more
  workflows.
- All code, scripts, and runbooks left with the customer — MIT-licensed.
  Nothing locked behind a vendor contract.

The pitch isn't "we'll build you a tool." The pitch is "in 30 days you'll
have one defensible AI workflow in production *and* a clear answer for
your auditor about what was done."

That's sellable now. The SaaS comes after we've seen the repeated pain.

---

## Stages

### Stage 0 — pre-revenue *(we are here)*

- Ship v0.1.4 publicly. Tag, dist artifacts, GitHub release, README.
- Update orc-ebon.vercel.app to reflect v0.1.4.
- Send the pilot pitch to 10 regulated-industry contacts.
- Goal: 3 design-partner conversations within 4 weeks.

### Stage 1 — design-partner pilots *(weeks 4–16)*

- 3 free pilots, each instrumenting one regulated workflow.
- Weekly check-ins. Write every pain point down.
- At the end: 3 case studies, 3 logo permissions, a sharply-narrowed
  feature list for the paid product.
- Goal: identify the 1–2 features people will pay for repeatedly.

### Stage 2 — first paid pilots *(months 4–9)*

- Move from free to $25–50k paid pilots.
- Build the highest-signal paid feature against actual customer demand.
- Probably: the hosted MCP gateway *or* the compliance dashboard.
- Goal: 3 paid pilots that convert to ongoing relationships.

### Stage 3 — hosted Team product *(months 9–18)*

- Build the Team SKU around what worked in Stage 2.
- Open SaaS waitlist; convert paid pilots in.
- Build out the second strategic connector.
- Goal: $30–80k MRR.

### Stage 4 — enterprise/VPC *(months 18+)*

- A regulated buyer anchors a six-figure deal.
- Build VPC packaging + SSO + retention guarantees against that deal.
- Goal: first $150k+ ACV, two reference enterprise customers.

---

## Competitive risk

| Risk | Likely timeline | Mitigation |
|---|---|---|
| Anthropic / OpenAI ships "verification against your corpus" as a built-in | 12–18 months | The wedge is the audit artifact, not the verification. Lean harder into compliance, multi-approver, EU AI Act mapping. |
| LangChain ships Article-12-compliant logging (their own #35357) | 18–24 months | Their framework is the agent layer; Orc is the runtime layer underneath. The audit-bundle / replay / approver-queue story is a different shape than they ship. |
| Patronus AI / Galileo / Vectara add audit-export to their products | 12–24 months | They optimize one part of the loop (the judge). Bundling judge + runtime + audit + approver is a different SKU. |
| A managed-compliance-tool vendor (Credo AI, Holistic AI) adds a runtime layer | 24+ months | They sell to policy / risk officers, not engineers. Building a runtime is far from their muscle. Easier for Orc to grow up into compliance than for compliance vendors to grow down into runtime. |
| Open-weight 70B faithfulness models match Sonnet F1 | already happening | Doesn't matter — Orc's model is pluggable. A customer that prefers Lynx-70B for their VPC just sets `model=lynx-70b`. The runtime is the moat. *Caveat: per the [multi-model benchmark](../benchmarks/results-2026-05-19-multi-model.md), open-weight Llama 3.3 70B needs binary mode + per-model prompt-tuning to land competitive — a real engineering item that turns into billable enterprise-pilot work.* |

---

## What we explicitly don't build yet

- **PyPI release.** Wait until the install path is documented and tested
  on a real customer's machine.
- **Web dashboard.** No design-partner has asked for one yet. Wait for
  signal.
- **Marketing / `gads` / other directives beyond research.** The research
  directive is enough to prove the pattern. Adding more directives is a
  Stage 3+ activity, after we have customers in production.
- **Embedding-based retrieval.** BM25 is enough for prose-heavy corpora,
  which is what the buyer ships. Add embeddings when a pilot specifically
  asks for them.
- **Cloud-hosted runtime.** Stage 3 at the earliest.
- **More than 3 connectors.** Diminishing returns past Drive + SharePoint
  + S3 until we have customer demand to prioritize the next one.
- **PDF ingestion.** Defer until a pilot's corpus actually requires it.

These are not "we'll never build them." They are "we will not build them
on speculation; we will build them when a paying customer is the reason."

---

## Open questions

These get answered by running pilots, not by sitting in a room:

- **Who actually pays?** Is the buyer the CRO, the CISO, the General
  Counsel, or the head of AI/ML? The org-chart answer matters more than
  the persona answer.
- **What's the realistic deal size?** $50k or $150k? Pilots will tell us.
- **What's the sales cycle?** 90 days or 9 months? Stage 1 pilots will
  reveal this.
- **What's the actual top feature?** I'm guessing compliance dashboard.
  I might be wrong. Pilots decide.
- **Is the MCP gateway a strong wedge or a nice-to-have?** Untested.
- **How important is on-prem vs. VPC vs. SaaS for the EU bank buyer?**
  Heavily regulated industries tend to want on-prem; we don't have the
  data point yet.
- **Are there 5 buyers worldwide for this, or 5,000?** Materially affects
  whether this is a venture-scale opportunity or a profitable
  consultancy + tool business.

The plan above is the best guess on 2026-05-19. The version that survives
contact with three pilots will look different. That's the point.

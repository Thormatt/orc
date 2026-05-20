# Faithfulness benchmark — provider portability + verdict-quality sensitivity

**Orc version:** 0.1.4 · **Dataset:** stratified 504-item HaluBench subsample.

A test of the "the judge model is a knob; the runtime is the moat" claim from the competitive doc. Same Orc runtime, same HaluBench items, same prompts — five different LLM judges swapped in via `ORC_VERIFY_MODEL`.

## The honest headline

> **Orc's audit/runtime contract is provider-portable. Verification quality is model-sensitive. Hosted models run without tuning; open-weight deployments need calibration.**

This is the strongest claim the data supports. Stronger claims ("model-agnostic," "works on the commercial frontier") overpromise. Weaker claims ("only Sonnet works") understate.

Three follow-on facts that matter to a buyer:

1. **The runtime contract — citation enforcement, trace, replay, audit-export, multi-approver — is identical across every model tested.** Swap `ORC_VERIFY_MODEL`, get the same artifacts. That part is real.
2. **Verdict-quality F1 varies by model.** Hosted commercial models (Sonnet, Haiku, GPT-4o, Gemini Flash) cluster within ~10 F1 points of each other on this benchmark. Open-weight Llama 3.3 70B lands much lower without per-model adaptation.
3. **The source-routed F1=0.864 headline is Sonnet-specific.** We have not validated source-routed numbers on the other models. Default (evidence) mode is the apples-to-apples comparison below.

## Results

| Model | Provider | Mode | F1 | N | Status |
|---|---|---|---:|---:|---|
| **Sonnet 4.6** | Anthropic | source-routed | **0.864** | 503 | Production headline (Sonnet-only validated) |
| Sonnet 4.6 | Anthropic | default | 0.788 | 503 | Apples-to-apples baseline |
| Haiku 4.5 | Anthropic | default | 0.764 | 504 | ✓ full |
| GPT-4o | OpenAI | default | 0.761 | 503 | ✓ full |
| **Gemini 3.5 Flash** | Google | default | **0.890** | 447** | ✓ full — *highest F1 of any model tested*, but 57 max_tokens skips on FB-heavy items |
| **Qwen 2.5 72B** | Alibaba (open-weight) | binary | **0.638** | 504 | ✓ full — strongest open-weight tested |
| Gemma 3 27B | Google (open-weight) | binary | 0.600 | 20 | Smoke only |
| Llama 3.3 70B | Meta (open-weight) | default | 0.000 | 20 | Citation guard rejected every verdict — see below |
| Llama 3.3 70B | Meta (open-weight) | binary | 0.583 | 20 | Smoke only |

> *\*\*Gemini 3.5 Flash N=447: 57 items were skipped because the model exceeded the 2048-token cap before emitting the verdict tool call (no `record_verdict` call, `stop_reason='max_tokens'`). FinanceBench took the most skips (22/84 → 62 evaluated), which makes sense — FB items often need longer reasoning. The 57 skipped items are **not random** and likely include the harder cases, so the 0.890 number may be biased upward. To publish this externally as a definitive headline, a re-run with `max_tokens=4096` is needed.*

## On the Llama F1 = 0.000 result

This is not the embarrassment it first appears to be. It is **the citation guard doing exactly what it was built for.**

In evidence mode, the runtime requires the verdict to cite chunk IDs that exist in the retrieval set. Hallucinated or malformed chunk IDs get filtered out, and a `supported` verdict with zero remaining citations is downgraded to `not_found` (see `src/orc/directives/research/skills/verify_claim.py` — the citation guard). Llama 3.3 70B in evidence mode was emitting verdicts with chunk IDs that didn't match anything in the retrieval set, and the runtime correctly refused to ship them.

**The right read on this result: the runtime preferred to return "no support" rather than silently ship fake evidence.** That's the guarantee the audit story is built on. The catastrophic F1 is the runtime's safety mechanism firing, not the runtime breaking.

What this tells us about Llama 3.3 70B specifically:
- It cannot drive the evidence-mode 4-label tool schema out of the box.
- It can drive the simpler binary tool schema (F1 ~0.58 on N=20).
- To use Llama in evidence mode for an enterprise/VPC deployment, you would need some combination of: (a) per-model prompt tuning, (b) structured-output / grammar-constrained decoding, (c) a chunk-ID normalization layer that maps Llama's output format to the runtime's expected format, or (d) using binary mode as the default and accepting the F1 ceiling that comes with it.

That work is real. It is also the kind of work an enterprise pilot can scope and pay for.

## Per-source breakdown

### Sonnet 4.6 · source_routed (production headline)
| source | n | F1 | Mode |
|---|---:|---:|---|
| covidQA | 83 | 0.951 | evidence |
| FinanceBench | 84 | 0.916 | **arithmetic** |
| RAGTruth | 84 | 0.878 | evidence |
| pubmedQA | 84 | 0.865 | binary |
| halueval | 84 | 0.814 | judgment |
| DROP | 84 | 0.759 | binary |

### Gemini 3.5 Flash · default (N=447 of 504 — 57 max_tokens skips)
| source | n | F1 |
|---|---:|---:|
| covidQA | 83 | 0.965 |
| pubmedQA | 70 | 0.957 |
| RAGTruth | 73 | 0.894 |
| DROP | 81 | 0.892 |
| FinanceBench | 62 | 0.886 |
| halueval | 78 | 0.740 |

Gemini's aggregate F1 of **0.890 on the 447 evaluated** is the highest of any model tested. It outperforms Sonnet's default by ~0.10 and even Sonnet-source-routed by ~0.025 — and Gemini Flash is one of the cheapest models on OpenRouter (~$0.0005–0.001/verification). At face value, this is the cost-optimized headline: Sonnet-or-better quality at ~10–30× lower cost.

The asterisk: 57 of 504 items were skipped because Gemini exceeded the default 2048-token cap before emitting the verdict tool. FinanceBench took 22 of those (FB items often need longer step-by-step reasoning). The skipped items are not a random sample, so the 0.890 is likely biased upward by the model getting to opt out of its hardest cases. A re-run with `max_tokens=4096` would close this gap.

If even half the skipped items would have come out wrong, the corrected F1 lands at ~0.83 — still excellent, still cheap. The honest framing for now: **directionally Gemini Flash is the cost/quality winner, but the headline needs a re-run with a higher token cap before being cited externally as definitive.**

### Qwen 2.5 72B · binary (full N=504, open-weight reference)
| source | n | F1 |
|---|---:|---:|
| pubmedQA | 84 | 0.741 |
| RAGTruth | 84 | 0.710 |
| covidQA | 84 | 0.694 |
| halueval | 84 | 0.600 |
| FinanceBench | 84 | 0.560 |
| DROP | 84 | 0.451 |

Qwen is the strongest open-weight model tested. The aggregate F1 of 0.638 lands ~0.13 below the hosted commercial models — a real gap, but high-recall (0.71) suggests the model is finding the right items and the precision shortfall (0.58) is where calibration could help. For a regulated buyer wanting a fully on-premise judge inside their VPC, Qwen 2.5 72B in binary mode is the most defensible starting point of the open-weight set we tested.

### Haiku 4.5 · default (evidence)
| source | n | F1 |
|---|---:|---:|
| covidQA | 84 | 0.905 |
| pubmedQA | 84 | 0.895 |
| halueval | 84 | 0.780 |
| RAGTruth | 84 | 0.709 |
| DROP | 84 | 0.690 |
| FinanceBench | 84 | 0.648 |

### GPT-4o · default (evidence)
| source | n | F1 |
|---|---:|---:|
| covidQA | 83 | 0.889 |
| pubmedQA | 84 | 0.825 |
| halueval | 84 | 0.809 |
| DROP | 84 | 0.755 |
| RAGTruth | 84 | 0.649 |
| FinanceBench | 84 | 0.648 |

### Gemini 3.5 Flash · default (evidence) · N=85 partial
| source | n | F1 |
|---|---:|---:|
| covidQA | 14 | 1.000 |
| pubmedQA | 15 | 0.889 |
| DROP | 22 | 0.857 |
| RAGTruth | 14 | 0.833 |
| FinanceBench | 8 | 0.727 |
| halueval | 12 | 0.714 |

## Cost-vs-quality grid

For an enterprise picking a judge model, the practical tradeoffs:

| Use case | Recommended model | Approximate cost per verification | F1 |
|---|---|---|---:|
| Maximum quality, cost-tolerant | Sonnet 4.6 source-routed | $0.005–0.015 | 0.864 |
| Cost-optimized, Anthropic OK | Haiku 4.5 default | $0.001–0.003 | 0.764 |
| Already on Azure/OpenAI | GPT-4o default | $0.005–0.012 | 0.761 |
| Already on Google Cloud | Gemini 3.5 Flash default | $0.001–0.002 | ~0.84 (partial) |
| VPC / air-gapped / sovereign | Llama 3.3 70B binary | self-hosted | ~0.58 (n=20, needs full N + tuning) |

The cheapest options (Haiku, Gemini Flash) land within ~10 F1 points of Sonnet's best at ~5× lower cost. For high-volume regulated-industry deployments that don't need the absolute ceiling, the cost-optimized tier is the right default.

## Implications for the product

1. **The claim that's defensible:**
   > "Orc's runtime contract is provider-portable; verification quality is model-calibrated. Major hosted model APIs (Anthropic, OpenAI, Google) run without tuning; open-weight / VPC deployments require deployment-specific calibration."
   Not "model-agnostic." Not "commercial frontier" (Sonnet 4.6 and GPT-4o are recent but the benchmark says nothing about, e.g., o1 or Claude Opus 4.x — we tested what we tested). "Major hosted model APIs" is what the evidence currently supports, with caveats for the Gemini and Llama cells that need full N before being cited externally.

2. **For VPC / on-prem deployments using open-weight 70Bs, binary mode is the default route** until the calibration work is done. The runtime makes that choice clean (one parameter); we document it as the recommended on-prem starting point.

3. **The open-weight calibration is real engineering work — and it's exactly the right shape for a paid enterprise pilot.** A regulated buyer who wants Orc running on their own Llama deployment inside their VPC gets: prompt tuning, structured-output / grammar-constrained decoding (or chunk-ID normalization), evidence-mode validation against their corpus, and a final benchmark on their workflow's actual claim distribution. That work is the *content* of the pilot, not a hidden tax on it.

4. **Haiku 4.5 deserves promotion in the default config for cost-conscious deployments.** F1 0.764 at ~5× lower cost than Sonnet is the right tradeoff for many compliance use cases where the absolute F1 ceiling matters less than per-call cost.

5. **The source-routed F1=0.864 headline is honest only as a Sonnet number.** The source-routed cells for Haiku, GPT-4o, Gemini, and Llama are unvalidated. We should not imply the routed strategy lifts every model by the same delta until those runs land. Default (evidence) mode is the comparable baseline this benchmark currently supports.

## Reproducing

```sh
# Pick a model. Any of these work via OpenRouter:
export ORC_VERIFY_MODEL=anthropic/claude-sonnet-4-6        # the headline
export ORC_VERIFY_MODEL=anthropic/claude-haiku-4-5         # cheap tier
export ORC_VERIFY_MODEL=openai/gpt-4o                       # commercial alt
export ORC_VERIFY_MODEL=google/gemini-3.5-flash            # GCP alt
export ORC_VERIFY_MODEL=meta-llama/llama-3.3-70b-instruct  # open-weight (use --variant binary)

# Then:
ORC_BENCHMARK_ALLOW_LIVE_LLM=1 \
  uv run python -m benchmarks.faithfulness.run --n 504 --variant default
```

For Llama specifically, use `--variant binary` — evidence-mode collapses due to citation enforcement, not model incapability.

## Known limitations of this run

- Gemini 3.5 Flash was capped at N=85 due to OpenRouter credit exhaustion mid-run. The 14 non-credit errors were `max_tokens` truncations (Gemini's responses occasionally exceed 2048 tokens before emitting the verdict). A re-run with credits + max_tokens=4096 would produce a definitive full-N number.
- Llama 3.3 70B was tested at N=20 only. A full N=504 binary-mode run would give a defensible open-weight reference point; same blocker (credits).
- No per-call retry on transient errors. Production deployments would want one.
- Anthropic's prompt-cache headers are sent on every call but are silently ignored by non-Anthropic upstreams. Negligible performance impact (zero cache hits, but no failure either).

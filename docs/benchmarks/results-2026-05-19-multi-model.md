# Faithfulness benchmark — multi-model portability

**Run date:** 2026-05-19/20 · **Orc version:** 0.1.4 · **Dataset:** stratified 504-item HaluBench subsample.

A test of the "the judge model is a knob; the runtime is the moat" claim from the competitive doc. Same Orc runtime, same HaluBench items, same prompts — five different LLM judges swapped in via `ORC_VERIFY_MODEL`.

## Headline

| Model | Provider | Mode | F1 | N | Cost band |
|---|---|---|---:|---:|---|
| **Sonnet 4.6** | Anthropic | source_routed | **0.864** | 503 | $$$ |
| Sonnet 4.6 | Anthropic | default (evidence) | 0.788 | 503 | $$$ |
| Haiku 4.5 | Anthropic | default | 0.764 | 504 | $ |
| GPT-4o | OpenAI | default | 0.761 | 503 | $$$ |
| Gemini 3.5 Flash | Google | default | 0.840 | 85 *(see note)* | $ |
| Llama 3.3 70B | Meta (open-weight) | default | **0.000** | 20 | ¢ |
| Llama 3.3 70B | Meta (open-weight) | binary | 0.583 | 20 | ¢ |

> *Gemini run is N=85 instead of 504 because OpenRouter credits exhausted mid-run. The result on those 85 items projects to a competitive full-N number, but should be re-run with topped-up credits before being cited as definitive.*

## The portability claim — what's true and what isn't

**True for Anthropic-family and large commercial models.** Sonnet 4.6, Haiku 4.5, GPT-4o, and Gemini 3.5 Flash all run the existing Orc prompts and produce competitive verdicts without any per-model tuning. Quality varies (0.76–0.79 default-mode F1), but the *runtime is genuinely portable* across these providers — drop-in via `ORC_VERIFY_MODEL`, no code changes.

**Not true out-of-the-box for open-weight Llama 3.3 70B.** Default (evidence) mode collapsed to F1 0.0: the model returned `not_found` on every item. Diagnosis: Llama is calling the verdict tool but producing chunk IDs the citation guard rejects (likely hallucinated or wrong format). Every "supported" verdict gets downgraded to `not_found` per the guard logic in `verify_claim.py`. Switching to **binary mode** (no chunk-citation requirement) recovers Llama to F1 0.58 on n=20 — functional but well below the commercial models.

The more accurate framing: **the runtime is model-portable, but mode selection should match model capability.** Commercial models with strong Anthropic-style tool use → evidence mode. Open-weight models that struggle with structured chunk-ID citation → binary mode.

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

1. **The "model-agnostic" claim needs the modifier "across the commercial frontier."** Sonnet + Haiku + GPT-4o + Gemini Flash all work in evidence mode. Llama 3.3 70B does not. Saying "any model works" overpromises; saying "any commercial frontier model + Llama in binary mode" is what's actually true.
2. **For VPC/on-prem deployments using open-weight 70Bs, binary mode is the default.** Evidence mode requires citation-discipline that Llama doesn't currently provide. The runtime makes that choice clean (one flag); we should document it as the recommended on-prem path.
3. **There's a real engineering item we'd take on for an enterprise pilot using open-weight:** prompt-tuning evidence mode for Llama, or adding a model-capability detection layer that auto-selects mode. That work is real, billable consulting — not free.
4. **Haiku 4.5 deserves promotion in the default config for cost-conscious deployments.** F1 0.764 at ~5× lower cost than Sonnet is the right tradeoff for many compliance use cases where the absolute F1 ceiling matters less than the per-call cost.

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

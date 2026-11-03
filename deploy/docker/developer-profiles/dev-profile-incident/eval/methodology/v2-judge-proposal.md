# Evaluation V2: judge-scoring proposal (OpenRouter route) — PROPOSED, NOT APPROVED

The development pilots left every **judge-only field unscored**. These are the incident description and the matched items' descriptions and names (entities, instruments, assets). Everything else was scored without a judge. This page states exactly what judge scoring would cost and how it is controlled. **No judge call has been made; none will be made without the owner's explicit approval of the budget below.**

## 1. Judges

| Role | Registry id | Model | Route | Why |
|---|---|---|---|---|
| Primary | `openrouter-gpt-oss-20b-v1` | `openai/gpt-oss-20b` | OpenRouter → DeepInfra or DekaLLM (bf16), no fallback, `data_collection: deny`, every parameter required | Same model and settings as the approved catalog judge `catalog-gpt-oss-20b-v1`. A family distinct from all five subjects (NVIDIA, Qwen, Gemma) and from the reference generator (Claude) |
| Sensitivity | `openrouter-mistral-large-4-v1` | `mistralai/mistral-large-4-0` (= catalog `mistral-large-4-1t-a52b`) | OpenRouter → Mistral, no fallback | A second independent family re-judges a subset |

- **Settings and prompts:** temperature 0 and 1,500 output tokens, with the registered judge prompts (`incident`, plus `fact`, `unsupported` and `contradiction` for chat). Request bodies are pinned by `tests/v2/fixtures/judge-requests-v1.json`.
- **Endpoint swap:** `score --judge <id>` swaps only the endpoint of the configured judge model. Any other difference (model, role, kinds, settings) is refused.

## 2. Exact expected calls (development pilot backlog)

Judge verdicts are cached **per run** (`scoring/judge-cache/`), keyed by the (expected, predicted) pair. So the expected number of calls is the number of unique unjudged pairs in each run (counted from the saved score-stage-v3 outputs on 2026-11-02):

| Run | Unique pairs → primary calls |
|---|---|
| `v2-c1a-track-a-pilot` | 285 |
| `v2-c1a-track-a-recovery-super` | 56 |
| `v2-c1b-track-b-pilot-captions` (all three stages) | 261 |
| `v2-c1e-openrouter-gemma4-31b` | 81 |
| `v2-c1f-openrouter-qwen35-9b` | 123 |
| **Total** | **806** |

**By field** (lookups before deduplication): incident description 326; entity description 385; asset name 277; asset description 277; instrument name 42; instrument description 42.

**Sensitivity judge:** the 80 calibration pairs plus a 20% stratified subset of the primary pairs (about 160), so about **240 calls**.

## 3. Hard caps and maximum spend (enforced in code)

| Judge | Max HTTP requests | Max tokens | Max USD (every token at the output price) |
|---|---|---|---|
| Primary (gpt-oss-20b, $0.03 in / $0.13 out per M) | **1,000** (806 + 24% for retries) | **2,300,000** (806 × 2,275-token bound per call, + 25%) | **$0.30** |
| Sensitivity (Mistral Large 4, $0.70 in / $2.10 out per M) | **300** | **700,000** | **$1.47** |
| **Total** | **1,300** | **3,000,000** | **$1.77** |

- **How the caps are enforced:** every attempt reserves its token bound before it is sent, on two ledgers: the judge's shared ledger (`--judge-ledger-dir`, counted across every run on this machine) and the run's judge ledger. An attempt that would pass either cap is refused before sending, and its field stays unscored.
- **Money ceiling:** the OpenRouter key's own $10 limit is the outer ceiling, and $9.83 remains.
- **C3 is separate:** about 3,100 primary and 620 sensitivity calls, estimated from the development ratios. It would be requested with the C2 package.

## 4. Failed calls, refusals and resume

- **Infrastructure failures** (rate limit, timeout, transport, host suspended) are retried at most twice, with 60 s and 180 s back-off (`retry-v2`). Every attempt is recorded with its tokens and outcome.
- **No answer:** a reply cut off at `max_tokens`, or one that does not parse as the expected JSON, is **not retried** (`parse_retries` 0). The field stays **unscored**; it is never scored as a fail. It is counted in the summary's `unscored` block.
- **Budget refusal:** the field stays unscored and is reported. Nothing is retried past a cap.
- **Resume without duplication:** verdicts are cached per pair before scoring continues, so re-running `score` sends only the pairs that have no cached verdict. The ledger log is append-only, so the budget counts continue across resumes. A completed score re-runs offline (`--offline`) with identical results.

## 5. Calibration (must pass before judged results are reported as results)

- **Sample:** 80 pairs, stratified by field. They are drawn with seed 20261102 from the 660 unique pilot pairs (Track A plus the Track B final stage), about a quarter from the external models:

  | Field | Pairs |
  |---|---|
  | Incident description | 20 |
  | Entity description | 20 |
  | Asset name | 12 |
  | Asset description | 12 |
  | Instrument name | 8 |
  | Instrument description | 8 |

  The labelling workbook is private: `evalv2-data/v2/review/judge-calibration/judge-calibration-labelling.xlsx`. Reviewers see only the expected and predicted texts; the model, run and clip are in a separate key sheet.
- **Human labels:** two reviewers label all 80 independently as PASS / FAIL / UNSURE ("do the two texts describe the same thing, with no contradiction?"). Disagreements are adjudicated by a third person or by discussion, and UNSURE is resolved there.
- **Measures:**
  - judge vs adjudicated human label: Cohen's κ, accuracy, and false-pass and false-fail rates;
  - human–human κ;
  - primary vs sensitivity κ.
- **Acceptance rule (proposed for pre-registration):** primary judge vs human κ ≥ 0.60.
  - If it is lower, judged fields are reported as **indicative only**, with the human-labelled subset as the reference.
  - The sensitivity judge's agreement is reported either way.
- **Reviewer time:** about 1.5–2 hours per reviewer.

## 6. Approval requested

- **The calls:** up to 1,300 judge calls and 3.0 M tokens, at most $1.77, on OpenRouter for the development pilot backlog and calibration, as in §3.
- **Two reviewers** for the 80-pair calibration set.

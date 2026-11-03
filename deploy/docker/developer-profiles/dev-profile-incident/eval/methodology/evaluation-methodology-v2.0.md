# Evaluation Methodology v2.0 (Evaluation V2: several models, the whole application)

> **Status: proposed (2026-11-01).**
> - The evaluation owner approved the direction: the scope, the two tracks, AI-first references with tiered human verification, and the layered `eval/v2` package. These are recorded in [`decisions.md`](../../.docs/decisions.md).
> - The **primary metrics, judge, repeat count and freeze rules below are proposals.** They become binding only in the pre-registration record ([`evaluation-v2-preregistration.md`](evaluation-v2-preregistration.md)), which is completed before any run on the test half.
> - **V1 is untouched:** methodology v1.0–v1.3, `ground-truth-v1.0`, the Stage 0–3 results and the end-to-end profile stay frozen.

## 1. Scope
| Dimension | Unit | Question | Reference | Primary metric (proposed) |
|---|---|---|---|---|
| A Incident extraction | video-run | How well are the incident fields filled? | `ground-truth-v1.0` | D9 v2 incident component (mean of the six field scores) |
| B Object identification | item | Are entities, instruments and assets found and described? | `ground-truth-v1.0` items | Mean of entity F1 and object F1 at τ = 0.40 |
| C Evidence boxes | ground-truth item | Does the evidence show the right object at the right moment? | Box references (new, tiered) | Correct-evidence rate on `human_verified` items |
| D Chatbot | question | Is the right material retrieved? Is the answer correct, cited and grounded? | Chat references (new, tiered) | Retrieval R@5 (incident level); answer fact recall |
| E AI dashboard | chart request | Is the request turned into the right spec or refused? Are the figures right? | Dashboard references (new, tiered) | Exact-spec accuracy, refusals included |
| F Tokens and cost | call | What does each output cost? | — | Descriptive only |
| G Latency and reliability | call, run | How long does each stage take, and how often does it fail? | — | Descriptive only |

F and G are instruments applied to every call in A–E, not separate runs.

## 2. What is evaluated
- **System under test:** `main` at a SHA pinned at the freeze, with `incident-contract-v4`. A later contract, such as v5 from `origin/mvp3`, makes a separate V2 version and is never mixed into this one.
- **Track A, model comparison (P1 only).**
  - The same v4 P1 request goes to every video-capable subject on the direct path. Same prompt, schema, settings, clips and scorer.
  - This is the only cross-model ranking, and it runs first.
  - **Roster (proposed for C2):** Cosmos 3 Nano, Cosmos 3 Super and Nemotron 3 Nano Omni (NVIDIA, Switchyard), plus one **independent open-weight baseline**: Gemma 4 31B IT (Google, Apache-2.0 weights) on the NVIDIA API catalog, subject to a capped dev compatibility pilot (C1e). Kimi K2.6 (Moonshot AI) is an optional second only if its probes show real video input.
    - **Route when no NVIDIA catalog key exists (2 Nov):** OpenRouter, a prepaid OpenAI-compatible gateway (a call fails rather than bill past the balance). It serves Gemma 4 31B and **Qwen3.5-9B** (Alibaba Qwen, open weights; the closest Qwen video model, since Qwen3-VL-8B is listed there for images only), both with video input and structured outputs. Subjects pin the serving providers with a routing-only `extra_body` (no fallback, `data_collection: deny`, every parameter required), and each response names the provider that served it.
    - **Dev pilots (2 Nov, OpenRouter, 50 runs each, $0.17 in all):** Qwen3.5-9B: 38/50 contract-valid (9 runaway reasoning hit max_tokens with no answer, 2 invalid, 1 HTTP error), severity MAE 0.61, type 0.79, start/end MAE 4.6/6.7 s, entities F1 0.51, latency p50/p95 76/295 s. Gemma 4 31B: 24/50 valid; all 26 invalid outputs put timestamps past the clip end (the provider sends a fixed frame budget without timing), severity MAE 0.54, type 0.83, end MAE 33 s, entities F1 0.61, p50 12 s. Paired with Super on the same valid runs: severity MAE Qwen 0.61 vs Super 0.92 (38 runs), Gemma 0.57 vs 1.09 (23); type accuracy is lower for both (0.79 and 0.83 vs about 0.96). Dev only, no intervals: not a ranking.
    - **Proposed C3 roster:** Nano, Super, Omni, **Qwen3.5-9B** (primary external baseline: the Qwen family, the higher coverage and usable timing) and **Gemma 4 31B** (second external baseline; its timing failure is reported as a provider-sampling limitation). Same request, scorer and caps policy as the NVIDIA subjects.
    - **Excluded, with reasons (2 Nov):** Qwen3-VL-8B is on neither Switchyard nor the catalog, and running it needs a GPU (the WSL host has none; the VM GPU is shared and gated) plus a ~17 GB weight download. Cosmos Reason 2 8B is on the catalog but is NVIDIA's post-training of Qwen3-VL-8B, so it is not an independent family. Llama 3.2 Vision, Phi-3 Vision, GLM-5.3 Flash, Step-5 and Muse Glimmer accept images only on the catalog (Track A sends video).
    - **Fairness rules for the external baseline:** the same P1 prompt and settings, clips sent whole, the same strict contract-v4 validator with only ID normalisation and no repair. If strict `json_schema` is refused, only the decoding constraint is dropped (reported as unconstrained decoding). Clips the endpoint cannot take (Gemma documents 60 s at 1 fps) count against its coverage and reliability; a paired ≤ 60 s subset is reported beside the full result. Frame sampling is the provider's and is disclosed.
- **Track B, pipeline comparison.**
  - Each production default candidate (Cosmos 3 Nano, Cosmos 3 Super) runs the production profile on a local experiment agent: caption pass, P1, grounding crops and the zoomed second look.
  - Set-of-marks, object tracking and person tracking are off. A tracking-on condition for crops only is added if the shared perception stack is approved.
  - Track B is compared with Track A **within one model only**.
  - **As built (J1):**
    - The experimental path runs no caption pass, because its windows need the R2 scratch prefix (open decision below). Until then, Track B's raw P1 output measures only that the agent's P1 request and Track A's give the same results.
    - The zoom effect uses the agent's P1 report from before the zoomed second look (`experiment.p1_report`), checked against the final report with every correction undone.
    - Profiles are versioned, and their caps include the agent's own retries.
    - The agent retries a runaway P1 output once and returns only the retry. Track A scores the same runaway as a miss, so runs with a retried P1 are left out of the caption-effect pairs.
- **Effects measured without extra calls:**
  - *caption effect:* Track B's raw P1 output minus the same model's Track A output;
  - *zoom effect:* the final report minus the same report with every zoom correction undone (`name_before`, `description_before`).
- **Per-stage provenance.** Every record names the model of each stage:
  - P1;
  - the caption pass (the P1 model);
  - grounding and zoom (the agent's `VLM_NAME`);
  - embeddings;
  - judge;
  - chat;
  - chart spec.

  It also records each stage's prompt id and SHA-256, settings and flags.
- **Unsupported tasks.** A subject that cannot do a task (for example, a text-only model on video) is reported as **unsupported**, never as a score of 0, and never imputed.

## 3. Data and references
- **Clips:** `dataset-manifest-v1` (80 verified clips) and `split-v2` (25 dev / 55 test).
  - Dev is for prompt and configuration work, calibration and pilots.
  - Test is for frozen runs only.
  - **The test clips are not unseen** (D15 exposure table); every report says so.
- **Incident and object ground truth:** `ground-truth-v1.0`, frozen and AI-adjudicated, **not individually human-verified**.
  - Proposed: a human-verified stratified subset (about 30 incidents, two reviewers, κ). Results are reported against both.
- **New references** (boxes, chatbot, dashboard). Every record carries one tier:

  | Tier | Meaning |
  |---|---|
  | `ai_proposed` | Generated, not checked |
  | `ai_validated` | Deterministic code checks pass, and a separate blind validation pass agrees |
  | `human_verified` | A person accepted, edited or rejected it |

  - **Generation is independent of the subjects.**
    - Claude (in Claude Code) and an offline open-vocabulary detector generate. Neither is a subject.
    - The production grounding model never makes references.
    - The validator never sees the generator's answer.
    - Every countable chat or dashboard answer is computed by code from the ground truth.
  - **Human QA:** about 20% stratified, including a random sample of `ai_validated` records (the AI error estimate), plus every flagged record (ambiguous, or validators disagree).
  - **Primary metrics use `human_verified` records only.** AI-tier results are reported separately, labelled as AI references, together with their measured error.
  - **Amended 2026-11-03 (owner):** no human review will take place, so no record becomes `human_verified`. Primary metrics use `ai_validated` records with an AI-only review annotation (AI-consensus / AI-disputed; all Human-unverified), labelled PROVISIONAL. See [v2-ai-only-reference-review.md](v2-ai-only-reference-review.md).
  - **Correlation is disclosed.** The generator, the validator and the `ground-truth-v1.0` adjudicator are the same model family. So `ai_validated` means "the passes agree", not "correct".
- **Matching validation set** (proposed): about 300 predicted/ground-truth item pairs from saved V1 outputs, sampled across similarity bins (including below τ) and labelled by people. It measures the matcher's own precision and recall.

## 4. Metrics
**A: Incident extraction.**
- *Type:* accuracy, macro-F1, confusion matrix.
- *Severity* (`severity-v2-mae`, proposed primary for the C2 freeze): **MAE** on the canonical 1–5 scale (mean |predicted − ground truth| in levels; lower is better) is the primary metric. Exact accuracy and quadratic-weighted κ are secondary (higher is better); within ±1, signed bias, an absolute-error histogram and a per-category (ground-truth type) breakdown are reported with them.
  - **One denominator:** MAE, exact and QWK are computed over the same valid pairs: a run with model output, a ground-truth level and a predicted level 1–5. The denominator `n`, `attempted`, `coverage = n / attempted` and the count of every other status are reported beside them: `invalid_output` (contract_invalid or parse_failure; no severity is read from an invalid output), `invalid_label` (a value that is not a 1–5 level), `missing_prediction`, `no_model_output` (request failure) and `missing_ground_truth`. Excluded runs are never dropped silently, and reliability is reported on all runs (*Reliability* below).
  - **Change from earlier pilot summaries:** the severity "accuracy" quoted there was V1's field accuracy, which scores an invalid output as a miss, so its denominator differed from QWK's. That view stays in `continuity_v1` for comparison with V1; it is not a V2 headline metric.
  - **Track B stages:** a run with no usable reply has only a P1 row; it is carried into every later stage's severity with its outcome, so all stages share one `attempted` (score stage v3).
  - **Never combined:** MAE is not averaged or weighted together with higher-is-better metrics. Any model comparison states each metric with its direction.
- *Start and end:* absolute error, a tolerance curve (±2/5/10 s), and temporal IoU of the incident window.
- *Description:* `judge-prompt-v1` pass rate (continuity with V1), plus atomic-fact recall and contradiction rate from a validated rubric judge.
- *`confidence_score`:* calibration against type correctness (Brier score, ECE).
- Title, location and severity_reason have no ground truth and are reported as not scored.
- *Reliability:* `contract_ok`, `core_ok`, first-attempt success and eventual success.

**B: Objects.**
- The V1 view is kept: Hungarian matching on MiniLM similarity at τ = 0.40.
- *Added:*
  - entity-type-aware matching;
  - full similarity matrices, so a τ curve (0.30–0.80) and its area can be computed offline;
  - matcher precision and recall against the matching validation set;
  - count error and over-listing rate.
- *On matched pairs:* entity type, instrument threat (exact, ±1, MAE), holder correctness, name adequacy.
- Instruments appear in only 18 clips, so they are descriptive only.

**C: Evidence boxes.**
- *Without box references:* located rate, image-gap rate, grounding calls per item, cap truncation, zoom applied rate and confidence. Items left out by the crop cap, or never judged by the locate step because of an infrastructure or setup gap (no frame produced, a failed upload, a missing detector or store), are unscorable, not misses. All labelled as operational or proxy measures.
- *With references:*
  - temporal hit: the item's second lies inside the reference's visible span;
  - IoU against the reference box **at the same second**;
  - correct evidence: a temporal hit and IoU ≥ 0.5, with the full IoU distribution reported. A second inside a span where the reference has no box (its two passes disagreed there) leaves IoU and correctness unscored, not a miss.
- Reported end to end per ground-truth item and conditional on matched items.

**D: Chatbot.**
- *Retrieval*, from one candidate pool:
  - incident-level P@K, R@K, MRR and nDCG@K;
  - standard P@K kept apart from returned-set precision, and post-filtering from fill-to-K, as in the MVP2 spec.
- *Answers:*
  - fact recall against reference facts;
  - unsupported-claim rate against the retrieved context;
  - contradiction rate against the ground truth;
  - citation precision and recall;
  - refusal precision and recall on unanswerable and false-premise questions;
  - exact match on counts and lists (for the most common type, the type the answer calls the most common; a ranking of the others after it is not an extra item).
- *Conditions:* oracle corpus (reports derived from the ground truth) versus system corpus (a frozen analysis run), always reported apart.

**E: AI dashboard.**
- *Spec:* exact-spec accuracy, per-field accuracy, refusal precision and recall, valid-but-wrong rate, parse-failure rate, and stability across repeats.
- *Figures:* checked model-free for every allowlisted spec on a frozen snapshot with a frozen clock. The production SQL, a replay of the console's chart pipeline and an independent recount must agree.
  - Figure accuracy (the reply's chart shows the reference chart's figures) is reported beside spec accuracy, because a valid-but-wrong spec can show the right figures.
  - Labels are ordered in the C collation, as in the harness database. Another collation may break ties, and top-N cut-offs among ties, differently.
- *Chart type:* a rule, not a model decision, so it is unit-tested.

**F: Tokens and cost.**
- Record provider-reported prompt, completion and reasoning tokens per call and per stage, plus effective frame sampling where it is reported.
- Report:
  - relative usage within one provider;
  - tokens per successful output and per video-minute;
  - calls per analysis;
  - cost where a price exists. Switchyard charges the project $0, and the sponsor's infrastructure cost is unknown.
- Raw token counts are never compared across tokenizers as equal work.

**G: Latency and reliability.**
- *Latency:* measured per stage and end to end. Pacing and rate-limit waits are excluded from model time. p50 and p95 with bootstrap intervals, compared within one session only.
- *Reliability:* failure taxonomy, first-attempt and eventual success, and timeouts by clip length.

## 5. Execution and scoring rules
- **Execution is separate from scoring.**
  - Execution writes immutable, content-addressed artifacts: request, response, prediction, usage and timing.
  - Judge and embedding calls are a separate budgeted stage, cached by content hash.
  - Scoring reads artifacts only, with the network blocked. A cache miss leaves a field unscored, never 0.
  - Re-scoring with a new scorer version or threshold makes no model call.
- **Budgets.**
  - Separate ledgers cover inference, caption pass, grounding, zoom, object tracking, embeddings, judge and generation, per subject and per task.
  - Every HTTP attempt counts against them, and a call that would pass a cap is refused before it is sent.
- **Failures.**
  - Retries cover infrastructure failures only, with V1's `retry-v2` and `recovery-v1` semantics.
  - An invalid model output is a miss and is never retried.
  - Original and recovery-adjusted results are reported apart.
- **Runs resume** from checkpoints, and idempotency keys stop a successful call from being repeated.

## 6. Statistics
- **Paired comparisons:** per clip (per question for chat and dashboard). The 95% cluster bootstrap resamples clips, with repeats moving with their clip. 10,000 resamples, fixed seed.
- **Repeats** (proposed): 2 on the test half for primary comparisons, interleaved per clip in a seeded order (D10).
- **Multiplicity:** Holm within each dimension's primary family. Each subject is compared with the production reference. Everything else is labelled exploratory.
- **Reporting:** paired trade-offs with their intervals. No pass or fail thresholds. The minimum detectable effect is stated.
- **Failure-aware:** conditional accuracy, end-to-end accuracy (failures count as misses) and reliability are always shown side by side.

## 7. Judges and instruments
- **Judge family.** The judge comes from a family distinct from every subject and from the reference generator. Cosmos and Nemotron are subjects (V1's pinned judge, Nemotron 3 Nano 30B, shares Omni's family), and Claude makes references. Availability of a third family is verified read-only before the freeze.
- **Human calibration.** About 100 items labelled by people give judge–human κ. A judge-sensitivity check runs on the subject that shares the old judge's family.
  - **Amended 2026-11-03:** not done. It is replaced by an explicitly labelled AI-consensus agreement analysis on the 80 calibration pairs. The human κ ≥ 0.60 threshold is **not** claimed as met ([v2-ai-only-reference-review.md](v2-ai-only-reference-review.md)).
- **V1 instruments stay for continuity.** MiniLM at τ = 0.40 and `judge-prompt-v1` are reused unchanged for continuity rows, with τ curves and the matching validation set as sensitivity analyses.

## 8. Freeze and pre-registration
- **Before any test-half run,** a freeze manifest pins:
  - the `main` SHA and contract version;
  - every prompt (id and SHA-256);
  - scorer versions;
  - dataset versions and tiers;
  - pipeline profiles (agent SHA and flags);
  - migration names and hashes;
  - the frozen clock for the dashboard;
  - the analysis plan;
  - the rule behind the default-model recommendation.
- **After the freeze,** any change makes a new version. Any change informed by test results is exploratory.

## 9. Limitations carried forward
- `ground-truth-v1.0` and τ = 0.40 are AI-assisted, not human-validated. The human-verified subset and the matching validation set measure how much that matters, but do not remove it.
- **No human-verified references or judge calibration (2026-11-03).** The AI-only review uses three non-subject, non-judge, non-Claude families. However, LLM reviewers share biases and cannot detect ground-truth errors, so every result stays PROVISIONAL and Human-unverified.
- The test clips were used in V1 and the 2 Oct benchmark.
- Claude-made references are correlated with each other and with the V1 adjudication.
- The provider decides frame sampling. Cosmos 3 Super ignores `num_frames`; for Nano the docs conflict. Effective sampling is recorded per call, not assumed.
- The production profile changes often, so V2 describes the profile frozen at its SHA.

## 10. Open decisions
- Judge family (pending availability) and the size of the human calibration set.
- The repeat count on the test half.
- Reviewers for the human QA subsets.
- Whether chat questions derived from the ground truth are accepted. They break the MVP2 blind-authoring rule, with mitigations: paraphrasing, a leakage report, and oracle and system results reported apart.
- Where reference text is stored (it quotes ground-truth annotations; local by default).
- A dedicated R2 scratch prefix for the Track B caption pass on the experiment agent.
- An embedding key for the evaluation host.

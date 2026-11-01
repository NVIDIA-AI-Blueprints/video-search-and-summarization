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
- **Track B, pipeline comparison.**
  - Each production default candidate (Cosmos 3 Nano, Cosmos 3 Super) runs the production profile on a local experiment agent: caption pass, P1, grounding crops and the zoomed second look.
  - Set-of-marks, object tracking and person tracking are off. A tracking-on condition for crops only is added if the shared perception stack is approved.
  - Track B is compared with Track A **within one model only**.
  - **As built (J1):**
    - The experimental path runs no caption pass, because its windows need the R2 scratch prefix (open decision below). Until then, Track B's raw P1 output measures only that the agent's P1 request and Track A's give the same results.
    - The zoom effect uses the agent's P1 report from before the zoomed second look (`experiment.p1_report`), checked against the final report with every correction undone.
    - Profiles are versioned, and their caps include the agent's own retries.
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
  - **Correlation is disclosed.** The generator, the validator and the `ground-truth-v1.0` adjudicator are the same model family. So `ai_validated` means "the passes agree", not "correct".
- **Matching validation set** (proposed): about 300 predicted/ground-truth item pairs from saved V1 outputs, sampled across similarity bins (including below τ) and labelled by people. It measures the matcher's own precision and recall.

## 4. Metrics
**A: Incident extraction.**
- *Type:* accuracy, macro-F1, confusion matrix.
- *Severity:* exact, within ±1, MAE, signed bias, quadratic-weighted κ.
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
- *Without box references:* located rate, image-gap rate, grounding calls per item, cap truncation, zoom applied rate and confidence. Items left out by the crop cap are unscorable, not misses. All labelled as operational or proxy measures.
- *With references:*
  - temporal hit: the item's second lies inside the reference's visible span;
  - IoU against the reference box **at the same second**;
  - correct evidence: a temporal hit and IoU ≥ 0.5, with the full IoU distribution reported.
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
  - exact match on counts and lists.
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

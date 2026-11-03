# Evaluation V2 pre-registration record

> **Status: COMPLETED DRAFT, PROPOSED FOR OWNER APPROVAL (2026-11-03). Not approved, not frozen.**
> - Approve this record **before any run on the test half** ([methodology v2.0](evaluation-methodology-v2.0.md) §8). The freeze manifest then pins its SHA-256.
> - Every value names its source of truth (a file hash, a registry entry or a decision).
> - Once approved, the record is append-only: a correction is a new, dated section, never an edit.
> - **Amendments to methodology v2.0** are marked **[AMENDS v2.0 §n]** and need the same approval.

## 1. System under test

**Code and contract**

| Item | Pin |
|---|---|
| `main` SHA | `04270a3f2`, or the merge commit at the freeze if any later PR merges first. The freeze manifest records the final SHA, and any change after it is a new version |
| Contract | `incident-contract-v4` |
| `contracts/VERSION` | `5fbb84d93b94e821…` |
| P1 prompt (`incident_extraction_prompt.txt`) | `409ed9b3187a56be…` |
| Schema (`incident_report.schema.json`) | `a13062782013bd26…` |
| `inference.json` | `d5b89f976d207e13…` |
| Prompt registry (`contracts/prompts/registry.json`) | `a89c8adeb127dfaa…`. It pins the caption, preamble, grounding, zoom, chart (system `c6cf0780`, allowlist `cbf157ba`, severity words `b644908a`) and chat prompts |

**Track A (model comparison):** P1 only, through the direct path. Every subject gets a byte-identical v4 P1 request, and clips are sent as presigned R2 URLs.

**Track B (production pipeline).** **Historical C1b-equivalent configuration, labelled "Track B (C1b profile)"**; this is not current production (see §9 and the [readiness report](v2-c2-freeze-readiness.md#3-track-b-configuration-audit)).
- **Agent:** the experiment agent at `acbf892ba`, from its own checkout, started with `env -i` and `INCIDENT_EXPERIMENTS_ENABLED=1`.
- **Profile:** `production-v4-captions`. The caption pass runs inline, with at most 30 caption calls, 40 crop calls and 80 zoom calls.
- **Flags, as pinned by the agent in `agent.jsonl` for C1b:**
  - person tracking, object tracking, privacy blur and set-of-marks off;
  - `zoom_names` on;
  - chunking: 20 s windows, 2 s overlap, 32 frames, only for clips over 30 s.
- **Guard:** the Track B adapter refuses a reply whose pinned agent SHA or flags differ.

**Dashboard:** the console's chart request on `agent-route-v3`, which is the agent route plus `chart-severity-words-v2` (#476, `b644908a…`). It is read by Nemotron 3 Ultra, which is the production chart model.

**Chat:** grounded mode, Nemotron 3 Ultra, run through the chat harness (`evalv2-chat-harness-v1`) over the oracle and system corpora.

**SQL harness:** the finals schema from `supabase/migrations/` (14 files; concatenated SHA-256 `e3b1f34353b16248…`; last file `20261102090000_drop_blur_faces.sql`). The dashboard snapshot is `corpus-snapshot.json` (`1c5b2927…`), with its clock frozen at `2026-11-01T12:00:00Z`.

## 2. Subjects and stage models

| Subject | Track | P1 model | Caption, grounding and zoom model | Endpoint | Verified on |
|---|---|---|---|---|---|
| `nano-direct` | A | `nvidia/cosmos-3-nano-reasoner` | — | Switchyard | C1a, 2026-11-02 |
| `super-direct` | A | `nvidia/cosmos-3-super-reasoner` | — | Switchyard | C1a |
| `omni-direct` | A | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | — | Switchyard | C1a |
| `qwen35-9b-openrouter` | A | `qwen/qwen3.5-9b` | — | OpenRouter (pinned providers, no fallback, `data_collection: deny`) | C1f |
| `gemma4-31b-openrouter` | A | `google/gemma-4-31b-it` | — | OpenRouter (as above) | C1e |
| `nano-pipeline` | B | Nano | the agent's `VLM_NAME` (Super), as in production | experiment agent → Switchyard | C1b |
| `super-pipeline` | B | Super | Super | experiment agent → Switchyard | C1b |
| `ultra-chart` | dashboard | `nvidia/nemotron-3-ultra` | — | Switchyard | C1c and #476 evaluation |
| chat subject | chat | `nvidia/nemotron-3-ultra` | — | Switchyard | C1c chat |

**Unsupported pairs** (reported as unsupported, never scored):
- Omni, Qwen and Gemma on Track B: the pipeline runs Nano and Super only.
- Every subject on evidence boxes (dimension C is out of scope, §4).

## 3. Data

**Clips and ground truth**

| Item | Pin |
|---|---|
| Clips | `dataset-manifest-v1` (`aebb209b…`) |
| Split | `split-v2` (`c03919fa…`): 25 dev / 55 test. The test half, 3,408 s in total (mean 62.0 s), is used only in C3 |
| Ground truth | `ground-truth-v1.0` (`391fcd27…`), frozen 2026-10-28. It was labelled by people with AI-assisted adjudication. No human-verified subset exists |

**References used in C3 (none human-verified)**

| Dataset | Pin | Contents | Status |
|---|---|---|---|
| `chat-references-v1` | `08ab3909…` | 119 test-half references (189 in all). Answers are computed by code from ground truth; wording by Claude with blind re-derivation | `ai_validated` / `ai_proposed`. The AI-only review of the test-half references is deferred until after C3 |
| `dashboard-references-test-v1` | `53782844…`, snapshot `1c5b2927…` | **New held-out dashboard set**: 75 requests (60 specs over all 16 dimensions, 15 must-refuse, one per unsupported kind) | Specs from `rb2.spec_plan` (seed 20261104) with every dev spec excluded; requests rendered from the fixed templates `dashboard-test-templates-v1`. Expected specs, refusal decisions and figures are deterministic. No model wrote or reviewed them, and nothing has been sent to any model. Tier `ai_proposed` (the label for non-human references) |
| `dashboard-references-v2` | `103603a8…` | Dev RB2 | Development only; reported separately and never pooled with C3 |

**Exclusions** (decided now, before any test run):
- **No clip is excluded.**
- **Sensitivity analysis:** a 52-clip analysis excluding Animal003, Assault018 and Burglary005, the three clips that shaped V1 prompts.
- **Dimension C (evidence-box localisation)** is excluded from C3 (§4).

## 4. Primary metric per dimension and the comparisons

| Dimension | Primary metric | Primary comparisons (paired by clip, 95% cluster bootstrap) | Multiplicity family |
|---|---|---|---|
| A Incident extraction | **Severity MAE** (`severity-v2-mae`, lower is better) and **type accuracy** **[AMENDS v2.0 §1: replaces the D9 composite; no composite incident score exists]** | Nano − Super on Track A; Nano − Super on Track B | A (4 tests) |
| B Object identification | Mean of the entity F1 and the object F1 at τ = 0.40 (MiniLM, Hungarian matching) | Nano − Super, Track A and Track B | B (2 tests) |
| C Evidence boxes | **Not evaluated in C3** **[AMENDS v2.0 §1]**. RA1 continues separately, and no bounding-box IoU result will be claimed | — | — |
| D Chatbot | **Exact-answer accuracy** (code-computed answers) and **retrieval R@5** (library retrieval questions) on the oracle corpus **[AMENDS v2.0 §1: judged fact recall moves to secondary because the judge is not human-calibrated]** | Single subject (Ultra): estimates with intervals; oracle vs system reported side by side, not tested | D (2 estimates) |
| E AI dashboard | **Exact-spec accuracy, refusals included**, on `dashboard-references-test-v1` | Single production subject (Ultra, `agent-route-v3`): estimate with interval | E (1 estimate) |
| F Tokens and cost; G Latency and reliability | Descriptive only | — | — |

**Secondary metrics (exploratory, all named):**

| Dimension | Secondary metrics |
|---|---|
| A | Start/end timestamp MAE; severity exact match and QWK; V1-continuity field accuracy; judged description pass rate (PROVISIONAL AI-judged) |
| A and B | All other model pairs, and each external subject vs Super |
| B | Per-kind F1; judged attribute pass rates (PROVISIONAL) |
| D | Citation precision and recall; refusal precision and recall; retrieval P@1, MRR@10, nDCG@5; judged fact recall, unsupported-claim rate and contradiction rate (PROVISIONAL AI-judged; holder identification is excluded as not interpretable, see §6) |
| E | Figure accuracy; refusal precision and recall; false acceptance; field accuracy; Nano and Super comparison arms |
| F and G | Tokens, cost, latency p50/p95, validity, retries |

**Effects (within one model):**
- **Caption effect:** Track B raw P1 minus Track A P1.
- **Zoom effect:** the final stage minus the pre-zoom stage, with no extra calls.

**Failure-aware reporting:**
- Each metric is reported conditional on valid outputs **and** end to end, where an invalid or failed run counts as a miss.
- Severity coverage (n / attempted) is shown beside severity MAE.

## 5. Statistics

- **Repeats:**
  - Track A: 2 per subject and clip, interleaved with schedule seed 20261101.
  - Track B: 1.
  - Chat and dashboard: 1.
- **Bootstrap:**
  - 10,000 resamples, seed 20261027.
  - Cluster unit: the video for A and B, the incident (or library record) for D, the reference record for E.
  - Paired differences resample clips jointly.
- **Multiplicity:** Holm within each dimension's primary family (A: 4 tests; B: 2). D and E are estimates without tests.
- **Minimum detectable effect** (80% power, two-sided 0.05, n = 55, SD of paired per-clip differences from the dev pilots):

  | Metric | Track A | Track B |
  |---|---|---|
  | Severity MAE difference | 0.33 levels | 0.34 levels |
  | Type accuracy difference | 12.6 points | 14.2 points |

  The dev differences (Nano − Super severity: +0.27 on A, +0.18 on B) are **below** these MDEs, so a null result is likely and is reported as "not resolved", never as "no difference".

## 6. Judges and instruments

**Primary judge:** `openrouter-gpt-oss-20b-v1` (`openai/gpt-oss-20b`).
- Routing: OpenRouter → DeepInfra or DekaLLM, no fallback, no data retention.
- Settings: temperature 0, `max_tokens` 1,500, parse retries 0, `max_prompt_bytes` 65,536.
- Prompts: `judge-prompt-v1` for incident fields; for chat, `judge-fact-v1` (`7a9363c9…`), `judge-unsupported-v1` (`10c7b1f3…`) and `judge-contradiction-v1` (`81cec67b…`).
- Pass threshold: score ≥ 0.5.

**Judge–human κ:** **none.**
- No human calibration was done (owner decision, 2026-11-03).
- The **AI-consensus agreement** on the 72 consensus calibration pairs is κ 0.63 [0.43, 0.80], with the judge more lenient than the AI-consensus. It does **not** meet the pre-registered human criterion (κ ≥ 0.60 against people).
- All judged metrics are therefore **secondary and PROVISIONAL AI-judged**.
- **Sensitivity judge:** none in C3 (Mistral Large 4 is paused; 89/206 dev pairs, missing not at random).

**Chat holder identification:** excluded. The holder fact embeds the full ground-truth entity description, actions included, which the judge then requires (0/5 in both dev corpora).

**Matcher:** MiniLM (local), τ = 0.40, Hungarian matching. There is no separate matching validation set (no human labels); τ curves are reported as a sensitivity analysis.

**Unresolved reference disputes** (kept, never resolved by the biased adjudicator): 8 judge-calibration pairs and 2 dev chat references. None is a test-half record.

## 7. Budgets and stop rules

- **Caps:**
  - Every run's caps are set at the C3 estimate +30% ([readiness report §5](v2-c2-freeze-readiness.md#5-estimated-c3-workload)), with `budget_mode: hard_stop`. A call that could cross a cap is refused before it is sent.
  - Paid OpenRouter calls are capped per run in USD.
  - Proposed C3 total: **$2.00**, against an expected ≈ $0.70.
- **Stop rules:**
  - 10 consecutive failures of one kind stop a subject (a systematic failure).
  - A provider outage or HTTP 5xx is retried by `retry-v2`.
  - HTTP 429 is paced and retried, and counts against the attempt cap.
  - A session limit stops the run, which resumes from its checkpoint.
- **Recovery policy:**
  - A recovery run re-sends only items left open by **infrastructure** failures, with the same request bytes.
  - A model failure (invalid output, runaway reasoning) is a result and is never retried for score.
- **No run is repeated to improve a result.**

## 8. Default-model recommendation rule (R8) — PROPOSED

The question is whether the console default (Cosmos 3 Nano) and the agent default (Cosmos 3 Super) should be aligned. The rule, applied once to the C3 results:

1. **Recommend Super** only if:
   - the Track B severity MAE difference (Super − Nano) has a 95% CI entirely below 0, **and**
   - Super's end-to-end validity on Track B is no more than 5 points below Nano's, **and**
   - Super's Track B p95 latency is ≤ 300 s.
2. **Recommend Nano** under the mirror-image condition.
3. **Otherwise: no change**, reported as "not resolved by C3".

Type accuracy, object F1, cost and latency are reported beside the rule. They are never combined into a score. There is no hidden threshold.

## 9. Disclosures

- **The test clips are held out but not unseen** ([v2-dataset-integrity.md](v2-dataset-integrity.md)):
  - the 2026-10-02 V1 benchmark ran three models on all 55;
  - three test clips shaped earlier prompts;
  - the AI-assisted GT correction viewed frames of 38 clips from both halves;
  - the GT adjudicator and the reference generator are the same family (Claude).
- `ground-truth-v1.0` and τ are AI-assisted.
- **References:** every reference is AI-generated or rule-generated, and none is human-verified. The dashboard test set is template-worded, unlike the Claude-worded dev set, so dev and test dashboard scores are not directly comparable.
- **Track B** is the historical C1b profile (tracking off) at agent `acbf892ba`. It is not current production, which runs person tracking, object tracking and captions on.
- **Frame sampling** is decided by the provider (Cosmos 3 Super ignores `num_frames`).
- **Serving:** temperature-0 serving is not bit-deterministic.
- **#476:** the dev gain was not statistically significant (+0.04 [−0.03, 0.11]), and one request regressed.

## 10. Approval

- **Approved by:**
- **Approved at:**
- **Freeze manifest SHA-256:**

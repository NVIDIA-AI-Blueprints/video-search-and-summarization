# Evaluation V2: run readiness (RA1, RA2, RB2, C1a, C1b, C1c dashboard, C1c-chat)

> **Status (2026-11-02): prepared, not run.** No model, embedding, R2 or database call was made, no weight was downloaded, and no reference dataset was generated. The live executor (B1) and the scoring stage are built and tested with fakes only; B3 is fixed. The pilots wait only for the owner's approval markers (and, for C1b, B2).
> - Counts come from offline dry runs on `ground-truth-v1.0` (read-only) with a synthetic detector and scripted passes. Their answers are fake; their counts are the real runs' counts.
> - Budgets use measured V1 figures where they exist and say so where a figure is an assumption.
> - Dry-run outputs are private (`eval/eval_data/v2-dry-run/`, gitignored) and are never registered.
> - Methodology: [v2.0](evaluation-methodology-v2.0.md). Sizes, caps and rules marked **proposal** are not decisions.

## 1. Where each run stands

| Run | What it makes | Still needed before it can start (besides its approval) | Outputs and storage |
|---|---|---|---|
| **RA1** box references | 298 box records (80 clips) | X5 detector env and weights (B5); a session plan for 45,709 frame reads (B6); X6 reviewers (B7) | `$EVALV2_DATA_ROOT/v2/references/box-references-v1/`: frames, cached proposals, packets and answers (keyed by packet SHA-256), `records.jsonl`, `summary.json`, review sheets |
| **RA2** chat references | 189 questions (dev 70, test 119) | X7 and X20 (owner); X6 reviewers | `.../chat-references-v1/`: packets, answers, `records.jsonl`, `summary.json` |
| **RB2** dashboard references | 75 requests (60 specs, 15 unsupported) and the corpus snapshot | The allowlist export, on `main` but not on this branch until it is refreshed (B4); the C2 frozen clock for the real snapshot | `.../dashboard-references-v1/`: packets, answers, `records.jsonl`, `corpus-snapshot.json`, `summary.json` |
| **C1a** Track A pilot | 150 P1 calls (3 models × 25 dev clips × 2) | The approval marker (§2). Scoring: the judge probe (B8) | `--out` run directory: artifact store (content-addressed, append-only, sealed run), `budget.jsonl`, `checkpoint.jsonl`, `attempts.jsonl`, run manifest; `scoring/` |
| **C1b** Track B pilot | 50 `/analyze` runs (Nano, Super × 25 dev clips) | The marker; an experiment agent running `main`'s P3 and P4 code (B2) | As C1a, plus `agent.jsonl` (the pinned agent) and crop and zoom summaries for recalibrating caps |
| **C1b + captions** | As C1b, with the inline caption pass (agent PR #439) | B2 with #439; the base64 probe, then a marker that lists `production-v4-captions` in `enabled_profiles` | As C1b |
| **C1c** dashboard pilot (ran 2026-11-02, approved) | 225 chart calls (3 text models × 75 RB2 requests × 1) | The owner's approval of the proposal and its marker ([§9](#9-dashboard-evaluation-c1c)) | As C1a (no clip, no R2); `scoring/evalv2-dashboard-score-v1/` |
| **C1c-chat** chat pilot | 140 chat answers (70 dev RA2 questions × oracle and system corpora), at most 180 chat-model calls | The marker; the RA2 human review for any result beyond plumbing (§10) | `--out` run directory: `plan.json`, `corpora/`, `answers.jsonl`, `budget.jsonl`, `manifest.json`; `score/` |

- **Immutability.** A reference run only appends: packets and answers are addressed by the packet's SHA-256, and an answer to a changed packet is refused.
- **Versioning.** A finished `records.jsonl` gets an append-only `datasets/registry.json` entry that pins its SHA-256 (`root: data`). Human review (`review apply`) writes a new file under a new dataset id; nothing is edited in place.

## 2. Launch commands

From `deploy/docker/developer-profiles/dev-profile-incident/eval`, with `EVALV2_DATA_ROOT` set to the main checkout's `eval/eval_data`.

**Reference runs and sessions.** `references ra1|ra2|rb2` never calls a model. Each call advances every item as far as the answers on disk allow, then stops.
- It writes pending packets to `$RUN/generator/packets/` or `$RUN/validator/packets/`.
- A Claude Code session answers each packet with the registered prompt the packet names, writing `{"packet_id", "packet_sha256", "answer"}` to `.../answers/<packet id>.json`. It copies `packet_sha256` from the packet file.
- The validator session never opens `$RUN/generator/`.
- `--generator-model` and `--validator-model` record the sessions' models in every record.

```bash
M=<model id of the Claude Code sessions>
# RA1 (box references)
RUN=$EVALV2_DATA_ROOT/v2/references/box-references-v1
uv run python -m evalv2 references ra1 --out $RUN --plan-only                                  # items, frame times, extraction plan
uv run --no-project --script scripts/v2_box_frames.py extract $RUN/extraction-plan.json $RUN/frames
uv run python -m evalv2 references ra1 --out $RUN --detector-command "<X5 detector command>" \
    --generator-model $M --validator-model $M                                                  # proposals (cached) + generator packets
for p in $RUN/generator/packets/*.json; do uv run --no-project --script scripts/v2_box_frames.py draw $p $RUN/frames $RUN/drawn; done
#   generator session (gen-box-select-v1): each frame's image is $RUN/drawn/<frame.image>
uv run python -m evalv2 references ra1 --out $RUN --generator-model $M --validator-model $M     # blind validator packets (assert_blind)
for p in $RUN/validator/packets/*.json; do uv run --no-project --script scripts/v2_box_frames.py draw $p $RUN/frames $RUN/drawn; done
#   validator session (gen-box-validate-v1), separate
uv run python -m evalv2 references ra1 --out $RUN --generator-model $M --validator-model $M     # records.jsonl, summary.json
uv run --no-project --script scripts/v2_box_frames.py sheet $RUN/records.jsonl $RUN/frames $RUN/review-sheets
uv run python -m evalv2 review export --records $RUN/records.jsonl --out $RUN/review.xlsx --seed 20261101 \
    --links $RUN/review-sheets/links.json

# RA2 (chat references): the same three advances; prompts gen-chat-wording-v1, then gen-chat-rederive-v1
RUN=$EVALV2_DATA_ROOT/v2/references/chat-references-v1
uv run python -m evalv2 references ra2 --out $RUN --generator-model $M --validator-model $M
uv run python -m evalv2 review export --records $RUN/records.jsonl --out $RUN/review.xlsx --seed 20261101 \
    --strata kind,tier,strata.class

# RB2 (dashboard references): prompts gen-dashboard-request-v1, then gen-dashboard-respec-v1
RUN=$EVALV2_DATA_ROOT/v2/references/dashboard-references-v1
uv run python -m evalv2 references rb2 --out $RUN --clock <C2 frozen clock> --generator-model $M --validator-model $M
uv run python -m evalv2 review export --records $RUN/records.jsonl --out $RUN/review.xlsx --seed 20261101 \
    --strata kind,tier,strata.class

# Dry runs (what this page reports): add --dry-run, any private --out; RB2 also --allowlist tests/v2/fixtures/chart-allowlist.json
```

**Pilots (B1 resolved).** `run --live` executes a Track A or Track B config; the same command on the same `--out` resumes it. Run from a clean checkout of this branch (a dirty eval tree is refused), with the main checkout's credentials file:

```bash
cd deploy/docker/developer-profiles/dev-profile-incident/eval
export EVALV2_DATA_ROOT=/home/hengz/video-search-and-summarization/deploy/docker/developer-profiles/dev-profile-incident/eval/eval_data
ENVF=$EVALV2_DATA_ROOT/../.env.local          # VLM_GATEWAY_*, R2_* (signing only), NVIDIA_API_KEY (judge)
RUNS=$EVALV2_DATA_ROOT/v2/runs                # private (gitignored)

# 1. Marker skeletons; the owner fills approved_by and approved_at (and may lower any figure, never raise it)
uv run --frozen python -m evalv2 approval draft --config experiments/v2/c1a-track-a-pilot.json > $RUNS/c1a-approval.json
uv run --frozen python -m evalv2 approval draft --config experiments/v2/c1b-track-b-pilot.json > $RUNS/c1b-approval.json

# 2. C1a (Track A, Switchyard)
EVALV2_ALLOW_LIVE=1 uv run --frozen python -m evalv2 run --config experiments/v2/c1a-track-a-pilot.json --live \
    --approval $RUNS/c1a-approval.json --out $RUNS/c1a --env-file $ENVF

# 3. C1b (Track B, local experiment agent)
NAT_BIN=<nat with P3+P4> experiments/agent/start-experiment-agent.sh       # loopback :8765, experiments on, no Supabase or R2
EVALV2_VSS_AGENT_URL=http://127.0.0.1:8765 EVALV2_ALLOW_LIVE=1 uv run --frozen python -m evalv2 run \
    --config experiments/v2/c1b-track-b-pilot.json --live --approval $RUNS/c1b-approval.json --out $RUNS/c1b --env-file $ENVF

# 4. Scoring: live (judge calls under the marker's judge budget), then any re-score offline from the caches
EVALV2_ALLOW_LIVE=1 uv run --frozen python -m evalv2 score --config experiments/v2/c1a-track-a-pilot.json \
    --out $RUNS/c1a --approval $RUNS/c1a-approval.json --env-file $ENVF
EVALV2_ALLOW_LIVE=1 uv run --frozen python -m evalv2 score --config experiments/v2/c1b-track-b-pilot.json \
    --out $RUNS/c1b --approval $RUNS/c1b-approval.json --env-file $ENVF
uv run --frozen python -m evalv2 score --config experiments/v2/c1a-track-a-pilot.json --out $RUNS/c1a --offline
#   Outputs: scoring/<score version>/<slot>/ (score stage v2 and later; v3 is current); v1 outputs in scoring/<slot>/ stay as they were.
#   Severity per model (MAE primary), merging a pilot and its recovery run item by item:
#   uv run --frozen python -m evalv2 severity-report --videos $RUNS/c1a/scoring/evalv2-score-stage-v3/primary/offline-videos.jsonl \
#     --videos $RUNS/c1a-recovery/scoring/evalv2-score-stage-v3/primary/offline-videos.jsonl --out <private dir> --title "C1a"
#   --offline --embed-local also computes the MiniLM vectors locally (no network, no judge): object matching
#   is scored before judge credits are confirmed; judge fields stay unscored until a live score
#   --slot sensitivity re-judges with catalog-mistral-large-4-v1 (Omni sensitivity check)
```

- **Recovery run** (after exit 4 from a cap refusal). A config cannot change under a resume, so the open items are re-sent by a separate recovery config: the same subjects, task, repeats, seed and request, plus `"recovery": {"of_experiment", "of_manifest", "items": [{"subject", "item_id", "repeat"}]}` naming only the earlier run's open items. The plan then holds exactly those items (a name the plan does not hold is refused), the run has its own marker and `--out`, and its caps plus the earlier run's spend must stay inside the owner's approved totals. Final items of the earlier run are never re-sent; scoring takes each item's outcome from whichever run finalised it.
- **`--env-file`** names a credentials file explicitly; a named file that does not exist is refused (exit 2) instead of being skipped. Its values fill names that are unset or blank (V1's `eval/config.py` loads the profile's committed `.env`, whose credential names are blank placeholders); a non-empty value already set is never replaced.
- **Exit codes** of `run --live`: 0 sealed; 2 refused before anything was sent (every reason listed); 3 stopped part-way (agent drift, a refusal, a configuration error; finished items are kept, fix the cause and resume); 4 finished with open items (a budget refusal or an infrastructure failure after its retries; resume retries them, a cap refusal needs a new marker and config).
- **What it does.** Work items run in V1's seeded interleaved order (`experiment_runner.schedule`). Track A is paced at the gateway's 60 rpm and 4 in flight, with a token window per model (Omni 30,000 tokens a minute, estimated from its measured calls); Track B runs one analysis at a time with V1's 20 s gap. Every attempt reserves its bound (Track B: holds the profile's worst case) before it is sent; retry-v2 and the host-suspend relabel are the adapters' own. Clip URLs are presigned R2 GETs from `dataset-manifest-v1` (signed locally, read-only, re-signed before expiry and on resume, never stored).
- **What it records.** `attempts.jsonl` (one row per HTTP attempt: model, prompt SHA-256, request hash, tokens, latency, waiting, outcome, failure kind); `manifest.start.json` (write-once) and the sealed manifest: eval commit and dirty flag, config hash, dataset hashes, the marker, the config and marker caps, pacing, endpoints by variable name, P1 prompt provenance or the Track B profiles, and Track B's pinned agent.
- **Refused before sending:** preflight problems; no marker, or one that does not fit (below); a test-half clip in a pilot (in the plan, and again at signing); a dirty eval tree; a missing credential (named, never printed); a resume whose config, inputs or plan changed.

- **C1e external baseline (proposed, not approved).** First the probes, which are opt-in for catalog candidates: `python -m evalv2.probe --models google/gemma-4-31b-it --only schema_inline,frames_many,long_clip,over_60s` (dry run without `--live`; 4 calls, worst case 0.13 M tokens). Then `experiments/v2/c1e-external-baseline-pilot.json` (50 dev runs, ≤ 65 attempts, ≤ 1.5 M tokens). Both need catalog credits confirmed and their own markers. Kimi K2.6 (approved: 2 probes, 0.05 M): `--models moonshotai/kimi-k2.6 --only schema_inline,p1_short` (worst case 48,432 tokens; `p1_short` is the unchanged P1 v4 request checked with V1's strict policy). The comparison comes from `python -m evalv2 track-a-report --run <c1a> --run <c1a-recovery> --run <c1e> --out <private dir>`: full-set outcomes, contract-valid metrics (severity MAE primary), paired clips, duration buckets, tokens and latency. **The catalog balance cannot be read through the API** (NVIDIA staff said in 2025 that credits were replaced by rate limits; other NVIDIA pages still mention credits), so the owner confirms it from their build.nvidia.com account before any catalog call.

- **OpenRouter route (proposed, no account yet).** Probes: `python -m evalv2.probe --models google/gemma-4-31b-it,qwen/qwen3.5-9b --endpoint openrouter --only schema_inline,frames_many,long_clip,over_60s` (8 calls, worst case 257,536 tokens). Pilots: `experiments/v2/c1e-openrouter-gemma4-31b.json` and `c1f-openrouter-qwen35-9b.json` (50 dev runs each, ≤ 65 attempts, ≤ 1.5 M tokens). Needs `OPENROUTER_API_KEY` in `.env.local` and a prepaid balance. An `openai_compat` subject's `request.extra_body` may carry only the `provider` routing block; anything else is refused.

- **C3 external add-on (proposed for the C2 package):** 55 test clips × 2 repeats per external model. Measured tokens per answered call: Gemma 6.7 k, Qwen 14.9 k (a runaway costs up to about 20 k). Expected about 0.8 M (Gemma) and 1.9 M (Qwen); proposed caps 1.2 M / 145 attempts and 2.6 M / 145 attempts. At the pinned providers' prices that is under $1.50 together, within the $10 key limit.

- **Judge stage (approved 2026-11-02: at most 1,300 calls, 3.0 M tokens, $1.77).**
  - **Marker:** a run whose config carried no judge share is scored live with a dedicated `evalv2-judge-approval-v1` marker. It names the experiment, the config hash, the allowed judge ids and a judge budget, and it replaces the config's judge caps.
  - **Global caps:** the shared judge ledger (`--judge-ledger-dir`) holds the registry caps across every run, retry and resume: primary `openrouter-gpt-oss-20b-v1` 999 calls / 2.299 M tokens; sensitivity `openrouter-mistral-large-4-v1` 299 calls / 0.695 M tokens. The compatibility probe uses 2 calls.
  - **Command:** `EVALV2_ALLOW_LIVE=1 uv run --frozen python -m evalv2 score --config <run config> --out <run> --judge openrouter-gpt-oss-20b-v1 --approval <judge marker> --judge-ledger-dir <private>/v2/judge-ledger --env-file <.env.local>`.
  - **Status of the scores:** judged scores are **provisional**. The planned human calibration (two reviewers, 80 pairs, κ ≥ 0.60) will not happen; an AI-consensus agreement analysis replaces it and does not meet that threshold ([AI-only review](v2-ai-only-reference-review.md), [proposal](v2-judge-proposal.md)).

**Approval marker** (`evalv2-live-approval-v1`, JSON). `approval draft` fills everything except the approval itself:

```json
{
  "approval": "evalv2-live-approval-v1",
  "experiment": "v2-c1a-track-a-pilot",
  "config_sha256": "<sha256_json of the validated config, as approval draft prints it>",
  "purpose": "pilot",
  "half": "dev",
  "approved_by": "<owner>",
  "approved_at": "2026-11-0X",
  "max_items": 150,
  "max_total_tokens": 6000000,
  "caps": [{"ledger": "inference", "max_attempts": 195, "max_tokens": 6000000}],
  "enabled_profiles": [],
  "judge": {"max_requests": 1100, "max_tokens": 1650000},
  "note": "relayed by the evaluation lead on ..."
}
```

- The run is refused when the experiment, config hash, purpose or half differ, when the plan holds more items than `max_items`, when any config cap (of any scope) exceeds its ledger's marker cap or names a ledger the marker does not list, when a charged ledger has no run-wide cap, when the run-wide token caps sum past `max_total_tokens`, or when a gated profile is not in `enabled_profiles`. The marker's caps are also added to the run's ledger.
- `judge` approves the scoring stage: a live `score` refuses without it, and the config's judge cap must fit it. Every judge attempt is reserved on the judge's shared ledger (`$EVALV2_DATA_ROOT/v2/judge-ledger/<judge id>.jsonl`, the `judges.json` ceilings: 1,500 + 500 calls and 2.25 M + 0.75 M tokens, together the approved 2,000 calls and 3 M tokens across every scoring stage) and on the run's own judge ledger.
- C1b's marker draft has `max_items` 50, `max_total_tokens` 9,000,000 and caps inference 2 M, grounding 2.5 M, zoom 4.5 M tokens. The caption variant needs its own marker with `"enabled_profiles": ["production-v4-captions"]`, written only after the base64 probe.

`plan` and `preflight` pass offline for all three configs (output in the [appendix](#appendix-plan-and-preflight-output)); with `budget_mode: hard_stop` preflight lists every cap below the worst case under `hard_stop`.

## 3. Counts from the dry runs

### RA1: box references

| | dev | test | total |
|---|---|---|---|
| Clips (of which 320×240) | 25 (20) | 55 (45) | 80 (65) |
| Clip length | 1,598.9 s | 3,394.0 s | 4,992.9 s |
| Items: entity / instrument / asset | 50 / 18 / 30 = **98** | 119 / 14 / 67 = **200** | 169 / 32 / 97 = **298** |
| Detector queries: distinct / clip-query pairs | 30 / 61 | 55 / 134 | 73 / 195 |
| Native frames to extract (generator + validator) | 1,612 + 1,597 = 3,209 | 3,424 + 3,393 = 6,817 | **10,026** |
| Frames per clip: mean / median / min / max | 128.4 / 99 / 11 / 600 | 123.9 / 84 / 20 / 666 | 125.3 / 84.5 / 11 / 666 |
| Disk, native frames (q92 JPEG, size measured per clip) | 134.5 MB | 228.0 MB | **362.5 MB** |
| Drawn frames in packets (generator + validator) | 8,430 + 8,367 = 16,797 | 14,509 + 14,403 = 28,912 | **45,709** |
| Disk, drawn frames | 575.3 MB | 867.4 MB | **1.44 GB** |
| Packets (generator + validator) | 196 | 400 | 596 |
| Clips whose last second only the generator sees | 15 | 31 | 46 |

- **Frames.** Generator frames fall on every whole second and validator frames half a second later (`frame_plan`). Nine clips over 120 s hold 51 items and dominate the frame counts.
- **Dry-run tiers** (a synthetic detector missing 10% of visible frames, passes wrong on 1%): dev 2 `ai_validated` and 96 `ai_proposed`; test 1 and 199; 0 `human_verified`. All 298 validator packets passed `assert_blind`.
  - The flags are all `validators_disagree`.
  - Seconds: 12,123 agreed, 3,365 disagreed and 45 single-pass.
- **How the tier rule behaves** (all 80 clips, scripted passes):

| Synthetic scenario | `ai_validated` | flagged | Disagreeing seconds |
|---|---|---|---|
| Perfect passes, every item always in view | 298 | 0 | 0% |
| Perfect passes, items enter and leave the view | 104 | 194 | 1.6% |
| Detector misses 2%, passes wrong 0.5% | 28 | 270 | 6.9% |
| Detector misses 5%, passes wrong 1% | 11 | 287 | 13.5% |
| Detector misses 10%, passes wrong 1% (the dry run) | 3 | 295 | 21.7% |

A record is `ai_validated` only when every second either pass saw agrees. The validator's frame for second *s* is at *s* + 0.5 s, so an item entering or leaving the view between the two frames flags the whole record. This decides the review load (B7, proposal P1).

### RA2: chat references (every answer computed by code, except written facts)

| Class | dev | test | | Class | dev | test |
|---|---|---|---|---|---|---|
| incident.type | 5 | 10 | | library.type_count | 5 | 5 |
| incident.severity | 5 | 10 | | library.severity_at_least_count | 4 | 4 |
| incident.window | 5 | 10 | | library.instrument_incidents | 5 | 5 |
| incident.people_count | 5 | 10 | | library.most_common_type | 1 | 1 |
| incident.instrument_list | 5 | 9 | | library.retrieval | 5 | 5 |
| incident.instrument_holder | 5 | 10 | | **Library scope** | **20** | **20** |
| incident.asset_list | 5 | 10 | | | | |
| incident.facts | 5 | 10 | | | | |
| incident.unanswerable | 5 | 10 | | | | |
| incident.false_premise | 5 | 10 | | | | |
| **Incident scope** | **50** | **99** | | **Total** | **70** | **119** |

- **Halves are separate libraries.** A library question counts or searches only its half's reports (`params.corpus`). Only 9 test incidents have instruments, so the test half has 9 instrument-list questions.
- **Answer kinds:** count 33, value 30, list 51, window 15, holder 15, refusal 30, written facts 15.
  - `recount` agreed for all 189.
  - All 189 validator packets passed `assert_blind`, and leakage was computed for every record: mean description overlap 0.014, max 0.148.
- **Dry-run tiers:** 187 `ai_validated`, 2 `ai_proposed`, 0 `human_verified`.
  - The two flags are true positives: a template question names a word that is the instrument's ground-truth name.
  - Before the fixes in §6 the same run flagged 11: 7 false leakage failures and 4 false disagreements.

### RB2: dashboard references

| | |
|---|---|
| Valid specs on the allowlist | 1,690 (16 dimensions, 14 take filters) |
| Planned specs | 60: four per filterable dimension (no filter, a type, a severity, both) and two per unfilterable one. Each period appears 12 times; 28 have a type filter and 28 a severity filter |
| Unsupported requests | 15 (20% of 75), each with its own hint covering every kind the prompt lists |
| Requests / packets | 75 / 150 |
| Corpus snapshot (80 ground-truth reports, 90 days before the clock) | 80 videos, 80 revisions, 80 runs. 55 of 60 reference charts have rows; 5 are empty (a filter that matches nothing on the corpus) |
| Dry-run tiers | 71 `ai_validated`, 4 `ai_proposed` (a scripted 5% validator error), 0 `human_verified`; 75 of 75 blind checks passed |

## 4. Budgets and timings

**Inputs.**
- *Measured:*
  - Mean P1 tokens per call: Nano 8,727, Super 9,898, Omni 19,690 (2 Oct benchmark, 55 clips). The largest calls were 22,321, 20,965 and 28,656.
  - Mean P1 latency: 11.6 s, 21.7 s and 30.8 s.
  - Retries add 10.9% attempts (Stage 3: 27 of 247).
  - Grounding uses 350 tokens per call, at a median of 5 calls per run (end-to-end profile).
  - Omni has a 30,000 tokens/min quota.
- *Assumed:* 1,500 tokens per zoom call and 11 zoom calls per run (2.5 per located item); 7,000 tokens per caption call; 525 tokens per judge call (V1's measured mean); 60 s per Track B run including the 20 s V1 gap; about 3 s per image a session reads. For C3 only: 4,000 tokens per chat answer, 3,000 per dashboard call, and a judge limit of 40 rpm (the catalog's is unverified).
- *Rates:* planning rate $10 per million tokens. Switchyard charges $0. Reference sessions run in Claude Code, not on the gateway, so the gateway charge for them is also $0.

| Run | Calls | Tokens, typical | Tokens, worst case | Wall time | $ at planning rate |
|---|---|---|---|---|---|
| **RA1** | 596 session packets; detector over 10,026 frames (80 clip batches) | 10.71 M = text 2.22 M + images 7.91 M + output 0.58 M | 21.4 M (every packet answered twice) | Sessions: 45,709 image reads × 3 s ≈ 38 h in one session, ≈ 9.5 h in four. Detector: ≈ 2.8–5.6 h on CPU (1–2 s per frame) | $107 (worst $214) |
| **RA2** | 378 session packets | 0.84 M (library validator packets average 9.1 k tokens on dev and 20.3 k on test) | 1.7 M | 378 × ≈ 20 s ≈ 2.1 h | $8.4 (worst $17) |
| **RB2** | 150 session packets | 0.15 M | 0.3 M | 150 × ≈ 10 s ≈ 25 min | $1.5 (worst $3) |
| **C1a** | 150 P1 (≈ 166 attempts) | 1.92 M metered = 50 × (8,727 + 9,898 + 19,690); 2.48 M as charged (16 failed attempts at 35,000) | 15.75 M = 450 attempts × 35,000 (cap 20.48 M) | V1 pacing (one call at a time, 20 s gaps): 50 × (31.6 + 41.7 + 50.8) s + retries ≈ 2.3 h. At 4 in flight ≈ 13 min of model time, but Omni's quota sets a floor of 50 × 19,690 / 30,000 ≈ 33 min | $19.2 (worst $158) |
| C1a scoring | ≤ 1,026 judge calls = 171 judged fields × 2 × 3, plus 342 for the Omni sensitivity check | 0.54 M + 0.18 M | same | catalog limits unverified (X18) | $7.2 |
| **C1b** | 50 runs: P1 ≈ 53, grounding ≈ 250, zoom ≈ 550 | 1.38 M = P1 0.47 M + grounding 0.09 M + zoom 0.83 M | 31.5 M = 50 × (70 k + 240 k + 320 k) (cap 40.95 M) | One analysis at a time, since its own grounding fills 4 in flight: 50 × 60 s ≈ 50 min, worst ≈ 3.1 h | $13.8 (worst $315) |
| C1b + captions | + ≈ 181 caption calls (86 dev windows × 2 × 1.05) | + 1.27 M | + 19.5 M = 50 × 30 × 13,000 (the profile's 30 caption calls at the 13,000-token bound) | + ≈ 10–30 s per clip over 30 s | +$12.7 |
| **C3** (rough, 55 test clips, 2 repeats) | Track A 330 P1; Track B 220 runs; ≤ 6,048 judge calls; dashboard 450; chat ≈ 476 answers | 16.7 M = A 4.21 M + B 6.06 M + judge 3.18 M + dashboard 1.35 M + chat 1.90 M; 21.7 M with captions (708 calls) | A + B 173 M (+ 86 M with captions at 30 × 13,000 per run) | A ≈ 3.8 h paced, ≈ 1.2 h at the Omni floor; B ≈ 3.7 h; judge ≈ 2.5 h at 40 rpm | $167 ($217 with captions; worst $1,733) |

- **Caps** in the pilot configs are the owner-approved budgets (`budget_mode: hard_stop`): C1a 6 M inference tokens (2 M per subject, 195 attempts for 150 calls plus retries); C1b 9 M tokens over its ledgers (inference 2 M, grounding 2.5 M, zoom 4.5 M, half per subject); judge 1,100 calls and 1.65 M tokens for C1a's scoring, 900 and 1.35 M for C1b's (2,000 and 3 M together). They sit below the pessimistic worst case (every attempt at the 35,000-token ceiling with every retry taken: 15.75 M for C1a, 31.5 M for C1b), so preflight lists that gap as `hard_stop` risks; the ledger refuses, before sending, any attempt that would pass a cap, and the item stays open. Typical use is about 1.9 M (C1a) and 1.4 M (C1b) tokens. Recalibrate after the pilots (P5).
- **Rate limits:** 60 rpm and 4 in flight are never the binding limit at these volumes (C1a reaches ≈ 11 rpm at 4 in flight). Omni's token quota and the gateway's ≈ 100 s timeout are.
- **C3** matches the plan's §6 range of 17–20 M tokens. It needs the C2 freeze manifest; the test half refuses to run without one.

## 5. Human review (X6)

**Sample sizes** (the existing sampler: 20% stratified, which includes a random `ai_validated` sample, plus every flagged record). The real flagged share is unknown until a run, so rows show the dry run and scenarios.

| Dataset (strata) | Records | Flagged | Random sample | **To review** |
|---|---|---|---|---|
| Boxes (kind, tier, category): dry run | 298 | 295 | 3 | 298 |
| Boxes: perfect passes, items enter and leave | 298 | 194 | 24 | 218 |
| Boxes: 2% misses, 0.5% errors | 298 | 270 | 9 | **279** |
| Boxes: if 25% were flagged / if 10% | 298 | 75 / 26 | 47 / 55 | 122 / 81 |
| Chat (kind, tier, class): dry run | 189 | 2 | 39 | **41** |
| Chat: if 10% / 25% flagged | 189 | 25 / 57 | 39 / 32 | 64 / 89 |
| Dashboard (kind, tier, class): dry run | 75 | 4 | 15 | **19** |
| Dashboard: if 10% / 25% flagged | 75 | 11 / 21 | 14 / 12 | 25 / 33 |

Chat sampled with the default strata has one stratum, so 38 random records could miss a class. `--strata kind,tier,strata.class` gives 15 strata, one per class (§6, item 4).

**Reviewer hours.**
- *Minutes per item (assumed):* flagged box record 5 (a sheet of up to 12 frames, every disagreed second included); sampled box record 3; chat question 2.5 (incident 2, library and facts 3); dashboard request 1.
- *Double review:* a second reviewer double-reviews 20% of every sample for κ.

| Scenario | Boxes | Chat | Dashboard | Subtotal | With κ (+20%) |
|---|---|---|---|---|---|
| Tier rule as it stands, realistic noise (279 / 64 / 25) | 270 × 5 + 9 × 3 = 1,377 min | 160 min | 25 min | 26.0 h | **31.2 h** |
| If a quarter of box records were flagged (122 / 64 / 25) | 75 × 5 + 47 × 3 = 516 min | 160 min | 25 min | 11.7 h | **14.0 h** |
| Few flags (81 / 41 / 19) | 26 × 5 + 55 × 3 = 295 min | 102.5 min | 19 min | 6.9 h | **8.3 h** |

The plan assumed 12–18 h. The box tier rule decides whether that holds (P1).

**Packet dry run:** workbooks of the fake dry-run records were written privately to `eval/eval_data/v2-dry-run/review/`:
- `box-dev` (98 rows) and `box-test` (200);
- `chat` (41, strata by class);
- `dashboard` (19).

Each re-imports with 0 decisions. Box review sheets (`v2_box_frames.py sheet`) and the workbook's link column (`review export --links`) are tested on synthetic frames. No real frames were extracted.

### Reviewer instruction sheet
1. **Keep it private.** The workbook and sheets quote ground-truth text. Keep them on the review machine, never commit, upload or paste them, and delete local copies after import. Write your own name in `reviewer`, never a model or tool name; the import refuses those.
2. **Per row,** pick `decision`:
   - `accept`: the proposal is right as it stands.
   - `edit`: write the corrected JSON in `edited_value`, starting from the `proposal` column.
   - `reject`: wrong and not fixable here.

   Add a short `note` for every edit or reject. Leave a row blank to skip it. Never change `record_id`, or the hidden hash column.
3. **Box records** (open the sheet in `link`):
   - A green box is the reference at that second. A red frame is a second the two AI passes disagreed on; it has no reference box.
   - Accept when every green box holds the described item and most of the box is the item: for a person, the whole visible person; for an object, the object rather than the hand or group.
   - To drop wrong seconds, edit: delete those seconds from `boxes` and delete the `visible_spans` key (the scorer re-derives spans).
   - Never add or move a box. If most green boxes are wrong, or show another person or object, reject.
4. **Chat questions:**
   - Accept when the question is natural and unambiguous, does not give the answer away, the two paraphrases ask the same thing, and the expected answer (computed by code) is exactly what it asks.
   - For a facts question, also check that every fact is one checkable statement the report supports.
   - Edit only the wording or the facts; never edit `answer`, `support` or `answer_space`. Reject a question that cannot be made unambiguous.
   - An unanswerable or false-premise question is right when the report really cannot answer it, or really contradicts its premise.
5. **Dashboard requests:**
   - Accept when the request (and both paraphrases) asks for exactly the expected spec: metric, dimension, period (none named means all time), type and severity filters.
   - For an unsupported request, accept when no allowlisted spec can express it.
   - Edit the wording only; never edit `expected`.
6. **Agreement:** the second reviewer reviews the double-review rows independently, in their own copy. Then run `review kappa --a <first decisions> --b <second decisions>`.

## 6. Defects fixed in this branch (each with a regression test in `tests/v2/test_v2_run_readiness_regressions.py`)
1. **Box packets overwrote each other's drawn frames** (`references/boxes.py`). A packet frame's `image` path named the clip but not the item, so `draw` wrote every item of a clip to the same files, each with differently numbered marks. On the 80 clips, 45,709 drawn frames mapped to 10,026 paths, so 35,683 would have been overwritten. The path now names the item.
2. **Leakage matched substrings** (`references/chat.py`, `leakage`). It flagged 7 of 189 dry-run questions as stating their answer when none did: "car" inside "carried", "bat" inside "combat", and severity 1 or 5 inside "on a scale of 1 to 5". Matching is now on whole words, a plural `-s`/`-es` counts, and a 1–5 scale is ignored as the chat scorer ignores it.
3. **Agreement compared raw strings** (`references/chat.py`, `answers_agree`).
   - A blind validator that copies names as the report writes them disagreed with the normalised, deduplicated reference list. That hit 4 of 189 dry-run questions, and would hit 12 asset-list and 4 instrument-list incidents of the ground truth.
   - The same applied to `"3"` against 3, `"Road accident"` against `"road accident"`, a count labelled `value`, and window bounds written as text.
   - Answers are now compared in canonical form, and a list must be answered as a list.
4. **Review sampling could not stratify chat or dashboard records by class** (`cli.py`). The default strata hold one chat stratum. `review sample|export --strata` now names the fields.

**Also added:**
- the reference-run drivers (`references/ra1.py`, `ra2.py`, `rb2.py`);
- the file handoff for sessions (`references/passes.py`);
- the dry-run fakes (`references/dry_run.py`);
- `python -m evalv2 references`;
- box review sheets and `review export --links`;
- the caption profile `production-v4-captions` (then pending X16; since 2026-11-02 inline and gated on the base64 probe, see B3);
- the three pilot configs under `experiments/v2/`.

## 7. Blockers beyond approval
- **B1. Resolved (2026-11-02).** `run --live` (`evalv2/executor.py`) wires the work items, the seeded V1 schedule, pacing (`core/pacing.py`), the adapters, the artifact store, budget log, checkpoint and run manifest, behind the approval marker (`evalv2/approval.py`), with presigned R2 GETs (`evalv2/clips.py`). `score` (`evalv2/score_stage.py`) scores stored predictions with the registry's judge, local MiniLM and their caches, and re-scores offline. Tests: `tests/v2/test_v2_live_executor.py` and `test_v2_score_stage.py` (fake upstream, fake agent, fake clock).
- **B2. The experiment agent's code.** P3 (#422, run provenance) and P4 (#427, `max_zoom_calls`) are merged into `main` (`0d7d75930`). But `start-experiment-agent.sh` runs the `nat` at `NAT_BIN`, and the local one (`~/.cache/vss-agent-venv`) is an editable install of the main checkout's `services/agent`, which is still at `ae7dd8554`, before both. The Track B adapter refuses replies without provenance. Update that checkout to `origin/main` (the owner's checkout), or point `NAT_BIN` at a venv built from a checkout of `origin/main`.
- **B3. Resolved (2026-11-02).** The adapter pins the agent's settings without per-run outcomes (`PER_RUN_FLAGS`: `chunking.enabled`), so a clip of 30 s or less no longer stops the run; a changed chunking setting is still drift. Each run's artifact records `caption_pass.chunking_enabled`, the skip reason and the store. The caption profile sends `experiment_captions: "inline"` and `max_caption_calls: 30` (13,000 tokens per caption call on the `caption` ledger, agent PR #439) and is gated: it is sent only when the marker enables it, after the base64 probe.
- **B4. RB2's allowlist.** `contracts/prompts/chart/allowlist.json` (P2, #418) is on `main` and equal to the fixture the dry run used (checked). This branch gets it when it is refreshed from `main`; until then, pass `--allowlist tests/v2/fixtures/chart-allowlist.json`.
- **B5. The detector (X5)** must:
  - label each proposal with the query that produced it (the driver keeps an item's proposals by label);
  - keep coordinates unrounded. An identical static box on a generator and a validator frame trips `assert_blind`, and that item then gets no record (tested).
- **B6. RA1 session effort.** 45,709 drawn frames (7.9 M image tokens) at one image per read is about 38 h of session time. Parallel sessions, skipping frames with no marks, or contact sheets (P2) cut it.
- **B7. Review load.** As §3 and §5 show, the box tier rule flags most records even with perfect passes, and about 31 h of review would follow.
- **B8. Scoring.** The configs name `catalog-gpt-oss-20b-v1` (primary) and `catalog-mistral-large-4-v1` (sensitivity); `NVIDIA_API_KEY` is in the main checkout's `.env.local`. Both judges stay unverified until the judge probe (`python -m evalv2.probe`) has answered from them; the catalog's rate limit is assumed at 40 rpm. Local MiniLM is cached and loaded with `HF_HUB_OFFLINE=1`.

## 8. Proposals awaiting approval (not adopted)
- **P1. Box tier rule.** Two options; re-run the §3 scenario sweep to size either before deciding.
  - Treat a sighting that only one pass saw at a span's first or last second as single-pass (as the clip's last second already is), so only interior disagreement flags a record.
  - Or validate a record when at least 95% of jointly seen seconds agree. Disagreeing seconds stay excluded, as now.
- **P2. RA1 contact sheets:** several frames per drawn image, labelled with their time, to cut the reads by that factor. This is a protocol change to `draw` and the packets.
- **P3. Plan sizes are provisional targets:**
  - chat: 5 (dev) and 10 (test) per incident class, plus 20 library questions per half;
  - dashboard: 60 specs plus 15 unsupported;
  - review: 20% plus every flag.
- **P4.** Re-draw or keep the 5 dashboard specs whose reference chart is empty on the corpus.
- **P5.** Recalibrate every cap from the pilots' measured usage (`cap_summary`) before the C2 freeze.
- **P6. C1c ([§9](#9-dashboard-evaluation-c1c)):**
  - treat `dashboard-references-v2` as the dashboard development set, and make the held-out set a fresh RB2 at the C2 clock with another plan seed;
  - the pilot itself: 3 Switchyard text models × 75 requests × 1 repeat, capped at 675 attempts and 1.01 M tokens.

## 9. Dashboard evaluation (C1c)

> **Status (2026-11-02): approved by the owner (225 calls, 1.01 M tokens); run from this branch.**
>
> **After #459 (2026-11-03):** chart requests go through the agent's `/chart-requests`. The prompt, model (Ultra) and settings are unchanged, so the pilot stands ([assessment](v2-dashboard-change-assessment.md)). New runs set `tasks.dashboard.options.request_shape` to `agent-route-v2`, the agent's upstream body. The sealed pilot keeps `console-gateway-v1`.

**What it measures.** The AI dashboard ("Ask for a chart", methodology v2.0 §4 E). For each RB2 reference request, the `dashboard` task (`evalv2/tasks/dashboard/runner.py`) sends the console's chart request to a subject and stores the reply. Scoring then compares the reply with the reference's spec or refusal and draws both charts from the frozen corpus snapshot.

- **The request is the console's, byte for byte.** `evalv2/requests/chart.py` ports `GatewayChartInterpreter` and the route's rules: the system prompt is `contracts/prompts/chart/system.txt` (hash-checked against the prompt registry), the request is trimmed as zod trims it, 1 to 300 characters, and the settings are `temperature 0`, `max_tokens 200`, `stream false` and `enable_thinking false`, in the console's key order. Eval is now a producer of `contracts/golden/chart.json` `request`. `tests/test_parity_golden.py` checks the body as JSON and as the bytes httpx posts upstream: `vlm-gateway` re-posts the console's body with httpx, as the eval adapter does.
- **The reply is read the way the console reads it.** `parse_reply` takes the JSON from the first `{` to the last `}`. A reply is then a spec, a refusal, an invalid object, no JSON, or empty (`reply_kind`).
- **Two differences from the console, both recorded.** The adapter waits up to 300 s, where the console gives up after 45 s; a slower reply is counted in `over_console_timeout`. Each figure is drawn as a bar chart in UTC at the snapshot's clock, the same rule as RB2's `corpus_figures`.

**References and the split (proposal).** `dashboard-references-v2` is now registered (hashes only; the text stays in `$EVALV2_DATA_ROOT/v2/references/`). It holds 75 records: 60 specs over all 16 dimensions and 5 periods, and 15 unsupported requests. Every record is `ai_validated` and none is `human_verified`. It also holds the 80-report corpus snapshot.
- RB2 has no dev/test halves. **The proposal is to treat all of v2 as the development set**, without carving it up. A split of 75 records would leave about 15 test requests, too few for any interval. The v2 clock is also the dry-run clock (2026-11-01T12:00Z), and RB2 must be re-run at the C2 frozen clock anyway.
- The held-out set is then a fresh RB2 at the C2 clock (a new id, `dashboard-references-v3`), made with another plan seed so that its filters differ, and freshly worded. Nothing tuned on v2 can leak into it.
- `purpose: pilot` and `half: dev` are kept, so the executor's pilot rules still apply (the plan names record ids, never test clips).

**Subjects (proposed).** All three are on Switchyard (`VLM_GATEWAY_BASE_URL`, `VLM_GATEWAY_API_KEY`), which charges the project $0. They are not paid.
- `ultra-chart`: `nvidia/nemotron-3-ultra`, the console's chart model (`CHART_MODEL` default in `src/lib/env.ts`; the local console sets no override).
- `nano-chart`: `nvidia/nemotron-3-nano-30b-a3b`, for comparison.
- `super-chart`: `nvidia/nemotron-3-super-120b-a12b`, for comparison. Switchyard lists it (RB1), but it is unverified.
- The owner may approve only `ultra-chart` by setting `tasks.dashboard.subjects` to `["ultra-chart"]` before drafting the marker. That gives 75 calls, 225 attempts at worst and 337,500 tokens.

**Calls and tokens per record** (estimated offline; no tokenizer of these models is local):

| | Estimate | Basis |
|---|---|---|
| System prompt | ≈ 1,000 tokens | 3,883 characters: 971 at 4 characters a token, 986 MiniLM WordPiece tokens |
| Request | ≈ 14 tokens (at most 24) | RB2 requests: 71 characters on average, at most 113 |
| Reply | ≈ 30–50 tokens (at most 200) | The longest spec is 127 characters and 49 WordPiece tokens; `max_tokens` is 200 |
| **Per call** | **≈ 1,080 typical; 1,500 reserved** | `TOKENS_PER_ATTEMPT` is the bound every attempt reserves and preflight assumes |
| Calls per record | 1 per subject and repeat | Plus at most 2 retry-v2 retries, on a 429, a timeout, a transport error or a host suspend |

**Proposed pilot and caps** (`budget_mode: worst_case`: the caps cover the pessimistic worst case, so preflight passes and no cap can stop the run):

| | Per subject | Run (3 subjects) |
|---|---|---|
| Work items (calls without retries) | 75 | **225** |
| Attempts: typical / cap (worst case) | ≈ 83 / 225 | ≈ 250 / **675** |
| Tokens: typical / cap (worst case) | ≈ 0.09 M / 337,500 | ≈ 0.27 M / **1,012,500** |
| $ at the $10/M planning rate | ≈ $0.9 (worst $3.4) | ≈ $2.7 (worst $10.1); Switchyard bills $0 |
| Wall time at 60 rpm and 4 in flight | — | ≈ 4–5 min |

**Approved by the owner on 2026-11-02:** at most 225 calls and 1.01 M tokens. The config therefore caps 225 attempts and 1,010,000 tokens run-wide (100 attempts and 450,000 tokens per subject), in `hard_stop` mode. Retries draw from the same caps, and an item a cap refuses stays open and is reported. The scoring stage makes no model call and needs no judge budget.

**Launch** (from `eval/`, after the owner approves; the config hash changes if the subjects are cut):

```bash
export EVALV2_DATA_ROOT=/home/hengz/evalv2-data   # where v2/references/dashboard-references-v2/ lives
ENVF=<profile .env.local with VLM_GATEWAY_BASE_URL and VLM_GATEWAY_API_KEY>   # no R2 is needed
RUNS=$EVALV2_DATA_ROOT/v2/runs
uv run --frozen python -m evalv2 preflight --config experiments/v2/c1c-dashboard-pilot.json      # 225 items, ok
uv run --frozen python -m evalv2 approval draft --config experiments/v2/c1c-dashboard-pilot.json > $RUNS/c1c-approval.json
#   the owner fills approved_by and approved_at (and may lower any figure, never raise it)
EVALV2_ALLOW_LIVE=1 uv run --frozen python -m evalv2 run --config experiments/v2/c1c-dashboard-pilot.json --live \
    --approval $RUNS/c1c-approval.json --out $RUNS/c1c --env-file $ENVF            # the same command resumes
uv run --frozen --with pgserver==0.1.4 --with 'psycopg[binary]>=3.2,<4' python -m evalv2 score \
    --config experiments/v2/c1c-dashboard-pilot.json --out $RUNS/c1c --figures sql  # code only: no judge, no network
#   --figures recount: the Python recount (equal to the SQL by test), without pgserver; --figures none: specs only
```

**Scoring outputs** (`$RUNS/c1c/scoring/evalv2-dashboard-score-v1/primary/dashboard-summary.json` and `dashboard-items.jsonl`, private). Per subject:
- **Coverage.** `items`, `answered` and `request_failed`. A failed request (an HTTP error, or a non-completion after its retries) is counted but never scored.
- **Spec metrics, each with its numerator, denominator and interval:**
  - exact-spec accuracy, over supported requests answered;
  - refusal precision, over refusals;
  - refusal recall, over unsupported requests;
  - overall accuracy, over every answered request: the exact spec, or a correct refusal;
  - invalid-reply rate;
  - figure accuracy.
- **Errors, kept apart:**
  - an invalid reply (`parse_failure`, split by `no_json`, `empty` and `invalid_object`);
  - a wrong spec (`valid_but_wrong`);
  - `false_accept` and `false_refusal`.
- **Per-field accuracy** over valid specs: `metric`, `dimension`, `period`, `incidentType`, `severity`, and `filters` (both filters right), with its denominator.
- **Figures.** The production `get_incident_dashboard` runs in the SQL harness on the snapshot. An all-time query that counts nothing would read `CURRENT_DATE`, so only that query uses the frozen-clock recount. The summary counts the calls from each source.
- **Per tier.** Results are also given per reference tier (`by_tier`). Today everything is `ai_validated`.
- **Intervals.** A record-level bootstrap: requests are resampled, and a request's repeats stay together. It uses 10,000 resamples, seed 20261027 and 95% percentiles. The video-level cluster bootstrap does not apply here: a dashboard request belongs to no video, and the corpus is shared.

**Functional tests are not quality results.** `tests/v2/test_v2_dashboard_task.py` (fake models, synthetic references) shows that the plumbing works, not how good any model is:
- request parity with the golden;
- a correct spec, a wrong spec, a refusal and an invalid reply, each stored and scored apart;
- a failed request;
- a refusal at the cap, made before sending;
- a marker that does not fit;
- resume without re-sending;
- aggregation with denominators and a reproducible bootstrap;
- figures from the recount and from the SQL. The SQL test runs where pgserver is installed: `uv run --with pgserver ... pytest`.

**Oracle check on the real references** (offline, no model call). Each reference spec was given as its own reply and scored with SQL figures on the real snapshot:
- 60 `exact`, 15 `refusal_correct`, and every figure right;
- 137 SQL dashboard calls, and 11 all-time calls that counted nothing and so used the frozen-clock recount.

Quality numbers come only from the approved pilot, and only as development results.

**Before C1c results count as final:**
1. **Human review of RB2 (X6).** None of the 75 records is `human_verified`. A reviewer must accept, edit (wording only, never `expected`) or reject them, using the §5 sheet, item 5. Only `human_verified` records count as primary. `review apply` writes them under a new dataset id with the same record ids, so the run is re-scored against it, through a copy of the config that names it. Accepted and rejected records need no new call; a record whose wording was edited needs its items sent again, since the stored reply answers the old wording. The existing workbook (`v2/review/rb2-dashboard-review.xlsx`, 30 rows, a 40% stratified sample) covers a sample only. A primary result needs every scored record reviewed, or the reported tier restricted to the reviewed ones.
2. **Validator ambiguities to settle in review:**
   - "last month" and "past week" were read as rolling 30 and 7 days;
   - minor, moderate and severe were mapped to low, medium and high;
   - "property" was read as asset, and "official" as report status;
   - 4 breakdowns filter on their own dimension (for example severity filtered to one severity).
3. **The figure snapshot.** The corpus snapshot is the ground-truth reports, spread over 90 days before the dry-run clock. P4 (the 5 specs whose reference chart is empty) is still open. The held-out result needs the C2 clock and the fresh RB2 (above).

## 10. Chat evaluation (C1c-chat)

> **Status (2026-11-02): built and tested offline, not run.** No chat model, judge or network call was made. The config [`experiments/v2/c1c-chat-pilot.json`](../experiments/v2/c1c-chat-pilot.json) is a **proposal**, not approved.

**What it measures, reported separately per corpus (never pooled):**
- **retrieval** (library questions): P@K, R@K, MRR, nDCG at K = 1, 3, 5, 10 and V1's coverage (|L| / K) on the router's ranked matching reports, plus the matching set's precision and recall, against the reference's `support`;
- **answers:** the exact rate on code-derived references (counts, values, lists, windows), by class; refusal recall on the unanswerable and false-premise questions, and the false-refusal rate on the rest. Judge-needed checks (written facts, holders, unsupported claims, contradictions) stay **pending** (counted, never 0) until a judge budget is approved;
- **citations:** incident-level precision and recall against `support`, citation validity (the share of model citations the router kept), and answers that cite nothing.

Each block carries its denominator, per tier group (`all`, `ai_validated`, `ai_proposed`), with 95% intervals from a cluster bootstrap (seed 20261027, 2,000 resamples; cluster = the incident for incident questions, the record for library questions).

**Design (J2, J3).**
- **The harness** (`evalv2/tasks/chat/harness.py`) runs the agent's real chat router (`create_incident_chat_router`, grounded mode) in-process. The route function is called directly, with no server or socket.
- **Snapshot ports:** `SnapshotRevisionStore` and `SnapshotLibraryIndex` read a frozen corpus snapshot, never the finals database.
  - The **oracle** corpus is `ground-truth-v1.0`'s reports (`p1_shaped`) of the dev half.
  - The **system** corpus is the C1b captions run's `super-pipeline` final reports (crop images stripped). `Burglary010` has no report there, so its question is recorded `no_report`.
  - Incident ids are clip ids in both corpora; expected answers never use item ids, so no item mapping is needed.
- **Chat models:** a deterministic `FakeChatModel` for tests and dry runs (the network is blocked). The agent's own `OpenAIChatModel` is used only with `--live`.
  - Every call goes through `MeteredChatModel`, which reserves the call's pessimistic tokens on the `inference` ledger before sending it and settles the measured tokens after. A call that would pass a cap is never sent; the run stops with the item left open.
- **Two interpreters.** The agent's venv cannot read the ground-truth workbook loader, and the eval venv has no `vss_agents`. So `prepare`, `score` and `readiness` run with the eval Python, and `estimate` and `run` with the agent's.
  - Build the agent venv without pycairo: `cd services/agent && uv sync --frozen --group dev --no-install-package pycairo`.
- **The task** (`evalv2/tasks/chat/runner.py`, registered as `chat`) gives `python -m evalv2 plan|preflight` its items and worst case. The generic executor refuses a chat config.
- **Scoring** (`evaluate.py`) reuses the chat scorers.
  - A library answer names incidents by type and title, never by id. So an incident list is read from the incidents the answer cites, and a code-composed count's citations are the incidents its prose names.

**Pacing (fixed 2026-11-02, after the first live attempt):**
- **What went wrong:** the first live run sent calls back to back while another pilot shared the gateway key. Switchyard refused 136 of 140 with HTTP 429 (60 requests a minute, 4 in flight), so no inference ran for them.
- **The fix:** the metered model now spaces calls at least 1.2 s apart (50 a minute) and retries a 429 after 60 s, then 180 s. Each retry is a reserved attempt, so the attempt cap still binds, and a 429 is settled at 0 tokens.
- **Rule:** run the chat pilot alone on the gateway.
- **Agent-side tests:** run them with the agent's Python and `--noconftest`, because the eval conftest imports streamlit: `$A -m pytest tests/v2/test_v2_chat_harness_agent.py --noconftest`.

**Launch** (from `eval/`, `EVALV2_DATA_ROOT=/home/hengz/evalv2-data` or the main checkout's `eval/eval_data`; `$OUT` private):
```bash
C=experiments/v2/c1c-chat-pilot.json; A=../../../../../services/agent/.venv/bin/python
uv run --frozen python -m evalv2 preflight --config $C                    # 140 items; hard_stop gap listed
uv run --frozen python -m evalv2.tasks.chat prepare --config $C --out $OUT  # plan.json + corpora/ (frozen, hashed)
$A -m evalv2.tasks.chat estimate --config $C --out $OUT                  # offline token estimate, no call
$A -m evalv2.tasks.chat run --config $C --out $OUT                       # fake model, network blocked (plumbing)
# Live, only with the owner's marker (approval draft --config $C prints a skeleton); the same command resumes:
EVALV2_ALLOW_LIVE=1 LLM_BASE_URL=<Switchyard> INCIDENT_CHAT_API_KEY=<key> \
  $A -m evalv2.tasks.chat run --config $C --out $OUT --live --approval <marker>   # --retry-errors re-asks http_error
uv run --frozen python -m evalv2.tasks.chat score --config $C --out $OUT    # $OUT/score/report.json, offline
uv run --frozen python -m evalv2.tasks.chat readiness --config $C           # reference readiness (below)
```
A fake run's numbers are not results. They only show that the plumbing works.

**Expected calls, tokens and caps.**
- **Calls:** an incident question costs 1 call. A library question costs 2: the plan, then an answer, which a count or breakdown question skips because code composes it.
- **Pilot:** 140 items (100 incident, 40 library), so at most 180 calls.
- **Offline estimate** (`estimate`: the registered chat prompts and the real corpus contexts, characters / 3 plus the client's 1,024 output tokens per call):

| Call | Calls | Max bound | Sum of bounds |
|---|---|---|---|
| Incident answer | 99 | 4,769 | 260,842 |
| Library plan | 40 | 1,494 | 59,166 |
| Library answer | 40 | 5,600 | 207,840 |
| **Total** | **179** | | **527,848** |

- **Configured per-call bounds** (5,000 / 2,000 / 6,000) give a worst case of 820,000 tokens.
- **Proposed caps:** 180 attempts and 600,000 tokens on `inference` (per subject and run-wide), `hard_stop`; judge 0.
- **Endpoint:** Nemotron 3 Ultra (`nvidia/nemotron-3-ultra`, the grounded chat default) on the team's Switchyard gateway (`INCIDENT_CHAT_BASE_URL`, else `LLM_BASE_URL`), which the C1 pilots recorded as no charge to the project.

**References and human review.**
- RA2's references are **AI-generated**: 163 `ai_validated` and 26 `ai_proposed`, **0 `human_verified`**.
- Every question is derived from the ground truth it is scored against. This leaks wording and is a **disclosed limitation**: the leakage overlap is in each record, and no question states its answer.
- **Readiness report** (offline, private: `$EVALV2_DATA_ROOT/v2/references/chat-references-v1/readiness/readiness.json`): **all 189 records are consistent** with the frozen ground truth on code recount (answer, support and answer space).
  - Two classes cannot be recounted: the 15 `incident.facts` (written facts) and the 10 `library.retrieval` (only the target is labelled relevant).
- **Before any result counts as more than a pilot,** people must review 60 records: all 26 `ai_proposed`, plus a 20% class-stratified sample of 34 `ai_validated` (seed 20261101, strata `kind,tier,strata.class`).
  - 22 of the 60 are dev-half (pilot) records: 5 facts, 5 retrieval, 12 others.
  - By metric: answers.exact 36, retrieval 18, fact recall 15, refusal 6, holder 3; citations 54.
  - The existing workbook `v2/review/ra2-chat-review.xlsx` (59 rows) was sampled with strata `kind,tier,strata.category`. Chat records have no category, so its 33-record random sample is not stratified by class. It overlaps this list on 55 records. Re-export it with `--strata kind,tier,strata.class`, or review the union (64).

**Known limits.**
- **One repeat:** run-to-run variance is not measured.
- **Code-composed counts:** the planner decides whether code can count a question, so a count refused as uncountable shows as a refusal, not a wrong number.
- **Unreviewed retrieval relevance:** for `library.retrieval`, another incident may also fit the question; only the target is labelled relevant until review.
- **System corpus:** it is one subject's run (`super-pipeline`, captions) and is sealed; another analysis run is a new corpus.

## Appendix: plan and preflight output
Run offline, with `EVALV2_DATA_ROOT` set to the main checkout's `eval/eval_data` (the frozen ground truth verified by hash). Every command exited 0. `hard_stop` lists, per cap, the gap between the approved cap and the pessimistic worst case (shortened here to its count).

```text
$ uv run --frozen python -m evalv2 plan --config experiments/v2/c1a-track-a-pilot.json
{"experiment": "v2-c1a-track-a-pilot", "tasks": {"incident_objects": {"items": 150, "by_subject": {"nano-direct": 50, "super-direct": 50, "omni-direct": 50}}}}
$ uv run --frozen python -m evalv2 preflight --config experiments/v2/c1a-track-a-pilot.json
{"experiment": "v2-c1a-track-a-pilot", "half": "dev", ..., "ok": true, "problems": [], "budget_mode": "hard_stop", "hard_stop": [8 entries]}
$ uv run --frozen python -m evalv2 plan --config experiments/v2/c1b-track-b-pilot.json
{"experiment": "v2-c1b-track-b-pilot", "tasks": {"pipeline": {"items": 50, "by_subject": {"nano-pipeline": 25, "super-pipeline": 25}}}}
$ uv run --frozen python -m evalv2 preflight --config experiments/v2/c1b-track-b-pilot.json
{"experiment": "v2-c1b-track-b-pilot", "half": "dev", ..., "ok": true, "problems": [], "budget_mode": "hard_stop", "hard_stop": [9 entries]}
$ uv run --frozen python -m evalv2 preflight --config experiments/v2/c1b-track-b-pilot-captions.json
{"experiment": "v2-c1b-track-b-pilot-captions", "half": "dev", ..., "ok": true, "problems": [], "budget_mode": "hard_stop", "hard_stop": [12 entries]}
$ EVALV2_DATA_ROOT=/home/hengz/evalv2-data uv run --frozen python -m evalv2 preflight --config experiments/v2/c1c-dashboard-pilot.json
{"experiment": "v2-c1c-dashboard-pilot", "half": "dev", "tasks": {"dashboard": {"items": 225, "by_subject": {"ultra-chart": 75, "nano-chart": 75, "super-chart": 75}}}, "retry_policy": "retry-v2", "ok": true, "problems": []}
```

| Config | Cap per subject (attempts / tokens) | Run cap | Pessimistic worst case per subject |
|---|---|---|---|
| C1a, ledger `inference` | 65 / 2,000,000 | 195 / 6,000,000 | 150 / 5,250,000 |
| C1b: `inference`, `grounding`, `zoom` | 65 / 1 M; 1,300 / 1.25 M; 2,600 / 2.25 M | ×2 (9 M tokens in all) | 50 / 1.75 M; 1,000 / 6 M; 2,000 / 8 M |
| C1b + captions: `inference`, `grounding`, `zoom`, `caption` | 65 / 0.75 M; 1,300 / 1 M; 2,600 / 1.75 M; 750 / 1 M | ×2 (9 M tokens in all) | as C1b; caption 750 / 9.75 M |
| Judge (scoring stage) | — | C1a 1,100 / 1.65 M; C1b 900 / 1.35 M | C1a ≤ 1,026 calls (§4) |

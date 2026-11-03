---
name: incident-run-eval
description: Use to run the standalone P1/RP1 evaluation pipeline in eval/ - start the local embedding server, ingest ground truth, generate the seeded split, batch-score VLM incident extraction against ground truth, aggregate across models and categories, or run its hermetic tests. Not for producing a single report (use incident-analyze-video) and not NVIDIA's `nat eval`.
license: Apache-2.0
metadata:
  version: "0.20.0"
  profile: "dev-profile-incident"
  tags: "incident evaluation benchmark ground-truth vlm"
---
# Incident Run Eval

`eval/` is its own `uv` project. It shares no runtime with other services. Its scoring code began as a verbatim copy of the v1 console's modules (v1 is archived under `archived/incident-console/`).

Canonical facts:
- Setup and variables: [`eval/README.md`](../../eval/README.md)
- Methodology and recorded 3-model results: [`eval/docs/vlm_benchmark_results.md`](../../eval/docs/vlm_benchmark_results.md)
- Handoff and open items (including the dropped `gt_*` tables): [`.docs/mvp2/eval.md`](../../.docs/mvp2/eval.md)
- Retrieval benchmark (Track R): only the offline metrics module `eval/retrieval_metrics.py` exists (work-order step 1, tested by `tests/test_retrieval_metrics.py`). There is no retrieval run command yet; the harness is step 3 of [`.docs/mvp2/eval.md` §10](../../.docs/mvp2/eval.md#10-work-order-and-definition-of-done).

## Prerequisites

1. `cd deploy/docker/developer-profiles/dev-profile-incident/eval && uv sync`
2. `eval/.env.local` is a symlink to `../.env.local`. **Scoring needs no database:** by default `eval_run.py` loads the workbook into an in-memory store (`eval/offline_store.py`) and never connects to Supabase, even though the file holds finals credentials. Finals dropped the `gt_*` and relational report tables (`20261023000000`). Never pass `--db` against finals; `--db --init-schema` creates tables and is for a local or throwaway database only. Beyond the R2 variables, it needs `VLM_GATEWAY_API_KEY` (or `INCIDENT_LLM_API_KEY`), `INCIDENT_LLM_BASE_URL` (the real Switchyard gateway, not the mock), `INCIDENT_JUDGE_MODEL` and `INCIDENT_EMBEDDING_BASE_URL`. These are **not** in the committed `.env`, so check they exist before any live run.
3. The ground-truth workbook and all working data stay under `eval/eval_data/`, which `eval/.gitignore` excludes. Never commit it.

## What a run sends

P1 uses the shared [incident contract](../../contracts/README.md) (`incident-contract-v4`):
- a signed R2 URL, then the contract prompt, with the schema enforced via `response_format`;
- no few-shot examples;
- a fixed `max_tokens 16384` and `num_frames 64` (the same request for every model, but `cosmos-3-super-reasoner` samples about 24 frames whatever is requested; never call the visual input identical);
- a 300 s timeout per call, the console's `contracts/inference.json` value.

Responses are judged under validation policy `core-scored-v1`. A core failure is scored as a miss and listed under `core_failures`, and its RP1 is skipped. When core fields are valid (`core_ok`), RP1 runs on the unchanged prediction with timeline context. **Never loosen the parser to raise a score.** A model with many contract or core failures is a finding, not a bug to paper over. Details: [`eval/README.md`](../../eval/README.md).

## Instructions

```bash
# 0. Dataset videos: inventory, download and verify (resumable; R2 read-only), split keys current
uv run python scripts/eval_fetch_videos.py --list
uv run python scripts/eval_fetch_videos.py               # exits non-zero on any integrity finding
uv run python scripts/eval_refresh_split_keys.py --check  # non-zero when split manifests name moved R2 keys
cp eval_reproducibility/split_*.json eval_data/

# 1. Local embedding server (separate terminal; prints the INCIDENT_EMBEDDING_BASE_URL to set)
uv run python scripts/eval_embedding_server.py

# 2. One-off: validate the ground-truth workbook (no database), then generate the seeded split
uv run python scripts/eval_ingest_gt.py --dry-run
uv run python scripts/eval_generate_split.py

# 3. Smoke test first (1 model, 1 category, 1 video), then the full run
uv run python scripts/eval_run.py --models nvidia/cosmos-3-nano-reasoner --categories Assault --limit 1
uv run python scripts/eval_run.py

# 4. Aggregate across models/categories
uv run python scripts/eval_aggregate.py

# Ground-truth review (proposed corrections, not ground truth): workbook + audit + manifest
uv run python scripts/gt_review_build.py                 # source: ../Complete Ground Truth Labelling.xlsx (never modified)
uv run python scripts/gt_review_backup.py snapshot --reason "..."   # before/after every review session

# Ground-truth correction (PR 4): packets, frames, AI proposals, candidate workbook
uv run python scripts/gt_review_packet.py
uv run --no-project --python 3.12 --script scripts/gt_frames.py eval_data/gt_review_packets/index.json
uv run python scripts/gt_candidate_build.py --version v0.2   # reads ground_truth/review/ai-proposals-v0.2.jsonl
uv run python scripts/gt_review_backup.py snapshot --kind Candidate --version v0.2 --reason "..."

# Ground-truth freeze (PR 5): a candidate until an approval record names it
uv run python scripts/gt_freeze.py --candidate "ground_truth/review/Ground Truth Candidate v0.2.xlsx" [--approval <record>]
```

Prompt experiments (PR 6A): `uv run python scripts/eval_experiment.py plan|run|rescore|compare` ([`eval/experiments/README.md`](../../eval/experiments/README.md)). `plan` and `compare` are free. `run` and `rescore` make paid calls and refuse without `--confirm-paid`; never pass it before the user approves that stage's budget. Never run a `test` config except the final, pre-registered one. Experiment prompts are added only through `prompt_registry.add`, never by editing a registered file. Free and offline: `report`, `verify`, `budget`, `measure` and `scripts/eval_experiment_plumbing.py` (Stage 0 step 1). Several configs given to `run` form one interleaved stage under hard caps, with a per-call `cost-log.jsonl`. The VSS arm needs the local experiment agent (`experiments/agent/start-experiment-agent.sh`), never the VM agent. A `purpose: pilot` config never feeds selection or reported accuracy, and may use `scoring: incident_only`. Caps count every HTTP attempt (retries included). Never start a paid stage until its pricing or quota is confirmed and its budget approved. Judge-request caps are derived, never guessed: `eval_experiment.py caps <configs> --write` (re-run after any ground-truth change; a run refuses a cap below the derivation). Gateway timeouts (HTTP 524, about 100 s through Cloudflare) are `timeout` outcomes, counted in reliability and end-to-end accuracy, never dropped. An upstream 429 that the agent wraps in a 500 is `rate_limited` (counts toward the 25% stop rule). Keep the host awake during a run: a WSL or laptop suspend kills in-flight calls; run paid stages under `experiments/keep-awake.sh --` with the lid open on AC (a suspended failed attempt is `host_suspended`). Approved: `pipeline.retry_policy: retry-v2` and `eval_experiment.py recover` (dry run by default; infrastructure failures only, including DNS/connection failures (`transport_error`) and R2 403s on expired signed URLs (`expired_url`); at most 15 runs per config via `--max-runs`; originals untouched; each recovered run signs a fresh URL and fills its original slot in `merged-original+recovery-v1.json`). Methodology v1.2 is in force (tau = 0.40, AI-calibrated, not human-validated); `eval_experiment.py rematch` re-matches saved results offline; calibration via `scripts/eval_matching_calibration.py build|extend|evaluate|evaluate-v2` (dev only, labels blind to similarity). A stage writes `checkpoint.json` after every video-run; continue an interrupted stage with `run <same configs> --confirm-paid --resume <stage dir>`. Paused stages (`--session-hours`, 4 consecutive no-output runs, or session rate-limit share > 25%) exit 4 with the checkpoint kept; resume them. On WSL a paid run refuses without `keep-awake.sh` (`--allow-sleep` overrides). Stage 2 configs: `experiments/configs/s2-*.json`. Stage 3 (C1-C4, 55 test clips, 1 repeat, run once; [plan](../../eval/docs/experiments/stage3-plan.md)): configs `experiments/configs/s3-*.json`, pinned by `experiments/stage3/manifest.json`; `scripts/eval_stage3.py supervise` (paid, owner-approved 2026-10-29) is the single resumable launch command: preflight before every session, 4-hour sessions resumed from the checkpoint, then recovery-v1, both report views and the integrity audit; rerun it after any stop, and `eval_stage3.py progress` prints `<stage>/progress.json`. `scripts/eval_stage3.py manifest [--write]|preflight|report|progress` are offline (preflight checks roles, shared settings, test guards, manifest drift, ground-truth digest, clips, caps, run-once and C1=C2 / C3=C4 request parity via the agent venv). Stage 3 ran once (2026-10-29/30; [results](../../eval/docs/experiments/stage3-results.md)): never rerun it or start a second Stage 3 stage; the run-once guard refuses. Consolidated report: [`final-report.md`](../../eval/docs/experiments/final-report.md); plan audit and deviations: [`plan-audit.md`](../../eval/docs/experiments/plan-audit.md). Offline, reporting-only checks: `scripts/eval_secondary_metrics.py --title T --out <new dir> NAME=<results.json>...` (macro accuracy, per-video mean F1, instrument threat and holder, stricter-threshold lower bounds; never changes D9 or a primary metric) and `~/.cache/vss-agent-venv/bin/python -I scripts/eval_near_duplicates.py --out <new file>` (pHash near-duplicate check over the split-v2 clips; read-only; needs the agent venv's OpenCV). End-to-end VSS profile ([plan](../../eval/docs/experiments/e2e-profile-plan.md)): configs `experiments/configs/e2e-*.json` (`purpose: profile`, cropping on, `scoring: none`, a per-request crop-call cap sent as the agent's `max_crop_calls`, and `budget.max_crop_calls` / `max_crop_tokens`), pinned by `experiments/e2e-profile/manifest.json`; offline `scripts/eval_e2e_profile.py manifest [--write]|preflight|report` (preflight re-hashes every Stage 3 result against its audit); the paid run is `eval_experiment.py run experiments/configs/e2e-c2-vss-base.json experiments/configs/e2e-c3-vss-v1.json --confirm-paid` under `keep-awake.sh`, and it ran once on 2026-10-30 ([results](../../eval/docs/experiments/e2e-profile-results.md)): never rerun it (run-once guard). Severity reporting (methodology v1.3, reporting only, offline): `scripts/eval_severity_report.py --baseline NAME --title T --out <new dir> NAME=<results.json>...` gives MAE (primary severity metric), signed error, exact, within-±1, confusion, coverage and paired MAE differences with video-cluster intervals. It never changes a score or D9. `ground-truth-v1.0` is frozen on dataset-level approval of AI-assisted labels (not individually human-verified); say so in every reported result. Scoring follows evaluation-methodology-v1.1: the judge is limited to 1,500 output tokens, and a truncated or invalid judge reply stops the run as a scoring failure. AI-assisted adjudication (owner-authorised): `gt-adjudication-v1.0`, close-ups with `scripts/gt_frame_detail.py`, applied only to a separate `AI-Adjudicated Candidate` via `gt_candidate_build.py --adjudication` (never as human approvals). Ground-truth review packages: `scripts/gt_review_package.py` (new version every time; never approves anything). Readiness and the approval gates: [`eval/docs/experiments/readiness.md`](../../eval/docs/experiments/readiness.md).

Frozen ground truth loads only through `eval_gt_schema.load_frozen_gt_index(<dir>)`, which verifies checksums and refuses a candidate. Never write the approval record yourself: it is the user's approval of the final annotations and exclusions ([`eval/ground_truth/freeze/README.md`](../../eval/ground_truth/freeze/README.md)).

A rebuild never replaces an existing review workbook silently. It needs `--overwrite`, and it needs `--discard-review-decisions` as well if anyone has reviewed entries; either way it backs the old file up first. Prefer a new `--version`.

The review workbook (`ground_truth/review/Ground Truth Review v0.1.xlsx`) is gitignored on purpose; its storage and the review workflow are in [`eval/ground_truth/review/README.md`](../../eval/ground_truth/review/README.md). Never edit the source workbook, and never treat a proposed value as final before PR 5 freezes it. The candidate applies only `Auto-verified` (independently re-checked) and `Approved` entries; AI proposals follow `ground_truth/correction_prompts/gt-correction-v1.0.md`, stay `Pending` until a person approves them, and must never read results, predictions or experiment prompts.

**Always smoke-test before a full run.** A full run spends hosted-inference quota across 3 models × 5 categories.

### VLM benchmark (incident fields + processing time)

```bash
cp eval_reproducibility/split_*.json eval_data/       # if the manifests are missing
uv run python scripts/eval_dataset_stats.py           # first: all 55 videos resolve? mean length and size (no model calls)
uv run python scripts/eval_run.py --incident-only --categories Assault --limit 1   # smoke: 3 P1 calls + 3 judge calls
uv run python scripts/eval_run.py --incident-only     # full: 165 (video, model) pairs, ~4 h; judge: 1 probe + 1 per scored row
uv run python scripts/eval_benchmark_export.py        # tables -> eval_data/results/benchmark/<run start>/
```

- `--incident-only` needs no embedding server and skips RP1.
- Processing time is the successful P1 HTTP round trip (`timing.p1_elapsed_s`), never waiting.
- Fixed rate-limit policy (`P1_RATE_LIMIT_POLICY`): 20 s between P1 attempts; HTTP 429 only is retried after 60 s (or a longer `Retry-After`), at most twice. Never tune it per model or per run.
- Field metrics cover model outputs only. `rate_limited`, `timeout` and `http_error` rows are reported separately; timeouts are never model misses.
- An empty predicted description scores 0.0 with no judge call.
- Models run interleaved per video, in rotating order.
- Every attempted `(video, model)` pair stays a row, failures included.
- There is no blended score. `duration` is a derived diagnostic.
- Details: [`eval/README.md`](../../eval/README.md#vlm-benchmark-incident-fields-processing-time).

### Evaluation V2 (`evalv2/`, methodology v2.0)

The V2 package sits beside the V1 modules and changes none of them. A live run (`run --live`) or a live `score` needs `EVALV2_ALLOW_LIVE=1` **and** the owner's approval marker (`--approval`, `evalv2-live-approval-v1`); without both it refuses and sends nothing. Never write `approved_by` yourself: only the owner's approval fills it. Launch commands, marker format and budgets: [`eval/methodology/v2-run-readiness.md`](../../eval/methodology/v2-run-readiness.md#2-launch-commands).

```bash
uv run python -m evalv2 plan --config <config.json>             # work items per task and subject; no calls
uv run python -m evalv2 preflight --config <config.json>        # config rules + each cap covers the summed worst case of every task and subject it scopes; every charged task, subject and ledger has a cap
uv run python -m evalv2 run --config <config.json> --dry-run    # plan + preflight as one statement
uv run python -m evalv2 approval draft --config <config.json>   # marker skeleton for the owner (approves nothing)
EVALV2_ALLOW_LIVE=1 uv run python -m evalv2 run --config <config.json> --live --approval <marker.json> --out <eval_data/v2/runs/X> --env-file <eval/.env.local>   # live, approval-gated; same command resumes
EVALV2_ALLOW_LIVE=1 uv run python -m evalv2 score --config <config.json> --out <run dir> --approval <marker.json> --env-file <eval/.env.local>   # judge calls, approval-gated
uv run python -m evalv2 score --config <config.json> --out <run dir> --offline   # re-score from the judge and embedding caches; no network
uv run --with pgserver==0.1.4 --with 'psycopg[binary]>=3.2,<4' python -m evalv2 score --config experiments/v2/c1c-dashboard-pilot.json --out <run dir> --figures sql   # C1c dashboard: code-only scoring, no judge (--figures recount needs no pgserver)
uv run python -m evalv2 audit frozen-v1                         # V1's frozen artifacts unchanged (90 files)
uv run python -m evalv2 audit run --store <dir> --run <run id>  # a sealed run's index and artifacts intact
uv run python -m evalv2 datasets list                           # registered datasets (ids, kinds, how verified)
uv run python -m evalv2 datasets verify --data-root <eval_data> # pinned hashes hold; absent private data is reported
uv run python -m evalv2 review export --records <r.jsonl> --out <eval_data/...xlsx> --seed <n>   # 20% stratified + flagged
uv run python -m evalv2 review import --workbook <w.xlsx> --out <decisions.jsonl>                # people's decisions
uv run python -m evalv2 review apply --records <r.jsonl> --decisions <d.jsonl> --out <r2.jsonl>  # -> human_verified
uv run python -m evalv2 review kappa --a <a.jsonl> --b <b.jsonl> [--categories 1,2,3,4,5]        # Cohen or weighted κ
uv run python -m evalv2 verify-v1 --record <results.json> [--record ...] [--report <stage3-report.json>] --data-root <eval_data>
```

- **Box references** (R2) are made by `evalv2.references` with the prompts `gen-box-select-v1` and `gen-box-validate-v1`. `scripts/v2_box_frames.py` runs in a throwaway environment (`uv run --no-project --script ... extract|draw`). Making references, the detector's weights and the frame extraction belong to the approved run **RA1**.
- **Chat references** (R3) are made by `evalv2.references.chat`. Every countable answer is computed from the ground truth, and the prompts are `gen-chat-wording-v1` and `gen-chat-rederive-v1`. Generating them is the approved run **RA2**.
- **Chat evaluation** (task `chat`, pilot **C1c-chat**): the agent's real chat router (grounded mode) runs in-process over frozen corpus snapshots (oracle = ground truth, system = a sealed Track B run), never the finals DB. `prepare`, `score` and `readiness` run with the eval Python; `estimate` and `run` with the agent's (`services/agent/.venv`, built with `uv sync --frozen --group dev --no-install-package pycairo`). Without `--live` the chat model is a fake and the network is blocked; `--live` needs `EVALV2_ALLOW_LIVE=1`, the owner's marker, and the agent client's `INCIDENT_CHAT_BASE_URL` (or `LLM_BASE_URL`) and `INCIDENT_CHAT_API_KEY`. The generic `run` refuses a chat config. Commands, budgets and the review list: [`v2-run-readiness.md` §10](../../eval/methodology/v2-run-readiness.md#10-chat-evaluation-c1c-chat).
  ```bash
  uv run python -m evalv2.tasks.chat prepare --config <c1c.json> --out <run dir>           # plan.json + corpora/
  <agent python> -m evalv2.tasks.chat estimate|run --config <c1c.json> --out <run dir>     # run: fake model unless --live --approval
  uv run python -m evalv2.tasks.chat score --config <c1c.json> --out <run dir>             # retrieval, answers, citations apart
  uv run python -m evalv2.tasks.chat readiness --config <c1c.json>                         # RA2 recount + review list (private)
  ```
- **Track B** (task `pipeline`) runs subjects with `adapter: vss`, endpoint `experiment-agent` and a `profile` (`production-v4`, `production-v4-object-tracking`), with `request` overriding only caps and token bounds.
  - It runs only against the local experiment agent (`experiments/agent/start-experiment-agent.sh`, `EVALV2_VSS_AGENT_URL` on loopback). Never the shared VM agent.
  - Set caps for the `inference`, `grounding` and `zoom` ledgers, and `object_tracking` for the tracking profile. For the tracking profile the experiment agent's `INCIDENT_OBJECT_TRACKING_MAX_DETECTOR_CALLS` must be at most 80 (its default): a reply reporting a higher cap stops the run.
  - The caption pass runs only with the gated `production-v4-captions` profile (inline, 30 caption calls), and only when the run's marker lists it in `enabled_profiles` (after the base64 probe).
  - Crop evidence is scored through `tasks/pipeline/crops.py`, which needs box references (run **RA1**). Its `cap_summary` shows whether the profile caps bound; recalibrate them before the freeze.
  - Running it is the approved pilot **C1b**.
- **Dashboard figure checks:**
  - The recount against the production SQL: `uv run --frozen --with pgserver --with 'psycopg[binary]' pytest tests/v2/test_v2_dashboard_figures.py -k sql`. This runs a throwaway local Postgres; nothing is shared.
  - The console's own chart code against the recount: `EVALV2_NODE=<node 22.18+ with type stripping> uv run --frozen pytest tests/v2/test_v2_dashboard_figures.py -k console`. It needs the console's `npm ci`.
  - Both tests are skipped in the plain suite. The local `node` 22.22.1 has no TypeScript support; a cached `npx -y node@22` binary does.
- **SQL figures:** `uv run --no-project --script scripts/v2_sql_harness.py dashboard|summaries --snapshot S.json --params P.json` runs the production SQL on a frozen snapshot in a throwaway Postgres. Every parameter must be explicit, and dashboard period bounds are required.
- **`verify-v1`** re-scores stored V1 records with V2's incident-and-object scoring, with no model, embedder or judge call, and exits 1 on any difference. Running it on the real Stage 3 records is the separately approved check **RA0**; do not run it without that approval.

- **Private datasets** (ground truth, references) are read from `--data-root`, else `EVALV2_DATA_ROOT`, else `eval/eval_data`. In a worktree, point `--data-root` at the main checkout's `eval/eval_data`; it is read-only there.
  - Workbooks, decisions and records quote ground-truth text. They are written only to gitignored paths or outside the repository; any other path is refused.
- **Tiers.** Each new reference record is `ai_proposed`, `ai_validated` or `human_verified`.
  - Only `review apply` with a person's decision makes a record `human_verified`.
  - Primary metrics use `human_verified` records only.

- Configs are JSON (`evalv2-config-v1`). A run on the test half must name the approved freeze manifest.
- **Endpoints and their environment variables.** Names only; the values live in `.env.local` and are never printed.
  - `switchyard`: `VLM_GATEWAY_BASE_URL` (with `/v1`) and `VLM_GATEWAY_API_KEY`, as in V1.
  - `nvidia-api-catalog`: `NVIDIA_API_BASE_URL` (default `https://integrate.api.nvidia.com/v1`) and `NVIDIA_API_KEY`.
  - `local-openai`: `EVALV2_LOCAL_BASE_URL` and `EVALV2_LOCAL_API_KEY`.
- **Model capabilities** are in `evalv2/adapters/capabilities.json`, all `verified: false` until a model call confirms them. A subject that lacks a capability a task requires is reported as unsupported, never scored 0.
  - RB1's listings are in `evalv2/adapters/catalog_rb1.json`.
  - Use the endpoint's own id: on `nvidia-api-catalog`, Nemotron 3 Ultra is `nvidia/nemotron-3-ultra-550b-a55b` and Nemotron 3 Nano is `nvidia/nemotron-nano-3-30b-a3b`. Cosmos 3 is Switchyard only.
- Caps are per ledger: inference, caption, grounding, zoom, object_tracking, embedding, judge, generation.

## Verify

- `uv run pytest tests/` passes. The tests are hermetic: no live Postgres, agent or GPU.
- `eval_fetch_videos.py --verify-only` prints `OK`, and `eval_reproducibility/dataset-manifest-v1.json` has `"integrity": {"ok": true, ...}` with 80 clips.
- Result files say `"scoring_backend": "offline"` in their `benchmark` block unless `--db` was passed.
- `eval_ingest_gt.py --dry-run` prints no `WARNING: ... has incident type` line. If it does, a workbook `Type` is not one of the five standard types. Scoring still uses the row, normalised to lowercase; fix the workbook.
- The smoke run writes `eval_data/results/<category>__<model>.json` with non-null field scores.
- A benchmark smoke run also has `timing.p1_elapsed_s > 0` and a `video` block (size, length) on every video.

## Scoring (what the numbers mean)

The rules are fixed by [`evaluation-methodology-v1.0`](../../eval/methodology/evaluation-methodology-v1.0.md), and every pin is recorded in each result file.

| Field | Method |
|---|---|
| type, severity | Exact match (severity also within ±1, bias and confusion matrix) |
| description | Pinned judge (`nvidia/nemotron-3-nano-30b-a3b`, `judge-prompt-v1`, temperature 0); pass ≥ 0.5 |
| start, end, duration | ±5 s, plus mean absolute error |
| entities, instruments, assets | MiniLM + Hungarian matching at cosine ≥ 0.75 → precision / recall / F1; threat and holder accuracy |

- `INCIDENT_JUDGE_MODEL` must be unset or equal to the pin, or the run refuses to start.
- A judge or embedder failure is a scoring error that stops the run; it never counts as a wrong answer.
- Request failures (timeout, 429, HTTP error) are excluded from accuracy and counted.
- `eval_aggregate.py` refuses to pool results from different methodology versions.

## After changing anything

- If methodology or results change, update `eval/docs/vlm_benchmark_results.md`.
- `eval/` is the only maintained copy of the scoring code. Never update `archived/incident-console/` to match it.

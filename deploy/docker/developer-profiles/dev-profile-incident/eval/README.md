# P1/RP1 evaluation pipeline

A standalone Python project that benchmarks structured incident extraction (P1) by three VLMs against human ground truth, and writes an unscored prose report (RP1) per prediction. It shares no runtime with the other services. The recorded benchmark in [`docs/vlm_benchmark_results.md`](docs/vlm_benchmark_results.md) is the incident-contract-v2 run of 2026-10-02 (incident fields and processing time, 55 videos × 3 models).

> **Scoring is offline by default.** `eval_run.py` loads the ground-truth workbook into an in-memory store (`offline_store.py`), writes each prediction there and scores it with the unchanged scorer. It opens no database, so nothing reaches finals, which no longer has the eval tables ([eval open item 1](../.docs/mvp2/eval.md)). `--db` uses the legacy database path instead, and only `--db --init-schema` creates tables: use those for a local or throwaway database, never finals. `tests/test_offline_store.py` shows both paths give identical scores.

## Run it

```bash
cd deploy/docker/developer-profiles/dev-profile-incident/eval
uv sync

uv run python scripts/eval_embedding_server.py   # 1. local embedding server (own terminal); prints INCIDENT_EMBEDDING_BASE_URL
uv run python scripts/eval_ingest_gt.py --dry-run   # 2. validate the ground-truth workbook (no database)
uv run python scripts/eval_generate_split.py     # 3. seeded demonstration/evaluation split, once
uv run python scripts/eval_run.py                # 4. full run: 3 models, 5 categories
uv run python scripts/eval_run.py --models nvidia/cosmos-3-nano-reasoner --categories Assault --limit 1   # smoke test
uv run python scripts/eval_aggregate.py          # 5. aggregate across models and categories

uv run pytest tests/                             # hermetic tests: no live database, agent or GPU
```

Settings come from `.env.local`, a symlink to the profile's shared `../.env.local`; the variables it needs are listed in [`../.docs/mvp2/eval.md`](../.docs/mvp2/eval.md#11-eval-p1rp1-extraction-benchmark). The workbook and all working data stay in the gitignored `eval_data/`.

`retrieval_metrics.py` is the offline metrics module of the MVP2 retrieval benchmark (Track R, work-order step 1): pure functions that cut the baseline, post-filter and fill-to-K lists from a search response's candidate pool and compute the §3.5 metrics with paired seed-cluster bootstrap intervals. It has no script and calls no service; the harness that feeds it is step 3 ([`../.docs/mvp2/eval.md`](../.docs/mvp2/eval.md#10-work-order-and-definition-of-done)).

## Evaluation V2 package (`evalv2/`)

[Methodology v2.0](methodology/evaluation-methodology-v2.0.md) (proposed). The layered package beside the V1 modules imports downwards only:

- **`evalv2.core`:**
  - content-addressed, append-only artifacts and sealed runs (`artifacts.py`);
  - separate, retry-proof budget ledgers (`budget.py`; every HTTP attempt is charged before it is sent);
  - V1's retry policies, mirrored and tested for parity (`retry.py`);
  - checkpoints keyed by idempotency key (`checkpoint.py`);
  - strict JSON run configs (`config.py`);
  - run manifests (`manifest.py`);
  - the offline network block (`netguard.py`);
  - the private-output guard (`private.py`): ground-truth or reference text is written only where Git ignores it, or outside the repository;
  - the frozen-V1 guard (`frozen_v1.py` with `frozen_v1.json`, the SHA-256 of 90 frozen V1 files).
- **`evalv2.adapters`:** provider specifics only, never scoring.
  - The OpenAI-compatible adapter (`openai_compat.py`) covers Switchyard, the NVIDIA API catalog and a local vLLM or NIM endpoint.
    - It charges every attempt before sending it and applies V1's retry policy.
    - It classifies failures as V1 does.
    - It stores the request and response bodies but never the key.
  - A scripted `fake.py` adapter supports plumbing runs.
  - The Track B adapter (`vss.py`) runs a whole pipeline as one subject: one experimental `/analyze` call per run on a loopback experiment agent (`EVALV2_VSS_AGENT_URL`, default `http://127.0.0.1:8765`), never the shared VM agent.
    - **Before sending:** every ledger's worst case is checked, and nothing is sent when a cap could be passed.
    - **After the reply:** it charges what the agent reported sending, stage by stage, from the run provenance. An attempt without usage is charged at the token bound. A failure without provenance is charged at the worst case; a refused connection costs nothing.
    - **Pinning:** the agent's commit, dirty flag, flags and contract must stay the same across a run and a resume. The profile's expected flags must hold, and no undeclared stage may send a call.
    - **Refusals:** a refusal (400, 403, 409, 501) stops the run.
    - The signed URL is stored without its signature.
  - `capabilities.json` records what each model is believed to support. Every entry is `verified: false` until a read-only live check confirms it.
  - `registry.py` builds adapters from subject configs.
- **`evalv2.requests`:** request builders that load `contracts/` and hold no prompt text. `p1.py` builds the P1 body, and a test pins it to V1's `eval_vlm_client.p1_request_payload`.
  - `vss.py` holds the Track B pipeline profiles and builds each experimental `/analyze` body with its accounting.
  - Each profile sets: the P4 fields (`skip_rp1`, `max_crop_calls`, a required `max_zoom_calls`, optionally `experiment_object_tracking` or a registry prompt), the flags the agent must run with, the stages it may call, and its token bounds.
  - Profiles are versioned (`production-v4`, `production-v4-object-tracking`); any other change is a new profile id. Their caps are provisional until the Track B pilot.
- **`evalv2.tasks`:** the runner and scorer protocols and the task registry.
  - **`tasks/incident_objects/`** (Track A, P1 only), registered lazily as `incident_objects`:
    - **`runner.py`:** sends V1's P1 body through a subject's adapter, validates the reply with V1's contract policy, stores the prediction, and labels the outcome as V1 does.
    - **`scoring.py`:** follows V1's `run_evaluation` and calls V1's own rules through `v1.py`, the task's only bridge to V1. Similarity and judge scores are injected providers whose results are recorded, so a run's full similarity matrices and judgements make re-scoring offline and exact.
    - **`aggregate.py`:** reports V1's own aggregates as `continuity_v1`, plus the V2 metrics: type macro-F1 and confusion, severity quadratic-weighted κ, timestamp errors with a tolerance curve, the incident window's temporal IoU, and attribute accuracies.
  - **`tasks/pipeline/`** (Track B, the production pipeline), registered lazily as `pipeline`. It runs only `adapter: vss` subjects.
    - **`runner.py`:** sends one request per clip, subject and repeat, and stores three predictions of the same run:
      - `p1`: the raw P1 reply through V1's policy, as in Track A;
      - `p1_agent`: the report before the zoomed second look. This is `experiment.p1_report`, or else the final report with every zoom correction undone; the two must agree where both exist;
      - `final`: the final report.
    - Each stored run also holds the evidence entries, the agent's crop, zoom and tracking summaries, whether a caption pass ran, and whether V1's policy agreed with the agent.
    - Its worst case already counts the agent's own retries, so preflight does not multiply it.
    - **`scoring.py`:** scores each stage with Track A's scorers and pairs two stages run by run:
      - the zoom effect: `p1_agent` against `final`;
      - the caption effect: Track A against Track B `p1`, once the caption pass runs (X16).
    - **`crops.py`:** feeds a run's evidence entries to A8's crop scorers, matched to ground-truth items through the `final` stage (zoom never changes item ids). Box references are indexed per clip by kind and ground-truth id, and duplicates are refused.
      - A capped item is unscorable, not a miss.
      - `cap_summary` counts how often each cap refused calls: crop, zoom, the tracker's detector budget and its time cap. The pilot uses it to recalibrate the provisional caps.
  - **`tasks/crops/scoring.py`:** evidence boxes against box references, per reference tier.
    - **Operational:** was a box located? A crop-call cap, a missing detector or store, or a crop error makes the entry unscorable, never a miss.
    - **Temporal:** the evidence second (`box_seconds`, else the requested one) against the item's visible seconds.
    - **Same-second IoU:** correct evidence means IoU ≥ 0.5 at a visible second.
    - Primary metrics use `human_verified` references; the AI tiers are reported beside them.
    - The agent stores a 5%-padded box. A recorded `detected_box` is preferred; otherwise the padding is inverted and counted as an estimate.
  - **`tasks/chat/scoring.py`:** chat answers against chat references.
    - **Retrieval:** V1's MVP2 `list_metrics` (P@K, R@K, MRR) through the `tasks/chat/v1.py` bridge, plus nDCG@K.
    - **Exact answers:** counts, values, lists and windows.
    - **Refusals:** expected only for unanswerable and false-premise questions.
    - **Citations:** precision and recall.
    - **Judged checks:** fact recall, unsupported claims and contradictions, each through the judge cache with a hash-pinned prompt (`judge-*-v1.txt`), so a re-score replays offline.
    - The judge must not share a model family with a subject or the reference generator (`check_judge_family`).
    - Oracle and system corpora, and reference tiers, are aggregated apart; `primary` is `human_verified`.
  - **`tasks/judging.py`:** judge results as data. A cache is keyed by judge model, prompt hash, settings and texts. An offline judge never calls a model, and a missing judgement is unscored, not failed.
- **`evalv2.references`:** the box-reference tooling (R2). Making references is the approved run RA1.
  - **`boxes.py`:**
    - frame plans at every second;
    - proposals numbered by seed, never by rank;
    - the generator packet: the item's ground-truth words plus numbered frames;
    - the blind validator packet: offset frames and its own numbering. `assert_blind` proves it holds no answer field, no frame the generator saw, and no box it chose;
    - per-second boxes;
    - the record: agreeing seconds at IoU ≥ 0.5 become `ai_validated` after code checks; disagreement and absence are flagged.
  - **`detector.py`:** the offline open-vocabulary detector as a JSON subprocess protocol. It runs in a throwaway environment that records its weights' SHA-256, and is never a subject.
  - **`scripts/v2_box_frames.py`:** extracts native frames and draws numbered marks, in a throwaway environment.
  - The box prompts are `gen-box-select-v1` and `gen-box-validate-v1` in `evalv2/datasets/generation_prompts/`.
  - **`chat.py`:** chat references (R3). Making them is the approved run RA2.
    - Classes: 10 incident-scope and 5 library-scope.
    - Every countable answer is computed by code from the ground truth's contract-shaped reports, and `recount` must agree.
    - The generator only words the question and two paraphrases, or writes atomic facts for `incident.facts`.
    - The blind validator gets the question and the incident, never the reference. A record is `ai_validated` only when it agrees and the code checks pass, including that the question does not state its answer.
    - `leakage` reports each question's overlap with the target's description.
    - The oracle corpus (the ground truth's reports) and a system corpus are kept apart.
    - Prompts: `gen-chat-wording-v1` and `gen-chat-rederive-v1`.
- **`evalv2.references.dashboard`** and **`tasks/dashboard/scoring.py`:** the "Ask for a chart" spec benchmark (R4).
  - **Allowlist:** read from the console's export, `contracts/prompts/chart/allowlist.json`. A test fixture copy is drift-checked against the export when present.
  - **Specs:** `parse_spec` independently re-implements the console's `parseChartSpec`, and `all_specs` enumerates all 1,690 valid specs.
  - **References:** a generator words a request for a code-chosen spec, or an unsupported one (`gen-dashboard-request-v1`). A blind validator re-derives the spec (`gen-dashboard-respec-v1`), and the record is `ai_validated` only on exact agreement.
  - **Scoring:** each reply is one of exact, refusal-correct, valid-but-wrong, false refusal, false accept or parse failure, with per-field accuracy and stability across repeats, per tier. Making references is the approved run RB2.
  - **Figures** (with `figures_of`): what the user would see. The reference spec's chart is compared with the reply's chart on the frozen snapshot: figures match or differ, refusals, false accepts, or no chart. A valid-but-wrong spec can still show the right figures, so figure accuracy is reported beside spec accuracy.
- **`evalv2.references.dashboard_figures`, `dashboard_snapshot` and `dashboard_replay`:** a chart's figures, checked three independent ways on a frozen snapshot with a frozen clock (R4).
  - **Recount:** `dashboard()` recounts `get_incident_dashboard` in Python from the snapshot's rows, following the migration's documented rules. `chart()` re-implements how the console turns one dashboard into chart rows: the period from the clock, trends coarsened past 31 bars, and split bar charts.
  - **SQL:** the recount must equal the production SQL in the harness. The agreement test covers two seeded snapshots: bounded periods (including one across the US daylight-saving change), three time zones, every scope, type and severity filter, weekday and hour filters, and all-time calls.
  - **Console replay:** `scripts/v2_chart_replay.mjs` runs the console's own `generateChart` and RPC mapper in Node on recorded dashboard answers. Its answers must equal `chart()` for every spec as both bar and pie.
  - **Snapshots:** `seeded_snapshot` is synthetic, seeded and committable, and covers the dashboard's rules. `corpus_snapshot` is the evaluation corpus as a dashboard (run RB2; private paths only).
  - **Limitation:** labels sort in code-point order, as in the harness's C.UTF-8 database. A database with another collation may order tied labels, and the top-N cut-off among ties, differently.
- **`evalv2.sql`:** the SQL harness for model-free figures.
  - **Schema:** the finals migrations on a throwaway Postgres (`pgserver`), on top of the Supabase stub, as the schema's own SQL harness does.
  - **Snapshot:** a frozen `evalv2-sql-snapshot-v1` snapshot, loaded with its ids kept (identity columns included).
  - **Calls:** the dashboard and summary functions, with every parameter explicit. **A dashboard call without both period bounds, a timezone and a scope is refused**, because null bounds make `get_incident_dashboard` read `CURRENT_DATE`.
  - **All-time calls:** `dashboard_open_period` passes both bounds as null, as an all-time chart does. It refuses a result where nothing was counted: only then does the trend read `CURRENT_DATE`.
  - **Environment:** `pgserver` and `psycopg` are not eval dependencies. `scripts/v2_sql_harness.py` runs in a throwaway environment (`uv run --no-project --script ...`), and the real-schema test runs there too, so it is skipped in the eval suite.
- **`evalv2.verify_v1`:** re-scores stored V1 records with V2 by replaying the stored assignments and judge scores (V1 kept no matrices). It requires every score block, and V1's aggregates and the Stage 3 report's `items` and `severity`, to be identical. Running it on the real Stage 3 records is the approved check RA0.
- **`evalv2.datasets`:**
  - **`registry.json`:** every dataset by id, with its location and pinned hashes. It is append-only, and only hashes and counts are committed.
  - **Resolving:** `resolve` checks the hashes before anything is read. Private datasets are read from `--data-root`, else `EVALV2_DATA_ROOT`, else `eval_data/`.
  - **`ground_truth.py`:** reads `ground-truth-v1.0` through V1's `load_frozen_gt_index`, and also checks the manifest's `records_sha256`.
  - **`split-v2`:** verified, and checked against the split frozen with the ground truth.
  - **`records.py`:** reference records and their tiers. Only an imported human decision makes a record `human_verified`, and a reviewer id naming a model is refused.
  - **`loader.py`:** gives each task the datasets its config names, plus their hashes for the run manifest.
  - **`generation_prompts/`:** the append-only registry of prompts that make references. It is empty until the reference tooling adds its prompts.
- **`evalv2.review`:** the human-review loop.
  - **Sampling:** every flagged record, plus a stratified sample (20% by default). The sample is deterministic per seed, and its strata include the tier.
  - **Workbook:** the sample is exported to xlsx with a decision list and a hidden payload hash. People's decisions are imported back and applied; a decision on a changed record is refused.
  - **Agreement:** Cohen's κ and weighted κ.
  - **Error estimate:** the AI tiers' error rate is estimated from the reviewed sample, weighted by stratum.

**Adding a model:**
- **OpenAI-compatible endpoint:** add a subject entry to the run config and an entry to `adapters/capabilities.json`. No code changes.
- **Different API:** add one module implementing `adapters/base.ModelAdapter` and one `ADAPTERS` entry. Tasks and scorers stay unchanged.

The `test_every_adapter_meets_the_conformance_contract` test applies to every adapter.

Commands are in the [`incident-run-eval`](../skills/incident-run-eval/SKILL.md) skill. Tests live in `tests/v2/` and run with `uv run pytest tests/`. Nothing in the package calls a model yet.

## Dataset videos (local copies and manifest)

```bash
uv run python scripts/eval_fetch_videos.py --list          # inventory: dataset clips and skipped objects, no download
uv run python scripts/eval_fetch_videos.py                 # download (resumable) and verify; writes the manifest
uv run python scripts/eval_fetch_videos.py --verify-only   # re-hash local copies against the manifest, no R2
uv run python scripts/eval_refresh_split_keys.py --check   # are the split manifests' R2 keys current?
```

- **Dataset:** every `anomaly/<folder>/<file>.mp4` in R2 except `anomaly/shooting/` (80 clips, about 1.15 GB). Each clip is downloaded to the gitignored `eval_data/videos/<folder>/<original filename>`, so `_demo` names are kept. R2 is only listed and read.
- **Integrity:** the size must equal R2's, the R2 ETag must be reproduced from the local file (the MD5 for a single-part upload; for a multipart upload, the part-MD5 ETag at the part size that gives its part count), and the SHA-256 and MP4 `mvhd` duration are recorded. A file is verified as a `.part` and renamed only when it passes, and a rerun skips clips that already verify. The command exits non-zero on any finding.
- **Manifest:** [`eval_reproducibility/dataset-manifest-v1.json`](eval_reproducibility/dataset-manifest-v1.json) (tracked; no video, no credentials) lists every clip's incident id, ground-truth filename, R2 key and folder, sizes, digests and duration. It also lists the skipped objects and the integrity findings, including that every split video is exactly one clip and every clip is in a split. `clips_sha256` hashes the whole set.
- **`_demo` clips:** `anomaly/demo/<Id>_x264_demo.mp4` holds presentation-demo copies of dataset clips. Resolution ignores a `_demo` suffix before the extension (`dataset_manifest.canonical_basename`), so the ground-truth name `<Id>_x264.mp4` resolves to it, and a plain clip and its demo copy together are reported as ambiguous.
- **Split manifests:** `eval_refresh_split_keys.py` rewrites only their R2 key maps through the same resolver. The seed, membership and order never change. Copy the refreshed files to `eval_data/` for `eval_run.py`.

## VLM benchmark (incident fields, processing time)

```bash
cp eval_reproducibility/split_*.json eval_data/       # if eval_data/ has no manifests yet
uv run python scripts/eval_dataset_stats.py           # 1. size and length of every video; no model calls
uv run python scripts/eval_run.py --incident-only --categories Assault --limit 1   # 2. smoke: 1 video x 3 models
uv run python scripts/eval_run.py --incident-only     # 3. full: 55 videos x 3 models
uv run python scripts/eval_benchmark_export.py        # 4. tables from the newest run (or --run-dir runs/<run start>)
```

`--incident-only` scores the incident fields only (no entity/instrument/asset matching, so no embedding server, and no RP1). Each record keeps its P1 round-trip time (`timing.p1_elapsed_s`), the video's size and length, its `outcome` (`scored`, `contract_invalid`, `parse_failure`, or no model output: `timeout`, `rate_limited`, `http_error`) and its judge-call count. P1 follows the fixed rate-limit policy `eval_vlm_client.P1_RATE_LIMIT_POLICY` (20 s between attempts; HTTP 429 only is retried, after 60 s or a longer `Retry-After`, at most twice), and an empty predicted description scores 0.0 without a judge call. The rules and their reasons are in [`../.docs/decisions.md`](../.docs/decisions.md) (2026-10-02 benchmark entry).

What a correct ground-truth label is: [`ground_truth/annotation-guidelines-v1.0.md`](ground_truth/annotation-guidelines-v1.0.md) (approved 2026-10-27). Proposed corrections to the human labels, for review: `uv run python scripts/gt_review_build.py` builds the working workbook, the audit and its manifest in [`ground_truth/review/`](ground_truth/review/README.md); nothing there is final ground truth. The AI-assisted correction (PR 4: review packets, timestamped frames, `gt-correction-v1.0` proposals and the candidate workbook `scripts/gt_candidate_build.py`) is documented there too. The freeze to `ground-truth-v1.0` (PR 5; only with an approval record) and `split-v2` are in [`ground_truth/freeze/`](ground_truth/freeze/README.md). Prompt experiments (registry, two-arm runner, guards, reports): [`experiments/README.md`](experiments/README.md); readiness and budget: [`docs/experiments/readiness.md`](docs/experiments/readiness.md).

How predictions are scored is fixed by [`methodology/evaluation-methodology-v1.1.md`](methodology/evaluation-methodology-v1.1.md) (one pin changed from [v1.0](methodology/evaluation-methodology-v1.0.md): the judge output limit) (pins in `methodology.py`, recorded in every result).

Evaluation V2 (several models; incident fields, objects, evidence boxes, chatbot, dashboard, tokens and latency) follows [`methodology/evaluation-methodology-v2.0.md`](methodology/evaluation-methodology-v2.0.md) (proposed). It adds a layered package beside these V1 modules and changes none of them.

How P1 is sent, validated and scored: [`../.docs/mvp2/eval.md`](../.docs/mvp2/eval.md#11-eval-p1rp1-extraction-benchmark) and [`../.docs/analysis-schema.md`](../.docs/analysis-schema.md). Operating it: the [`incident-run-eval`](../skills/incident-run-eval/SKILL.md) skill.

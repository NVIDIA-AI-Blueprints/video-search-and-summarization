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
  - checkpoints keyed by idempotency key (`checkpoint.py`): every outcome name is classified explicitly, and only an infrastructure failure, a budget refusal or an unsent attempt stays open for recovery (an HTTP error status or a reply that is not a completion is final, as in V1's `recovery-v1`);
  - strict JSON run configs (`config.py`);
  - run manifests (`manifest.py`);
  - the offline network block (`netguard.py`);
  - the private-output guard (`private.py`): ground-truth or reference text is written only where Git ignores it, or outside the repository;
  - the frozen-V1 guard (`frozen_v1.py` with `frozen_v1.json`, the SHA-256 of 90 frozen V1 files).
- **`evalv2.adapters`:** provider specifics only, never scoring.
  - The OpenAI-compatible adapter (`openai_compat.py`) covers Switchyard, the NVIDIA API catalog and a local vLLM or NIM endpoint.
    - It charges every attempt before sending it, at V1's P1 ceiling (35,000 tokens, `P1_TOKENS_CEILING`) until usage is measured, and applies V1's retry policy. Any failed attempt that spanned a host suspend is `host_suspended`, whatever it failed with, as in V1.
    - It classifies failures as V1 does.
    - It stores the request and response bodies but never the key, and never a signed URL's signature or user info: the stored request drops every content URL's query string, fragment and `user:password@` (`stored_request`, `redact_url`); the request sent is unchanged. A failure's detail has every URL in it redacted the same way (`redact_text`), since a provider's error can echo the signed video URL.
  - A scripted `fake.py` adapter supports plumbing runs.
  - The Track B adapter (`vss.py`) runs a whole pipeline as one subject: one experimental `/analyze` call per run on a loopback experiment agent (`EVALV2_VSS_AGENT_URL`, default `http://127.0.0.1:8765`), never the shared VM agent.
    - **Before sending:** every ledger's worst case is held in the budget log (charged), and nothing is sent when a cap could be passed. The hold covers the call while it is in flight, so a run killed mid-call resumes with it counted and two requests in flight cannot both pass a cap that covers one.
    - **After the reply:** the hold is released to what the agent reported sending, stage by stage, from the run provenance. An attempt without usage is charged at the token bound. A failure without provenance keeps the worst case; a refused connection or request costs nothing.
    - **Pinning:** the agent's commit, dirty flag, flags and contract must stay the same across a run and a resume. The profile's expected flags must hold (they describe what the request ran: `experiment: true`, object tracking only in the tracking profile), and no undeclared stage may send a call. The object tracker's detector cap the reply reports (`object_tracking.detector.cap`) must be at most the profile's worst case (80) and is pinned too; a higher, missing or changed cap stops the run after charging.
    - **Refusals:** a refusal (400, 403, 409, 501) stops the run.
    - The signed URL is stored without its signature. An agent reply is stored, and its error reported, with every URL in it redacted (an agent error can quote the signed URL).
  - `capabilities.json` records what each model is believed to support, and `listed_on` which endpoints listed the id. Every entry stays `verified: false` until a model call confirms it.
  - `catalog_rb1.json` is the read-only endpoint check RB1 (2026-11-01): the Switchyard and NVIDIA catalog model listings with their hashes, the public documentation found, and the findings. Two catalog ids differ from Switchyard's: `nvidia/nemotron-3-ultra-550b-a55b` and `nvidia/nemotron-nano-3-30b-a3b`. Cosmos 3 is on Switchyard only. A third-family judge is on the catalog only, so it needs `NVIDIA_API_KEY` (X18).
  - `registry.py` builds adapters from subject configs.
- **`evalv2.requests`:** request builders that load `contracts/` and hold no prompt text. `p1.py` builds the P1 body, and a test pins it to V1's `eval_vlm_client.p1_request_payload`.
  - `chart.py` builds the console's "Ask for a chart" body (prompt from `contracts/prompts/chart/system.txt`, hash-checked; the route's trim and 300-character rule) and parses a reply as the console does. Eval is a producer of the `chart/request` golden, checked byte for byte as posted upstream.
  - `vss.py` holds the Track B pipeline profiles and builds each experimental `/analyze` body with its accounting.
  - Each profile sets: the P4 fields (`skip_rp1`, `max_crop_calls`, a required `max_zoom_calls`, optionally `experiment_object_tracking` or a registry prompt), the flags the agent must run with, the stages it may call, and its token bounds.
  - Profiles are versioned (`production-v4`, `production-v4-object-tracking`); any other change is a new profile id. Their caps are provisional until the Track B pilot.
- **`evalv2.tasks`:** the runner and scorer protocols and the task registry.
  - **`tasks/incident_objects/`** (Track A, P1 only), registered lazily as `incident_objects`:
    - **`runner.py`:** sends V1's P1 body through a subject's adapter, validates the reply with V1's contract policy, stores the prediction, and labels the outcome as V1 does. Every attempt reserves V1's P1 ceiling on the `inference` ledger, the bound preflight assumes. A failed record keeps the adapter's failure kind (`failure_kind`) for the checkpoint, since V1's label folds transport errors and budget refusals into `http_error`.
    - **`scoring.py`:** follows V1's `run_evaluation` and calls V1's own rules through `v1.py`, the task's only bridge to V1. Similarity and judge scores are injected providers whose results are recorded, so a run's full similarity matrices and judgements make re-scoring offline and exact.
    - **`aggregate.py`:** reports V1's own aggregates as `continuity_v1`, plus the V2 metrics: type macro-F1 (over the labels with ground-truth support; a label no clip has is `None`, not 0) and confusion, severity quadratic-weighted κ, timestamp errors with a tolerance curve, the incident window's temporal IoU, and attribute accuracies.
  - **`tasks/pipeline/`** (Track B, the production pipeline), registered lazily as `pipeline`. It runs only `adapter: vss` subjects.
    - **`runner.py`:** sends one request per clip, subject and repeat, and stores three predictions of the same run:
      - `p1`: the raw P1 reply through V1's policy, as in Track A;
      - `p1_agent`: the report before the zoomed second look. This is `experiment.p1_report`, or else the final report with every zoom correction undone; the two must agree where both exist;
      - `final`: the final report.
    - Each stored run also holds the evidence entries, the agent's crop, zoom and tracking summaries, whether a caption pass ran, whether V1's policy agreed with the agent, and the agent's `p1_attempts` with `p1_retried` (it re-sent P1 after a runaway output).
    - Its worst case already counts the agent's own retries, so preflight does not multiply it.
    - **`scoring.py`:** scores each stage with Track A's scorers and pairs two stages run by run:
      - the zoom effect: `p1_agent` against `final`;
      - the caption effect: Track A against Track B `p1`, once the caption pass runs (X16). `caption_effect` leaves out runs with `p1_retried`, whose `p1` is a retry Track A never gets.
    - **`crops.py`:** feeds a run's evidence entries to A8's crop scorers, matched to ground-truth items through the `final` stage (zoom never changes item ids). Box references are indexed per clip by kind and ground-truth id, and duplicates are refused.
      - A capped item is unscorable, not a miss.
      - `cap_summary` counts how often each cap refused calls: crop, zoom, the tracker's detector budget and its time cap (read from the reply's top-level `object_tracking`; `experiment.object_tracking` is only the request's setting). The pilot uses it to recalibrate the provisional caps.
  - **`tasks/crops/scoring.py`:** evidence boxes against box references, per reference tier.
    - **Operational:** was a box located? An entry the locate step never judged is unscorable, never a miss: a crop-call cap, a missing detector or store, a crop error, no frame produced, a failed upload or privacy blur, or cropping skipped or failed (`UNSCORABLE_GAPS`, the agent's own `image_gap` strings, which a test checks against the agent source; any entry with `located` still null and no box counts too).
    - **Temporal:** the evidence second (`box_seconds`, else the requested one) inside one of the reference's `visible_spans`.
    - **Same-second IoU:** correct evidence means a temporal hit with IoU ≥ 0.5 against the reference box at that second. A second in a span without a reference box (the passes disagreed there) leaves IoU and correctness unscored (`correct_unscored`), not a miss.
    - Primary metrics use `human_verified` references; the AI tiers are reported beside them.
    - The agent stores a 5%-padded box. A recorded `detected_box` is preferred; otherwise the padding is inverted and counted as an estimate.
  - **`tasks/chat/scoring.py`:** chat answers against chat references.
    - **Retrieval:** V1's MVP2 `list_metrics` (P@K, R@K, MRR) through the `tasks/chat/v1.py` bridge, plus nDCG@K.
    - **Exact answers:** counts, values, lists and windows, read with the references' own normaliser. A value is the one the answer asserts (the severity level it states, a 1-5 scale aside, or the one corpus type it names). A list is the set it asserts: every expected item and no other item of the reference's `answer_space` vocabulary (negated mentions assert nothing), and an empty list needs an explicit none. An extra item the vocabulary does not know is left to the unsupported-claim judge. For the most common type, the answer is the type(s) its superlative clause names ("assault is the most common type"), so ranking the rest after it ("followed by burglary (1)") is not an extra item; without such a clause the whole answer is read.
    - **Refusals:** expected only for unanswerable and false-premise questions.
    - **Citations:** precision and recall against the incidents the expected answer rests on (the reference's `support`: the question's incident, a count's counted incidents, a filter's list, a retrieval question's targets).
    - **Holders:** judged, not matched: the fact judge checks that the answer identifies the holder the ground truth describes (`holder_identified`).
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
    - Every countable answer is computed by code from the ground truth's contract-shaped reports, and `recount` must agree. An instrument's holder is the holder's ground-truth description (a system corpus numbers people on its own, so a local id like `E1` names nobody), judged at scoring. A library instrument filter matches whole words (`bat` is not `baton`).
    - The generator only words the question and two paraphrases, or writes atomic facts for `incident.facts`.
    - The blind validator gets the question and the incident (for a library question, every report of the oracle corpus, so it can recount or find the targets), never the reference. A retrieval question's generator also sees its target's report. A record is `ai_validated` only when it agrees and the code checks pass, including that the question does not state its answer.
    - `leakage` reports each question's overlap with the target's description.
    - The oracle corpus (the ground truth's reports) and a system corpus are kept apart.
    - Prompts: `gen-chat-wording-v1` and `gen-chat-rederive-v1`.
- **`evalv2.references.dashboard`** and **`tasks/dashboard/scoring.py`:** the "Ask for a chart" spec benchmark (R4).
  - **Allowlist:** read from the console's export, `contracts/prompts/chart/allowlist.json`. A test fixture copy is drift-checked against the export when present.
  - **Specs:** `parse_spec` independently re-implements the console's `parseChartSpec`, and `all_specs` enumerates all 1,690 valid specs.
  - **References:** a generator words a request for a code-chosen spec, or an unsupported one (`gen-dashboard-request-v1`). A blind validator re-derives the spec (`gen-dashboard-respec-v1`), and the record is `ai_validated` only on exact agreement.
  - **Scoring:** each reply is one of exact, refusal-correct, valid-but-wrong, false refusal, false accept or parse failure, with per-field accuracy and stability across repeats, per tier. Making references is the approved run RB2.
  - **C1c task** (`tasks/dashboard/runner.py`, registered as `dashboard`): one work item per reference record, subject and repeat; the console's request through the subject's adapter; the raw reply, its parse and its `reply_kind` stored. `tasks/dashboard/report.py` scores a run per subject (denominators, invalid replies apart from wrong specs, per-field and figure accuracy, a record-level bootstrap). Launch, caps and the review needed before results count: [readiness §9](methodology/v2-run-readiness.md#9-dashboard-evaluation-c1c).
  - **Figures** (with `figures_of`): what the user would see. The reference spec's chart is compared with the reply's chart on the frozen snapshot: figures match or differ, refusals, false accepts, or no chart. When the reference's own answer is not a chart (a pie of a dimension that needs bars), the same answer from the reply is right (`no_chart_match`). A valid-but-wrong spec can still show the right figures, so figure accuracy is reported beside spec accuracy.
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

### Live runs and scoring (`executor.py`, `approval.py`, `clips.py`, `score_stage.py`)

- **`run --config C --live --approval M --out DIR`** executes a Track A (`incident_objects`), Track B (`pipeline`) or C1c (`dashboard`, text only: no clip and no R2) config, with `EVALV2_ALLOW_LIVE=1` and the owner's `evalv2-live-approval-v1` marker; the same command on the same `DIR` resumes. It refuses before sending when the marker does not fit (config hash, purpose, half, items, any cap above the marker's, the run-wide token total, gated profiles), when a pilot would touch a test-half clip, on a dirty eval tree, or on a missing credential. Every attempt is reserved (or held) on the ledger first, paced (60 rpm, 4 in flight, per-model tokens per minute; Track B one at a time), and recorded in `attempts.jsonl`; the run manifest pins the commit, config, datasets, marker, caps and Track B's agent. Clip URLs are presigned R2 GETs (local signing, read-only).
- **`approval draft --config C`** prints a marker skeleton for the owner; it approves nothing.
- **`score --config C --out DIR`** scores the stored predictions (Track A incident and objects; Track B stages, zoom effect, caps and crops) with the registry's judge under the marker's `judge` budget and a judge ledger shared by every scoring stage, and MiniLM from the local cache. **`--offline`** re-scores from the judge and embedding caches under the network guard. A `dashboard` config is scored by code alone, with no judge and no network (`--figures sql|recount|none`).
- `budget_mode: hard_stop` in a config marks its caps as an approved budget below the pessimistic worst case: preflight lists the gap under `hard_stop` instead of failing.
- Track B's caption pass runs only with the gated `production-v4-captions` profile (`experiment_captions: "inline"`, 30 caption calls, agent PR #439), sent only when the marker lists it in `enabled_profiles`. `chunking.enabled` varies per clip and is recorded per run, never pinned.
- Launch commands, the marker format and the pilot budgets: [`methodology/v2-run-readiness.md`](methodology/v2-run-readiness.md#2-launch-commands).
- Human review of the AI-generated references (what to review, which results depend on it): [`methodology/v2-human-review-guide.md`](methodology/v2-human-review-guide.md). Judge scoring proposal (calls, caps, cost, calibration; not approved): [`methodology/v2-judge-proposal.md`](methodology/v2-judge-proposal.md). Test-clip exposure and how to describe the held-out benchmark: [`methodology/v2-dataset-integrity.md`](methodology/v2-dataset-integrity.md). Proposed independent 20-video test set (not approved): [`methodology/v2-fresh-test-set-proposal.md`](methodology/v2-fresh-test-set-proposal.md).
- Development pilot results (development split only, no held-out results): [`methodology/v2-dev-pilot-results.md`](methodology/v2-dev-pilot-results.md); chart-ready tables and figures in [`methodology/results/v2-dev/`](methodology/results/v2-dev/), regenerated with `scripts/v2_dev_results_export.py`.

Commands are in the [`incident-run-eval`](../skills/incident-run-eval/SKILL.md) skill. Tests live in `tests/v2/` and run with `uv run pytest tests/`. Only `run --live`, a live `score` and `python -m evalv2.probe --live` call a model, each behind `EVALV2_ALLOW_LIVE` (or the probe's marker) and an approval marker; the tests use fakes.

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

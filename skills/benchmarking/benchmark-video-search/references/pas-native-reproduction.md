# Native PhysicalAI PAS reproduction

All implementation and build inputs live in this checkout. Videos and model
weights remain external inputs. No separate worktree or evaluation repository
is required. No ground-truth boxes or representative frames enter retrieval.

The validated recipe uses native RT-CV detections, source identity captured
before batching, the I1–I4 evaluation configuration, and an arithmetic mean of
the strongest 14 embedded frame scores **per person track**. A clip uses its
strongest track. Evidence still points to that track's strongest individual
frame. Tracks with fewer than 14 embedded frames average the available frames.
This is query-dependent scoring, not a query-independent representative crop.
The global pool size was selected on the diagnostic subset; it is not a held-out
accuracy claim. Product defaults keep pool size 1.

Live routing uses `person-state-photo`: embed or attribute only. Embed receives
the original text; attribute receives the existing fixed `A photo of ….` wrapper.
There is no fusion, RRF, critic, reranker, or LLM query rewriting in this recipe.
The earlier optional representative weighting stays at its default zero.

## Prepare and deploy

Use the existing `prepare_search_reproduction.py prepare` command (see
`raw-sdk-runner.md`) to provision an owned search workspace with the pinned
Cosmos/SigLIP2/RT-DETR/LLM model inputs. Use this checkout's CLI and core:

```bash
export PYTHONPATH="$PWD/libs/vss/core/src:$PWD/libs/vss/cli/src"
export VSS_CONFIG_HOME="$WORKSPACE/cli-config"
PYTHON="$PWD/libs/vss/.venv/bin/python"
SCRIPTS="$PWD/skills/benchmarking/benchmark-video-search/scripts"

docker build -t vss-rt-cv-pas:branch deploy/docker/services/rtvi/rtvi-cv/pas-eval
"$PYTHON" "$SCRIPTS/prepare_pas_eval_profile.py" \
  --workspace "$WORKSPACE" --image vss-rt-cv-pas:branch
docker compose -f "$WORKSPACE/resolved.yml" build vss-agent rtvi-embed
```

The profile copies stock configs into the runtime directory and changes only:
Person threshold 0.85→0.5; detector interval 1→0; minimum crop size 32→12;
streammux padding 0→1. `useUniqueID: 1` and `enableReAssoc: 1` are already
set in the stock tracker config. Tiling is disabled. Model weights are unchanged.
The RT-CV model download policy from `prepare` is kept: downloads are disabled
only when verified `--cv-models` inputs were supplied. The built image ID is
recorded, and `prepare_search_reproduction.py check` fails if the running RT-CV
container uses a different image.
The Dockerfile builds both source identity patches and runs their C++ regression
against DeepStream 9.1 / CUDA 13.2.

On a previously indexed owned workspace, delete all uploaded gallery sources and
all Elasticsearch indices before the new ingest:

```bash
"$PYTHON" "$SCRIPTS/reset_clip_gallery.py" \
  --workspace "$WORKSPACE" --ingestion-manifest "$WORKSPACE/ingestion.json" \
  --output-dir "$RUN/reset"
```

This command verifies the gallery against the supplied ingestion manifest,
deletes videos through the configured CLI, waits for empty searched indices,
stops the owned Compose project, clears its dedicated Elasticsearch data and
Kafka buffer volumes, and recreates the project. It preserves source videos,
model files/caches and unrelated deployments. It deletes all ES indices,
including old custom indices. Do not use it on a shared deployment.
For a new empty workspace, use `docker compose -f "$WORKSPACE/resolved.yml" up -d --no-build`.

```bash
"$PYTHON" "$SCRIPTS/prepare_search_reproduction.py" check --workspace "$WORKSPACE"
"$PWD/libs/vss/.venv/bin/vss" configure --base-url "$ORIGIN"
"$PYTHON" "$SCRIPTS/ingest_clip_gallery.py" --workspace "$WORKSPACE" \
  --dataset-root "$DATASET" --output "$RUN/ingestion.json" \
  --verify-source-attribution
```

`--verify-source-attribution` is the integration check for the source identity
patches: ingestion fails, and the manifest stays incomplete, if any `mdx-raw-*`
or `mdx-behavior-*` document names a numeric, missing or unregistered source.

`DATASET` is the extracted 80-clip subset directory. Ingestion validates all
video hashes, requires empty registration and searched indices, uses the
2025-01-01 timestamp anchor, waits for embedding/raw/behavior indexing, and
refreshes CLI discovery after ingestion. Save the new manifest as
`$WORKSPACE/ingestion.json` for the next owned reset.

## Evaluate

```bash
"$PYTHON" "$SCRIPTS/prepare_physicalai_pas_reference.py" \
  --dataset-root "$DATASET" --output-dir "$RUN/reference"
"$PYTHON" "$SCRIPTS/run_raw_clip_eval.py" --dataset-root "$RUN/reference" \
  --ingestion-manifest "$RUN/ingestion.json" \
  --output-dir "$RUN/attribute-189" --execution-backend sdk \
  --modes attribute-only --top-k 80 --attribute-frame-pool-size 14
"$PYTHON" "$SCRIPTS/run_raw_clip_eval.py" --dataset-root "$DATASET" \
  --ingestion-manifest "$RUN/ingestion.json" \
  --deployment-manifest "$WORKSPACE/deployment.json" \
  --output-dir "$RUN/live-600" --execution-backend sdk \
  --modes live-routed --live-routing-profile person-state-photo \
  --top-k 80 --attribute-frame-pool-size 14
```

The first run scores the 189 frame-supported PAS queries against their frozen
protocol (285 released eligible tracks, 80 clips, 1,812 positive query–clip
pairs). The second uses all 600 canonical queries (350 event, 250 PAS). They use
different PAS cohorts/GT and must be labelled separately. The builder can check
an existing frozen JSONL with optional `--ground-truth`.

To rescore the live router on the reference cohort, first copy exactly the
189 selected captures into a separate directory (the scorer deliberately rejects
foreign query captures):

```bash
"$PYTHON" - "$RUN" <<'PYTHON'
import json, shutil, sys
from pathlib import Path
run = Path(sys.argv[1])
selected = run / "live-reference-captures"
selected.mkdir()
for query in json.loads((run / "reference/gt/queries_gt.json").read_text())["queries"]:
    shutil.copy2(run / "live-600/captures/live-routed" / (query["query_id"] + ".json"), selected)
PYTHON
```

Then use `score_raw_clips.py` with
`--queries "$RUN/reference/gt/queries_gt.json"`, the new ingestion manifest,
`--capture-dir "$RUN/live-reference-captures"`, `--top-k 80`, and an
explicit output path. Repeat at `--top-k 10` for mAP@10, denominator min(R,10).
With the whole 80-clip gallery retrieved, mAP@80 is full clip mAP. Require
`complete.json` success and 600 measured live routing calls, and check
`routes.jsonl` contains only embed/attribute routes. Live routing and fresh
tracking may vary slightly across runs. After both runs, confirm the gallery and
its attribution are unchanged:

```bash
"$PYTHON" "$SCRIPTS/ingest_clip_gallery.py" --workspace "$WORKSPACE" \
  --audit-only --verify-source-attribution --output "$RUN/gallery-after-eval.json"
```

## How this differs from the Physical AI protocol

The scores are comparable in cohort, GT and clip-level AP, but the retrieval
method is not the reference method:

- **No track eligibility filter.** The reference ranks only the 285 released
  tracks with more than 12 frames and mean score ≥ 0.70. Native retrieval ranks
  every RT-CV track that has a behavior document.
- **Pooled, query-dependent track score.** The reference scores one
  query-independent representative crop per track. This recipe averages a
  track's 14 best-matching frames for each query. A track with fewer than 14
  embedded frames averages only the frames it has, so short tracks are not
  penalised for their length.
- **Tuned on the evaluated cohort.** The pool size of 14 was chosen on the same
  diagnostic subset that is scored here, so these are development-cohort
  results, not held-out accuracy.
- **Separate cohorts.** The 66.30% forced-attribute row covers the 189
  frame-supported PAS queries. The live-routed PAS rows include queries the
  router sent to embed. The live-routed event row is the router's event subset,
  not an embed-only run.

Previous measured results before this branch consolidation: 66.31% forced
attribute on the 189-query reference; 75.52% overall, 81.87% event, 66.62%
canonical PAS, and 66.24% reference PAS using the binary live router. These
are comparison values, not a guarantee for a new ingest.

## Fresh validation on this branch, 2026-10-27

After deleting all previous registrations, resetting the owned Elasticsearch and
Kafka storage, redeploying the branch-built image/application packages, and
re-ingesting all 80 clips:

| Evaluation | Queries | Full clip mAP |
|---|---:|---:|
| Forced attribute, frame-supported PAS reference | 189 | 66.30% |
| Binary live router, all canonical queries | 600 | 75.66% |
| Binary live router, event | 350 | 81.59% |
| Binary live router, canonical PAS | 250 | 67.37% |
| Binary live router, frame-supported PAS reference | 189 | 66.71% |

The live run made 600 measured routing calls: 374 embed and 226 attribute,
with zero fusion routes. 289 targeted Python tests and the DeepStream C++
source identity regression passed. Runtime config and deployed source hashes
were verified against this checkout. Fresh tracking/live routing explain small
run-to-run differences from the comparison figures above.

# Reproduce raw video search

Run from this branch's repository root on a dedicated Linux x86-64 GPU host.
Prerequisites: Docker Engine + Compose, NVIDIA Container Toolkit, `git`, `uv`,
`ffprobe`, root or noninteractive sudo for runtime cleanup, an NGC key with
access to the pinned registry/models, outbound access to registry/model/package
hosts, and sufficient free space on **both** Docker storage and the work filesystem (preflight reserves
200 GiB for cold startup/data plus 15% filesystem headroom for Elasticsearch).
The reference placement uses two RTX PRO 6000 Blackwell 96 GB GPUs: CV/VLM on 0,
embedding/LLM on 1 (tested NVIDIA driver 595.91.07). An H100 configuration is
available with
`--hardware-profile H100`, but has not been qualified on this host.

Have these inputs locally; filenames and checksums are validated against
`scripts/reproduction-lock.json` in the benchmark directory:

- Cosmos release root (21 required files, 4.81 GB total): `MANIFEST.sha256`,
  the supplied vision/text ONNX graphs
  and external vision weights under `onnx/`, and processor/configuration/code
  files under `cosmos_embed1_448p_lora8_vitlora20_2stage_6640iter_hf/`.
  The large Hugging Face weight shards and prebuilt TensorRT engines are not used.
- Dataset root containing `clips/`, `additional_gt/clips_gt.json`, and
  `gt/queries_gt.json`. Videos must be directly under `clips/`, with unique stems.
  Minimal schemas (arbitrary counts/IDs; all relevant IDs must be gallery IDs):
  `{"gallery":[{"chunk_id":"clip-A","video":"clips/example.mp4"}]}` and
  `{"queries":[{"query_id":"q1","query":"person running","query_domain":"event","relevant_clip_ids":["clip-A"]}]}`.
  Query domains are `event` or `pas`; timestamps use the fixed 2025-01-01 file
  anchor. Full reference comparison uses the original 80 clips / 600 queries.

## Install and deploy

Set the paths and the host interface address reachable from containers.
The work directory must be new, outside the repository and input directories.
This is a full search profile; startup downloads the pinned CV/LLM/VLM assets.

```bash
set -euo pipefail
export NGC_CLI_API_KEY='<your-key>'
export COSMOS_RELEASE=/path/to/cosmos-release
export DATASET_ROOT=/path/to/dataset
export WORK=/path/on/large-disk/search-review-1
export ORIGIN=http://HOST_INTERFACE_IP:7777
export VSS_CONFIG_HOME="$WORK/cli-config"

printf '%s' "$NGC_CLI_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin
uv sync --project libs/vss --package nvidia-vss-cli --no-dev --locked
PY="$PWD/libs/vss/.venv/bin/python"
VSS="$PWD/libs/vss/.venv/bin/vss"
SCRIPTS="$PWD/skills/benchmarking/benchmark-video-search/scripts"

"$PY" "$SCRIPTS/prepare_search_reproduction.py" prepare --workspace "$WORK" \
  --cosmos-release "$COSMOS_RELEASE" --origin "$ORIGIN"
docker compose -f "$WORK/resolved.yml" pull --ignore-buildable
docker compose -f "$WORK/resolved.yml" up -d --build
"$PY" "$SCRIPTS/prepare_search_reproduction.py" check --workspace "$WORK"
"$VSS" configure --base-url "$ORIGIN"
"$VSS" configure check
```

Optional: `prepare --cv-models /path/to/cv-models` uses the locked RT-DETR/SigLIP2
ONNX, weights and tokenizer files instead of downloading them.
`prepare --llm-downloads /path/to/ngc` seeds an existing NGC checkpoint
`hub/` directory; runtime compilation caches are never copied. Device IDs can be
changed with `--cv-gpu` and `--embed-gpu`. Generated configuration contains
credentials: keep the private work directory private.

## Ingest and benchmark

Ingestion requires an empty gallery and search indexes. A failure leaves a partial
manifest: inspect it, then reset; the command never silently repeats an upload.
The committed raw evaluator supports embedding-only, attribute-only and live-
routed modes without critic, reranker or persistence. This recipe measures the
committed `person-state-photo` route profile. Keep this host otherwise idle.

```bash
"$PY" "$SCRIPTS/ingest_clip_gallery.py" --workspace "$WORK" \
  --dataset-root "$DATASET_ROOT" --output "$WORK/ingestion.json"
"$PY" "$SCRIPTS/run_raw_clip_eval.py" --dataset-root "$DATASET_ROOT" \
  --ingestion-manifest "$WORK/ingestion.json" --deployment-manifest "$WORK/deployment.json" \
  --output-dir "$WORK/results" --execution-backend sdk --modes live-routed \
  --live-routing-profile person-state-photo --top-k-values 10 80 \
  --warmup-queries 3 --gpu-telemetry
"$PY" "$SCRIPTS/ingest_clip_gallery.py" --workspace "$WORK" \
  --audit-only --output "$WORK/gallery-after-benchmark.json"
cat "$WORK/results/metrics.json"
"$PY" "$SCRIPTS/report_raw_clip_eval.py" --run-dir "$WORK/results"
```

This writes a self-contained `report.html` beside the run artifacts, with
aggregate diagnostics and searchable per-query routes, scores, latencies, ground
truth, and retrieval details. Review the report before sharing because it
contains query text, model decompositions, prompts, and retrieval results.

The `person-state-photo` profile makes one fresh LLM call per query to select
embedding or attribute retrieval. Embedding receives the original query;
attribute receives the original query with a fixed `A photo of ` prefix. The
model-generated caption and extracted source/time/count metadata do not change
the retrieval request. This fixed-gallery benchmark does not validate metadata
filter extraction. The prompt, route decision, and actual request are captured.
Inference uses temperature 0, thinking disabled for the pinned Nemotron model,
and a 32768-token completion cap. Product routing remains the evaluator default
unless this profile is explicitly selected. This committed recipe uses native
VSS retrieval; the separate `union-rrf` experiment is not included in this tree.

Use only a run with `results/complete.json` and a successful final audit.
AP@10 uses `min(relevant clips,10)`; latency excludes CLI startup and ingestion.
Use the canonical `mAP@10_percent` fields. Fresh ingestion/routing can vary even
at temperature zero; compare per-domain scores, routes and indexed coverage.

## Full-gallery embedding score

Use this additional benchmark command to measure embedding alone, without routing
or reranking. It uses every query and reports event/PAS separately. No external
Drive scoring scripts or query-ID files are required. For the supplied dataset,
the event result contains all 350 queries against 80 clips.

```bash
GALLERY_K=$("$PY" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["gallery"]))' \
  "$DATASET_ROOT/additional_gt/clips_gt.json")
"$PY" "$SCRIPTS/run_raw_clip_eval.py" --dataset-root "$DATASET_ROOT" \
  --ingestion-manifest "$WORK/ingestion.json" --deployment-manifest "$WORK/deployment.json" \
  --output-dir "$WORK/embed-full" --execution-backend sdk \
  --modes embed-only --fusion-strategy native --top-k "$GALLERY_K"
cat "$WORK/embed-full/metrics.json"
```

The gallery-depth `mAP@K_percent` is full mAP; AP divides by all relevant gallery
clips and missing relevant clips contribute zero. The primary benchmark's k=10
result uses `min(relevant clips,10)`. This full-gallery recipe assumes one indexed
embedding window per clip, as in the supplied gallery; multiple windows require
exhaustive collection before claiming a full clip ranking. `native` here selects
the supported embedding benchmark and does not add a fusion or reranking step.

The Cosmos release adapter now declares its file preprocessing explicitly:
rounded frame indices over the decoded chunk, followed by TorchVision bilinear
resize with antialiasing before division by 255. Other model defaults and live
stream preprocessing are unchanged. This affects **video ingestion**, so existing
indexed embeddings keep their old behavior until a separate fresh ingestion.
Native-resolution frames are buffered per chunk before selecting eight frames;
memory therefore scales with chunk duration and resolution. The supplied 80-clip
gallery was tested; larger/high-resolution chunks require capacity validation.

The preprocessing change was validated with the actual decoder and copied
TensorRT engines in an isolated offline process. The earlier deployment
lifecycles below predate this change; a fresh deployment/ingestion benchmark of
this revision is required before claiming those offline estimates as deployed
results.

The automatic person-crop gallery experiment is not part of this commit. Its
builder and alternate search backend are intentionally omitted, so this guide
does not provide commands or quote its results as reproducible from this tree.

## Tear down completely

**Deletes this project's entire runtime, including all Elasticsearch indexes,
recordings, model/engine caches, containers, networks and volumes.** Original
datasets/checkpoints and the work directory's evidence remain. It does not prune
other projects. To repeat, use a new work directory and the same commands.

```bash
"$PY" "$SCRIPTS/prepare_search_reproduction.py" teardown --workspace "$WORK" --dry-run > "$WORK/teardown-plan.json"
"$PY" "$SCRIPTS/prepare_search_reproduction.py" teardown --workspace "$WORK"
```

## Validation: 2026-10-24 (product routing profile)

Tested code: `0a410a1f27b669b1844901ec39ddf8b77da3ab07`. Three fresh software
lifecycles on the same host, each with 80 clips and 600 queries (350 event / 250
PAS), completed deployment, ingestion, benchmarking and verified teardown.
All 5,400 primary searches and 1,800 fresh routing calls completed; all 54
CLI/SDK controls matched. Each teardown removed 26 containers, 19 volumes, one
network and all runtime/index storage (43 indexes, including hidden/system ones).
A renamed nine-clip/six-query dataset also passed the flow and 18 controls.

| Path | Round 8e raw mAP@10 (%) | Run 1 | Run 2 | Run 3 | SDK mean total (ms) |
|---|---:|---:|---:|---:|---:|
| embed-only | 60.4946 | 60.7753 | 60.7875 | 60.7875 | 45.79–46.15 |
| attribute-only | 40.2535 | 40.7864 | 38.9979 | 40.2268 | 369.09–377.10 |
| live-routed | 65.4881 | 63.5357 | 62.4814 | 63.2591 | 436.50–438.53 |

These are lifecycle passes, not exact accuracy parity or best-accuracy claims.
Fresh CV coverage and routes varied. Live accuracy remains below historical
Round 8e; its critic/CLI timings and replayed routes prevent a matched latency
speedup claim. The committed [person-state evidence](skills/benchmarking/benchmark-video-search/references/person-state-evidence.md)
describes the prompt experiment's cohort and limitations.

The separate RRF and v22 matched-study artifacts are not included in this commit.

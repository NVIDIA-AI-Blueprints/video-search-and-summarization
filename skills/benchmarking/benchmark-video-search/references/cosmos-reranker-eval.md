# Evaluate the Cosmos 3 Nano reranker

`scripts/rerank_raw_clip_eval.py` adds an optional second stage to a finished
`run_raw_clip_eval.py` run. It reranks each query's saved hits with the Cosmos
3 Nano (CR3) reranker from `tools/cosmos-reranker` and scores the original and
reranked orders side by side. The VSS search product does not change. This
reranker step does not contact the deployment or repeat retrieval, so
every comparison uses the same stage-1 hits.

It works for every mode the run evaluated: `embed-only`, `attribute-only` and
`live-routed`. Results are also split by the route each query took (`embed`,
`attribute` or `fusion`).

## What the reranker sees

Each hit is scored as a video window: the hit's `start_time`–`end_time`,
converted to seconds within its clip file through the ingestion manifest. Use
`--window-pad-s` to add context on both sides, clamped to the clip. The
reranker samples frames from the window:

- target frames per window = window duration × `--fps`. The sampler applies a
  minimum, the `--max-frames` cap, the available frames in the window, and even
  frame-count rounding. `--max-frames` is a cap, not an exact frame count; short
  windows can produce fewer frames.

The requested `--fps` controls sampling. The model is told the actual rate of
the sampled frames for each candidate group, so the timestamps account for the
minimum, cap, available frames, and even rounding. This uses vLLM 0.23's media
options; if a different vLLM version does not accept the update, the script
stops.

Attribute (PAS) hits are scored the same way, as the object's time span. The
bundled fine-tuned checkpoint was trained on person crop images, not video, so
compare it with zero-shot before relying on it for PAS.

The prompt uses the user's original query. `--query-text request` uses the text
the stage-1 request searched instead. For `person-state-photo` attribute
requests, that text has the "A photo of" prefix.

## Install

Use a GPU host with Python 3.12. Install the reranker evaluation environment
once from the repository root:

```bash
python3.12 -m venv tools/cosmos-reranker/.venv-eval
tools/cosmos-reranker/.venv-eval/bin/python -m pip install -e 'tools/cosmos-reranker[eval]'
```

vLLM defaults to using 85% of visible GPU memory. Run the reranker on a free GPU
or stop the VSS deployment first; `--gpu-memory-utilization` can lower its
allocation when sharing is necessary.

For the fine-tuned checkpoint, also follow "Fine-tuned CR3 checkpoint" step 1
in `tools/cosmos-reranker/README.md`. That step assembles the bundled adapters
and writes the merged visual model. Then set `ADAPTER` and `MERGED_MODEL` as it
describes.

## Run

1. Run the stage-1 evaluation as usual. Its deepest depth must cover the
   rerank depth, so add it to the depths, for example `--top-k-values 10 20` for
   `--rerank-depth 20`. A shallower run reranks only the hits it has.
2. Rerank the run. One model load serves the whole `--fps` × `--max-frames`
   grid:

```bash
RERANK_PY=tools/cosmos-reranker/.venv-eval/bin/python
SCRIPTS=skills/benchmarking/benchmark-video-search/scripts

# Zero-shot, sweeping sampling rate and frame cap
"$RERANK_PY" "$SCRIPTS/rerank_raw_clip_eval.py" --run-dir "$WORK/results" \
  --dataset-root "$DATASET_ROOT" --ingestion-manifest "$WORK/ingestion.json" \
  --output-name zero-shot --rerank-depth 20 --fps 0.5 1 2 4 --max-frames 8 16 32

# Fine-tuned checkpoint, with per-query score fusion
"$RERANK_PY" "$SCRIPTS/rerank_raw_clip_eval.py" --run-dir "$WORK/results" \
  --dataset-root "$DATASET_ROOT" --ingestion-manifest "$WORK/ingestion.json" \
  --output-name fine-tuned --model-id "$MERGED_MODEL" \
  --lora-path "$ADAPTER/language" --rerank-depth 20 --fps 2 --max-frames 32 \
  --fusion-alphas 0.3 0.5

# Regenerate the HTML report; it adds a reranker comparison section
"$RERANK_PY" "$SCRIPTS/report_raw_clip_eval.py" --run-dir "$WORK/results"
```

`--reranker identity` loads no model and keeps the stage-1 order. Run it first
to check the inputs: `reranked` and every `fused-aX` must match `stage1-pool`.

The script refuses runs that did not complete. It also refuses ground truth or
a manifest whose hashes differ from the ones the run recorded, and clip videos
whose hashes differ from the manifest's `file_sha256`. It never overwrites an
existing `--output-name`.

## Read the results

Results go to `<run-dir>/rerank/<output-name>/`:

| File | Contents |
| --- | --- |
| `identity.json` | Model, adapter, depth, sampling grid, model options, package versions, input hashes |
| `<setting>/summary.json` | Per mode and depth: mAP by domain and route for each variant, reranker latency, frames per candidate, score failures |
| `<setting>/captures/<mode>/<query_id>.json` | Each candidate's window, stage-1 and reranker scores, frame count and readout, the new orders and clip rankings, and AP per variant |
| `comparison.json` | One row per setting, mode, depth and variant, with the overall change against `stage1-pool` |

Settings are named `fps-<fps>_frames-<max-frames>`. Variants:

| Variant | Meaning |
| --- | --- |
| `baseline` | The run's own capture at that depth: the numbers in its `summary.json` |
| `stage1-pool` | The top k unique clips of the deepest capture's hits, in stage-1 order: the reranker's input |
| `reranked` | Reranker score only over the top `--rerank-depth` hits; deeper hits keep stage-1 order |
| `fused-aX` | `X × z(stage-1) + (1 − X) × z(reranker)`, with z-scores within each query |

Compare `reranked` with `stage1-pool` to measure the reranker. At depths below
the deepest, `baseline` can differ from `stage1-pool` for two reasons. Retrieval
at k = 10 can return different top hits than at k = 20. And when one clip has
several hits, the top 10 clips of 20 hits can include clips that 10 hits did not
reach.

A window that cannot be decoded keeps its stage-1 rank after all scored hits.
It is counted in `score_failures`, and the error is saved with the candidate.
Model and configuration errors stop the run. Settings that finished stay in
`comparison.json`, and the report marks the output incomplete.
`windows_outside_clip` counts hits whose bounds fall outside their clip; they
are clamped to the clip edge before scoring.

Reranker latency is the wall time to sample, encode and score one query's
candidates, grouped into batches with matching sampled frame rates.
`--warmup-queries` (default 1) scores the first
queries once per setting without timing them. Latency excludes retrieval,
routing and queries without hits, so add it to the run's end-to-end latency for
a total.

The bundled `fusion_calibration.json` was fitted on SigLIP2 scores, so it does
not apply to VSS stage-1 scores. Fusion here uses per-query z-scores instead.
Fusion weights chosen on the evaluation cohort are not held-out results.

## Limits

- On the embed path one clip can have several hits, so the top 20 hits can
  cover fewer than 20 clips.
- Each query is scored independently, with candidates grouped by their sampled
  frame rate. Throughput is lower than the upstream evaluator's cross-query
  batching.
- `max-frames` × the per-frame token budget must fit `--max-model-len` (default
  32768).

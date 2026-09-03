---
name: vss-evaluate-caption-accuracy
description: Measure whether an RT-VLM configuration change altered caption quality — capture paired baseline and candidate captions for a set of videos, score both against a ground truth with an LLM judge, and emit an accuracy and processing-time table. Use when changing frame selection, decode, or model settings and you need evidence there is no accuracy regression.
license: Apache-2.0
metadata:
  version: "3.2.0"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint rt-vlm captions accuracy evaluation frame-selection"
---

## Purpose

Answer one question with evidence: **did this RT-VLM change make captions worse?**

A configuration change that saves processing time is only useful if caption quality
holds. This skill captures captions twice over the same videos — once with the
change (HYP) and once without (REF) — scores both against a ground truth using an
LLM judge, and reports accuracy delta alongside time saved.

## Prerequisites

Everything except the judge runs **inside the RT-VLM container**. The container
needs a GPU, the model on disk, and the `nvdsframeselector` DeepStream plugin
(shipped with DeepStream in the RT-VLM image).

| Requirement | Notes |
|---|---|
| Running RT-VLM container | Default name `rtvi_vlm-$USER`. Start it before running any stage |
| Model weights | Set `MODEL_PATH` in the deployment `.env`. Runs here used `Qwen3-VL-32B-Instruct`; any vLLM-compatible VLM works |
| `VLM_MODEL_TO_USE=vllm-compatible` | In the deployment `.env` |
| Source videos | A directory of `.mp4` files. Point `DEDUP_DIR` at it |
| `OPENAI_API_KEY` | In the deployment `.env`. Only needed for the `gt` stage (ground truth is gpt-4.1) |
| `claude` CLI on the **host** | The judge shells out to it. It is **not** installed in the container |

### Video paths and the scene map

`scripts/run_captioning.py` maps a filename to a short scene name in its `SCENES`
dict. Add your own videos there:

```python
SCENES = {
    "warehouse.mp4":               "warehouse",
    "GoPro5_10min_compressed.mp4": "new_warehouse",
    # "<your-file>.mp4":           "<scene-name>",
}
```

Scene names are what you pass on the command line; files are resolved inside
`DEDUP_DIR`. A scene whose file is missing is skipped with a warning.

### Model configuration

The skill does not choose a model — it inherits whatever the container is
configured with, and sets only the knobs under test. Both arms run the same
model so the comparison isolates the configuration change, not the checkpoint.

## Stages

They run in different places, so invoke them separately:

| Stage | Where | What |
|---|---|---|
| `gt` | container | Ground truth from gpt-4.1. Expensive — run once per video set and reuse via `GT_SRC` |
| `capture` | container | Paired REF + HYP per scene, back to back |
| `judge` | **host** | `claude-opus-4-8` scores REF and HYP against GT, per chunk |
| `table` | either | Accuracy + time-saved markdown, optionally against a baseline run |

```bash
# 0. ground truth (once per video set)
docker exec -e DESC=my-run -w /workspace rtvi_vlm-$USER \
  bash skills/benchmarking/vss-evaluate-caption-accuracy/scripts/run_eval.sh gt scene-a scene-b

# 1. capture — paired REF + HYP
docker exec -e DESC=my-run -w /workspace rtvi_vlm-$USER \
  bash skills/benchmarking/vss-evaluate-caption-accuracy/scripts/run_eval.sh capture scene-a scene-b

# 2. judge — on the host, not in the container
DESC=my-run bash skills/benchmarking/vss-evaluate-caption-accuracy/scripts/run_eval.sh judge scene-a scene-b

# 3. table
DESC=my-run bash skills/benchmarking/vss-evaluate-caption-accuracy/scripts/run_eval.sh table scene-a scene-b
```

Replace `-w /workspace` with the path the repository is mounted at in your
container.

## Knobs

| Var | Default | Meaning |
|---|---|---|
| `DESC` | `eval-<date>` | Run name. Everything lands in `results/<DESC>/` |
| `VSS_REPO_DIR` | `/workspace` | Path the repository is mounted at **inside** the container |
| `RTVI_CONTAINER` | `rtvi_vlm-$USER` | Container name to exec into |
| `MODEL_PATH` | unset | Pin a checkpoint for both arms. Unset inherits the container's own `.env`. When set, it is also used as `RTVI_MODEL_PATH_ALLOWLIST`, which `VLM_TRUST_REMOTE_CODE=true` requires |
| `DEDUP_DIR` | `<repo>/videos` | Directory holding the source videos |
| `SFC` | unset | `NVDS_FSELECT_STATIC_FRAME_COUNT` — frames emitted for a chunk classified STATIC. Unset leaves the plugin default |
| `GT_SRC` | `$DESC` | Run to copy `gt.txt` from, so one ground truth serves many runs |
| `BASELINE` | none | Run to diff against in `table` |
| `RESULTS_ROOT` | `<skill>/results` | Where run folders live |
| `HYP_VER` | `v1` | Which `hyp_<desc>_vN.txt` to judge |
| `MAX_WORKERS` | `32` | Judge concurrency |

## Why REF and HYP are captured paired

REF caption generation is nondeterministic run to run. Judging HYP against a REF
captured in a different session moved per-scene deltas by up to 0.05 — larger than
most effects being measured. `capture` therefore runs HYP and REF back to back per
scene in one session. Only GT is reused, because it is expensive and comes from a
different model.

## Reading the output

`table` writes `results/<DESC>/summary.md`: accuracy and processing time per scene,
LLM-judge entity/event F1 detail, and — with `BASELINE` — the incremental effect
versus that run. Totals are chunk-weighted, so a 60-chunk scene does not carry the
same weight as a 14-chunk one.

Two cautions:

- **Check the noise floor first.** Repeat runs of an identical configuration have
  spanned ~0.01 on a single scene, and the REF/HYP delta has changed sign between
  them. A delta smaller than that is "unchanged", not "improved". If a result
  matters, repeat it.
- **A flat combined score can hide offsetting axes.** `combined_score_macro_0_1`
  averages entity, event, critical-event and interaction F1. Interaction F1 is often
  a handful of samples and carries little signal, so it can mask a real entity-F1
  move. Read the F1 detail block, not just the combined column.

## Verifying that a change was behaviour-neutral

Frame-level provenance is stronger evidence than matching totals. When frame
selection is active the plugin logs one line per chunk:

```bash
grep -oE 'EOS OF-only -> [0-9]+' results/<DESC>/server_logs/hyp_<scene>.log \
  | grep -oE '[0-9]+$' | tail -n <chunks> | sort -n | uniq -c
```

Identical per-chunk frame counts across two runs mean the selector chose the same
frames. Matching chunk counts alone do not. The first values belong to pipeline
warmup rather than the scene, so take the trailing `<chunks>` entries.

## Contents

- `scripts/run_eval.sh` — the four stages
- `scripts/run_captioning.py` — capture engine (gt / ref / hyp)
- `scripts/multi_judge.py` — judge engine; uses the local `claude` CLI, so no
  `ANTHROPIC_API_KEY` is needed
- `scripts/score.py` — per-chunk scoring into `summary.csv`
- `scripts/aggregate_table.py` — the report

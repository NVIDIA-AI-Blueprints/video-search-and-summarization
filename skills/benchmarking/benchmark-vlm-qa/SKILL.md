---
name: benchmark-vlm-qa
description: Benchmark video Q&A accuracy and latency of a deployed RT-VLM (Cosmos Reason 3) via vss vlm run, using questions and videos from the DSS vss-devx-base dataset. Replaces the deprecated nat eval / vss-agent QA path. Not for tool-calling or trajectory evaluation, and not for LVS summarization throughput.
license: Apache-2.0
metadata:
  version: "1.0.0"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint performance benchmarking vlm qa"
---

# Benchmark video Q&A via `vss vlm`

Measure **accuracy** (LLM-as-judge vs ground truth) and **latency** of end-to-end
video question answering by calling **`vss vlm run`** against a deployed Cosmos
Reason 3 RT-VLM. Questions and clips come from DSS dataset **`vss-devx-base`**
(`nvdataset`).

This replaces `docker exec vss-agent nat eval` for the QA slice. It does **not**
score tool-calling or trajectories.

## When to use

- The user asks to benchmark / evaluate VLM video Q&A after vss-agent / NAT eval
  was removed.
- The user wants latency and answer accuracy on `vss-devx-base`.

## When not to use

- Tool-calling or trajectory evaluation — out of scope.
- LVS summarization throughput — use `benchmark-video-summarization`.
- Ad-hoc single questions — use `/vss-ask-video`.

## Prerequisites

- A VSS stack with RT-VLM serving Cosmos Reason 3, and `vss configure` already run
  so `vss configure check` lists `rt_vlm` as `ok`.
- `uv` and this checkout (CLI via `uv run --project services/agent --no-dev --extra cli vss`).
- DSS access: `NGC_API_KEY`, plus `NVDATASET_TENANTID=0573334707593577` and
  `NVDATASET_GROUPID=vss-bp-team` (defaults in the script; dataset owner: Jiayi Ni).
- An OpenAI-compatible judge LLM: `EVAL_LLM_JUDGE_BASE_URL` and `EVAL_LLM_JUDGE_NAME`
  (typically the same NIM as the profile LLM). Skip with `--skip-judge` for latency only.

Bootstrap is in the repo-root [AGENTS.md](../../../AGENTS.md). Do not construct
RT-VLM URLs; `vss vlm run` reads the recorded config.

## Run

```bash
export NGC_API_KEY=<key>
export EVAL_LLM_JUDGE_BASE_URL="${LLM_BASE_URL}"   # OpenAI-compat origin, e.g. http://127.0.0.1:8000
export EVAL_LLM_JUDGE_NAME="${LLM_NAME}"

# Optional: already-extracted dataset
# export VSS_EVAL_DATASET=/path/to/vss-devx-base

<repo>/deploy/docker/developer-profiles/dev-profile-base/eval/run_vlm_qa_benchmark.sh
```

Useful flags (forwarded to `benchmark_vlm_qa.py`):

| Flag | Purpose |
|---|---|
| `--dry-run` | Resolve QA items and video files; no VLM calls |
| `--limit N` | First N QA items (smoke) |
| `--skip-judge` | Latency only |
| `--skip-download` | Use an already-downloaded `vss-devx-base` |
| `--timeout SEC` | Passed through as `vss vlm run --timeout` (default 300) |
| `--num-frames N` | Frame budget (default 20, matching the old RT-VLM agent config) |
| `--model ID` | Override the RT-VLM model `vss configure` recorded |

Outputs under `<dataset>/../../results/vlm_qa/` (or `--output-dir`):

- `summary.json` — mean accuracy, latency mean / p50 / p90 / p95 / p99
- `qa_evaluator_output.json` — per-item judge scores (same shape as NAT QA output)
- `latency_summary.json` — per-item wall-clock around `vss vlm run`
- `workflow_output.json` — raw answers
- `summary.csv`

## Rules

- Drive the VLM only through `vss vlm run`. Never `POST /generate` or hand-built
  `/v1/chat/completions`.
- Do not wrap `vss` in extra retries or timeouts; `--timeout` is the bound.
- Filter to `evaluation_method` containing `qa` with a text `ground_truth`.
  Report items and trajectory-only items are skipped.

Implementation: [`deploy/docker/developer-profiles/dev-profile-base/eval/benchmark_vlm_qa.py`](../../../deploy/docker/developer-profiles/dev-profile-base/eval/benchmark_vlm_qa.py).
Dataset download contract: [`README_eval.md`](../../../deploy/docker/developer-profiles/dev-profile-base/eval/README_eval.md).

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
  so `vss configure check` lists `rt_vlm` as `ok` and `vst` as `ok`.

  **Configure with a routable address, not `localhost`.** Clips are addressed as VIOS
  sensors so RT-VLM fetches them by URL; the URL VIOS mints is built from the
  configured origin. A loopback origin mints a loopback URL, which means nothing
  inside the RT-VLM container, so the CLI falls back to inlining the clip as base64
  and the VLM rejects anything large with `HTTP 422 ... content ... valid string`.
  `--base-url http://<host-ip>:7777` avoids that; `--inline-media` forces the old
  inline behaviour and is only safe for clips under ~10 MB.
- `uv` and this checkout (CLI via `uv run --project services/agent --no-dev --extra cli vss`).
- The `nvdataset` CLI. It is **not** on PyPI, and the index used by the old
  deep-search eval (`urm.nvidia.com/.../sw-ngc-data-platform-pypi`) returns 403.
  Install from the documented read-only index instead — no credentials needed:

  ```bash
  uv tool install --index https://artifactory.pdx.nvidia.com/artifactory/api/pypi/sw-ngc-data-platform-pypi-local/simple nvdataset
  ```

- DSS access, one of:
  - `NVDATASET_API_KEY` — a **Personal Key** from
    [org.ngc.nvidia.com/setup/personal-keys](https://org.ngc.nvidia.com/setup/personal-keys)
    scoped to the service `NVIDIA Dataset Service`, with the NGC org switched to the
    one owning the dataset. This is *not* the global NGC key used by the NGC CLI; a
    global key returns 403. `NGC_API_KEY` is accepted only for backward compatibility.
  - `nvdataset auth login` (Starfleet SSO), which needs no key. Add `--flow device`
    on a remote box with no browser. Group access requires membership in
    `ngc-datasetservice-viewer-<tenant>-<group>` (reader) or `...-user-...` (writer).

  Plus `NVDATASET_TENANTID=0573334707593577` and `NVDATASET_GROUPID=vss-bp-team`
  (defaults in the script; dataset owner: Jiayi Ni).
- An OpenAI-compatible judge LLM: `EVAL_LLM_JUDGE_BASE_URL` and `EVAL_LLM_JUDGE_NAME`.
  The old NAT eval judged with the Nemotron endpoint inside vss-agent; with vss-agent
  deprecated, prefer a GPT or Claude model from inference hub. Authenticate with
  `EVAL_LLM_JUDGE_API_KEY`. `NGC_API_KEY` is deliberately **not** sent to non-NVIDIA
  judge hosts — it is set for the dataset download and must not reach a third party.
  Skip with `--skip-judge` for latency only.

Bootstrap is in the repo-root [AGENTS.md](../../../AGENTS.md). Do not construct
RT-VLM URLs; `vss vlm run` reads the recorded config.

## Run

```bash
export NVDATASET_API_KEY=<personal-key>            # or: nvdataset auth login [--flow device]
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

- `summary.json` — mean accuracy, latency mean / p50 / p90 / p95 / p99, and the
  model the deployment reported serving, so a number is never left unattributable
- `qa_evaluator_output.json` — per-item judge scores (same shape as NAT QA output)
- `latency_summary.json` — per-item wall-clock around `vss vlm run`
- `workflow_output.json` — raw answers
- `summary.csv`

## Rules

- Drive the VLM only through `vss vlm run`. Never `POST /generate` or hand-built
  `/v1/chat/completions`.
- Do not wrap `vss` in retries. `--timeout` is the bound; the script adds only a
  hard kill 60 s past it, so a CLI that never returns cannot cost the whole run.
  A killed item is recorded as an error naming the watchdog, never as a low score.
- Items must declare `evaluation_method` containing `qa` and carry a text
  `ground_truth`. Report, trajectory-only, and unmarked items are skipped.

Implementation: [`deploy/docker/developer-profiles/dev-profile-base/eval/benchmark_vlm_qa.py`](../../../deploy/docker/developer-profiles/dev-profile-base/eval/benchmark_vlm_qa.py).
Dataset download contract: [`README_eval.md`](../../../deploy/docker/developer-profiles/dev-profile-base/eval/README_eval.md).

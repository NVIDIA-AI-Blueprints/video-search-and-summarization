---
name: benchmark-video-summarization
description: Benchmark a deployed LVS instance — set up test media, run single-file latency and burst-throughput tests, analyze GPU and latency metrics, and get configuration recommendations to improve performance.
license: Apache-2.0
metadata:
  version: "3.2.0"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint performance benchmarking lvs"
---
## Instructions

Follow the routing table and step-by-step workflow below. Execute each step in order on a first run. For repeat runs, go directly to the **Repeat Runs** section. Detailed reference material lives in `references/` and benchmark scripts live in `scripts/`.

> **⚠️ Shared GPU systems:** On a multi-user host, multiple LVS instances may be running on different ports. Always benchmark against the instance YOU deployed. Never assume port 38111 — always use the exact endpoint URL returned by `vss-deploy-profile` when you deployed LVS. If you did not deploy an LVS instance in this session, deploy one first before running benchmarks.

## Purpose

Measure the latency and throughput limits of a deployed LVS (Long Video Summarization) instance, identify GPU and pipeline bottlenecks, and suggest configuration changes that improve performance. Produces XLSX reports and JSON result files per scenario, plus a natural-language analysis with improvement recommendations.

**Do NOT use this skill for:**
- Deploying LVS — use the `vss-deploy-profile` skill with the `lvs` profile.
- Production monitoring or alerting — this is an offline benchmarking tool.
- Non-LVS VSS profiles (RTVI, search-only, etc.) — the `/files` API and `/summarize` endpoint are LVS-specific.

## Routing

| Situation | Action |
|---|---|
| User has not deployed LVS in this session | Deploy LVS with `vss-deploy-profile` (lvs profile) using a unique `COMPOSE_PROJECT_NAME` — note the exact endpoint URL it returns, then return here |
| `LVS_BACKEND` is not set | Ask the user for the endpoint URL of the LVS instance they deployed — do not guess or default |
| Results directory already exists with data and user asks to analyze only | Skip to **Step 6: Analyze Results** |
| First run or new hardware configuration | Run the full workflow Steps 0–7 |
| Repeat run, setup already complete | Use `run_benchmark.sh` (see **Repeat Runs** section) |

---

## Prerequisites

| Requirement | How to Check |
|---|---|
| LVS deployed by you via `vss-deploy-profile` (lvs profile) with a unique `COMPOSE_PROJECT_NAME` | `curl -sf ${LVS_BACKEND}/v1/ready` returns 200 |
| `LVS_BACKEND` set to your deployment's endpoint | `echo $LVS_BACKEND` (e.g. `http://localhost:38111`) |
| `LVS_CONTAINER_NAME` set to your LVS container name | `echo $LVS_CONTAINER_NAME` (e.g. `vss-lvs`) |
| `VIA_DEV_API=true` on YOUR LVS container | `docker inspect ${LVS_CONTAINER_NAME} --format '{{range .Config.Env}}{{println .}}{{end}}' \| grep VIA_DEV_API` |
| `NGC_API_KEY` set in shell | `echo $NGC_API_KEY` (non-empty) |
| `ngc` CLI installed | `ngc --version` |
| Python 3.10+ | `python3 --version` |
| `ffprobe` and `ffmpeg` installed | `ffprobe -version && ffmpeg -version` |
| Docker with Compose plugin | `docker compose version` |

---

## Deploy LVS for Benchmarking

Before benchmarking, deploy a fresh LVS instance using `vss-deploy-profile`. On shared systems, always use a unique `COMPOSE_PROJECT_NAME` so your containers and volumes are isolated from other users.

```bash
# Choose a unique project name (e.g. your username)
export COMPOSE_PROJECT_NAME="lvs-bench-$(whoami)"

cd <repo>/deploy/docker

# REQUIRED FOR BENCHMARKING: the benchmark uploads videos via the LVS dev /files
# route, which is gated by VIA_DEV_API (default false; POST /files returns 404 when
# off). The lvs-server container loads its environment from
# services/video-summarization/.env (the compose `env_file:`), so VIA_DEV_API must
# be set THERE — putting it in any other env file will NOT reach the container.
# Set it before deploying:
grep -q '^VIA_DEV_API=' services/video-summarization/.env \
  && sed -i 's/^VIA_DEV_API=.*/VIA_DEV_API=true/' services/video-summarization/.env \
  || echo 'VIA_DEV_API=true' >> services/video-summarization/.env

# Deploy LVS on your designated GPUs
./scripts/dev-profile.sh up \
  --profile lvs \
  --hardware-profile RTXPRO6000BW \
  --llm-device-id <LLM_GPU> \
  --vlm-device-id <VLM_GPU>

# If LVS was already running, re-run the deploy command above so compose
# recreates the container with the new env (a plain `docker restart` does
# NOT re-read env_file changes).
```

Note the LVS endpoint (`http://<HOST_IP>:38111`) and container name: the compose file sets it statically to `vss-lvs` (verify with `docker ps --filter name=lvs`). Set these before continuing:
```bash
export LVS_BACKEND=http://localhost:38111
export LVS_CONTAINER_NAME=vss-lvs
export VLM_GPUS=<VLM_GPU>
export LLM_GPUS=<LLM_GPU>
```

> **Model downloads:** First deployment downloads LLM (~20 GB) and VLM (~17 GB) model weights. This takes 20–40 minutes depending on network speed. Subsequent deployments reuse the volumes created under your `COMPOSE_PROJECT_NAME` and start in minutes.

---

## Step 0: Pre-flight Check

**On shared systems another tenant may hold port 38111 — so the benchmark must verify it is hitting YOUR instance and YOUR GPUs, not someone else's.** `preflight.sh` does this (and fails fast if not).

Set your deployment's values, then run the pre-flight check:
```bash
export LVS_BACKEND=http://localhost:38111                          # YOUR LVS /summarize endpoint
export LVS_CONTAINER_NAME=vss-lvs                                   # YOUR LVS container (see deploy step)
export VLM_GPUS=<VLM_GPU>                                           # GPU(s) your VLM uses
export LLM_GPUS=<LLM_GPU>                                           # GPU(s) your LLM uses

./scripts/preflight.sh
```

`preflight.sh` exits non-zero unless **all** of the following hold:
- the config parses and `vlm_gpus`/`llm_gpus` are valid GPU ids within the host's GPU range;
- `LVS_BACKEND` is reachable (`/v1/ready` → 200) and the dev `/files` route is enabled (not 404);
- **your `LVS_CONTAINER_NAME` actually owns the backend port** — its server bound successfully (no `address already in use` in its logs) and no other container publishes that port;
- **the configured `VLM_GPUS`/`LLM_GPUS` are reserved by your LVS's VLM/LLM containers** — so you can't silently benchmark idle GPUs or another tenant's instance (the exact failure this guards against).

`run_benchmark.sh` runs `preflight.sh` automatically before every run; you can also run it standalone (above) any time.

If `/files` returns 404, the dev route is off — enable `VIA_DEV_API=true` on your LVS (see the deploy step) before continuing.

---

## Step 1: Download Test Videos

The benchmark uses warehouse surveillance videos from the VSS sample dataset, hosted in NGC. `scripts/fetch-videos.sh` downloads the package and places a curated set — `warehouse_4min.mp4`, `warehouse_5min.mp4`, `warehouse_10min.mp4` — into `<VSS_BENCHMARK_DATA_DIR>/videos/`, the exact filenames the default `scripts/config.yaml` references. It requires the `ngc` CLI and an NGC API key, and prints install/auth instructions if either is missing.

```bash
export VSS_BENCHMARK_DATA_DIR=${VSS_BENCHMARK_DATA_DIR:-$HOME/vss-benchmark-data}

# Idempotent; add FORCE=1 to re-fetch, or pass a package version (default 3.2.0)
./scripts/fetch-videos.sh

# Probe video durations to verify what was downloaded
find "${VSS_BENCHMARK_DATA_DIR}/videos" -name "*.mp4" | while read f; do
  dur=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null | cut -d. -f1)
  echo "$(basename $f): ${dur}s"
done
```

---

## Step 2: Generate Test Clips (if needed)

Normally Step 1 provides real 5- and 10-minute clips and this step can be skipped. Use it only if a curated clip is missing (e.g. the NGC package layout changed) or you need custom durations: loop an available source video into the filenames the default `scripts/config.yaml` references — `warehouse_5min.mp4` (300s) and `warehouse_10min.mp4` (600s).

```bash
# Find a source video to loop from
SOURCE=$(find "${VSS_BENCHMARK_DATA_DIR}" -name "*.mp4" | head -1)
echo "Using source: ${SOURCE}"

# Create a videos/ subdirectory where the media server expects files
mkdir -p "${VSS_BENCHMARK_DATA_DIR}/videos"

for spec in warehouse_5min:300 warehouse_10min:600; do
  NAME="${spec%%:*}"; DURATION="${spec##*:}"
  OUT="${VSS_BENCHMARK_DATA_DIR}/videos/${NAME}.mp4"
  [ -f "$OUT" ] && echo "Already exists: ${OUT}" && continue
  ffmpeg -stream_loop -1 -i "${SOURCE}" -t ${DURATION} -c copy "${OUT}" -y -loglevel error
  echo "Created: ${OUT}"
done

# Verify clips
find "${VSS_BENCHMARK_DATA_DIR}/videos" -name "*.mp4" | while read f; do
  dur=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null | cut -d. -f1)
  echo "$(basename $f): ${dur}s"
done
```

---

## Step 3: Start Media Server

The media server serves test videos over HTTP so the LVS `/files` endpoint can download them by URL.

```bash
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Start the media server (nginx:alpine)
cd "${SKILL_DIR}"
docker compose -f scripts/media-server.yaml up -d
sleep 2

# Verify health
curl -sf "http://localhost:8888/health" && echo "Media server ready" || echo "Media server not ready"

# List available videos (JSON directory listing)
curl -s "http://localhost:8888/videos/" | python3 -m json.tool 2>/dev/null || \
  find "${VSS_BENCHMARK_DATA_DIR}/videos" -name "*.mp4" -exec basename {} \; | sed 's|^|http://localhost:8888/videos/|'
```

Note the filenames returned — you will need them in the next step to update `config.yaml`.

---

## Step 4: Configure

Edit `scripts/config.yaml` with the actual video filenames discovered in Step 3:

1. **Update video URLs** — In both `single_file_test` and `file_burst_test` sections, replace `HOST_IP` with your host's LAN IP (`hostname -I | awk '{print $1}'` — NOT localhost; LVS downloads videos from inside its container) and make sure the filenames match those served by the media server (e.g., `http://<HOST_IP>:8888/videos/warehouse_5min.mp4`).

2. **Set GPU assignments** — Update `vlm_gpus` and `llm_gpus` to match your LVS deployment. Check YOUR container:
```bash
docker inspect "${LVS_CONTAINER_NAME}" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E "VLM_GPUS|LLM_GPUS|CUDA_VISIBLE"
```

3. **Adjust chunk sizes** — The default `chunk_sizes: [10, 30]` tests both 10-second and 30-second chunking. Larger chunks reduce API call overhead but increase per-chunk latency.

4. **Adjust concurrency levels** — The default `concurrency_levels: [1, 2, 4, 8]` for file_burst. Remove levels that exceed your hardware's memory capacity.

---

## Step 5: Run Benchmark

```bash
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SKILL_DIR}/scripts"

# Create Python virtual environment if it doesn't exist
[ ! -d "vss-bench-env" ] && python3 -m venv vss-bench-env

# Activate venv and install requirements
source vss-bench-env/bin/activate
pip install -r requirements.txt -q

# LVS_BACKEND must already be set to YOUR deployment's endpoint
if [ -z "${LVS_BACKEND}" ]; then
  echo "ERROR: LVS_BACKEND is not set. Set it to your own LVS endpoint before running."
  exit 1
fi
export VIA_BACKEND="${LVS_BACKEND}"
export VIA_VLM_GPUS="${VLM_GPUS:?ERROR: VLM_GPUS must be set (e.g. export VLM_GPUS=6)}"
export VIA_LLM_GPUS="${LLM_GPUS:?ERROR: LLM_GPUS must be set (e.g. export LLM_GPUS=7)}"

# Run single_file scenario
python vss_perf_benchmark.py --config config.yaml --scenario single_file_test

# Run file_burst scenario (can be run separately or together)
python vss_perf_benchmark.py --config config.yaml --scenario file_burst_test
```

The benchmark creates an output directory (default: `vss-perf-report/`) with per-scenario subdirectories. Each scenario run generates an XLSX report and `execution_summary.json`.

**Note:** The output directory must not exist or must be empty before each run. Move or rename previous results before re-running:
```bash
mv vss-perf-report vss-perf-report-$(date +%Y%m%d-%H%M%S)
```

---

## Step 6: Analyze Results

```bash
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${SKILL_DIR}/vss-perf-report"

# Show top-level execution summary for single_file
echo "=== single_file_test execution summary ==="
cat "${OUTPUT_DIR}/single_file_test/execution_summary.json" | python3 -m json.tool

# Show per-test-case summaries
find "${OUTPUT_DIR}" -name "test_case_summary.json" | while read f; do
  echo ""
  echo "=== $(dirname $f | xargs basename) ==="
  cat "$f" | python3 -m json.tool
done

# List generated XLSX reports
find "${OUTPUT_DIR}" -name "*.xlsx" | while read f; do
  echo "Report: $f"
done
```

Read the JSON result files and extract the following key metrics for analysis:

- **E2E latency** (`e2e_latency` in seconds): total time from request to response
- **VLM pipeline latency** (`vlm_pipeline_latency`): time spent in the vision model pipeline
- **VLM pipeline %**: `vlm_pipeline_latency / e2e_latency * 100`
- **CA-RAG latency** (`ca_rag_latency`): context-aware RAG inference time
- **VLM GPU utilization mean** (`vlm_gpu_usage_mean`): GPU compute utilization % for VLM
- **LLM GPU utilization mean** (`llm_gpu_usage_mean`): GPU compute utilization % for LLM
- **GPU memory mean** (`vlm_gpu_memory_mean`, `llm_gpu_memory_mean`): memory pressure %
- **File burst throughput** (`throughput_files_per_second`): concurrent files processed per second
- **Optimal concurrency** (`optimal_target_concurrency.estimated_concurrency`): the concurrency level that meets `target_latency_seconds`

---

## Step 7: Suggest Improvements

After analyzing the results, present a findings table and recommendations based on the following rules:

| Observation | Likely Cause | Recommended Action |
|---|---|---|
| VLM GPU utilization mean < 70% | VLM is under-utilized — chunks too small or few frames | Increase `chunk_duration` (e.g., 10 → 30) or `num_frames_per_chunk` (e.g., 20 → 40) |
| VLM pipeline % > 70% of E2E latency | VLM processing dominates — resolution too high | Reduce `vlm_input_width`/`vlm_input_height` (e.g., 1312x736 → 896x504 for ~4k tokens) |
| GPU memory mean > 85% | Near out-of-memory — risk of OOM at higher load | Reduce batch size or lower `num_frames_per_chunk`; avoid higher concurrency levels |
| File burst throughput plateaus between concurrency levels | Hardware is saturated — optimal concurrency found | The concurrency level just before plateau is the recommended operating point |
| High iteration variance (std% > 20%) | Thermal throttling or memory pressure between runs | Allow longer cooldown between iterations; check GPU temperatures |
| CA-RAG latency > 20% of E2E latency | Elasticsearch indexing or retrieval bottleneck | Check Elasticsearch container health; consider increasing heap size |
| LLM GPU utilization mean < 50% | LLM is waiting on VLM output | VLM is the bottleneck; optimize VLM settings first |

---

## Repeat Runs

Once setup is complete (Steps 1–4), use `run_benchmark.sh` for subsequent runs:

```bash
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SKILL_DIR}"

# Run single_file scenario
./scripts/run_benchmark.sh --scenario single_file_test

# Run file_burst scenario
./scripts/run_benchmark.sh --scenario file_burst_test

# Run with a versioned output directory
./scripts/run_benchmark.sh --scenario single_file_test --output-dir vss-perf-report-v2

# Run all scenarios in config.yaml
./scripts/run_benchmark.sh

# Debug mode (verbose API payloads)
./scripts/run_benchmark.sh --scenario single_file_test --debug
```

`run_benchmark.sh` checks LVS readiness, starts the media server if needed, creates/reuses the Python venv, and passes all extra arguments directly to `vss_perf_benchmark.py`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `POST /files` returns 404 | `VIA_DEV_API` not set or set to `false` | Set `VIA_DEV_API=true` in `services/video-summarization/.env` (the lvs-server `env_file`) and recreate the LVS service |
| GPU metrics all zeros | GPU monitoring requires local execution with NVML access | Run the benchmark on the GPU host directly (not via SSH without GPU passthrough) |
| Videos not found on media server | `VSS_BENCHMARK_DATA_DIR` not set correctly or `videos/` subdirectory missing | Check path with `ls ${VSS_BENCHMARK_DATA_DIR}/videos/`; ensure Step 2 clips were created |
| OOM on LVS container during file_burst | Too many concurrent requests consuming GPU memory | Reduce `concurrency_levels` in `config.yaml` (remove the highest levels) |
| ngc download fails | `NGC_API_KEY` not set or expired | Run `ngc config set` and verify the key at https://ngc.nvidia.com/setup/api-key |
| Output directory not empty error | Previous run's results exist | Move previous results: `mv vss-perf-report vss-perf-report-backup` |
| `ModuleNotFoundError` in benchmark | Python venv not activated or requirements not installed | Run `source scripts/vss-bench-env/bin/activate && pip install -r scripts/requirements.txt` |

---

## Cross-reference

- **vss-deploy-profile** — deploy LVS with the `lvs` profile before benchmarking
- **vss-summarize-video** — operational use of LVS for video summarization (not benchmarking)
- **Benchmark modes reference** — [`references/benchmark-modes.md`](references/benchmark-modes.md)
- **Analyzing results reference** — [`references/analyzing-results.md`](references/analyzing-results.md)

bump:3

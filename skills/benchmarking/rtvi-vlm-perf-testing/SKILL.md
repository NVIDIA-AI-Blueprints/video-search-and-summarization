---
name: rtvi-vlm-perf-testing
description: Plan, run, and diagnose reproducible RT-VLM GPU performance canaries and benchmarks. Use for fresh-container stream-capacity, semantic-isolation, latency, throughput, or regression experiments against an RTVI microservices checkout.
license: Apache-2.0
metadata:
  version: "3.3.0"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia rt-vlm performance benchmarking gpu canary"
---

# RTVI VLM Perf Testing

## Operating Rules

This skill orchestrates a separate RTVI microservices checkout; the VSS repository does not contain that benchmark implementation. Require the checkout path in the manifest or from the user, and confirm that it contains `perf/benchmark/` before using repository-specific benchmark commands. Read that checkout's contributor instructions before changing scripts, configs, docs, or benchmark behavior. Check `git status --short` before editing and preserve unrelated dirty worktree changes.

The committed planning and canary helpers require Python 3.9+. Executed canaries additionally require SSH, `tmux`, Docker with NVIDIA Container Toolkit, FFmpeg/FFprobe, and an NVIDIA GPU on the named remote host. Model images and protected artifacts may require the user's existing NGC or artifact-registry access; never request that credentials be stored in a manifest.

Do not start or stop services when the user only asks to inspect existing reports. Treat setup, teardown, and benchmark runs as side-effecting operations and state that you are about to run them.

When editing shell scripts, validate with `bash -n`. When changing commands, environment variables, Docker behavior, benchmark scenarios, or report workflows, update the matching repo docs under `CLAUDE.md`, `perf/benchmark/CLAUDE.md`, and relevant README or perf guide files.

Before launching a new experiment, freeze its identity, workload, metrics, scenarios, and isolated
paths using `references/performance-contract.md`. Validate and render the plan without executing it:

```bash
python3 skills/benchmarking/rtvi-vlm-perf-testing/scripts/perf_plan.py validate plan.json
python3 skills/benchmarking/rtvi-vlm-perf-testing/scripts/perf_plan.py render plan.json
```

The helper is standard-library only and never starts workloads. A launch, restart, remote edit, or
cleanup remains side effecting and requires the applicable authorization. Use a fresh pinned runtime
for every new or rerun experiment; never reuse a stale benchmark container.

For a pinned one-, two-, or four-stream remote GPU canary, use the committed executor instead of recreating a remote
runner. Dry-run is the default; `--execute` authorizes staging, runtime launch, benchmark, evidence
collection, and cleanup on the host named in the manifest:

```bash
python3 skills/benchmarking/rtvi-vlm-perf-testing/scripts/canary_executor.py \
  launch /absolute/path/to/canary-manifest.json
python3 skills/benchmarking/rtvi-vlm-perf-testing/scripts/canary_executor.py \
  launch /absolute/path/to/canary-manifest.json --execute
```

The launcher stages one immutable manifest and its three committed helpers, starts one durable
`tmux` job through a short SSH command, then watches terminal status through a separate connection.
It returns as soon as terminal JSON arrives rather than waiting for SSH EOF. A disconnect leaves the remote job running. The job
writes atomic `status.json`, append-only `events.jsonl`, command and service logs, runtime inspection,
results, cleanup evidence, and checksums below `<output_root>/<run_id>`. Read
`references/performance-contract.md` before creating its manifest. The executor accepts one, two, four, eight, or sixteen
`independent_live_stream` sources, plus thirty-two only with checksum-pinned object media. It starts one publisher per unique RTSP identity and requires fresh
per-stream measurements in every repetition. Use the ordinary plan/runner workflow for capacity or
larger multi-stream experiments.

For the two-, four-, eight-, sixteen-, or object-backed thirty-two-stream semantic-leakage gate, set top-level `"semantic_isolation": true`. The executor
uses deterministic solid colors through eight streams; at sixteen it pairs each color with SOLID or a
contrasting BORDER, sends concurrent caption requests bound to distinct stream IDs, and
requires three source-correct responses from every source before the performance canary starts. It preserves the
stream mapping and captions in `evidence/semantic-isolation.json`, drains those probe streams, and
fails closed on swapped, mixed, missing, or undeleted results.

To qualify real object fixtures through the same RTSP and model-preprocessing path, add `semantic_media`
with one safe label per stream, each containing an absolute `path` and exact `sha256`. The executor loops
each image through its own publisher, normalizes odd dimensions for H.264, and changes the forced-choice
probe to object recognition. Set `"qualification_only": true` to stop successfully after this gate; reuse
the unchanged checksum-pinned mapping with `false` for the subsequent performance run.
Thirty-two streams require this mapping; unqualified synthetic 32-source manifests are rejected.

Before a launch, remove already-present containers only when harness labels prove exact ownership:

```bash
python3 skills/benchmarking/rtvi-vlm-perf-testing/scripts/container_guard.py \
  --run-id <previous-run-id> --name <expected-container-name> --execute --json
```

Derive the Compose project mechanically from the run ID. Label auxiliary containers with
`com.nvidia.rtvi.harness.run_id=<run-id>`. The guard force-removes matching owned containers when
`--execute` is authorized, rejects an exact-name unlabeled or mismatched container, and leaves all
unrelated containers untouched. After cleanup, use a new run ID and new output/scratch/cache paths.
The guard requires Python 3.9+ and access to the Docker CLI/socket; it never invokes a shell.

## Environment Preflight

Use the setup help as the source of truth for current flags and environment variables:

```bash
bash perf/setup_perf_env.sh -h
```

Important defaults and requirements:

- `RTVI_IMAGE` is required for the RTVI VLM service image.
- `NGC_API_KEY` is required for NVIDIA container registry access.
- `NVIDIA_VISIBLE_DEVICES` selects GPUs for the stack and benchmark.
- `VLM_MODEL_PRESET=cr3-nano-reasoner-fp8` selects CR3 Nano Reasoner FP8 and fills `VLM_MODEL_TO_USE=cosmos-reason3` plus `MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix` unless those variables are explicitly exported. `VLM_MODEL_PRESET=cr3-nano-reasoner-nvfp4` selects the Blackwell-oriented CR3 Nano Reasoner NVFP4 path `ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix` with the same model key.
- `VST_LOCAL_PACKAGE` defaults to `perf/vst_package.tar.gz`; setup prefers it over cached or downloaded VST packages.
- `ARTIFACTORY_USER` and `ARTIFACTORY_TOKEN` are required when the VST package must be downloaded or when any benchmark videos are missing locally.
- Current VST image defaults are `nvcr.io/rxczgrvsg8nx/vst-dev/vst-streamprocessing:2.1.0-26.04.1`, `vst-sensor:2.1.0-26.04.1`, `vst-ingress:2.1.0-26.04.1`, and `nvstreamer:2.1.0-26.04.1`; override with `VST_IMAGE_TAG`, `VST_IMAGE_REGISTRY`, or per-image variables.
- Published RTVI VLM benchmarks must run with `VLLM_ENABLE_PREFIX_CACHING=false` and `VLLM_DISABLE_MM_PREPROCESSOR_CACHE=true`. `perf/setup_perf_env.sh` writes these values by default and ignores stale generated `.env.perf` values for those two keys unless they are explicitly exported in the shell for a non-standard experiment.
- `RTVI_ENABLE_GOP_DECODE_OPT` defaults to `true` and only affects file-based decoding. It attaches a GOP-aware probe that skips delta frames for GOPs without selected target timestamps; disable with `false`, `0`, `no`, or `off` when isolating file-decode behavior. It has no effect on live RTSP or when all frames are selected.
- Leave `VLLM_MM_ENCODER_ATTN_BACKEND` empty for the patched default vLLM path. Use `XFORMERS` only as an explicit experiment or emergency workaround if a current-vLLM image cannot be rebuilt with the Qwen2.5-VL vision attention patch.
- For OSL=100 comparisons, record `VLLM_IGNORE_EOS`. Use `true` only when the intended measurement requires fixed-length generation up to `max_tokens`; do not compare those runs directly with runs that allow EOS to stop generation early.
- EVS is off by default. `VIA_EVS_SESSION=true` enables EVS++ session mode with similarity-based pruning; `VLM_VIDEO_PRUNING_RATE` on its own enables fixed-rate pruning. Both are service-level settings read only by the RTVI VLM container, so changing them requires redeploying the stack. They cannot be set per benchmark request or per scenario.
- Set `VLM_VIDEO_PRUNING_RATE=0.5` whenever EVS++ is enabled. This is what activates pruning inside vLLM: the engine only enables its EVS path when `video_pruning_rate` is passed at engine init, and this variable is its only source. With `VIA_EVS_SESSION=true` but the rate unset, sessions are still created and the handler still carries its internal `0.5`, but the engine never prunes, so the run pays EVS++ overhead with none of the token reduction. Treat a session-mode run with unchanged prompt-token counts as this misconfiguration until proven otherwise.
- `VIA_EVS_SESSION=true` raises `ValueError` at startup on `NemotronH_Nano_Omni_Reasoning_V3` instead of falling back, so a failed service start after enabling EVS on that architecture is expected behavior rather than a deployment fault.
- EVS session generations need `VLLM_IGNORE_EOS=true` to run to `max_tokens`. Without it they stop at the natural EOS and report far fewer generated tokens than the non-EVS path, which reads as a throughput win when it is really a shorter OSL.
- Record `VIA_EVS_SESSION`, `VLLM_EVS_SIMILARITY_THRESHOLD`, and `VIA_EVS_TOKEN_BUDGET` in every report. The standard EVS++ perf configuration is `VLLM_EVS_SIMILARITY_THRESHOLD=0.4` and `VIA_EVS_TOKEN_BUDGET=1`; flag any run that deviates before comparing it against a baseline. All EVS variables are passed through by `compose.yaml` and `compose.perf.yaml`, and exported shell values override `.env.perf`; the full list with defaults is in `perf/benchmark/PERF_GUIDE.RTVI_VLM.md`.
- Record vLLM startup logs for model precision, `max_model_len`, processor/backend settings, and KV cache dtype. FP8 model weights do not by themselves prove that KV cache is FP8.
- Capture resolved runtime settings from startup logs or the live process, including preprocess workers, multimodal cache policy, CUDA graph or eager mode, GPU memory utilization, scheduler limits, queue bounds, and model profile. Requested environment variables alone are not runtime proof.
- Keep `RTVI_EMPTY_CUDA_CACHE_ON_RESULT=false` for perf runs. Enabling per-result `torch.cuda.empty_cache()` is diagnostic-only because it adds allocator synchronization on the hot path.
- BCD no-drop live-stream runs should use `RTVI_RTSP_LATENCY=300`, `RTVI_RTPJITTERBUFFER_DROP_ON_LATENCY=false`, `RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS=2`, `RTVI_DISABLE_LIVESTREAM_PREVIEW=true`, and `RTVI_ENABLE_LIVE_TIMESTAMP_FILTER=false` unless the user is intentionally testing those knobs.
- For utilization, prefer benchmark DCGM summaries or `nvidia-smi dmon`; avoid regular `nvidia-smi` polling in perf loops.
- Resolve container-to-host telemetry port mappings and fetch the exact configured DCGM or Prometheus URL before launch. Fail early when telemetry is required and unreachable; otherwise record the missing telemetry before the run instead of silently reporting zeroes.

Expected benchmark videos live under the configured `PERF_VIDEOS_DIR`. Missing videos are downloaded with Artifactory credentials during setup.

## Setup And Teardown

Run setup from the repo root:

```bash
bash perf/setup_perf_env.sh
```

The setup script prepares VST, nvstreamer, Redis, Prometheus, Grafana, and RTVI VLM; patches VST RTSP URLs into benchmark configs; and starts the monitoring stack. Use teardown after runs:

```bash
bash perf/teardown_perf_env.sh
```

If setup fails, first inspect missing environment variables, Docker login or image pull failures, VST package availability, missing video downloads, and RTSP endpoint substitutions in `perf/benchmark/rtvi_vlm_config_*.yaml`.

## Running Benchmarks

Activate the perf virtual environment when present:

```bash
source ~/rtvi-vlm-perf-env/bin/activate
```

Use the platform-specific config that matches the machine under test:

- H100: `perf/benchmark/rtvi_vlm_config_h100.yaml`
- BCD 3.2: `perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml`
- L40S: `perf/benchmark/rtvi_vlm_config_l40s.yaml`
- RTX PRO: `perf/benchmark/rtvi_vlm_config_rtx_pro.yaml`
- Jetson: `perf/benchmark/rtvi_vlm_config_jetson.yaml`
- Spark or local smoke: `perf/benchmark/rtvi_vlm_config_spark.yaml` or `rtvi_vlm_config_test.yaml`

Typical command shape:

```bash
python3 perf/benchmark/rtvi_perf_benchmark.py --config perf/benchmark/rtvi_vlm_config_h100.yaml --scenario max_live_streams_test_1_token_448
```

Before launching a named scenario, especially from copied instructions, validate
the exact names in the selected config:

```bash
python3 perf/benchmark/rtvi_perf_benchmark.py --config <config.yaml> --list-scenarios
```

Run the scenario families requested by the user or needed for comparison:

- Max streams: `max_live_streams_test_1_token`, `max_live_streams_test_100_token`, `max_live_streams_test_1_token_448`, and `max_live_streams_test_100_token_448`.
- Single and concurrent streams: scenario names containing `single_stream` or `concurrency`.
- File burst and latency: scenario names containing `file_burst` or `e2e_latency`.

Useful max-stream overrides include `--initial-stream-count`, `--add-stream-count`, `--binary-search-refinement`, and `--no-binary-search-refinement`. Useful concurrency and latency overrides include `--concurrency-levels`.

For BCD 3.2, run the named scenarios from `rtvi_vlm_bcd_3_2_config.yaml`:

- BCD 1 max live streams: `max_live_streams_test_1_token_2k`, `max_live_streams_test_100_token_2k`, `max_live_streams_test_1_token_4k`, `max_live_streams_test_100_token_4k`, `max_live_streams_test_1_token_8k`, and `max_live_streams_test_100_token_8k`.
- BCD 2 stream latency: `concurrency_test_1_token_2k`, `concurrency_test_100_token_2k`, `concurrency_test_1_token_4k`, `concurrency_test_100_token_4k`, `concurrency_test_1_token_8k`, and `concurrency_test_100_token_8k`.
- BCD 3 file throughput: `file_burst_1_token_2k`, `file_burst_100_token_2k`, `file_burst_1_token_4k`, `file_burst_100_token_4k`, `file_burst_1_token_8k`, and `file_burst_100_token_8k`.
- BCD 4 file latency: `e2e_latency_1_token_2k`, `e2e_latency_100_token_2k`, `e2e_latency_1_token_4k`, `e2e_latency_100_token_4k`, `e2e_latency_1_token_8k`, and `e2e_latency_100_token_8k`.

### Run Integrity Gates

- Before any 30min+ file-burst or target-latency run, first send a direct minimal video request with 1 frame and 1 output token; stop if it hangs, times out, leaves the file asset in use, or fails cleanup.
- Freeze the benchmark mode (`max-live`, `concurrent-live`, or `file-burst`), counted load unit, synchronized or staggered start policy, source identity policy, and session reuse policy. Do not compare stream counts across modes as equivalent capacity.
- For independent-stream claims, require the expected number of distinct stream assets and fresh-frame coverage for every asset. Reusing one RTSP URL measures subscriber capacity; multiple URLs or ports do not prove independent source capacity without coverage at the requested cardinality.
- Treat response error fields, empty or incomplete outputs, bounded-queue drops, server chunk errors, timeouts, and failed cleanup as failed work even when HTTP or the benchmark process reports success.
- For admission tests, record offered, admitted, active, completed, rejected, timed-out, and cleaned-up units separately. Admission is not sustainable capacity.
- Use a fresh service per scenario for memory or capacity comparisons. Zero API assets does not prove framework allocators, workers, or caches released cross-scenario state; reuse only when service reuse is part of the frozen experiment.
- Use bounded batched cleanup with one shared deadline. Require zero stream assets, file assets, active requests, and cleanup errors, plus GPU memory returning near the recorded idle baseline before promoting the next scenario.

For H100 BCD 3.2 full-suite reruns, seed max-live-stream probes near the last
clean H100 reference to reduce convergence time. The reference run
`rtvi-vlm-bcd32-full-sessionreset-rtsp-20260504T191256Z` found max streams of
160/115 at 2K, 78/58 at 4K, and 36/28 at 8K for OSL=1/100 respectively. Use
these conservative starts unless the hardware, model, RTSP source, or BCD
settings changed:

- `max_live_streams_test_1_token_2k`: `initial_stream_count=144`, `add_stream_count=4`
- `max_live_streams_test_100_token_2k`: `initial_stream_count=104`, `add_stream_count=4`
- `max_live_streams_test_1_token_4k`: `initial_stream_count=70`, `add_stream_count=2`
- `max_live_streams_test_100_token_4k`: `initial_stream_count=52`, `add_stream_count=2`
- `max_live_streams_test_1_token_8k`: `initial_stream_count=32`, `add_stream_count=1`
- `max_live_streams_test_100_token_8k`: `initial_stream_count=25`, `add_stream_count=1`

For BCD file-burst sweeps, record every concurrency level before judging the
scenario. A request failure at an overloaded high level, such as 128
concurrency, can make the whole test case report failed even when lower levels
are valid. Preserve the last clean concurrency level, its p95/p99 latency, GPU
mean, and the exact failure text so the report can distinguish throughput
limit from service crash.

For long full-suite BCD runs, create a timestamped temporary config that only changes `output_dir`, and tee logs under `/tmp/rtvi-bcd-runs/`. Monitor progress with a filtered summary instead of draining huge per-stream statistics:

```bash
rg "Stability check|Adding [0-9]+ stream|System degradation|UNSTABLE|Scenario|Error|FAILED|completed|Phase 2" /tmp/rtvi-bcd-runs/<run>.log | tail -n 40
```

Record the three run handles before walking away from a long benchmark:

- `CONFIG`: timestamped config under `/tmp/rtvi-bcd-runs/`
- `REPORT`: timestamped report directory under the repo root
- `LOG`: tee log under `/tmp/rtvi-bcd-runs/`

When monitoring an active long run, prefer `tail` or scenario-scoped `awk` over
polling the PTY session. If the log is quiet, check the log mtime and the
benchmark process before declaring a hang; max-stream windows can be separated
by 60 seconds or more, and freshness waits can intentionally run for several
minutes. If the mtime is stale beyond the configured stability or freshness
timeout, inspect RTVI/VST container logs and active stream count before
interrupting the benchmark.

For BCD `e2e_latency_*` file cases, the 60-minute videos can legitimately keep
the benchmark log quiet for many minutes, especially with OSL=100. Check the
per-video `request_timeout_seconds`, RTVI health, recent `/generate_captions`
or `/chat/completions` server logs, and server/vLLM worker activity before
calling it a hang. Short `nvidia-smi dmon` windows can show 0% SM/NVDEC during
CPU-side file decode or request scheduling; prefer the final DCGM summary for
the completed request. For live diagnosis, use a short-window tool such as
`top -b -d 2 -n 3 -p <pids>`; `ps %CPU` on long-lived RTVI/vLLM workers is a
lifetime average and can falsely suggest current CPU activity. If the file
upload has no paired generation response, the benchmark socket remains open,
short-window CPU is idle, and `dmon` is flat beyond the request timeout, treat
the request as a stuck server-side file path and preserve logs before restart.

If a live-stream run stops at `Cleaning up N active streams...`, inspect RTVI
server logs before calling it a benchmark hang. Current benchmark code should
prefer `DELETE /v1/streams/delete-batch` for cleanup; when that batch request
reaches the server it should complete in one drain-timeout window instead of
roughly `N * drain_timeout`. If logs show per-stream
`DELETE /v1/streams/delete/<id>` calls, or the active benchmark was launched
before the batch-cleanup patch, treat it as old sequential cleanup behavior.
Look for `Drain timed out after ...; forcing completion` followed by DELETE 200
responses. That indicates teardown is progressing slowly, not that latency
measurement is still running.

If source or config changes are committed while a benchmark is already running,
call out that the active process is using the code and generated config from
launch time. Let the active run finish if it is producing valid data; use a new
setup or benchmark launch for measurements that must include the latest changes.

For RTVI VLM scenarios, use `vlm_api_mode: chat_completions` to benchmark
`/v1/chat/completions` instead of `/v1/generate_captions`. Configure
`chat_completions_params` for chat-specific generation overrides and
`chat_messages` for explicit OpenAI-format system/user/assistant messages.
The default remains `generate_captions`, and existing `generate_captions_params`
are reused when chat-specific params are omitted.

For EVS++ runs, redeploy to change settings because the benchmark CLI cannot change
them.

Run the non-EVS baseline as its own deployment, and clear the EVS variables
explicitly instead of relying on shell state. Compose interpolates
`${VIA_EVS_SESSION:-}`, so an export left over from an earlier EVS run silently turns
the baseline into an EVS run. Keep `VLLM_IGNORE_EOS=true` so baseline OSL still matches
the EVS run:

```bash
cd docker/rtvi_vlm/deploy
unset VIA_EVS_SESSION VLM_VIDEO_PRUNING_RATE VLLM_EVS_SIMILARITY_THRESHOLD VIA_EVS_TOKEN_BUDGET
export VLLM_IGNORE_EOS=true
docker compose -f compose.perf.yaml --env-file .env.perf down
docker compose -f compose.perf.yaml --env-file .env.perf up -d
```

`unset` only clears the shell. `--env-file` also feeds interpolation, so confirm
`.env.perf` carries no EVS keys; `setup_perf_env.sh` does not write them, but a
hand-edited file can. Verify the baseline container has no EVS settings before
benchmarking it:

```bash
docker compose -f compose.perf.yaml --env-file .env.perf exec rtvi-server env | rg 'VIA_EVS|PRUNING' || echo "no EVS vars set"
```

Then deploy the EVS++ run with this configuration:

```bash
cd docker/rtvi_vlm/deploy
export VIA_EVS_SESSION=true VLM_VIDEO_PRUNING_RATE=0.5 VLLM_IGNORE_EOS=true
export VLLM_EVS_SIMILARITY_THRESHOLD=0.4
export VIA_EVS_TOKEN_BUDGET=1
docker compose -f compose.perf.yaml --env-file .env.perf down
docker compose -f compose.perf.yaml --env-file .env.perf up -d
```

`VIA_EVS_TOKEN_BUDGET=1` is the default; it forces generation per clip instead of
accumulating visual tokens across clips, holding caption counts equal to the
baseline so latency and throughput compare directly.

Re-run identical scenario names for the baseline and the EVS++ run so report rows
align. All benchmark modes exercise EVS++ when session mode is on, because routing
depends on `VIA_EVS_SESSION` alone; the file-based modes go through the session path
just as the live-stream modes do.

## Perf Optimization Playbook

When asked to improve throughput or isolate a regression, keep the measurement baseline and optimization experiment separate. Baseline first with cache-disabled benchmark settings, BCD no-drop RTSP settings, a timestamped config, a timestamped report directory, and a tee log.

Look for avoidable host synchronization and CPU/GPU copies before changing model behavior:

- Search hot paths for `.cpu()`, `.numpy()`, `.item()`, `torch.cuda.synchronize()`, `torch.cuda.empty_cache()`, blocking queue waits, per-frame logging, per-chunk subprocess calls, and per-request upload/delete work inside measured steady state.
- Prefer long-lived HTTP sessions, reusable uploaded file pools, reusable stream state, and benchmark-side summary logging. Do not add regular `nvidia-smi` polling to the measurement loop.
- If GPU utilization alternates between long lulls and high bursts, align the latency window with `nvidia-smi dmon` or DCGM samples before calling it under-saturation. Ten-second RTSP chunks can create decode and inference bursts even when the final aggregate GPU mean is high.

For CPU/GPU transfer reduction, use this roadmap:

- Short term: remove unnecessary hot-path synchronizations, keep `RTVI_EMPTY_CUDA_CACHE_ON_RESULT=false`, pre-upload file-burst media, and avoid copying tensors to CPU only for bookkeeping.
- Medium term: use a vLLM build with multimodal tensor IPC support, such as the tensor IPC work tracked around vLLM PR 32104, and enable it only for explicit experiments with `VLLM_MM_TENSOR_IPC=torch_shm` or the legacy `VLLM_MULTIMODAL_TENSOR_IPC=true`. Verify at vLLM startup that the engine argument is accepted; older vLLM builds silently keep the CPU-copy path when they ignore guarded args.
- Long term: implement model-specific GPU preprocessing so the path becomes NVDEC CUDA surface to CUDA tensor to GPU resize/normalize/patchify to vLLM multimodal processed tensors to vLLM tensor IPC. Treat this as model-specific work for Qwen/Cosmos processors; do not mix it into an apples-to-apples benchmark unless the user is explicitly testing that optimization.
- Tensor IPC and zero-copy transport can help BF16 and FP8 models by reducing movement and serialization overhead. The numerical dtype and memory benefit still depend on the model processor, KV cache dtype, and vLLM configuration, so record those details in the report notes.

## Reports And XLSX

Benchmark outputs usually land in `rtvi-vlm-perf-report` or a custom dated report directory. Inspect JSON before generating summaries: `execution_summary.json`, `max_live_streams_results.json`, `test_case_summary.json`, and scenario-specific result files.

Generate charts and XLSX from `perf/benchmark`:

```bash
cd perf/benchmark
python3 plot_perf_reports.py all --reports h100=./rtvi-vlm-perf-report --configs h100=rtvi_vlm_config_h100.yaml --output ./perf_charts
python3 generate_perf_xlsx.py --reports H100=./rtvi-vlm-perf-report --configs H100=rtvi_vlm_config_h100.yaml --charts ./perf_charts --output perf_report.xlsx --release "3.1 EA2"
```

Use `openpyxl` to inspect XLSX files when available. If it is unavailable, read workbook XML from the XLSX zip as a fallback. Do not commit generated report directories, XLSX files, or run-specific hardware outputs unless the user explicitly asks for an artifact to be checked in.

BCD XLSX rows should expose min, max, avg, p50, p75, p90, p95, and p99 latency where the report JSON records them. They should also expose per-stage chunk latency for decode, queue, VLM inference, server processing, and server E2E when request profiling is enabled. Check dropped chunks for max-stream rows and failed request/error-rate columns for file-burst and request-latency rows before calling a BCD run clean.

## Commit Hygiene

Before committing after `setup_perf_env.sh` or a live benchmark, explicitly check for generated artifacts and machine-specific config substitutions:

```bash
git status --short
git diff -- perf/benchmark/rtvi_vlm_config_*.yaml perf/benchmark/rtvi_vlm_bcd_3_2_config.yaml
```

Do not commit hard-coded lab RTSP URLs, local backend ports, generated report directories, memory logs, XLSX files, or hardware metric outputs. Keep `RTSP_STREAM_URL` placeholders in checked-in template configs unless the user explicitly requests a committed machine-specific config.

## Regression Analysis

When comparing reports, align results by platform, scenario, token budget, resolution, model, and stream source. Report max-stream deltas first, then latency percentiles, throughput, GPU memory, power, NVDEC utilization, and failure counts when available.

Flag likely non-code causes before attributing regressions: changed GPU count, driver or container versions, model changes, VST image tag drift, hard-coded RTSP endpoints, missing or different benchmark videos, stale generated report files, and monitor gaps.

For max-stream runs, check that `phase2_final_stable` and `phase2_unstable_ceiling` are coherent. If linear probing reaches the cap without instability, an old unstable ceiling can make the report look contradictory. For BCD max live streams, a stream count is only meaningful when fresh stream coverage meets the threshold, dropped chunks are zero, and p90/p95 latency stays below the 10 second chunk real-time limit. Leave `enable_latency_growth_instability_check` disabled for BCD no-drop measurements; enable it only for a diagnostic run where consecutive latency growth itself should fail the probe. Instantaneous GPU/NVDEC samples are bursty with 10 second RTSP chunks; use aggregate report metrics before concluding that GPU is or is not saturated.

When diagnosing inconsistent live-stream latency:

- `Fresh streams: 0/N` with 0 latency means the probe has no new responses yet, not a low-latency stable state. Inspect RTVI container logs, stream add success, backend URL, and whether the server is producing SSE events.
- `concurrent_live_streams` report stats now discard leading same-stream startup burst samples by default. These are queued 10 second RTSP chunks emitted within a short local time window after a delayed stream start; they are recorded in `latency_filter` and excluded from final avg/p90/p95/p99. Sustained high latencies that arrive at normal chunk cadence are still included and indicate queueing or saturation.
- Rising latency with 0 dropped chunks usually means the system is approaching inference or scheduling saturation.
- In BCD stream-latency scenarios such as `concurrency_test_*`, each concurrency level runs for a fixed duration. Do not judge from ramp-up samples alone; wait for the final p95/GPU/NVDEC summary. Sustained rising per-stream latency above the 10 second chunk budget at 64 or 128 streams indicates queueing or saturation even when stream creation had zero errors.
- BCD max-stream pass/fail is based on fresh coverage, zero dropped chunks, and p95 staying below the 10 second chunk real-time limit. Do not fail a BCD no-drop run only because the old moving-average latency-growth heuristic would have tripped.
- `GPU latest %` and `NVDEC latest %` are single burst samples. Use the final Prometheus/DCGM means for utilization claims, especially with 10 second RTSP chunks.
- A fresh Phase 2 linear-extension probe can legitimately pass after an incremental probe at the same count failed, because the incremental probe may include aged queues from the previous stream count. Do not skip fresh retests solely because the same count already failed in binary search.
- Drops or chunk-ID gaps make a BCD no-drop max-stream point unstable even if latency still looks acceptable.
- RTSP jitterbuffer settings are only meaningful if the service pipeline passes them to `rtpjitterbuffer`. If `RTVI_RTPJITTERBUFFER_FASTSTART_MIN_PACKETS` appears ineffective, inspect container logs or GST debug for the actual element properties.
- `Pipeline disposal timed out` and CUDA OOM after repeated probes usually point to teardown, buffer lifetime, or decoder cache handoff problems; inspect `video_file_frame_getter.py` and container logs.
- Long pauses after `Cleaning up N active streams...` often mean sequential stream deletion is waiting on RTVI live-stream drain timeouts. Confirm with RTVI logs before interrupting; a DELETE 200 every drain timeout means cleanup is still advancing.
- File-burst throughput should pre-upload reusable files so `/files` upload/delete is outside measured steady state.
- Compare caption counts between the baseline and the EVS++ run first. The standard configuration sets `VIA_EVS_TOKEN_BUDGET=1` specifically so counts match; if the EVS run emitted far fewer captions, budget accumulation or generation gating is still active, and the throughput numbers are not comparable rather than improved.
- Check generated token counts and caption counts before reporting any speedup that appears immediately after EVS is enabled. Raising `VLLM_EVS_SIMILARITY_THRESHOLD` prunes more frames, so caption coverage and quality need a sanity check alongside the perf delta.

When this skill itself changes, update both the repo copy under
`skills/benchmarking/rtvi-vlm-perf-testing/SKILL.md` and the installed Codex copy under
`~/.codex/skills/rtvi-vlm-perf-testing/SKILL.md` when that local copy exists.

After changing the plan or canary contract, run
`(cd skills/benchmarking/rtvi-vlm-perf-testing/scripts && python3 -m unittest -v test_perf_plan.py test_canary_executor.py && python3 -m py_compile perf_plan.py container_guard.py canary_executor.py)`.

Finish with the commands run, report paths inspected or generated, clear regression findings, and any validation that was skipped because it would require starting services or running long benchmarks.

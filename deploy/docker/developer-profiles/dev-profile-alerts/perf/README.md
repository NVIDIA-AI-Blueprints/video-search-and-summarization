# Alert Verification Perf

Performance tests for the **dev alerts verification Docker Compose profile**: `dev-profile-alerts` with `MODE=2d_cv`. The benchmark measures the whole profile end to end (VIOS, RTVI-CV, Behavior Analytics, Alert Bridge, RT-VLM), so it lives with the profile rather than under one service.

This folder follows the standard `perf/` layout from the *Microservice Perf Onboarding for Artifacts Promotion* guide, with the profile folder as the "service folder". The shared `benchmark-perf-test` Jenkins job runs it when `vss-alert-ms` is promoted, and when the profile itself is promoted if it is released as a compose artifact.

Real-time alerts (`MODE=2d_vlm`), Helm and the industry profiles are out of scope.

## What is measured

Alert verification runs on live RTSP streams, not on file uploads:

```
NVStreamer (looped video) -> VST -> RTVI-CV -> Behavior Analytics -> Kafka -> Alert Bridge -> RT-VLM -> ES
```

So scenarios sweep the **number of concurrent streams**. For each level, the benchmark:

1. registers NVStreamer streams as VST sensors until that many are active
2. waits for the pipeline to settle
3. measures for `measure_minutes`
4. reads the Alert Bridge Prometheus histograms and counters for that window

The PromQL comes from `services/alert/test/latency/prometheus_latency.py`, the same tool used for `docs/performance-alert-verification.mdx`.

Each level becomes one test case, `{benchmark_mode}_{video}_{N}streams`, with these metrics:

| Group | Metrics |
|---|---|
| `latency` | `e2e`, `upstream`, `kafka_lag`, `queue_wait`, `vst`, `vlm`, `worker`: avg / p50 / p90 / p99 seconds. `clipped_quantiles` lists histograms whose p99 reached the top bucket (E2E 120 s, VLM 30 s). |
| `throughput` | `verified_events`, `verified_per_min`, `verified_per_min_per_stream`, confirmed / rejected / after-dedup / dropped counts |
| `reliability` | `verification_failures`, `verification_failure_rate` |
| `gpu`, `cpu` | `cv_gpu_*` and `vlm_gpu_*` (GPU roles from the config entry), host CPU. Full host metrics come from the shared perf library. |

A test case's `success` means the level ran and verified at least one alert. Like the RTVI-VLM and VIOS perf adapters, the benchmark does not judge regressions itself. The dashboard compares each run with the baselines in `configs/vss_perf_alert_kpis.yaml` (`warning_percent` / `critical_percent`).

The benchmark exits with:

- `0` when every test case produced data
- `1` when some did not
- `2` when the run could not start or the upload failed

A result file is written in all three cases.

## Layout

```
deploy/docker/developer-profiles/dev-profile-alerts/perf/
  configs/
    perf-configs.yaml        # WHERE: hardware x deployment matrix
    config.yaml              # WHAT: global settings and test scenarios
    defaults.yaml            # fixed settings for comparable runs (verifier/DeepStream tuning, VLM checkpoints)
    vss_perf_alert_kpis.yaml # KPI definitions + baselines for the dashboard (standard VSS KPI format)
  benchmark/
    requirements.txt
    core/
      vss_perf_benchmark.py  # entry point the pipeline calls
      alert_metrics.py       # Prometheus window -> metrics
      streams.py             # NVStreamer discovery + VST sensor registration
      system_monitor.py      # role-mapped GPU + CPU sampling
      vss_perf_common.py     # shared VSS result schema, build_and_save, MinIO upload (copy of the RTVI-VLM module)
  scripts/                   # stage hooks, run in this order by the shared job
    pre_flight.sh            # checks, nvcr.io login, generate per-run files
    deploy.sh                # dev-profile.sh --dry-run -> compose up with the perf overlay
    post_deploy.sh           # wait for containers, /ready, Prometheus scraping
    teardown.sh              # collect logs, compose down (always runs)
    run_benchmark.sh         # all of the above + the benchmark, for local runs
    _common.sh
  setup/
    compose/
      alerts-verification-perf.yaml   # compose overlay (composePath)
      prometheus.yml                  # scrapes alert-bridge:9081
    scripts/
      prepare_runtime.py              # resolves a config entry, builds per-run files
  tests/expected_result.json          # sample result for format checks
```

## How the deployment works

The stack under test is the shipped profile. Nothing tracked in the checkout is edited.

1. **`pre_flight.sh`** runs `prepare_runtime.py prepare`, which writes these files under `PERF_WORK_DIR`:
   - `verifier-config.yml`: the profile's verifier `config.yml` with `defaults.yaml -> verifier_config_overrides` applied, including `include_latency_info: true` and `metrics.per_sensor_labels: true`
   - `deepstream-configs/`: the profile's DeepStream configs plus `low-latency-mode=1` and `set-rtp-protocol=7`
   - `videos/`: one hard link of the scenario video per stream, which NVStreamer serves as RTSP
   - `perf-runtime.env`: `MODE=2d_cv`, `NUM_SENSORS`, `MODEL_NAME_2D`, the VLM checkpoint for `vlmPrecision`, `PERF_ALERT_IMAGE` (from `ARTIFACT_REF`), and paths to the files above
2. **`deploy.sh`** runs `dev-profile.sh up -p alerts -m verification -H <hw> --dry-run`, so the profile's own logic writes `generated.env`. It then starts Compose with this layering:
   ```
   -f compose.yml -f <perf overlay>
   --env-file containers.env --env-file dev-profile-alerts/.env
   --env-file dev-profile-alerts/generated.env --env-file $PERF_WORK_DIR/perf-runtime.env
   ```
3. **The overlay** changes only what a measurement needs:
   - Alert Bridge Prometheus (`PROMETHEUS_METRICS_ENABLED`, port 9081) and a `vss-perf-prometheus` scraper on host port 9091
   - the promoted alert image
   - higher `nofile` limits
   - the DeepStream config mount swapped for the perf copy

These are the same tuning changes the alerts eval framework applies in `ci-vss-oss/eval/configs/bp_config_latency.yaml`, except they go into generated copies instead of edits to tracked files.

## Run it locally

You need a GPU machine with Docker, the NVIDIA Container Toolkit, `NGC_CLI_API_KEY`, and `pip install -r benchmark/requirements.txt`.

```bash
cd deploy/docker/developer-profiles/dev-profile-alerts/perf/scripts
export NGC_CLI_API_KEY=...                 # image + model pulls
export PERF_ALERT_VIDEO_PATH=/data/warehouse-ladder-1080p-10fps.mp4   # or let config.yaml's url download

# deploy, run quick_test, tear down; result JSON in ./vss-perf-results
./run_benchmark.sh -c spark-1-rtdetr-cr3-bf16 -s quick_test -d

# several scenarios on one deployment (sized for the largest stream count)
./run_benchmark.sh -c h100-2-cv1-vlm1-rtdetr-cr3-bf16 -s quick_test -s stream_sweep_test -d

# stack already up: benchmark only
./run_benchmark.sh -c spark-1-rtdetr-cr3-bf16 -s quick_test -k
```

Check the output format against the sample:

```bash
python3 benchmark/core/vss_perf_benchmark.py --validate tests/expected_result.json
python3 benchmark/core/vss_perf_benchmark.py --validate vss-perf-results/alert-verification_<config_id>_<ts>.json
```

Check that the KPI file converts to the dashboard format:

```bash
python3 ../../../../../services/vios/test/bdd_tests/tests/perf/vss_perf_kpi_export.py \
  configs/vss_perf_alert_kpis.yaml -o /tmp/alert-verification_kpis.json
```

Upload to MinIO with `-u`. Upload goes through `vss_perf_common`, which defaults to the perf dashboard's MinIO (`10.111.52.248:9000`, bucket `perf-results`). Override it with `MINIO_ENDPOINT`, `MINIO_BUCKET`, `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`. Results go under `alert-verification/`. Leave `-u` off for test runs so nothing reaches the dashboard.

## Stage hook inputs

The hooks read these from the environment. The shared job is expected to set them; `run_benchmark.sh` sets them for local runs.

| Variable | Meaning |
|---|---|
| `PERF_CONFIG_ID` | `perf_configs` id (required) |
| `PERF_SCENARIO` | scenario key; default `quick_test` |
| `PERF_WORK_DIR` | scratch dir; default `$WORKSPACE/alert-perf-work` |
| `ARTIFACT_REF`, `ARTIFACT_TYPE` | the promoted image is deployed as the alert service when `ARTIFACT_TYPE=image` |
| `NGC_CLI_API_KEY` | required |
| `HOST_IP` | optional; defaults to the primary route IP |
| `LLM_ENDPOINT_URL` | required for entries with `llmMode: remote` |

## Scenarios

| Scenario | Trigger | What |
|---|---|---|
| `quick_test` | promotion (default) | 1 stream, 3 min warm-up, 10 min measured |
| `stream_sweep_test` | weekly / manual | 1, 20, 30, 50 streams; reproduces the published tables |
| `max_streams_test` | weekly / manual | 10 to 50 streams; stops at the first level over `target_e2e_p90_seconds`; reports `max_sustainable_streams` |

## Artifacts promotion setup (artifacts-promotion repo)

The perf folder belongs to the profile, so every artifact that triggers it points `scm.folder` / `SCM_FOLDER` at `deploy/docker/developer-profiles/dev-profile-alerts`. Both triggers use the same `PERF_SERVICE`, so their results land in one dashboard series.

**Manifest** (`configs/<release>/<manifest>.yml`), on the `vss-alert-ms` image artifact:

```yaml
scm:
  url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  branch: "develop"
  folder: "deploy/docker/developer-profiles/dev-profile-alerts"
trigger_remote_pipeline:
  file: "./pipelines/<alert-triggers>.yaml"
  pipeline:
    - "alert-verification-perf-amd64"         # the -sbsa image lists alert-verification-perf-arm64-sbsa
```

**Trigger file**: copy the `vault` block from the LVS entry unchanged, then:

```yaml
- name: "alert-verification-perf-arm64-sbsa"
  enable: false                               # flip after a manual run succeeds
  type: "jenkins"
  vault: { ... same as lvs-perf-amd64 ... }
  pipeline_details:
    url: "https://prod.blsm.nvidia.com/a3ieng-devops-test"
    job_name: "benchmark-perf-test"
    job_params:
      ARTIFACT_TYPE: "image"
      ARTIFACT_REF: "nvcr.io/$source_org/$source_team/$source_name:$source_version"
      PERF_SERVICE: "alert-verification"
      SCM_URL: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
      SCM_BRANCH: "develop"
      SCM_FOLDER: "deploy/docker/developer-profiles/dev-profile-alerts"
      PERF_FOLDER: "perf"
      IMAGE_ARCH: "arm64-sbsa"
      NODE_LABELS: "DGX-SPARK"
      PERF_CONFIG_IDS: "all"
      PERF_SCENARIO: "quick_test"
      UPLOAD_TO_MINIO: "true"
      TRIGGERED_BY: "artifact_promotion"
      RELEASE: "$source_version"
      PERF_USE_RUN_BENCHMARK_SCRIPT: "false"   # use the stage hooks in perf/scripts
  test_results_ngc:
    resource: "$source_org/$source_team/test_results"
    version: "perf-$source_name-$source_version"
```

The amd64 entry is identical except `IMAGE_ARCH: "amd64"` and `NODE_LABELS: "H100-SXM"`. All amd64 configs are currently `enabled: false` (multi-GPU), so the amd64 trigger only becomes useful after those are enabled.

**If the profile is promoted as its own compose artifact**, add the same entries to that artifact's `trigger_remote_pipeline`, with the same `scm` block, and change only:

```yaml
      ARTIFACT_TYPE: "compose"
      ARTIFACT_REF: "<the compose artifact reference>"
```

For a compose artifact, `prepare_runtime.py` does not override the alert image. The stack is deployed from the checked-out `SCM_BRANCH` with the profile's default image tags. That only tests the promoted profile if the job checks out the commit the artifact was built from; confirm this with DevOps.

## Open items

- **Media server URL**: `config.yaml` uses a placeholder URL for the alert video. Point it at the shared media server, or set `PERF_ALERT_VIDEO_PATH` on the nodes.
- **NVFP4 checkpoint**: `defaults.yaml -> vlm_precision_models.nvfp4` is empty, so `spark-1-rtdetr-cr3-nvfp4` is disabled.
- **Multi-GPU entries**: disabled per the onboarding guide (no NVLink on shared nodes). RTVI-CV and RT-VLM do not exchange tensors across GPUs, so ask DevOps whether the rule applies here.
- **RTX PRO 6000 node label**: unconfirmed.
- **Hook support in the shared job**: the LVS path uses one `run_benchmark.sh`. If the shared job does not run `perf/scripts/*.sh` hooks for Compose yet, set `PERF_USE_RUN_BENCHMARK_SCRIPT=true` and point it at `perf/scripts/run_benchmark.sh -c $PERF_CONFIG_ID -s $PERF_SCENARIO -O <out> -u -d`.
- **VST tuning not applied**: the eval framework also sets `use_sensor_ntp_time: false` in VST. The alerts profile uses the image's built-in VST config, so the overlay does not change it.

---
name: vss-benchmark-video-ingest
description: |
  Benchmark video ingestion for an already deployed NVIDIA VSS Search profile.
  Use for ingest throughput in video-minutes per second, upload latency percentiles,
  concurrency sweeps, and time-to-searchable measurements using local videos.
  Do not use for search-result quality, alerting, summarization, or deployment tuning.
license: Apache-2.0
metadata:
  version: "3.3.0-rc0"
  requires-vss: ">=3.3.0,<3.4.0"
  author: "NVIDIA Video Search and Summarization team <tmhoang@nvidia.com>"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags:
    - nvidia
    - blueprint
    - benchmark
    - performance
    - vss
    - ingest
allowed-tools: Read Glob Grep Bash
---

# VSS Ingest Benchmark

Measure an existing Search deployment from the caller's machine. Each worker
uploads a local video through `vss vios add`, waits for its raw frames and Embed
chunks in public Elasticsearch, then starts its next video. VIOS webhooks dispatch
inference. CLI success establishes media availability; ES counts establish this
benchmark's completion criterion.

## Boundaries

- Use the installed checkout CLI, outside the deployed cluster. Read the checkout's
  root `AGENTS.md` for CLI setup and usage.
- Working CV/Embed `camera_streaming` and removal webhooks, model settings and
  indexing are deployment prerequisites. This skill does not deploy, tune or scale.
- Use public CLI and ES surfaces only. Do not inspect pods, logs, queues, GPUs or
  internal telemetry. Never retry an upload or replace a failed CLI command with
  direct backend writes.
- Use permitted local footage with detectable objects. Sparse raw output can leave
  a video unconfirmed; that observation alone does not identify a service fault.
- Keep secrets out of config, arguments and artifacts. `VSS_AUTH_TOKEN` is optional
  and authenticates ES reads only, independently of the CLI and version API.

## Prepare

Install the CLI from the checkout that supplies this skill. If a managed runtime
already provides that CLI, use it. A prepared checkout remains necessary for the
repository's shared version checker; set `VSS_REPO_ROOT` to its location.

```bash
# From the VSS checkout:
export VSS_REPO_ROOT="$(git rev-parse --show-toplevel)"
uv tool install "$VSS_REPO_ROOT/libs/vss/cli"
vss --version
vss configure --base-url "$VSS_PUBLIC_URL"
vss configure show
cd "$VSS_REPO_ROOT/skills/benchmarking/vss-benchmark-video-ingest"
python -m pip install -r scripts/requirements.txt
test -f config.local.yml || cp config.yml config.local.yml
```

Use the user-supplied public origin. Optionally set `VSS_CONFIG_HOME` before
configuration to isolate this deployment. The runner uses `vss` on `PATH`, or an
explicit `--cli-executable`; it installs no dependencies during measurement.
Keep CLI configuration stable throughout the run.

Set corpus and deployment overrides in ignored `config.local.yml`, and select it
with `--config config.local.yml` for every command. It is not loaded automatically.
See [configuration](references/configuration.md) for precedence and defaults, and
[version compatibility](references/version-compatibility.md) for the required
shared-checker gate, checkout resolution and exit codes.

```bash
python scripts/check_compatibility.py --config config.local.yml
```

Stop if that check fails. Run smoke first against a new deployment, then the
requested sweep. Use a fresh results directory for each attempt.

## Workload and execution

| Profile | Video classes | Concurrencies |
|---|---|---|
| smoke | 50MB | 1 |
| standard | 50MB, 500MB | 1, 5, 10, 20 |
| stress | 2GB | 20 |
| custom | supplied classes or `--video` | supplied values |

Every worker uploads the entire selected class once:
`uploads = workers × files`, `payload = workers × class bytes`, plus warmup.
The default warmup is one upload; `--warmup 0` disables it. Files are measured with
ffprobe; unreadable videos fail validation. Directory scans select `.mp4` and
`.mkv` one level deep. See [corpus inputs](references/corpus-and-datasets.md).

`scripts/run.py` performs these steps:

1. Validate inputs, installed CLI configuration and VIOS connectivity.
2. Apply the deployed-version gate.
3. Measure the corpus, write its CSV and project transfer volume. Above the
   configured ceiling, require confirmation; use `--yes` for an authorized run.
4. Check public ES health; stop if unreachable or red.
5. Run warmup, exclude it from metrics, then clean up. Failed warmup stops the run.
6. Run concurrent workers, recording each upload and its ES readiness outcome.
7. Clean up after each point outside measurement, then write normalized artifacts.
8. Generate summaries and three charts from the CSVs and validate the artifacts.

```bash
# Read-only preparation; stops before ES preflight or uploading:
python scripts/run.py --config config.local.yml --corpus /path/to/corpus \
  --profile smoke --limit 1 --warmup 0 --dry-run

python scripts/run.py --config config.local.yml --corpus /path/to/corpus \
  --profile smoke --limit 1 --warmup 0 --results-dir ./results/smoke

# Custom video, without size-class folders:
python scripts/run.py --config config.local.yml --profile custom \
  --video /path/to/video.mp4 --video-class-name warehouse \
  --concurrency 1 --concurrency 5 --concurrency 10 --results-dir ./results/warehouse
```

In Hermes, start long runs with the terminal tool's `background: true` and
`notify: true`, then poll its process session. An outer foreground deadline can
kill the benchmark while uploaded media keeps processing.

## Completion and cleanup

Both counts must reach their expected values:

```text
raw_count >= max(1, ceil(duration_sec × fps) - 15)
embed_count >= ceil(duration_sec / embed_chunk_duration_sec)
```

The 15-frame raw tolerance remains. A 95% plateau never confirms completion.
`raw_drop_grace_sec` is a failure-only stall guard; observed coverage is retained
in results. Transient ES read failures remain within the readiness polling budget.
See [completion and accounting](references/completion-and-accounting.md).

Cleanup defaults to `always`; `on-success` retains failed uploads and `never`
retains all media. For selected uploads, the runner requires CLI confirmation of
stored-recording removal, then observes both run-owned ES counts at zero across
30 seconds, within a 300-second timeout. Any observed reappearance resets the
settle period. Failed deletion, unresolved ownership, ES read failure or a cleanup
timeout stops subsequent points. `--cleanup-settle-sec` and `--cleanup-timeout`
control these waits, outside upload and point timing.
Both configured index targets must exist and be searchable during cleanup;
missing indices or zero-shard responses leave cleanup unverified and stop the sweep.

This verifies sampled ES absence, not that all producers have stopped or capacity
is idle. Retained uploads may still consume resources. After an interruption, stop
the old run and follow [ledger recovery](references/security-guidelines.md#interrupted-run-recovery).
The standalone recovery helper deletes through the CLI only; it does not perform
the runner's ES settle check.

## Report results

- `latency_sec` uses client monotonic time from CLI launch through readiness
  observation. p50/p95/max describe confirmed uploads within each point.
- `ingest_confirmed_at` is client UTC when readiness is observed. Throughput is
  confirmed video-minutes divided by last confirmation minus first request among
  confirmed uploads. Poll intervals, ES response time and transient read failures
  can delay observation; this is not a server indexing timestamp.
- Invalid points are excluded from the throughput headline. Invalid completion
  timestamps leave throughput and its window blank.
- Payload MB/s counts only CLI-successful source bytes over point wall time;
  partial failed transfers and wire overhead are unknown. It cannot by itself
  identify or exclude an uplink bottleneck.
- Report actual corpus, matrix, settings, outcomes, cleanup status, valid-point
  throughput and latency, artifact paths and limitations. Do not attribute a
  bottleneck or server-side stage duration from these client observations.

```bash
python scripts/summarize.py --results-dir ./results/warehouse
python scripts/charts.py --results-dir ./results/warehouse
python scripts/validate_artifacts.py --results-dir ./results/warehouse
python -m unittest discover -s tests -v
```

See [metrics and artifacts](references/metrics-and-artifacts.md) for schemas and
validation, [comparisons](references/harness-comparability.md) for baseline
requirements, [troubleshooting](references/troubleshooting.md) for failures, and
[agent evaluations](evals/EVAL.md) for behavior tests and baseline comparison.

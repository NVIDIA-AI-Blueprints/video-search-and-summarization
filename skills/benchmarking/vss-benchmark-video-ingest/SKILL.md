---
name: vss-benchmark-video-ingest
description: |
  Benchmark video ingestion for an already deployed NVIDIA VSS Search profile, from the
  caller's machine, using VSS CLI uploads and public Elasticsearch reads. Use when the user wants ingest
  throughput (video-minutes per second), upload latency percentiles, a concurrency
  sweep, or an ingest capacity curve using the `vss_ingest_perf` metric conventions. Keywords: VSS ingest benchmark, ingest throughput, video-min/s, upload
  concurrency sweep, ingest latency p95, time-to-searchable.
  Do not use it for search-result quality evaluation, alert benchmarking, video
  summarization, or for deploying, tuning, or scaling a VSS deployment.
license: Apache-2.0
metadata:
  version: "v3.3.0"
  author: "NVIDIA Video Search and Summarization team <tmhoang@nvidia.com>"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags:
    - nvidia
    - blueprint
    - benchmark
    - performance
    - vss
    - ingest
    - throughput
allowed-tools: Read Glob Grep Bash
---

# VSS Ingest Benchmark

Measure ingestion on an **already deployed Search profile** using the caller's
VSS CLI. An active worker runs `vss vios add --type video PATH --name UNIQUE_FILENAME`,
then waits for that upload's CV frames and Embed chunks in Elasticsearch before
starting its next video. VIOS webhooks dispatch inference; the skill does not use
the Agent upload or completion handlers.

## Boundaries and prerequisites

- Use the VSS checkout's CLI. Read the [root AGENTS.md](../../../AGENTS.md) for bootstrap and usage.
  Run the CLI in the benchmark caller's environment, outside the deployed cluster.
- The deployment must already have working `camera_streaming` webhooks to CV and
  Embed, matching model/chunk settings, and Kafka/Logstash indexing. VLM may also
  be enabled; this benchmark's completion rule covers CV and Embed only.
- CLI exit 0 establishes VIOS media availability, **not searchability**. ES reads
  remain required. No public CLI ingest-completion command is assumed.
- Do not deploy, tune, scale, inspect pod logs, or collect internal telemetry.
  Use public routes only. Never retry uploads or fall back to Agent REST.
  CLI owns upload/timeline timeouts; ES polling has its own budget.
- Use permitted videos and check projected transfer before a run. No secrets in
  config, arguments or artifacts. `VSS_AUTH_TOKEN` applies to ES reads only;
  it does not authenticate CLI uploads or version-API requests.

## Prepare the benchmark

Prepare and configure the checkout CLI once, following its `AGENTS.md`. Run this
setup from within the repository checkout:

```bash
export VSS_REPO_ROOT="$(git rev-parse --show-toplevel)"
test -f "$VSS_REPO_ROOT/libs/vss/cli/pyproject.toml" || exit 1
cd "$VSS_REPO_ROOT/skills/benchmarking/vss-benchmark-video-ingest"
# Optional: isolate this deployment from other CLI users/runs.
export VSS_CONFIG_HOME="$HOME/.vss-ingest-benchmark"
uv tool install "$VSS_REPO_ROOT/libs/vss/cli"
vss --version
vss configure --base-url "$VSS_PUBLIC_URL"
vss configure show
test -f config.local.yml || cp config.yml config.local.yml
```

Use the public origin supplied by the user or deployment instructions. The runner
reads `configure show` and checks VIOS with `vios list --type video`. It derives ES
from that CLI configuration unless `--elasticsearch-url` selects another public
route to the **same** ES cluster. It also requires that deployment's version API.

The runner resolves an installed CLI once and calls it for each upload. Set
`cli.executable` to select a specific installation. Prepared project environments
remain supported as a fallback; no dependency synchronization or downloads occur
during the benchmark. Python/CLI process startup remains part of upload timing.
Keep the CLI configuration stable. See [configuration](references/configuration.md)
for executable selection.

Install runner/chart dependencies with `python -m pip install -r scripts/requirements.txt`.
Keep the shipped `config.yml` portable. Copy it to ignored `config.local.yml` for
deployment settings, set corpus and results directory there, and pass
`--config config.local.yml` consistently to compatibility, validation and run scripts.
A local file is not loaded automatically. The version API
defaults to the deployment origin plus `/api/v1/version`; set
`compatibility.version_url` only for another public route to that deployment.
Paths in YAML are relative to that file; flags are relative to the current directory.
Explicit flags override configuration defaults.

| Profile | Classes | Concurrencies |
|---|---|---|
| smoke | 50MB | 1 |
| standard | 50MB, 500MB | 1, 5, 10, 20 |
| stress | 2GB | 20 |
| custom | supplied classes or --video | supplied values |

Every worker uploads the entire class once: **uploads = workers × files**.
Projected payload = workers × class bytes, plus warmup. Each point has a fresh UUID
and unique per-worker sequence numbers, including with `cleanup: never`.
Use real footage with detectable objects. Sparse detections can fall short of the
raw-frame completion rule; report that limitation rather than assuming a service fault.

## First check for each benchmark

After the one-time CLI and dependency setup, the agent first triggers the standalone
compatibility check for the selected deployment, before `validate.py` or `run.py`:

```bash
python scripts/check_compatibility.py --config config.local.yml --vss-repo "$VSS_REPO_ROOT"
```

Stop on failure. Passing this initial check does not bypass the runner's own gate:
`run.py` checks the version again after initial input/CLI validation, before corpus
measurement or uploads. Use the same configuration and deployment overrides in
the standalone check and the subsequent run.

## Run the eight benchmark steps

`scripts/run.py` executes these steps in order. Run smoke first against a new
deployment, then the requested sweep. Stop on setup or compatibility failure;
do not retry failed uploads automatically. Use a fresh results directory for each
attempt, retaining the previous attempt's artifacts. In Hermes, start long runs
with the terminal tool's `background: true` and `notify: true`, then use the
process tool to poll the returned session ID. A foreground tool timeout can kill
the benchmark while uploaded media keeps processing; raising only the command's
requested timeout does not necessarily extend the agent's outer tool deadline.

### 1. Validate inputs and CLI connectivity

Check corpus accessibility, classes, concurrency and other client settings, the
configured CLI, and VIOS connectivity through `vss vios list --type video`.
An empty sensor list is valid. A standalone check uses the same configuration:

```bash
python scripts/validate.py --config config.local.yml --vss-repo "$VSS_REPO_ROOT" --corpus /path/to/corpus --profile smoke
```

### 2. Check deployed VSS version compatibility

Validation and the runner repeat the compatibility check, so running `run.py`
directly cannot skip it. The script reads `skill-version` and `requires-vss` from
`metadata.yml`, calls
`GET /api/v1/version`, and applies the metadata rule. An incompatible version,
failed lookup or invalid response stops the run. Local `vss --version` is not a
substitute for the deployed version.

See [version compatibility](references/version-compatibility.md) for the response,
release comparison and provenance. Passing the version gate does not establish
webhook health; the remaining checks and smoke run still apply.

### 3. Measure the corpus and project transfer

Measure every selected video with `ffprobe` and write `csv/ingest_corpus.csv`.
Unreadable or invalid selected videos fail validation. Never guess a video's duration.
Project the measured payload for all points plus warmup. Above
`limits.transfer_ceiling_gb`, the runner asks for confirmation; `--yes` supports
an authorized unattended run.

```bash
python scripts/run.py --config config.local.yml --vss-repo "$VSS_REPO_ROOT" --corpus /path/to/corpus \
  --profile smoke --limit 1 --dry-run
```

`--dry-run` performs input/CLI checks, the version gate, corpus measurement and
transfer projection. It stops before ES readiness preflight and uploads.

### 4. Check Elasticsearch readiness

Check the configured public ES `/_cluster/health` route before uploading. An
unreachable or red cluster stops the run. Completion uses the configured raw and
Embed indices; no ES writes or internal telemetry are used.

### 5. Run configured warmup

Warmups use the same CLI/ES flow and are excluded from metrics. A failed warmup
stops the sweep and writes `raw/warmup_details.jsonl`. `--warmup 0` skips warmup.

### 6. Run concurrent workers

The skill's scripts start N concurrent workers. Each worker timestamps one CLI
upload, records its returned identity, waits for its CV frames and Embed chunks in
ES, then starts its next video. Both pipelines are always required;
`--expect-raw`, `--expect-embed`, and their former YAML keys are rejected. VLM output
is excluded from completion. Existing frame tolerance and settled-coverage settings
remain in effect; see
[completion and accounting](references/completion-and-accounting.md).

```bash
python scripts/run.py --config config.local.yml --vss-repo "$VSS_REPO_ROOT" --corpus /path/to/corpus \
  --profile smoke --limit 1 --warmup 0 --results-dir ./results/smoke

# Or use your own video, without size-class folders:
python scripts/run.py --config config.local.yml --vss-repo "$VSS_REPO_ROOT" --profile custom \
  --video /path/to/video.mp4 --video-class-name warehouse \
  --concurrency 1 --concurrency 5 --results-dir ./results/warehouse
```

### 7. Clean up and write artifacts

`cleanup.policy` defaults to `always`: delete all run-owned VIOS handles,
including failed or unconfirmed uploads, through
`vss vios delete --type video --sensor RETURNED_ID` after each point, outside its
measurement window. Choose `on-success` to retain failed uploads for investigation,
or `never` to retain all media. Exit 0 confirms VIOS removal only; asynchronous
`camera_remove` webhooks own downstream cleanup. Missing returned IDs are resolved
from one public listing using the persisted UUID name. An unresolved or ambiguous
identity counts as `no_handle`. Failed cleanup stops subsequent sweep points;
the runner retains collected artifacts and exits nonzero. `never` skips cleanup
and this stop condition.

The runner fsyncs each UUID upload intent in `raw/upload_ledger.jsonl` before
launching the CLI, then records returned IDs before ES polling. Missing IDs can
be recovered from a unique exact-name video match in the public VIOS listing;
resolved IDs are persisted before deletion. Failed intent writes prevent uploads,
and failed identity writes retain media for recovery. A killed process cannot run
normal cleanup. After stopping that run, use
[safe recovery](references/security-guidelines.md#interrupted-run-recovery) to
preview and remove only its recorded assets before rerunning. Do not assume a CLI
delete proves CV capacity or ES cleanup has finished; have the deployment operator
verify downstream removal if subsequent uploads still stall.

Write the four CSVs, raw per-upload records and `run-metadata.json`, including
version-check evidence and the actual settings used.

### 8. Generate, validate and review results

The runner generates summaries and exactly three charts from the CSVs, then checks
the required artifacts. Regenerate or validate a finished run with:

```bash
python scripts/summarize.py --results-dir ./results/warehouse --uplink-mbps 1000
python scripts/charts.py --results-dir ./results/warehouse
python scripts/validate_artifacts.py --results-dir ./results/warehouse
```

Review the four CSVs (`ingest_corpus`, `ingest_requests`, `ingest_summary`,
`ingest_errors`), raw JSONL and metadata before reporting results. Charts show
throughput, p95 latency and outcomes at the tested concurrencies. Failed points
are not valid capacity results. Artifact validation checks schemas, required
files, metadata and disclosure of the current `VSS_AUTH_TOKEN` value. Review
summary prose separately: it must not claim internal telemetry, bottlenecks or
server-side stage timings from these client observations.

Runner exit codes: `0` successful run; `1` unsuccessful warmup/run or declined
transfer; `2` invalid setup or report generation/validation failure. The standalone
compatibility and artifact checks use
`0` for success and `2` for failure.

## Measurement contract

- `cli_duration_sec`: process startup + upload + the CLI's VIOS timeline wait.
- `latency_sec`: CLI launch through ES readiness observation, including polling.
  p50/p95/max use confirmed uploads within each point.
- `ingest_confirmed_at`: matched documents' `max(ingested_at)`, or client confirmation
  time if unavailable. It is not the video's recording timestamp.
- `video_min_per_sec`: confirmed video-minutes divided by
  `max(ingest_confirmed_at) - min(request_sent_at)` over confirmed uploads.
- `result_valid`: false for failed/unconfirmed uploads, insufficient raw coverage,
  or invalid completion timing. Invalid timestamp windows leave throughput and the
  success window blank; they are never clamped to a tiny positive denominator.
- `http_status` is blank for CLI uploads; `cli_exit_code` holds the actual process
  code, and `cli_exit_statuses` summarizes those codes. Exit 7 is `timed_out`; other
  nonzero codes are `failed`. Never infer HTTP 200 from CLI exit 0.
- `aggregate_mb_per_sec` uses acknowledged payload bytes over point wall time.
  Partial failed transfers and HTTP overhead are unknown, so this is a lower bound,
  not measured wire bandwidth. A low value cannot rule out an uplink limit.
- Readiness/count formulas retain the harness convention, but
  `harness_comparable: false`: CLI startup, media waiting, and webhook routing differ
  from the old Agent PUT route. Establish a new baseline.

Report: setup and run status; actual corpus/matrix/settings; valid and invalid points
with success counts, throughput, p50/p95/max; exact artifact paths; interpretation
and limitations. Distinguish observed facts from hypotheses. Discuss possible uplink
limits and deployed concurrency limits without claiming which internal service failed.

## References

- [Configuration](references/configuration.md): flags, YAML, migration.
- [Benchmark contract](references/benchmark-contract.md): architecture and worker lifecycle.
- [Completion and accounting](references/completion-and-accounting.md): identity and counts.
- [Metrics and artifacts](references/metrics-and-artifacts.md): schemas and timing.
- [Harness comparability](references/harness-comparability.md): baseline differences.
- [Endpoint reference](references/endpoint-reference.md): CLI/public ES surfaces.
- [Corpus and datasets](references/corpus-and-datasets.md): accepted files and measurement.
- [Version compatibility](references/version-compatibility.md): required API gate and metadata rules.
- [Troubleshooting](references/troubleshooting.md) and [health investigation](references/service-health-investigation.md).
- [Security](references/security-guidelines.md): authentication and cleanup.

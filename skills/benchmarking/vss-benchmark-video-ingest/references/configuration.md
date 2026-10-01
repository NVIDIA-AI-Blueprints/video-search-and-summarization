# Configuration

`config.yml` at the skill root supplies the defaults for every run. This file is the
key-by-key reference; [SKILL.md](../SKILL.md) covers setup and the eight execution steps.
The shipped file contains no deployment endpoints or machine-specific paths: it
leaves `corpus` unset, uses the `smoke` profile with no warmup, and writes to
`~/vss-ingest-run` unless overridden. Use a fresh results directory for each run.
Put personal settings in ignored `config.local.yml` and select it explicitly with
`--config config.local.yml`; it is not loaded automatically.

## Precedence

```text
command line  >  config.yml  >  built-in default
```

The file never overrides a flag. That ordering is what makes it safe to keep a filled-in
local config around: the command line stays authoritative, so a one-off `--concurrency 20`
always means what it says.

Selecting a file:

| Flag | Effect |
|---|---|
| *(none)* | Loads `config.yml` next to the skill root. Absent file is not an error. |
| `--config <path>` | Loads that file. A missing file **is** an error. |
| `--no-config` | Skips the file entirely; built-in defaults only. |

Both `run.py` and `validate.py` read the same file with the same precedence, so a
configuration that validates is the configuration that runs.

## Precedence details

**Repeated flags replace, they do not append.** `--concurrency`, `--video-class`, and
`--video` are `action="append"` options. If argparse were given the config list as its default, CLI
values would be appended to it and `--concurrency 20` against `concurrencies: [1, 5, 10]`
would run *four* points. The loader applies these list keys only when the flag is absent,
so it runs one point at 20.

**`--video` alone selects only the named videos.** Without an explicit
`--video-class`, it replaces both profile classes and configured `sweep.video_classes`;
no corpus root is required. Configured concurrencies remain effective, including with
`--profile custom`. Supply `--video-class` as well to compare against corpus classes.
When neither selection flag is given, the configuration can intentionally combine
`sweep.videos` and `sweep.video_classes`.

**An explicit named `--profile` replaces config matrix lists.** With no CLI profile,
the lists in `config.yml` define the configured profile.

**Relative paths resolve against the config file's directory.** `corpus: "./ingest-corpus"`
means the corpus beside `config.yml`, no matter which directory you invoke the runner
from. A path typed on the command line is still relative to your shell, because that is
what a shell argument should mean.

## Keys

### Top level

| Key | Built-in default | Notes |
|---|---|---|
| `cli.repo` | `$VSS_REPO_ROOT` or `~/video-search-and-summarization` | Omitted in the shipped file so the environment/default stays effective. Set it only for a deliberate checkout override; `--vss-repo` wins. |
| `cli.config_home` | `$VSS_CONFIG_HOME` or `~/.vss` | Shipped as `null` to retain environment/default discovery. Existing CLI config directory; `--cli-config-home`. No automatic reconfiguration. |
| `cli.executable` | automatic | Installed CLI executable path/name; `--cli-executable`. Explicit selection takes precedence over automatic discovery and the uv fallback. |
| `cli.uv` | `uv` | Prepared-project fallback launcher; `--uv-executable`. A nondefault value explicitly keeps the uv launcher unless `cli.executable` is also set. |
| `corpus` | none | Corpus root, laid out `<corpus>/<video_class>/*.mp4`. Not required when every class comes from `sweep.videos`. |
| `results_dir` | `./benchmark-results/vss/ingest` | Run artifacts: `csv/`, `raw/`, `run-metadata.json`, `visualizations/`. |

### CLI selection

With default `cli.uv`, the runner first uses `vss` on `PATH`, then checks prepared
`.venv/bin/vss` executables in `libs/vss`, `libs/vss/cli` and `services/agent` under
`cli.repo`. An explicit `cli.executable` wins and must exist. Install the intended
CLI from the same VSS checkout; `cli.repo` alone does not override a different
`vss` already on `PATH`.

The executable is resolved once before the sweep. Its virtual-environment settings
and `VSS_CONFIG_HOME` are preserved. If no installed executable is found, a prepared
project can run through `uv run --no-sync --no-dev`: current `libs/vss` uses package
`nvidia-vss-cli`, while the legacy Agent workspace uses extra `cli`. No dependencies
are installed or downloaded during a benchmark. Each upload still starts one CLI
process, whose startup is included in the measured latency.

### `compatibility`

| Key | Default | Notes |
|---|---|---|
| `compatibility.version_url` | CLI base URL + `/api/v1/version` | Public version API for the same deployment; `--version-url`. |
| `compatibility.request_timeout_sec` | `10` | Positive, finite version-request timeout; `--version-timeout`. |

`metadata.yml` supplies `skill-version` and `requires-vss`; these are skill policy,
not workload overrides. Validation and every run stop if the deployed version
cannot be retrieved or fails this rule. `--no-health-check` only skips the VIOS
probe. It does not skip the version gate. See [version compatibility](version-compatibility.md).

### `sweep`

| Key | Built-in default | Notes |
|---|---|---|
| `sweep.profile` | `smoke` | `smoke`, `standard`, `stress`, or `custom`. `custom` requires both lists below. |
| `sweep.video_classes` | from profile | Overrides the profile's classes. A predefined size (`50MB`, `500MB`, `2GB`, `10GB`) or the name of any folder under `corpus`. |
| `sweep.videos` | `[]` | **Your own video.** A list of files or directories, benchmarked without a corpus layout. Without `sweep.video_classes`, it replaces the profile classes; with configured classes, it is swept as an extra class. |
| `sweep.video_class_name` | `custom` | The class label `sweep.videos` files carry in the CSVs, the summary, and the chart legends. 1–32 characters of `A-Z a-z 0-9 . - _`. |
| `sweep.concurrencies` | from profile | Unique integers from 1 through 200; one sweep point and x-axis value per entry. |
| `sweep.limit` | `null` | Use only the first N videos of each class. |
| `sweep.warmup` | `1` | Discarded uploads before the measured points, so the first timed upload does not pay for a cold model, page cache, or index. |

A sweep point is one class at one concurrency, and every worker uploads the whole class
once, so `uploads = concurrency x videos in the class`.

### `es_readiness`

The Elasticsearch poll that tracks upload progress: it counts RT-CV frames in the raw
index and RT-Embed chunks in the embed index, and takes the completion instant from
`max(ingested_at)` across the matched documents. Both pipelines must reach readiness;
neither can be disabled.

| Key | Default | Notes |
|---|---|---|
| `es_readiness.elasticsearch_url` | CLI discovery | Blank uses configure show; `--elasticsearch-url` overrides it. The route must allow health and search reads and address the same ES cluster. |
| `es_readiness.request_timeout_sec` | `60` | Timeout for each ES read, not CLI uploads; `--es-request-timeout`. |
| `es_readiness.poll_interval_sec` | `5.0` | Readiness polling interval. |
| `es_readiness.timeout_sec` | `null` | Hard ceiling; `null` keeps the harness-derived budget. |
| `es_readiness.embed_index` | `mdx-embed-filtered-2025-01-01` | Keyed on `sensor.id.keyword` by the **VST sensor UUID**. |
| `es_readiness.raw_index` | `mdx-raw-2025-01-01` | Keyed on `sensorId.keyword` by the **camera name**. Not interchangeable with the above. |
| `es_readiness.embed_chunk_duration_sec` | `5` | Must match the deployment. `expected_chunks` falls back to `ceil(duration / this)` when the ingest response reports no chunk count. |
| `es_readiness.frame_processing_time_ms` | `33` | Per-frame budget used to size the per-upload readiness timeout. |
| `es_readiness.raw_drop_grace_sec` | `120` | Plateau window — see below. |
| `es_readiness.raw_completion_ratio` | `0.95` | Plateau threshold — see below. |

CLI discovery may record an ES ingress that permits root or index-catalog reads
but denies `/_cluster/health`. A denied health check stops the run before upload.
Use an operator-provided public route to the same cluster that permits both health
and readiness search reads, or ask the operator for that route. The skill does not
disable ingress guards, skip the check or automatically choose a fallback URL.

The retained harness readiness rule allows for late-frame drops in RT-CV deployments
using `live-source=1`; the exact `ceil(duration * fps)` count may be unreachable. Raw counts as complete when it hits the
exact expectation **or** when it is at/above `raw_completion_ratio` and has not advanced
for `raw_drop_grace_sec`. The shortfall is reported via `raw_completion_ratio` and
`rt_cv_dropped`, never hidden.

Both of these change what "finished" means. Change them only to match a deployment, and
say so alongside the numbers — a run at `0.90` is not comparable to a harness run.

### `upload`

| Key | Default | Notes |
|---|---|---|
| `upload.ramp.stagger_sec` | `0.25` | Spreads worker starts so the measurement is of steady-state concurrency, not a thundering herd. |
| `upload.ramp.max_ramp_sec` | `60` | Ceiling on the total ramp. |

### `cleanup` and `limits`

| Key | Default | Notes |
|---|---|---|
| `cleanup.policy` | `always` | `always`, `on-success`, `never`. CLI VIOS deletion; ES removal webhooks are asynchronous. |
| `limits.transfer_ceiling_gb` | `100.0` | Projected bytes above this require confirmation, or `--yes`. |

## Secrets

`VSS_AUTH_TOKEN` has **no config key**. It is read from the environment only, is sent as
`Authorization: Bearer` on ES reads only, and is never logged or written to an artifact —
`run-metadata.json` records `auth_token_provided: true|false` and nothing more. The
shipped config contains no secrets. Keep deployment-specific overrides in the
ignored local file, even when they contain no credentials.

If an operator embeds a credential in a URL anyway, `run-metadata.json` stores that value
through `redact_url`. Version endpoint URLs containing credentials, queries or fragments
are rejected. See [security-guidelines.md](security-guidelines.md).

## Validation

The loader rejects unknown keys with the full list of valid ones, so a typo fails at
startup rather than silently running the wrong matrix:

```text
ERROR  config.yml has unrecognized key(s): sweep.concurrenies.
  Valid keys: cli.repo, cleanup.policy, es_readiness.poll_interval_sec, ...
```

It also rejects malformed YAML, a non-mapping top level, a non-list `sweep.concurrencies`,
non-integer or `< 1` concurrency entries. The removed
`es_readiness.expect_raw` and `es_readiness.expect_embed` keys are rejected,
including when set to `true`; remove them from old configuration files. Their old
`--expect-raw` and `--expect-embed` flags are also rejected. Raw frames and Embed
chunks are always required for Search ingestion.

## Provenance

`run-metadata.json` records `config_file` (the path used, empty under `--no-config`) and
`config_keys` (every key the file set, with URL values redacted). A finished run can be
reproduced from its own artifact. `version_compatibility` records the deployed version,
skill version, requirement, endpoint and check time. Summaries use this recorded result.

## Legacy generic overrides

`--set key=value` is rejected: the old generic option accepted values without
applying them. Use the dedicated flag or YAML key instead, such as `--concurrency 5`,
`--warmup 0`, `sweep.concurrencies: [5]` or `sweep.warmup: 0`. Normal flag-over-config
precedence still applies.

## Migration from Agent uploads

Remove `vss_service_url`, `upload.flow`, `upload.chunk_size_bytes`, and
`upload.request_timeout_sec`. Old keys are rejected rather than silently ignored.
Set `cli.repo`, prepare the CLI, and configure the deployment once. There is no
`--upload-flow`, `--chunk-size`, or `--request-timeout`: the CLI owns transfer
implementation and bounded upload/timeline waits. Use `--readiness-timeout` for
indexing observation and `--es-request-timeout` for each ES request.

The shipped `config.yml` is the portable template. There is no separate
`config.yml.orig`; keep deployment settings in `config.local.yml` and select it
with `--config`.

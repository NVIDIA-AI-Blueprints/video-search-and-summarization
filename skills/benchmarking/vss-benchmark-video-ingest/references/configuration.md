# Configuration

`config.yml` is the portable template. Copy it to ignored `config.local.yml` for
local settings and select it with `--config config.local.yml`; a local file is
never loaded implicitly. The shipped template matches parser defaults and leaves warmup/results defaults
to the parser, so `--no-config` uses the same workload defaults.

## Precedence and paths

Explicit flags override the selected config, which overrides parser defaults.
Without `--config`, the runner reads the skill's `config.yml` if present.
`--no-config` skips it. An explicitly named missing file is an error.

YAML paths resolve relative to the configuration file; flag paths resolve relative
to the caller's directory. Repeated `--video`, `--video-class` and `--concurrency`
flags replace configured lists rather than appending to them.

`--video` without `--video-class` selects only the named videos, replacing
configured and profile classes. With an explicit class flag it adds the user-video
class. An explicit named profile replaces configured matrix lists; custom profiles
use the supplied selections. Configured concurrencies remain effective when a
video alone is selected. Both validation and execution use these rules.

## CLI and version gate

| Key | Default | Meaning / flag |
|---|---|---|
| `cli.repo` | `$VSS_REPO_ROOT` or `~/video-search-and-summarization` | Checkout containing the shared version checker; `--vss-repo`. Does not select or install a CLI. |
| `cli.config_home` | `$VSS_CONFIG_HOME` or `~/.vss` | Existing CLI configuration; `--cli-config-home`. |
| `cli.executable` | `vss` on `PATH` | Installed executable; `--cli-executable`. |
| `compatibility.version_url` | configured origin + `/api/v1/version` | Public version route for the same deployment; `--version-url`. |
| `compatibility.request_timeout_sec` | `10` | Positive finite version request timeout; `--version-timeout`. |

The runner resolves an installed CLI once and preserves its virtual environment.
It does not scan project environments, run through `uv`, synchronize dependencies
or download packages. Install the intended CLI before the benchmark. See
[version compatibility](version-compatibility.md) for the shared-checker contract.

## Corpus and sweep

| Key | Default | Meaning / flag |
|---|---|---|
| `corpus` | unset | Root containing selected class folders; `--corpus`. |
| `results_dir` | `./benchmark-results/vss/ingest` | Fresh artifact directory; `--results-dir`. |
| `sweep.profile` | `smoke` | `smoke`, `standard`, `stress` or `custom`; `--profile`. |
| `sweep.video_classes` | profile classes | Class names; repeat `--video-class`. |
| `sweep.videos` | empty | Local files/directories; repeat `--video`. |
| `sweep.video_class_name` | `custom` | Label for user videos; `--video-class-name`. |
| `sweep.concurrencies` | profile values | Unique integers 1–200; repeat `--concurrency`. |
| `sweep.limit` | unset | First N files per class; `--limit`. |
| `sweep.warmup` | `1` | Warmup uploads excluded from metrics; `--warmup`. |
| `upload.ramp.stagger_sec` | `0.25` | Delay between worker starts; `--stagger-sec`. |
| `upload.ramp.max_ramp_sec` | `60` | Maximum total worker ramp; `--max-ramp-sec`. |
| `limits.transfer_ceiling_gb` | `100` | Confirmation threshold for projected payload; `--transfer-ceiling-gb`. |

Custom requires selected classes or videos and concurrency values. A point attempts
`workers × files` uploads. `--yes` accepts projected volume for an authorized
unattended run. Class names contain 1–32 letters, digits, dots, underscores or
hyphens and begin with a letter or digit.

## Elasticsearch readiness

| Key | Default | Meaning / flag |
|---|---|---|
| `es_readiness.elasticsearch_url` | CLI discovery | Public ES route for the same deployment; `--elasticsearch-url`. |
| `es_readiness.request_timeout_sec` | `60` | Timeout of each ES read; `--es-request-timeout`. |
| `es_readiness.poll_interval_sec` | `5` | Readiness and cleanup polling; `--readiness-poll-interval`. |
| `es_readiness.timeout_sec` | unset | Cap the derived per-upload readiness budget; `--readiness-timeout`. |
| `es_readiness.raw_index` | `mdx-raw-2025-01-01` | CV index, keyed by camera name; `--es-raw-index`. |
| `es_readiness.embed_index` | `mdx-embed-filtered-2025-01-01` | Embed index, keyed by sensor UUID; `--es-embed-index`. |
| `es_readiness.embed_chunk_duration_sec` | `5` | Deployed chunk duration for expected counts; `--embed-chunk-duration`. |
| `es_readiness.frame_processing_time_ms` | `33` | Per-frame budget used to size readiness timeout; `--frame-processing-time-ms`. |
| `es_readiness.raw_drop_grace_sec` | `120` | Failure-only stall budget, scaled by concurrency; `--raw-drop-grace-sec`. |

Both pipelines must reach their expected counts. See
[completion and accounting](completion-and-accounting.md) for formulas, observation
delay and failure outcomes. `raw_completion_ratio` is an output metric, not a
configuration option.

The ES route must allow health and search reads. Discovery of a root/index route
does not prove that `/_cluster/health` is allowed. A denied health check stops the
run; use an operator-supplied public route to the same cluster without disabling
ingress guards or bypassing readiness.

## Cleanup

| Key | Default | Meaning / flag |
|---|---|---|
| `cleanup.policy` | `always` | `always`, `on-success`, `never`; `--cleanup`. |
| `cleanup.timeout_sec` | `300` | Maximum ES wait after CLI deletion; `--cleanup-timeout`. |
| `cleanup.settle_sec` | `30` | Required observed absence period; `--cleanup-settle-sec`. |

After warmup and each point, selected uploads need confirmed CLI recording deletion
and zero raw/Embed counts at successful polls spanning the settle period. Observed
reappearance restarts that period. The timeout and settle values must be positive
and finite, with settle shorter than timeout.

Deletion and verification occur outside measurement. Incomplete cleanup stops the
sweep. `never` skips both; `on-success` retains non-confirmed uploads, which may
continue consuming resources. Bounded ES absence does not confirm consumer
withdrawal or exclude later writes. The standalone recovery helper is CLI-only;
see [security and recovery](security-guidelines.md).

## Validation and provenance

Unknown keys and invalid values are rejected. Use dedicated flags/YAML keys;
`--set key=value` is not supported. There are no controls to disable Raw or Embed
completion, no raw-coverage success threshold and no separate upload timeout.
The CLI owns upload/timeline waits; readiness and cleanup have separate budgets.

`VSS_AUTH_TOKEN` has no config key. It is read from the environment and sent only
as the ES bearer token. `run-metadata.json` records token presence, effective
settings, the selected config path and its configured keys. URLs must not carry
credentials. Keep deployment-specific configuration and result files out of commits.

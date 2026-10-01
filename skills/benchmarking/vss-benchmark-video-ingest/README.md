# VSS Ingest Benchmark Skill

**Version:** v3.3.0 · **License:** Apache-2.0

## Overview

`vss-benchmark-video-ingest` is an agent skill that measures video ingestion for an already
deployed NVIDIA **VSS Search** profile — how much video the deployed path accepts per
unit time, and how long a single upload takes to become searchable at that load.

It runs from the caller's machine against public endpoints. **No `kubectl`, Helm,
cluster access, or Prometheus.** Elasticsearch is read only for `es_readiness`.

Each worker uploads through the checkout's CLI, then waits for its own CV frames
and Embed chunks in ES. VIOS webhooks dispatch inference. The metric formulas retain
the harness convention, but this CLI/webhook path needs a new baseline against old
Agent REST runs. See [SKILL.md](SKILL.md) for the complete workflow.

## Quick start

Prepare Python 3.10+, ffprobe, uv, the VSS checkout's CLI, and a deployed Search
profile with working direct VIOS webhooks. From any directory in this repository
checkout (see the [CLI bootstrap](../../../AGENTS.md#setup)):

```bash
export VSS_REPO_ROOT="$(git rev-parse --show-toplevel)"
test -f "$VSS_REPO_ROOT/libs/vss/cli/pyproject.toml" || exit 1
cd "$VSS_REPO_ROOT/skills/benchmarking/vss-benchmark-video-ingest"
python -m pip install -r scripts/requirements.txt
export VSS_CONFIG_HOME="$HOME/.vss-ingest-benchmark"
uv tool install "$VSS_REPO_ROOT/libs/vss/cli"
vss --version
vss configure --base-url "$VSS_PUBLIC_URL"
vss configure show

# Set corpus and any deployment overrides in an ignored local file.
test -f config.local.yml || cp config.yml config.local.yml
$EDITOR config.local.yml
python scripts/check_compatibility.py --config config.local.yml
python scripts/run.py --config config.local.yml --profile smoke --limit 1 --warmup 0 --dry-run
python scripts/run.py --config config.local.yml --profile smoke --limit 1 --warmup 0 --results-dir ./results/smoke
python scripts/validate_artifacts.py --results-dir ./results/smoke
```

The shipped config leaves the corpus unset and discovers deployment routes from
the CLI configuration. `config.local.yml` is loaded only when selected with
`--config`; keep it out of commits.

The runner resolves the installed CLI once, then launches it for each worker upload.
Set `cli.executable`/`--cli-executable` for a particular installation. Prepared checkout
project environments remain supported; no installation or synchronization runs under
load. CLI configuration selects VIOS and supplies the default ES and version routes.
Keep that configuration stable throughout the run.

After setup, the first per-benchmark script is `scripts/check_compatibility.py`.
It reads `metadata.yml` and calls the deployed VSS `GET /api/v1/version` endpoint. `run.py` and `validate.py` enforce the
same gate. Unknown or incompatible versions stop the run; local `vss --version`
does not satisfy this check. See [version compatibility](references/version-compatibility.md).

The runner follows the design's eight steps:

1. Validate inputs, CLI configuration and VIOS connectivity.
2. Check the deployed VSS version against the skill's metadata rule.
3. Measure the selected corpus with ffprobe, write its CSV and project transfer.
4. Check public Elasticsearch readiness.
5. Run configured warmups, excluding them from metrics.
6. Run concurrent CLI uploads, waiting for each video's ES completion.
7. Apply cleanup policy and write CSVs, raw records and run metadata.
8. Generate summaries and the three charts, then validate the artifacts.

`--dry-run` stops after measurement and transfer projection without uploading.
Finished-run reports can be regenerated without contacting VSS:

```bash
python scripts/summarize.py --results-dir ./results/smoke --uplink-mbps 1000
python scripts/charts.py --results-dir ./results/smoke
python scripts/validate_artifacts.py --results-dir ./results/smoke
```

Review summary interpretations separately: all measurements are client-observed;
these results cannot identify internal bottlenecks or server-side stage timings.

CLI exit 0 confirms VIOS media availability, not ES completion. `cli_exit_code` and
`cli_duration_sec` record this stage separately. Cleanup defaults to `always`
via CLI; ES removal remains asynchronous and is not asserted by a successful delete.

The old upload.flow, chunk size, Agent URL and upload timeout settings are removed;
see [configuration migration](references/configuration.md#migration-from-agent-uploads).

## Tests

```bash
python -m unittest discover -s tests -v
```

## Corpus layout

```text
ingest-corpus/
├── LICENSE          # required: source and usability rules
├── 50MB/
│   └── clip-001.mp4
├── 500MB/
├── 2GB/
└── 10GB/
```

Prefer one file per class. Uploads per sweep point scale with the file count, so a
three-file 2GB class at concurrency 20 is 60 uploads and 120 GB. Datasets are never
committed with the skill — see
[`references/corpus-and-datasets.md`](references/corpus-and-datasets.md).

**Or bring your own video.** The size classes are a convenience, not a requirement:

```bash
# a file, or a directory of them; no corpus layout needed
python scripts/run.py --config config.local.yml --profile custom --concurrency 1 --concurrency 5 \
  --video ~/footage/warehouse.mp4 --video-class-name warehouse-4k
```

`--video` is repeatable and needs no `--corpus`. Its files are ffprobe-measured like
any other, so they carry their real video-minutes and every metric, CSV, and chart
treats them identically. A folder of your own under the corpus root works too —
`--video-class warehouse-4k` reads `ingest-corpus/warehouse-4k/`.

## Structure

```text
vss-benchmark-video-ingest/
├── SKILL.md                     the skill itself
├── README.md
├── config.yml                   portable defaults; local overrides use --config
├── metadata.yml                 skill version and deployed-VSS compatibility rule
├── LICENSE
├── references/
│   ├── configuration.md                 config.yml keys, precedence, worked examples
│   ├── benchmark-contract.md            inputs, profiles, run flow, allowed sweeps
│   ├── completion-and-accounting.md     Elasticsearch readiness and accounting
│   ├── metrics-and-artifacts.md         CSV schema, charts, artifact layout
│   ├── corpus-and-datasets.md           corpus layout, licensing, ffprobe
│   ├── endpoint-reference.md            VSS routes this skill uses, and those it never does
│   ├── harness-comparability.md         what maps to vss_ingest_perf, what is dropped
│   ├── security-guidelines.md           secrets, redaction, cleanup, blast radius
│   ├── troubleshooting.md
│   └── version-compatibility.md
├── scripts/
│   ├── requirements.txt         PyYAML (config.yml) + matplotlib (charts); rest is stdlib
│   ├── config.py                config.yml loader, schema, and precedence
│   ├── run.py                   orchestrates the eight benchmark steps
│   ├── validate.py              inputs, CLI, version gate and corpus validation
│   ├── check_compatibility.py   deployed version API + metadata compatibility gate
│   ├── validate_artifacts.py    completed-run files, schemas, scope and token check
│   ├── corpus.py                ffprobe measurement
│   ├── httpio.py                public ES JSON reads and URL redaction
│   ├── completion.py            Elasticsearch readiness monitor
│   ├── es_readiness.py          readiness queries and budgets
│   ├── vss_cli.py               checkout CLI subprocess interface
│   ├── upload.py                one upload: send, confirm, record
│   ├── artifacts.py             the four normalized CSVs
│   ├── summarize.py             CSV -> summary.json + summary.md
│   └── charts.py                CSV -> the three required PNGs
└── evals/
    ├── evals.json               6 ACES cases: explicit, implicit, contextual x2, negative x2
    ├── EVAL.md
    └── config.yml
```

## Output artifacts

```text
benchmark-results/vss/ingest/
├── raw/upload_details.jsonl
├── csv/
│   ├── ingest_corpus.csv        one row per measured video
│   ├── ingest_requests.csv      one row per upload
│   ├── ingest_summary.csv       one row per sweep point (harness column names)
│   └── ingest_errors.csv        error taxonomy by HTTP status and observed phase
├── visualizations/
│   ├── summary.md  summary.json
│   ├── ingest-throughput-vs-concurrency.png
│   ├── ingest-latency-p95-by-concurrency.png
│   └── ingest-outcome-by-concurrency.png
└── run-metadata.json
```

## The rules that make the numbers mean something

- **Never mutate the deployment.** No Helm values, replicas, profile config, or
  resource allocation. Benchmark the endpoint as deployed.
- **Never collect cluster telemetry.** Every number is client-observed, and the summary
  says so.
- **Never raise a server-side limit to fit the sweep.** A refused concurrency is a
  result.
- **Never claim an internal bottleneck.** Report status codes, timeout and unconfirmed
  counts, and the transfer rate.
- **Always require both raw frames and Embed chunks in Elasticsearch.** Neither
  pipeline can be disabled; earlier events are not ingest completion.
- **Measure every clip with ffprobe.** Throughput is counted in video-minutes; a
  guessed length corrupts every number.

## Repository integration

The source directory is `skills/benchmarking/vss-benchmark-video-ingest/` in this
repository. Its frontmatter, installed name, and invocation name are all
`vss-benchmark-video-ingest`. See the [skill catalog](../../README.md).

## Installing the skill

Symlink the source directory under the skill's installed name. Run from any
directory in the repository checkout; this Codex example checks the source and
preserves any existing installation:

```bash
SKILL_REPO="$(git rev-parse --show-toplevel)" || exit 1
SKILL_SRC="$SKILL_REPO/skills/benchmarking/vss-benchmark-video-ingest"
SKILL_DEST="${CODEX_HOME:-$HOME/.codex}/skills/vss-benchmark-video-ingest"
test -f "$SKILL_SRC/SKILL.md" || exit 1
if [ -e "$SKILL_DEST" ] || [ -L "$SKILL_DEST" ]; then
  echo "Already exists: $SKILL_DEST; inspect it before any manual migration."
else
  mkdir -p "$(dirname "$SKILL_DEST")"
  ln -s "$SKILL_SRC" "$SKILL_DEST"
fi
```

For another host, set `SKILL_DEST` to `~/.claude/skills/vss-benchmark-video-ingest`,
`~/.cursor/skills/vss-benchmark-video-ingest`, or `~/.agents/skills/vss-benchmark-video-ingest`
using an expanded `$HOME` path. If an installation already exists, verify where it
points and preserve or migrate it explicitly before rerunning; do not force-replace it.

Verify the installed target, then restart the agent session to load it:

```bash
readlink "$SKILL_DEST"
head -4 "$SKILL_DEST/SKILL.md"   # name: vss-benchmark-video-ingest
```

## Prompt template

```text
Use the vss-benchmark-video-ingest skill to benchmark ingest on our deployed VSS Search
profile.

Deployment origin: https://vss-search.<ip>.nip.io
Elasticsearch: http://<es-host>:9200
Corpus:     ./ingest-corpus  (50MB and 500MB classes, one file each)
Profile:    standard
ES readiness ceiling: 1800s
Uplink:     1000 Mb/s
Results:    ./benchmark-results/vss/ingest

Smoke first. Show me the projected transfer volume before starting the standard sweep.
```

"Validate before uploading", "Elasticsearch readiness", and "never claim an internal
bottleneck" live in `SKILL.md`, so they do not belong in the prompt.

## Evals

```bash
SKILL_REPO="$(git rev-parse --show-toplevel)" || exit 1
SKILL_SRC="$SKILL_REPO/skills/benchmarking/vss-benchmark-video-ingest"
test -f "$SKILL_SRC/SKILL.md" || exit 1
astra-skill-eval evaluate "$SKILL_SRC" --agent-eval -a codex --env-mode local
```

Acceptance runs must include the baseline comparison — do not pass `--skip-baseline`.
See [`evals/EVAL.md`](evals/EVAL.md). Generated `evals/results/` output is local only
and must not be committed:

```gitignore
**/evals/results/
benchmark-results/
ingest-corpus/
```

## License

Copyright (c) 2026 NVIDIA Corporation. All rights reserved.

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full
license text.

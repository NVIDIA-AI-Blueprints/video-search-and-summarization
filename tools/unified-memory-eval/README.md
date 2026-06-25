# Unified Memory Eval

Small eval harness for VSS/OpenClaw memory experiments.

It contains:

- `questions/`: TSV eval questions for single-video QA and cross-conversation memory scenarios.
- `scripts/run_single.py`: single-video summary + follow-up QA eval.
- `scripts/run_cross.py`: cross-conversation memory eval with locator, follow-up, and comparison turns.
- `scripts/compare.py` and `scripts/compare_total.py`: result comparison helpers.

## Environment

This harness is managed with `uv` and intentionally has no third-party Python package
dependencies. It uses Python 3.13 to match the repo's uv-managed agent service
Python range.

From this folder:

```bash
uv sync
```

On Brev hosts, `uv` may be installed under `~/.local/bin`. If `uv` is not on PATH in
non-interactive SSH sessions, use `~/.local/bin/uv` or export that directory first.

Create a local `.env` from the template in this folder and fill values on the machine where you run the eval.

Required for judging:

```bash
OPENAI_API_KEY=
```

Optional:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENCLAW_MODEL=openai/gpt-5.5
JUDGE_MODEL=gpt-5.5
LVS_BACKEND_URL=http://127.0.0.1:38112
VIDEO_URL_TEMPLATE=
VLM_NAME=cosmos-reason1
```

`run_cross.py` also needs access to summary JSON files through `--summary-dir`.

## Run

From this folder, point `--eval-root` here so scripts use the local `questions/` and write local `results/`.

```bash
uv run python scripts/run_single.py --eval-root .
uv run python scripts/run_cross.py --eval-root . --summary-dir /path/to/body-cam-summaries
```

Useful modes:

```bash
uv run python scripts/run_single.py --eval-root . --save-memory
uv run python scripts/run_cross.py --eval-root . --reset-memory --summary-dir /path/to/body-cam-summaries
uv run python scripts/run_cross.py --eval-root . --skip-ingest
```

Generated outputs go under `results/` and should not be committed.

## Output Layout

Single-video runs write one folder per video plus aggregate reports:

```text
results/single/run_<run_id>/
  total.md
  total.json
  debug/
    memory_reset.log

  <video_id>/
    report.md
    report.json
    raw.json
    debug/
      summary.json
      summary_events.json
      openclaw.log
```

Cross-conversation runs write one run-level report and debug logs:

```text
results/cross/run_<run_id>/
  report.md
  report.json
  raw.json
  debug/
    validation_warnings.txt
    memory_save_openclaw.log
    memory_reset.log
    <scenario_id>_openclaw.log
```

`raw.json` is the detailed source of truth for per-question/per-turn records.
The scripts no longer write default raw TSV files such as `*_raw.tsv` or `cross_raw.tsv`.

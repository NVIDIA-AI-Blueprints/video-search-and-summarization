# Unified Memory Eval

Small eval harness for VSS/OpenClaw memory experiments.

It contains:

- `questions/`: TSV eval questions for single-video QA and cross-conversation memory scenarios.
- `scripts/run_single.py`: single-video summary + follow-up QA eval.
- `scripts/run_cross.py`: cross-conversation memory eval with locator, follow-up, and comparison turns.
- `scripts/compare.py` and `scripts/compare_total.py`: result comparison helpers.

Two types of evals run here:

- Single: they test recall from the working memory / hot context (per conversation), asking questions scoped to a single video.
- Cross: they test recall from a durable memory system (across conversations), asking questions that can span multiple past videos that were analyzed

## Getting started

### Environment setup

This harness is managed with `uv`, we will iterate and add 3rd party packages when needed. 
It uses Python 3.13 to match the VSS repo's uv-managed agent service Python range.

1. Launch a Brev or Colossus machine

2. Install `uv`

3. From this folder:
```bash
uv sync
```

4. Create a local `.env` from the template in this folder and fill values on the machine where you run the eval.

Required for judging:

```bash
OPENAI_API_KEY=<provide key>
LVS_BACKEND_URL=http://127.0.0.1:38112
```
where `LVS_BACKEND_URL` corresponds to a fake/frozen summarization endpoint that replays deterministic body-cam summary outputs 
for the eval, instead of calling a live VSS summarization service.

Optional:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENCLAW_MODEL=openai/gpt-5.5
JUDGE_MODEL=gpt-5.5
VIDEO_URL_TEMPLATE=
VLM_NAME=cosmos-reason1
```

5. Chosoe and copy over eval tasks from `./example/questions` into `./questions`. Or generate/create your own questions in this format, and place them in `./questions`.

### Run evals

From this folder, point `--eval-root` here so scripts use the local `questions/` and write local `results/`.

1. Run evals scoped to a single video. Also save summaries to openclaw memory as these run:
```bash
uv run python scripts/run_single.py --eval-root . --save-memory
```

2. Run evals that are cross-conversations and use memory from early conversations:
```
uv run python scripts/run_cross.py \
  --eval-root . \
  --summary-dir /home/ubuntu/frozen-summarization-endpoint/data \
  --skip-ingest
```

Generated outputs go under `results/` and should not be committed.
View `total.md`, `report.md` for the summary of the results.
For details, view the machine-readable data `total.json`, `report.json`.

Other modes:

```bash
uv run python scripts/run_single.py --eval-root . --save-memory
uv run python scripts/run_cross.py --eval-root . --reset-memory --summary-dir /path/to/body-cam-summaries
uv run python scripts/run_cross.py --eval-root . --skip-ingest
```

## References

### Output Layout

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

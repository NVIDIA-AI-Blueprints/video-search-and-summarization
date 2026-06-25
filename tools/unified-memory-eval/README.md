# Unified Memory Eval

Small eval harness for VSS/OpenClaw memory experiments.

It contains:

- `questions/`: TSV eval questions for single-video QA and cross-conversation memory scenarios.
- `scripts/run_single.py`: single-video summary + follow-up QA eval.
- `scripts/run_cross.py`: cross-conversation memory eval with locator, follow-up, and comparison turns.
- `scripts/compare.py` and `scripts/compare_total.py`: result comparison helpers.

## Environment

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
python3 scripts/run_single.py --eval-root .
python3 scripts/run_cross.py --eval-root . --summary-dir /path/to/body-cam-summaries
```

Useful modes:

```bash
python3 scripts/run_single.py --eval-root . --save-memory
python3 scripts/run_cross.py --eval-root . --reset-memory --summary-dir /path/to/body-cam-summaries
python3 scripts/run_cross.py --eval-root . --skip-ingest
```

Generated outputs go under `results/` and should not be committed.

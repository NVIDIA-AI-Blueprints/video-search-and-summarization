# Unified Memory Eval

Small eval harness for VSS/OpenClaw memory experiments.

It contains:

- `questions/`: JSON eval questions for single-video QA and cross-conversation memory scenarios.
- `frozen_summarization_server/`: LVS-compatible frozen summary replay server and BWC fixtures.
- `scripts/run_single.py`: single-video summary + follow-up QA eval.
- `scripts/run_cross.py`: cross-conversation memory eval with locator, follow-up, and comparison turns.
- `scripts/compare.py` and `scripts/compare_total.py`: result comparison helpers.

Two types of evals run here:

- Single: they test recall from the working memory / hot context (per conversation), asking questions scoped to a single video.
- Cross: they test recall from a durable memory system (across conversations), asking questions that span multiple past videos that were analyzed.

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

Required:

```bash
# Required for judging:
OPENAI_API_KEY=<provide key>

# Frozen summarization server
LVS_BACKEND_URL=http://127.0.0.1:38112
```

Optional:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENCLAW_MODEL=openai/gpt-5.5
JUDGE_MODEL=gpt-5.5
VIDEO_URL_TEMPLATE={video_name}.mp4
VLM_NAME=cosmos-reason1
```

5. Choose and copy eval tasks from `./examples/questions` into `./questions`, or create your own JSON question files. The examples are grouped by eval design:

```text
examples/questions/
  legacy-single/       # Original focused single-video question sets
  categorized-single/  # Balanced within-event, entity-relational, and temporal sets
  cross-incidents/     # Cross-conversation scenario sets
```

Legacy `*_eval.json` files contain a top-level question array and derive the video ID from the filename. Custom-named single-video files contain `video_id` plus a `questions` array. Single-video rows require a `category` of `within_event`, `entity_relational`, or `temporal`, and use JSON arrays for `expected_event_ids`. In cross-conversation files, use arrays for `expected_video_ids` and an object mapping each video ID to an event-ID array for `expected_event_ids`.

### Launch frozen summarization server

The single-video eval expects an LVS-compatible summarization endpoint at `LVS_BACKEND_URL`.
For reproducible evals, launch the bundled frozen summarization server. It replays deterministic
BWC summary/event JSON from `frozen_summarization_server/data`.

1. Prepare ground truth summaries:
```
# Pull down data:
export NVDATASET_TENANTID=...
export NVDATASET_GROUPID=...
export NGC_API_KEY=...
nvdataset download external-chicago-copa-body-worn-camera ~/downloads/

# Choose summarizations to include in these evals from this download
# Move them to the server data in `frozen_summarization_server/data/`:
log_1083757_body-cam_video_1.json
log_1083757_body-cam_video_2.json
log_1083757_body-cam_video_3.json
log_1083757_body-cam_video_4.json
log_1083757_body-cam_video_5.json
```

2. Run endpoint:

```bash
uv run uvicorn frozen_summarization_server.app:app \
  --host 127.0.0.1 \
  --port 38112
```

Useful commands:

```bash
# Run it in the background:
nohup uv run uvicorn frozen_summarization_server.app:app \
  --host 127.0.0.1 \
  --port 38112 \
  > frozen_summarization_server/server.log 2>&1 &
```


### Run evals

From this folder, both runners read `questions/` and write `results/` by default. Use either
`--question-file` for one input or `--question-dir` for every valid input of that runner's type.
Those two selectors are mutually exclusive. Use `--results-dir` independently to change the output root.

1. Run evals scoped to a single video. Also save summaries to openclaw memory as these run:
```bash
uv run python scripts/run_single.py --question-dir questions/legacy-single --results-dir results --save-memory
```

2. Run evals that are cross-conversations and use memory from earlier conversations:
```
uv run python scripts/run_cross.py \
  --question-file questions/cross-incidents/cross-incidents.json \
  --results-dir results \
  --skip-ingest
```

Generated outputs go under `results/` and should not be committed.
View `total.md`, `report.md` for the summary of the results.
For details, view the machine-readable data `total.json`, `report.json`.

Other modes:

```bash
uv run python scripts/run_single.py --question-dir questions/legacy-single --save-memory

# Run only one single-video question JSON file for faster iteration.
uv run python scripts/run_single.py \
  --question-file questions/legacy-single/log_1083757_body-cam_video_2_eval.json \
  --results-dir results \
  --save-memory

# Custom filenames carry their source video ID in the JSON file.
uv run python scripts/run_single.py \
  --question-file examples/questions/categorized-single/log_1083757_body-cam_video_1_eval.json \
  --results-dir results

# Directory discovery is schema-aware: each runner ignores files belonging to the other eval type.
uv run python scripts/run_cross.py --question-dir questions/cross-incidents --results-dir results --reset-memory
uv run python scripts/run_cross.py --question-dir questions/cross-incidents --results-dir results --skip-ingest
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

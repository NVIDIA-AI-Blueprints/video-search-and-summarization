# Unified Memory Eval

Small eval harness for VSS/OpenClaw memory experiments.

It contains:

- `examples/questions/`: JSON eval questions for single-video QA and cross-conversation memory scenarios.
- `frozen_summarization_server/`: LVS-compatible frozen summary replay server and BWC fixtures.
- `scripts/run_single.py`: single-video summary + follow-up QA eval.
- `scripts/run_cross.py`: cross-conversation memory eval with locator, canonical follow-up, and cross-video evidence-join turns.
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

Legacy `*_eval.json` files contain a top-level question array and derive the video ID from the filename. Custom-named single-video files contain `video_id` plus a `questions` array. Single-video rows require a `category` of `within_event`, `entity_relational`, or `temporal`, and use JSON arrays for `expected_event_ids`.

Cross manifests use one scenario per focal video. Each scenario defines one locator, references the focal video's canonical 15-question file, and may define cross-video evidence joins. The runner resolves `single_question_source` relative to the manifest, validates the canonical 5/5/5 category balance, and expands each scenario into:

```text
1 locator + 15 canonical follow-ups + N cross-video evidence joins
```

`cross_video_questions` may be empty while a suite is being prepared. When populated, every join must include the focal video and at least one additional video. Cross rows use arrays for `expected_video_ids` and an object mapping every supporting video ID to its representative event-ID array:

```json
{
  "schema_version": 1,
  "scenarios": [
    {
      "scenario_id": "s1",
      "incident_id": "log_1083757",
      "focal_video_id": "log_1083757_body-cam_video_1",
      "single_question_source": "../categorized-single/log_1083757_body-cam_video_1_eval.json",
      "locator": {
        "question": "Which remembered BWC video ...?",
        "expected_answer_target": "The video is ...",
        "expected_video_ids": ["log_1083757_body-cam_video_1"],
        "expected_event_ids": {
          "log_1083757_body-cam_video_1": [1]
        }
      },
      "cross_video_questions": [
        {
          "cqid": 1,
          "reasoning_axis": "scene_correlation",
          "question": "What detail requires evidence from both videos?",
          "expected_answer_target": "A precise joined conclusion.",
          "expected_video_ids": [
            "log_1083757_body-cam_video_1",
            "log_1083757_body-cam_video_2"
          ],
          "expected_event_ids": {
            "log_1083757_body-cam_video_1": [12],
            "log_1083757_body-cam_video_2": [9]
          }
        }
      ]
    }
  ]
}
```

The old flat cross-question array remains supported for compatibility.

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

From this folder, use `examples/questions/` for the bundled question sets. Both runners write
`results/` by default. Use either
`--question-file` for one input or `--question-dir` for every valid input of that runner's type.
Those two selectors are mutually exclusive. Use `--results-dir` independently to change the output root.

1. Run evals scoped to a single video. Also save summaries to openclaw memory as these run:
```bash
uv run python scripts/run_single.py --question-dir examples/questions/categorized-single --results-dir results --save-memory
```

2. Run evals that are cross-conversations and use memory from earlier conversations:
```
uv run python scripts/run_cross.py \
  --question-file examples/questions/cross-incidents/cross-incidents.json \
  --results-dir results \
  --skip-ingest
```

Generated outputs go under `results/` and should not be committed.
View `total.md`, `report.md` for the summary of the results.
For details, view the machine-readable data `total.json`, `report.json`.

Other modes:

```bash
uv run python scripts/run_single.py --question-dir examples/questions/legacy-single --save-memory

# Run only one single-video question JSON file for faster iteration.
uv run python scripts/run_single.py \
  --question-file examples/questions/legacy-single/log_1083757_body-cam_video_2_eval.json \
  --results-dir results \
  --save-memory

# Custom filenames carry their source video ID in the JSON file.
uv run python scripts/run_single.py \
  --question-file examples/questions/categorized-single/log_1083757_body-cam_video_1_eval.json \
  --results-dir results

# Directory discovery is schema-aware: each runner ignores files belonging to the other eval type.
uv run python scripts/run_cross.py --question-dir examples/questions/cross-incidents --results-dir results --reset-memory
uv run python scripts/run_cross.py --question-dir examples/questions/cross-incidents --results-dir results --skip-ingest
```

### Context-pressure stress tests

Both runners can inject synthetic irrelevant filler into the OpenClaw session before eval
questions. This stresses hot-context recall (single) and scenario-anchor retention (cross)
without changing question files, expected answers, or scoring.

CLI knobs (both runners):

- `--context-pressure-turns` (default `0`): filler turns per injection point
- `--context-pressure-chars` (default `0`): characters of filler per turn

Cross runner only:

- `--context-pressure-placement` (default `none`): `before_locator`, `after_locator`, or `before_each_turn`

Example commands:

```bash
# Baseline single-video eval
uv run python scripts/run_single.py \
  --question-dir examples/questions/categorized-single \
  --results-dir results \
  --run-id baseline_single

# Single-video with 20k chars of filler (5 turns x 4000 chars)
uv run python scripts/run_single.py \
  --question-dir examples/questions/categorized-single \
  --results-dir results \
  --run-id pressure_single_20k \
  --context-pressure-turns 5 \
  --context-pressure-chars 4000

# Cross eval: pressure after locator (tests "that same video" follow-ups)
uv run python scripts/run_cross.py \
  --question-file examples/questions/cross-incidents/cross-incidents.json \
  --results-dir results \
  --run-id pressure_cross_after_locator_80k \
  --reset-memory \
  --context-pressure-placement after_locator \
  --context-pressure-turns 10 \
  --context-pressure-chars 8000
```

Report JSONs (`total.json`, `report.json`, per-video `report.json`) record
`context_pressure_turns`, `context_pressure_chars`, `context_pressure_total_chars`,
`context_pressure_placement` (cross), `openclaw_model`, and `judge_model`.

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

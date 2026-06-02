# Evaluation Tools

CLI scripts that compute downstream evaluation metrics from already-produced
tracking outputs. Each script is a thin wrapper around the metric
implementations under `spatialai_data_utils.eval.*` — these tools do I/O,
argument parsing, and a bit of orchestration so the same evaluation logic
can be re-run from the command line without standing up a separate eval repo.

## Tools Overview

| Tool                                | Purpose                                                                                                                                                  |
|-------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| **`evaluate_aicity_mtmc.py`**       | Reproduce the official AICity Challenge MTMC (Multi-Camera 3D People Tracking) HOTA evaluation on the challenge's space-separated text format.            |

---

## evaluate_aicity_mtmc.py

### Overview

Reproduces the per-scene + per-class HOTA evaluation protocol used by the
official AICity Challenge MTMC validation server on top of
`spatialai_data_utils`' bundled TrackEval library. The numbers this CLI
prints should match the leaderboard numbers for the same
`(ground_truth, submission)` pair to within float noise.

The evaluation code is year-agnostic — it runs the 2025 edition's
submissions as-is, and is intended to handle the 2026 edition as long
as the submission text format and class-id table stay compatible. The
class table (`CLASS_ID_TO_NAME`) and the bundled scene-id mapping that
change year-to-year are exposed as data, so no algorithmic code needs
to change when the spec moves forward.

For every `(scene, class)` pair that has both ground-truth and prediction
rows it runs TrackEval's HOTA metric on `MTMCChallenge3DBBox` (3D-IoU
matching — the official AICity MTMC metric). It then:

1. Averages per-class HOTA / DetA / AssA / LocA into a per-scene number
   (unweighted mean across classes that produced a metric).
2. Weights those per-scene numbers by the count of GT object-frame rows
   that survived the `--num_frames_to_eval` truncation, exactly the way
   the validation server does.

This script is a thin argparse + logging wrapper around the library API
in
[`spatialai_data_utils.eval.tracking.aicity_mtmc_eval`](../../spatialai_data_utils/eval/tracking/aicity_mtmc_eval.py)
— the same pipeline is callable from notebooks / CI without going
through the CLI. See [Library API](#library-api) below.

### Input format

Both the ground-truth file and the prediction file use the official
AICity MTMC text format — one space-separated row per object-frame:

```text
<scene_id> <class_id> <object_id> <frame_id> <x> <y> <z> <w> <l> <h> <yaw>
```

with `frame_id` 0-indexed and `yaw` in radians. The default class id
table is the AICity Challenge MTMC class set as published for the 2026
edition (the 2025 set plus `PalletTruck` at ID 6):

| ID | Class          | Introduced |
|----|----------------|------------|
| 0  | Person         | 2025       |
| 1  | Forklift       | 2025       |
| 2  | NovaCarter     | 2025       |
| 3  | Transporter    | 2025       |
| 4  | FourierGR1T2   | 2025       |
| 5  | AgilityDigit   | 2025       |
| 6  | PalletTruck    | 2026       |

Predictions with any `class_id` outside the active table are rejected
as out-of-spec rather than silently accepted. The 2025 edition's
original six-class table (without `PalletTruck`) lives at
`spatialai_data_utils.datasets.aicity25.spec` and can be selected via
`--edition 2025`; the 2026 default lives at
`spatialai_data_utils.datasets.aicity26.spec`.

The scene-id mapping (`--scene_id_2_scene_name_file`) is a JSON object
keyed by the string form of the integer scene id, mapping to a
human-readable scene name used as a directory and table label:

```json
{
  "23": "Warehouse_023",
  "24": "Warehouse_024",
  "25": "Warehouse_025"
}
```

When `--scene_id_2_scene_name_file` is omitted the tool loads the
packaged default mapping for the edition selected by `--edition`:

- `--edition 2026` (default) → `spatialai_data_utils.datasets.aicity26.load_default_scene_id_to_name()`
  / `get_default_scene_id_to_name_path()` (the three `Warehouse_023`–`Warehouse_025` scenes).
- `--edition 2025` → `spatialai_data_utils.datasets.aicity25.load_default_scene_id_to_name()`
  / `get_default_scene_id_to_name_path()` (the four `Warehouse_017`–`Warehouse_020` scenes).

Pass `--scene_id_2_scene_name_file` explicitly when evaluating a custom
subset (or a future edition that hasn't been added to `--edition` yet).

### Quick Start

The script lives in the SDU repo and imports from the
`spatialai_data_utils` package, so run it from the repo root (or with
the package installed). Use `--edition` to pick the AICity Challenge
edition (default `2026`):

```bash
# 2026 (default)
python tools/evaluation/evaluate_aicity_mtmc.py \
    --ground_truth_file  data/aicity26/ground_truth/ground_truth.txt \
    --input_file         /path/to/your_2026_submission.txt \
    --output_dir         /tmp/aicity_mtmc_eval \
    --num_frames_to_eval 9000 \
    --quiet

# 2025 (legacy six-class table)
python tools/evaluation/evaluate_aicity_mtmc.py \
    --edition            2025 \
    --ground_truth_file  data/aicity25/ground_truth/ground_truth.txt \
    --input_file         data/aicity25/v0.6.0/aicity25_submissions_all/R101_iter_4684_conf05/track1_fixed.txt \
    --output_dir         /tmp/aicity_mtmc_eval \
    --num_frames_to_eval 9000 \
    --quiet
```

`--scene_id_2_scene_name_file` defaults to the bundled mapping for the
chosen `--edition` (`Warehouse_023`–`Warehouse_025` for 2026 — the
default; `Warehouse_017`–`Warehouse_020` for 2025), so the typical
AICity MTMC invocation needs only the GT, the submission, and an
output dir; pass `--edition 2025` to evaluate against the 2025 table.

End-to-end runtime on the four-warehouse AICity'25 GT (~880k rows) is
roughly 5 minutes on a single core, dominated by TrackEval's HOTA
computation on the largest classes (Person). The three-warehouse
AICity'26 GT (~1.48M rows) is comparable but skewed slightly higher
because each warehouse has more frames with PalletTruck instances.

### Arguments

| Argument                       | Required | Default   | Description |
|--------------------------------|----------|-----------|-------------|
| `--edition`                    | No       | `2026`    | AICity Challenge edition. Selects the spec's class-id table (`2026` = 7 classes at IDs 0–6 (the six 2025 classes plus `PalletTruck`); `2025` = the original six classes at IDs 0–5) **and** the default scene mapping (`2026` = `Warehouse_023`–`Warehouse_025`; `2025` = `Warehouse_017`–`Warehouse_020`). |
| `--ground_truth_file`          | Yes      | —         | Path to the AICity MTMC ground-truth text file. |
| `--input_file`                 | Yes      | —         | Path to the AICity MTMC prediction text file (a single submission, in the official space-separated 11-column format). |
| `--scene_id_2_scene_name_file` | No       | (packaged for `--edition`) | JSON `{scene_id_str: scene_name}` mapping. When omitted, the tool uses the bundled default mapping for the chosen `--edition`; pass this explicitly for a custom scene subset. |
| `--output_dir`                 | No       | (tempdir) | Where the split files, TrackEval scratch artefacts, and the final `aicity_mtmc_hota_summary.json` are written. Omit to use a tempdir and discard intermediates at exit. |
| `--num_cores`                  | No       | `1`       | Forwarded to TrackEval's `NUM_PARALLEL_CORES`. Has near-zero impact here because we run TrackEval once per `(scene, class)` pair with a single sequence. |
| `--frame_start`                | No       | `0`       | 0-indexed inclusive lower bound for `frame_id` (per scene). Combined with `--num_frames_to_eval` defines an arbitrary half-open window `[frame_start, num_frames_to_eval)` -- e.g. `--frame_start 4500 --num_frames_to_eval 9000` evaluates the second half of a 9000-frame scene; `--frame_start 0 --num_frames_to_eval 4500` evaluates the first half. Defaults to 0 (the official validation server's behaviour). |
| `--num_frames_to_eval`         | No       | `9000`    | Frame-count truncation per scene (0-indexed exclusive upper bound). Matches the official validation server default. |
| `--eval_type`                  | No       | `bbox`    | HOTA matching function: `bbox` (3D-IoU — the official AICity MTMC metric) or `location` (centre distance — useful for ablation only). |
| `--fps`                        | No       | `30.0`    | FPS written into TrackEval's per-sequence `seqinfo.ini` (cosmetic for single-sequence runs). |
| `--quiet`                      | No       | (off)     | Suppress TrackEval's per-(scene, class) `INFO` records and per-class metric tables, keeping the final summary table readable. |

### Output

Two pieces of output:

1. A formatted summary printed to stdout (via Python `logging`), with:
   - A per-(scene, class) HOTA / DetA / AssA / LocA table.
   - A per-scene table that also shows the GT row count used as the
     aggregation weight.
   - A single `WEIGHTED FINAL` row that mirrors the leaderboard number.
2. When `--output_dir` is provided, a JSON file at
   `<output_dir>/aicity_mtmc_hota_summary.json` with every metric in
   the 0–100 scale, suitable for ingestion by dashboards or CI:

   ```json
   {
     "eval_type": "bbox",
     "num_frames_to_eval": 9000,
     "scene_id_to_name": { "17": "Warehouse_017", ... },
     "per_scene_object_counts": { "Warehouse_017": 179981, ... },
     "per_scene_per_class": {
       "Warehouse_017": {
         "Person":       { "HOTA": 74.23, "DetA": 77.81, "AssA": 70.82, "LocA": 83.77 },
         "Transporter":  { "HOTA": 15.06, "DetA": 24.47, "AssA":  9.28, "LocA": 22.37 },
         ...
       },
       ...
     },
     "per_scene": {
       "Warehouse_017": { "HOTA": 56.86, "DetA": 61.79, "AssA": 53.33, "LocA": 61.69 },
       ...
     },
     "final": { "HOTA": 61.19, "DetA": 63.11, "AssA": 59.66, "LocA": 69.65 }
   }
   ```

   In addition, the `<output_dir>/split/<scene>/<class>/{gt,pred}.txt`
   intermediates are kept so you can re-run TrackEval manually on any
   single `(scene, class)` pair if you need to debug or experiment.

### Library API

Import the same functions the CLI uses from
`spatialai_data_utils.eval.tracking.aicity_mtmc_eval`:

| Symbol                                | Purpose |
|---------------------------------------|---------|
| `HOTA_FIELDS`                         | `["HOTA", "DetA", "AssA", "LocA"]` — the metric quartet reported by the leaderboard. |
| `split_aicity_mtmc_per_scene_per_class(...)` | Stream-split an AICity MTMC text file into `<scene>/<class>/<basename>` MOT-format files; returns `{scene: {class: row_count}}`. Optional kwarg `class_id_to_name` selects the active class table (defaults to AICity'25). Useful on its own for per-class analyses / visualizers / custom evaluators. |
| `run_aicity_mtmc_evaluation(...)` | End-to-end orchestrator: splits, runs HOTA per (scene, class), aggregates, returns a results dict. Optional kwarg `class_id_to_name` selects the active class table (defaults to AICity'25; pass `aicity26.spec.CLASS_ID_TO_NAME` for the 2026 edition). |
| `print_aicity_mtmc_summary(results)` | Log the per-(scene, class) + per-scene summary table to the module's logger. |
| `save_aicity_mtmc_results(results, output_dir)` | Persist a results dict to `<output_dir>/aicity_mtmc_hota_summary.json` in the 0–100 scale used by the official leaderboard. Returns the JSON path. |

Spec constants — the class-id → name table and the text-format
field count — live in per-edition sibling packages so the eval
module, the submission converters under `tools/aicity25/`, and any
future AICity MTMC consumer can share a single source of truth:

```python
from spatialai_data_utils.datasets.aicity25.spec import (
    CLASS_ID_TO_NAME,   # {0: "Person", 1: "Forklift", ..., 5: "AgilityDigit"}
    NUM_FIELDS,         # 11
)
# Or for the 2026 edition:
from spatialai_data_utils.datasets.aicity26.spec import (
    CLASS_ID_TO_NAME,   # 2025's six classes plus 6: "PalletTruck"
    NUM_FIELDS,         # 11 (unchanged)
)
```

Minimal usage from Python (2026 default — the project-wide default):

```python
from spatialai_data_utils.datasets.aicity26 import load_default_scene_id_to_name
from spatialai_data_utils.eval.tracking.aicity_mtmc_eval import (
    run_aicity_mtmc_evaluation,
    print_aicity_mtmc_summary,
    save_aicity_mtmc_results,
)

results = run_aicity_mtmc_evaluation(
    ground_truth_file="data/aicity26/ground_truth/ground_truth.txt",
    prediction_file="data/aicity26/.../track1.txt",
    scene_id_to_name=load_default_scene_id_to_name(),
    output_dir="/tmp/aicity_mtmc_eval",   # or None for a tempdir
)
print_aicity_mtmc_summary(results)
save_aicity_mtmc_results(results, "/tmp/aicity_mtmc_eval")

print(results["final"]["HOTA"])    # 0.611948 (in [0, 1] scale)
```

To evaluate a 2026 submission, swap the per-edition scene mapping
**and** pass the 2026 class table as `class_id_to_name`:

```python
from spatialai_data_utils.datasets.aicity26 import load_default_scene_id_to_name
from spatialai_data_utils.datasets.aicity26.spec import (
    CLASS_ID_TO_NAME as AICITY26_CLASS_ID_TO_NAME,
)
from spatialai_data_utils.eval.tracking.aicity_mtmc_eval import (
    run_aicity_mtmc_evaluation,
)

results = run_aicity_mtmc_evaluation(
    ground_truth_file="data/aicity26/ground_truth/ground_truth.txt",
    prediction_file="path/to/your_2026_submission.txt",
    scene_id_to_name=load_default_scene_id_to_name(),
    class_id_to_name=AICITY26_CLASS_ID_TO_NAME,
    output_dir="/tmp/aicity_mtmc_eval",
)
```

The orchestrator returns metrics as ratios in `[0, 1]` (matching
TrackEval's native scale).  Both `print_aicity_mtmc_summary` and
`save_aicity_mtmc_results` multiply by 100 for display /
persistence, matching the convention used by the official leaderboard.

### Notes & Gotchas

- The class names emitted here (e.g. `NovaCarter`, `FourierGR1T2`) match
  the AICity Challenge MTMC class set as published for 2025
  ([reference spec](https://www.aicitychallenge.org/2025-track1/))
  verbatim — *not* the SDU evaluation pipeline's preferred
  `Nova_Carter` / `Fourier_GR1_T2_Humanoid` spelling — because this tool's
  job is to reproduce the official challenge metric, not to align with
  the SDU internal taxonomy. The class names are display labels only;
  TrackEval keys results by sequence + the literal `"class"` token, not
  by class name, so the spelling does not affect the computed metric.
- The prediction file format does **not** carry a confidence column, so
  there is no `--confidence_threshold` option here — filter your
  submission upstream (e.g. via the
  `aicity25-submission` skill's
  `tools/aicity25/convert_sparse4d_to_aicity25.py --conf_thresh`).
- `num_frames_to_eval` truncates by **frame ID**, not by **row count**,
  so it is safe to leave at its `9000` default even when one scene has
  many fewer rows.
- The auto-derived scene name (`scene_<id>`) is purely for display; the
  HOTA numbers under `scene_<id>` are identical to those under the
  named-scene mapping for the same underlying data.

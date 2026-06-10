# Validation and Evaluation

Validates generated MTMC data from S3 and, in full mode, evaluates Sparse4D
detection output with BEV-sensor-specific metrics.

Sparse4D produces 3D detections for a set of camera sensors. In this tool, a
camera group is evaluated as a Bird's-Eye-View (BEV) sensor, so detection
metrics are written per BEV sensor group and then combined into one summary CSV.

Detection evaluation reports mean Average Precision (mAP) and the per-class
true-positive error metrics used by the bundled nuScenes-style evaluator.

## Requirements

Inputs are expected to be in NVSchema line-delimited JSON format:

- `calibration.json`: calibration data with camera sensors and BEV group
  membership.
- `ground-truth/`: required for full validation and evaluation.
- `mdx-bev/`: Sparse4D BEV detection output.

Representative `mdx-bev` record:

```json
{
  "version": "3.0",
  "id": "15",
  "timestamp": "2024-12-20T18:15:28.067986Z",
  "sensorId": "mdx-bev-region-1",
  "objects": [
    {
      "id": "0",
      "type": "Person",
      "confidence": 0.9597891,
      "coordinate": {"x": 1.703, "y": -4.07, "z": 0.846},
      "bbox3d": {
        "coordinates": [1.703, -4.07, 0.846, 0.535, 0.328, 1.704, 0.0, 0.0, 0.029],
        "confidence": 0.9597891
      }
    }
  ]
}
```

## Environment

Create `tools/validation_and_evaluation/.env` from `.env.example`:

```env
HOST_IP=localhost
AWS_ACCESS_KEY_ID="<AWS_ACCESS_KEY_ID>"
AWS_SECRET_ACCESS_KEY="<AWS_SECRET_ACCESS_KEY>"
AWS_REGION="<AWS_REGION>"
AWS_BUCKET="<AWS_BUCKET_NAME>"
AWS_S3_BASE_PREFIX_PATH="generated/"
SIMULATION_ID="<SIMULATION_ID>"
```

The tool downloads into `results/<SIMULATION_ID>/`.

## Data Validation Checks

Full mode performs validation before and after download:

- S3 ground-truth count: checks
  `<AWS_S3_BASE_PREFIX_PATH><SIMULATION_ID>/ground-truth/mega_gt` before
  downloading. It errors below
  `simulation_seconds * fps * ground_truth_record_count_error_threshold_ratio`
  and warns below
  `simulation_seconds * fps * ground_truth_record_count_warning_threshold_ratio`.
- S3 bin-file validation: checks sensor bridge bin directories under
  `ground-truth/` unless `--skip_s3_bin_files_check` is set.
- Ground-truth file validation: checks non-empty content, synchronized sensor
  timestamps, known calibration sensors, expected record count, and non-empty
  object `type` values.
- BEV file validation: checks non-empty `mdx-bev`, BEV delay relative to ground
  truth, BEV record count thresholds, intra-record sensor timestamp spread, and
  inter-record spacing.

BEV-only mode performs a smaller flow:

- Downloads only `mdx-bev/`.
- Skips ground-truth validation and detection evaluation.
- Runs the BEV file validation checks that do not require ground truth.

## Full Validation And Evaluation

```bash
python tools/validation_and_evaluation/run_validation_and_evaluation.py \
    --calibration_url s3://<bucket>/generated/<SIMULATION_ID>/calibration.json \
    --confidence_threshold 0.3 \
    --simulation_seconds 120
```

Full mode:

- Downloads and merges `ground-truth/`, `mdx-bev/`, and related datasets.
- Downloads `calibration.json`.
- Checks the S3 ground-truth record count before downloading/evaluating.
- Validates ground-truth data and BEV data.
- Optionally checks S3 bin files unless `--skip_s3_bin_files_check` is set.
- Splits GT and prediction data by BEV sensor and runs detection evaluation.
- Writes per-sensor metrics and uploads a combined summary CSV.

## BEV-Only Validation

Use this mode when you only want to validate `mdx-bev` and skip ground-truth
validation/evaluation:

```bash
python tools/validation_and_evaluation/run_validation_and_evaluation.py \
    --calibration_url s3://<bucket>/generated/<SIMULATION_ID>/calibration.json \
    --only_mdx_bev_validation
```

Boolean flags use `action="store_true"`. Pass the flag by itself; do not append
`True` or `False`.

Correct:

```bash
--skip_s3_bin_files_check --only_mdx_bev_validation
```

Incorrect:

```bash
--skip_s3_bin_files_check True --only_mdx_bev_validation True
```

## Key Arguments

| Argument | Default | Description |
|---|---:|---|
| `--calibration_url` | required | S3 or HTTPS URL for `calibration.json`. |
| `--confidence_threshold` | `0.0` | Filters predictions below this score before evaluation. |
| `--num_frames_to_eval` | `200000` | Maximum frames to include during detection evaluation. |
| `--ground_truth_frame_offset_secs` | `0.0` | Temporal offset applied to GT during detection evaluation. |
| `--eval_options` | `location` | Detection-matching function. `location` uses centre-distance matching (`DET_CONFIG_CENTER_DISTANCE`, historical MTMC default); `bbox` uses 3D-IoU bounding-box matching (`DET_CONFIG_IOU3D`). |
| `--simulation_seconds` | `120` | Expected simulation duration used for record-count thresholds. |
| `--ground_truth_record_count_warning_threshold_ratio` | `0.99` | Warn when GT record count falls below this ratio of expected count. |
| `--ground_truth_record_count_error_threshold_ratio` | `0.99` | Error when GT record count falls below this ratio of expected count. |
| `--bev_record_count_warning_threshold_ratio` | `0.85` | Warn when BEV record count falls below this ratio of expected count. |
| `--bev_record_count_error_threshold_ratio` | `0.5` | Error when BEV record count falls below this ratio of expected count. |
| `--min_tolerance_ms_for_bev_record` | `33` | Minimum expected spacing between consecutive BEV records. |
| `--max_tolerance_ms_for_bev_record` | `34` | Maximum expected spacing between consecutive BEV records. |
| `--bev_intra_record_timestamp_tolerance_ms` | `34` | Maximum timestamp spread allowed across camera entries inside one BEV record before warning. |
| `--bev_delay` | `33` | Warn when the first BEV record starts more than this many milliseconds after the first GT record. |
| `--skip_s3_bin_files_check` | `False` | Boolean flag. Skip the S3 bin-file count check in full mode. |
| `--only_mdx_bev_validation` | `False` | Boolean flag. Validate only `mdx-bev` and skip GT validation/evaluation. |

## Timestamp Checks

`bev_data_validation` performs three BEV timing checks:

- First-record BEV delay: compares the first BEV timestamp with the first GT
  timestamp when a GT file is available. If the actual delay is greater than
  `--bev_delay`, it logs a warning.
- Intra-record sensor synchronization: compares all sensor timestamps inside a
  single BEV record. If the spread is greater than
  `--bev_intra_record_timestamp_tolerance_ms`, it logs a warning and includes
  the number of unsynchronized records in the summary.
- Inter-record spacing: compares consecutive BEV record timestamps against the
  range `[--min_tolerance_ms_for_bev_record, --max_tolerance_ms_for_bev_record]`.
  Records outside the range are counted, warned, and summarized.

Ground-truth timestamp spacing uses the same min/max tolerance range and exits
when the spacing is outside the allowed range.

## Accuracy Metrics

Per-sensor detection outputs include:

- `mAP`: mean Average Precision. With the default `--eval_options location`,
  predictions are matched to ground truth by 2D center distance on the ground
  plane (`DET_CONFIG_CENTER_DISTANCE`); `--eval_options bbox` uses 3D-IoU
  matching (`DET_CONFIG_IOU3D`). AP is computed at the config's single matching
  threshold (currently `0.5`, defined in
  [`configs/eval/detection.py`](../../spatialai_data_utils/configs/eval/detection.py))
  and then averaged across object classes.
- `AP`: Average Precision for one object class.
- `ATE`: Average Translation Error, the 2D center-distance error in meters.
- `ASE`: Average Scale Error, computed as `1 - IoU` after aligning centers and
  orientation.
- `AOE`: Average Orientation Error, the smallest yaw difference in radians.
- `AVE`: Average Velocity Error in meters per second.
- `AAE`: Average Attribute Error, computed as `1 - accuracy`.

The true-positive error metrics are averaged per class at achieved recall levels
above 10%. If a class does not reach 10% recall, its TP errors are set to `1`.

## Outputs

Local outputs are written under:

```text
results/<SIMULATION_ID>/
```

Output files:

```text
results/<SIMULATION_ID>/calibration.json
results/<SIMULATION_ID>/ground-truth/ground-truth-sorted.json
results/<SIMULATION_ID>/mdx-bev/mdx-bev-sorted.json
results/<SIMULATION_ID>/evaluation_results/sparse4d/detection_results/<BEV_SENSOR>/output/metrics_summary.json
results/<SIMULATION_ID>/evaluation_results/sparse4d/detection_results/<BEV_SENSOR>/output/metrics_details.json
results/<SIMULATION_ID>/evaluation_results/sparse4d/detection_results/<BEV_SENSOR>/output/detection_metrics.csv
results/<SIMULATION_ID>/evaluation_results/detection_metrics_summary.csv
```

Each per-sensor `detection_metrics.csv` is also printed to the terminal as a
Pandas dataframe. The combined `detection_metrics_summary.csv` is uploaded to:

```text
<AWS_S3_BASE_PREFIX_PATH><SIMULATION_ID>/evaluation_results/detection_metrics_summary.csv
```

## Tests

Useful focused test commands:

```bash
python -m pytest tests/validation/test_bev_utils.py
python -m pytest tests/eval/detection/test_evaluate.py::TestSaveDetectionResults
```

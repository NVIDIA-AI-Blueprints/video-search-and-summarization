# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
AICity Challenge MTMC (Multi-Camera 3D People Tracking) Evaluation API.

Reproduces the per-scene, per-class HOTA evaluation protocol used by the
official AICity Challenge MTMC validation server on top of
``spatialai_data_utils``' bundled TrackEval library so it can be re-run
locally without standing up a separate evaluation repo.  The evaluation
logic is year-agnostic — the same module runs the 2025 submissions and
is intended to handle the 2026 edition as long as the submission text
format and class-id table stay compatible.  The class table and scene
mapping that change year-to-year are exposed as data
(:mod:`spatialai_data_utils.datasets.aicity25.spec` and the JSON shipped
under
:func:`spatialai_data_utils.datasets.aicity25.load_default_scene_id_to_name`),
so no algorithmic code needs to change when the spec moves forward.

The module exposes four public callables:

* :func:`split_aicity_mtmc_per_scene_per_class` — stream-split an AICity
  MTMC submission / GT text file into per-(scene, class) MOT-format
  files (also useful on its own for downstream per-class analyses /
  visualizations / custom evaluators).
* :func:`run_aicity_mtmc_evaluation` — end-to-end orchestrator that
  splits inputs, runs TrackEval HOTA per (scene, class), and returns a
  results dict with per-class, per-scene, and GT-object-count-weighted
  final HOTA / DetA / AssA / LocA.
* :func:`print_aicity_mtmc_summary` — log a formatted per-(scene,
  class) and per-scene summary table to a caller-supplied logger or to
  this module's default logger.
* :func:`save_aicity_mtmc_results` — persist a results dict to
  ``<output_dir>/aicity_mtmc_hota_summary.json`` in the 0-100 scale
  used by the official validation server.

The CLI wrapper for this API lives at
``tools/evaluation/evaluate_aicity_mtmc.py``.

Input format (both ground truth and predictions are space-separated text):

    <scene_id> <class_id> <object_id> <frame_id> <x> <y> <z> <w> <l> <h> <yaw>

with ``frame_id`` 0-indexed and ``yaw`` in radians.

The class-id table and the per-row field count are spec metadata.
The 2026 edition's table lives in
:mod:`spatialai_data_utils.datasets.aicity26.spec` (Person, Forklift,
NovaCarter, Transporter, FourierGR1T2, AgilityDigit at IDs 0-5 plus
``PalletTruck`` at ID 6) and is imported here as the **project-wide
default**.  The 2025 edition's original six-class table lives at
:mod:`spatialai_data_utils.datasets.aicity25.spec`.  Both
:func:`split_aicity_mtmc_per_scene_per_class` and
:func:`run_aicity_mtmc_evaluation` accept an optional
``class_id_to_name`` argument; pass the 2025 table explicitly to
evaluate 2025 submissions against the original six-class table::

    from spatialai_data_utils.datasets.aicity25.spec import (
        CLASS_ID_TO_NAME as AICITY25_CLASS_ID_TO_NAME,
    )
    from spatialai_data_utils.datasets.aicity25 import (
        load_default_scene_id_to_name as load_default_aicity25_scenes,
    )

    results = run_aicity_mtmc_evaluation(
        ground_truth_file=...,
        prediction_file=...,
        scene_id_to_name=load_default_aicity25_scenes(),
        class_id_to_name=AICITY25_CLASS_ID_TO_NAME,
        ...,
    )

Prediction rows whose ``class_id`` is not in the active table are
rejected as invalid; GT rows are warned and skipped.  The CLI wrapper
exposes the same selector as ``--edition {2025,2026}`` (default
``2026``).

Evaluation protocol:

1. Truncate to the first ``num_frames_to_eval`` frames per scene
   (matches the validation server's default of 9000).
2. Split GT / pred into ``<scene>/<class>/`` MOT-format text files.
3. For each (scene, class) pair that has *both* GT and pred rows, run
   TrackEval's HOTA metric on ``MTMCChallenge3DBBox`` (3D IoU matching,
   the official metric) or ``MTMCChallenge3DLocation`` (centre
   distance, useful for ablation).
4. Per-scene HOTA / DetA / AssA / LocA is the unweighted mean over
   classes that successfully evaluated for that scene.
5. Final HOTA / DetA / AssA / LocA is the **GT-object-count-weighted**
   mean of the per-scene numbers (weight = number of GT object-frame
   rows from that scene that survived the frame-count truncation).
"""

import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

import numpy as np

from spatialai_data_utils.datasets.aicity26.spec import (
    CLASS_ID_TO_NAME as _DEFAULT_CLASS_ID_TO_NAME_AICITY26,
    NUM_FIELDS,
)
from spatialai_data_utils.eval.tracking.hota.trackeval_utils import (
    prepare_evaluation_folder,
    run_evaluation,
    setup_evaluation_configs,
)


# Re-export of the project-wide default class-id table.  As of the
# 2026-default switch this resolves to the AICity'26 spec (six 2025
# classes plus ``PalletTruck`` at ID 6); the previous 2025 default
# remains accessible via ``spatialai_data_utils.datasets.aicity25.spec``
# for callers that need the older table verbatim.  The CLI's
# ``--edition`` flag and the per-function ``class_id_to_name`` keyword
# are still the recommended ways to pin a specific edition.
CLASS_ID_TO_NAME: Dict[int, str] = _DEFAULT_CLASS_ID_TO_NAME_AICITY26


logger = logging.getLogger(__name__)


# The four HOTA sub-fields we report.  TrackEval emits arrays indexed by
# IoU threshold (0.05..0.95); we take the mean to match the
# competition's "averaged HOTA" headline number.
HOTA_FIELDS: List[str] = ["HOTA", "DetA", "AssA", "LocA"]

# Per-tracker label used in TrackEval's folder layout.  Matches the
# default used by
# ``spatialai_data_utils.eval.tracking.hota.trackeval_utils.prepare_evaluation_folder``
# so we can index ``output_res[dataset_name]["data"][seq_name]`` below.
_TRACKER_NAME = "data"

# Filename written by :func:`save_aicity_mtmc_results`.
_SUMMARY_JSON_NAME = "aicity_mtmc_hota_summary.json"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _aicity_line_to_mot(parts: List[str]) -> str:
    """Convert one AICity MTMC row into the MOT line TrackEval expects.

    Input (space-separated, ``frame_id`` 0-indexed)::

        scene_id class_id object_id frame_id x y z w l h yaw

    Output (space-separated, ``frame_id`` 1-indexed; pitch/roll are
    zero because the AICity MTMC format only carries yaw)::

        frame_id object_id 1 x y z w l h pitch roll yaw

    The ``"1"`` is TrackEval's confidence column.  We always set it to
    1.0 because the AICity MTMC text format does not carry a
    per-detection confidence — submissions are expected to be already
    filtered by the contestant.
    """
    object_id = int(parts[2])
    frame_id = int(parts[3]) + 1
    x, y, z = float(parts[4]), float(parts[5]), float(parts[6])
    # Spell out `length` instead of `l` to dodge Ruff E741 (ambiguous
    # one-letter variable name — `l` is too close to `1`/`I`).
    width, length, height = float(parts[7]), float(parts[8]), float(parts[9])
    yaw = float(parts[10])
    pitch = 0.0
    roll = 0.0
    return (
        f"{frame_id} {object_id} 1 "
        f"{x:.5f} {y:.5f} {z:.5f} "
        f"{width:.5f} {length:.5f} {height:.5f} "
        f"{pitch:.5f} {roll:.5f} {yaw:.5f}\n"
    )


def split_aicity_mtmc_per_scene_per_class(
    input_path: str,
    output_root: str,
    file_basename: str,
    scene_id_to_name: Dict[str, str],
    num_frames_to_eval: int,
    is_pred: bool,
    class_id_to_name: Optional[Dict[int, str]] = None,
    frame_start: int = 0,
) -> Dict[str, Dict[str, int]]:
    """Stream-split an AICity MTMC file into per-(scene, class) MOT files.

    Output layout::

        <output_root>/<scene_name>/<class_name>/<file_basename>

    Lines whose 0-indexed ``frame_id`` is outside
    ``[frame_start, num_frames_to_eval)`` are dropped (the default
    ``[0, 9000)`` window matches the official validation server's
    truncation; pass a non-zero ``frame_start`` to evaluate a
    later-frame slice, e.g. the second half of the test set).  Lines
    whose scene id is not in *scene_id_to_name* are dropped silently
    for GT and raise for predictions — the prediction file must
    declare which scenes it intends to compete on.

    :param input_path: Path to the AICity MTMC text file (GT or
        submission).  Each row must have exactly 11 space-separated
        fields; malformed rows are flagged as warnings for GT and as
        hard errors for predictions.
    :param output_root: Directory under which the per-(scene, class)
        sub-trees are created.  Must already exist; will be augmented
        in place.
    :param file_basename: Filename to write under each ``<scene>/<class>/``
        directory — e.g. ``"gt.txt"`` or ``"pred.txt"``.
    :param scene_id_to_name: Mapping ``{scene_id_str: scene_name}`` that
        selects the scenes to evaluate; column 0 values not in the
        mapping are dropped (GT) or rejected (predictions).
    :param num_frames_to_eval: Frame-count truncation per scene
        (0-indexed exclusive upper bound).
    :param frame_start: 0-indexed inclusive lower bound for ``frame_id``.
        Defaults to ``0`` (the official validation server's behaviour).
        Combined with *num_frames_to_eval* this defines an arbitrary
        half-open window ``[frame_start, num_frames_to_eval)`` per
        scene — e.g. ``frame_start=4500, num_frames_to_eval=9000``
        evaluates the second half of a 9000-frame scene.  Must be in
        ``[0, num_frames_to_eval)`` — an out-of-range value raises
        ``ValueError``.
    :param is_pred: Controls error semantics — ``True`` raises on any
        malformed / out-of-spec row (submission must be valid),
        ``False`` warns and skips (GT can have extra scenes the user
        doesn't want to evaluate against).
    :param class_id_to_name: Optional ``{class_id: class_name}``
        table.  Defaults to the AICity'26 spec
        (:data:`spatialai_data_utils.datasets.aicity26.spec.CLASS_ID_TO_NAME`)
        — the project-wide default since the 2026-edition switch.  Pass
        the AICity'25 spec
        (:data:`spatialai_data_utils.datasets.aicity25.spec.CLASS_ID_TO_NAME`)
        to evaluate against the original six-class table (no
        ``PalletTruck``).  Rows whose ``class_id`` is not in the active
        table are warned-and-skipped for GT and rejected for
        predictions; the class names from this table are used verbatim
        as the per-class output directory names.
    :return: ``{scene_name: {class_name: number_of_rows_written}}``
        — used both for the per-scene weight and as a sanity log.
    :raises ValueError: If ``frame_start`` is negative or not strictly
        less than ``num_frames_to_eval`` (an empty window is rejected
        rather than silently producing no results).
    """
    if frame_start < 0:
        raise ValueError(f"frame_start must be >= 0, got {frame_start}.")
    if frame_start >= num_frames_to_eval:
        raise ValueError(
            f"frame_start ({frame_start}) must be < num_frames_to_eval "
            f"({num_frames_to_eval}); window would be empty."
        )
    if class_id_to_name is None:
        class_id_to_name = _DEFAULT_CLASS_ID_TO_NAME_AICITY26
    scenes_str_to_name = {str(k): v for k, v in scene_id_to_name.items()}
    valid_scene_ids = set(scenes_str_to_name.keys())
    valid_class_ids = set(class_id_to_name.keys())
    source = "predictions" if is_pred else "ground truth"

    writers: Dict[tuple, Any] = {}
    counts: Dict[str, Dict[str, int]] = {}

    try:
        with open(input_path, "r") as fp:
            for line_no, line in enumerate(fp, start=1):
                stripped = line.rstrip("\n")
                if not stripped.strip():
                    continue
                parts = stripped.split(" ")

                if len(parts) != NUM_FIELDS:
                    if is_pred:
                        raise ValueError(
                            f"Invalid entry in {source} at line {line_no}: "
                            f"each row must have {NUM_FIELDS} "
                            f"fields "
                            f"(scene_id class_id object_id frame_id x y z "
                            f"w l h yaw). Got {len(parts)}: {stripped!r}"
                        )
                    logger.warning(
                        "Skipping %s line %d with %d fields (expected %d): %r",
                        source, line_no, len(parts), NUM_FIELDS,
                        stripped,
                    )
                    continue

                scene_id_str = parts[0]
                try:
                    class_id = int(parts[1])
                    frame_id_0based = int(parts[3])
                except ValueError as exc:
                    if is_pred:
                        raise ValueError(
                            f"Non-numeric class_id / frame_id in {source} "
                            f"at line {line_no}: {stripped!r}"
                        ) from exc
                    logger.warning(
                        "Skipping %s line %d with non-numeric "
                        "class_id/frame_id: %r",
                        source, line_no, stripped,
                    )
                    continue

                if scene_id_str not in valid_scene_ids:
                    if is_pred:
                        raise ValueError(
                            f"Prediction line {line_no} references scene "
                            f"id {scene_id_str!r} not declared in the "
                            f"scene-id mapping JSON. Declared scenes: "
                            f"{sorted(valid_scene_ids)}."
                        )
                    continue

                if class_id not in valid_class_ids:
                    if is_pred:
                        raise ValueError(
                            f"Prediction line {line_no} references class "
                            f"id {class_id}; valid AICity MTMC "
                            f"class ids are {sorted(valid_class_ids)}."
                        )
                    # GT silently dropped these before, which made it
                    # impossible to notice an --edition mismatch (e.g. a
                    # 2026 GT with PalletTruck rows being run against the
                    # 2025 default table would just lose every
                    # PalletTruck row with no log line). Warn + skip so
                    # the operator gets one line per offending GT row
                    # and can re-run with the right --edition.
                    logger.warning(
                        "Skipping %s line %d: class_id %d is not in the "
                        "active class-id table %s. Pick a different "
                        "edition's class table via the CLI --edition "
                        "flag (or pass class_id_to_name=... at the API "
                        "level) if you intended to evaluate against it.",
                        source, line_no, class_id, sorted(valid_class_ids),
                    )
                    continue

                if frame_id_0based < frame_start or frame_id_0based >= num_frames_to_eval:
                    continue

                scene_name = scenes_str_to_name[scene_id_str]
                class_name = class_id_to_name[class_id]

                key = (scene_name, class_name)
                if key not in writers:
                    class_dir = os.path.join(output_root, scene_name, class_name)
                    os.makedirs(class_dir, exist_ok=True)
                    writers[key] = open(os.path.join(class_dir, file_basename), "w")

                writers[key].write(_aicity_line_to_mot(parts))
                counts.setdefault(scene_name, {}).setdefault(class_name, 0)
                counts[scene_name][class_name] += 1
    finally:
        for writer in writers.values():
            writer.close()

    return counts


def _run_hota_for_scene_class(
    gt_mot_file: str,
    pred_mot_file: str,
    work_dir: str,
    eval_type: str,
    num_cores: int,
    seq_name: str,
    seq_length: int,
    fps: float,
    quiet: bool,
) -> Optional[Dict[str, float]]:
    """Run HOTA for a single (scene, class) pair on MOT-format files.

    Sets up TrackEval's expected folder layout under ``work_dir`` via
    :func:`setup_evaluation_configs` /
    :func:`prepare_evaluation_folder`, copies the already-MOT-formatted
    GT and prediction files into place, and runs the bundled
    :class:`MTMCChallenge3DBBox` (``eval_type='bbox'``) or
    :class:`MTMCChallenge3DLocation` (``eval_type='location'``)
    evaluator on a single sequence.

    Returns ``{field: float}`` (mean across IoU thresholds) for every
    field in :data:`HOTA_FIELDS`, or ``None`` if the evaluation could
    not be run (missing/empty inputs, unexpected output shape, or an
    exception inside TrackEval).
    """
    if not os.path.exists(gt_mot_file) or os.path.getsize(gt_mot_file) == 0:
        return None
    if not os.path.exists(pred_mot_file) or os.path.getsize(pred_mot_file) == 0:
        return None

    dataset_config, eval_config = setup_evaluation_configs(
        work_dir, eval_type, num_cores,
    )
    # Silence TrackEval's own per-class summary tables — we always
    # render a single combined summary at the very end of the run, so
    # the per-(scene, class) metric tables (HOTA / CLEAR / Identity)
    # that the bundled Evaluator prints via ``print()`` would only
    # drown out our output.  These overrides also disable TrackEval's
    # plot/PR-curve writes, which we never consult here.
    eval_config["PRINT_RESULTS"] = False
    eval_config["PRINT_CONFIG"] = False
    eval_config["TIME_PROGRESS"] = False
    eval_config["DISPLAY_LESS_PROGRESS"] = True
    eval_config["OUTPUT_SUMMARY"] = False
    eval_config["OUTPUT_DETAILED"] = False
    eval_config["PLOT_CURVES"] = False

    out_pred_path, out_gt_path = prepare_evaluation_folder(
        dataset_config, seq_name, fps=fps, seq_length=seq_length,
    )

    with open(gt_mot_file, "r") as src, open(out_gt_path, "w") as dst:
        dst.write(src.read())
    with open(pred_mot_file, "r") as src, open(out_pred_path, "w") as dst:
        dst.write(src.read())

    # TrackEval also calls ``logging.info(...)`` on the root logger
    # (e.g. "Evaluating X..." for every sequence) — those are the
    # records we suppress when the caller opts into *quiet*.  The
    # filter is attached temporarily so this stays safe for callers
    # that have their own logging config.  Not fully thread-safe (a
    # concurrent thread could emit an INFO root record while the
    # filter is attached), but safe enough for the single-threaded
    # USE_PARALLEL=True / one-seq path used here.
    root_logger = logging.getLogger()
    _filter = None
    if quiet:
        _filter = logging.Filter()
        _filter.filter = lambda record: record.levelno >= logging.WARNING
        root_logger.addFilter(_filter)
    try:
        output_res, _output_msg = run_evaluation(
            out_gt_path, out_pred_path, dataset_config, eval_config, eval_type,
        )
    except Exception:
        logger.exception("HOTA evaluation failed for %s", work_dir)
        return None
    finally:
        if _filter is not None:
            root_logger.removeFilter(_filter)

    dataset_name = (
        "MTMCChallenge3DBBox" if eval_type == "bbox"
        else "MTMCChallenge3DLocation"
    )
    try:
        hota_result = output_res[dataset_name][_TRACKER_NAME][seq_name]["class"]["HOTA"]
    except KeyError:
        logger.warning(
            "Unexpected HOTA result shape for %s; skipping.", work_dir,
        )
        return None

    return {field: float(np.mean(hota_result[field])) for field in HOTA_FIELDS}


def _weighted_average(weights: Dict[str, int], values: Dict[str, float]) -> float:
    """Weight per-scene metric *values* by per-scene GT *weights*.

    Matches the official AICity MTMC validation rule: scenes that
    lack either a weight or a value are dropped (rather than treated as
    zero), so a scene with no surviving GT after truncation does not
    pull the final number down.
    """
    common = set(weights) & set(values)
    if not common:
        return 0.0
    numerator = sum(weights[k] * values[k] for k in common)
    denominator = sum(weights[k] for k in common)
    return numerator / denominator if denominator else 0.0


# ---------------------------------------------------------------------------
# End-to-end orchestrator
# ---------------------------------------------------------------------------


def run_aicity_mtmc_evaluation(
    ground_truth_file: str,
    prediction_file: str,
    scene_id_to_name: Dict[str, str],
    output_dir: Optional[str] = None,
    num_cores: int = 1,
    num_frames_to_eval: int = 9000,
    eval_type: str = "bbox",
    fps: float = 30.0,
    quiet: bool = True,
    class_id_to_name: Optional[Dict[int, str]] = None,
    frame_start: int = 0,
) -> Dict[str, Any]:
    """End-to-end orchestrator for one (gt, pred) AICity MTMC evaluation.

    Implements the official AICity Challenge MTMC evaluation flow:

    1. (Re)create ``output_dir`` — or use a temp dir when ``None`` is
       supplied — and split GT / pred into ``<scene>/<class>/`` MOT
       files there.
    2. For each (scene, class) pair that has both a non-empty GT and
       pred file, run :func:`_run_hota_for_scene_class` to get
       per-class HOTA / DetA / AssA / LocA.
    3. Per-scene metric = unweighted mean across classes that ran;
       final metric = per-scene mean weighted by the number of GT
       rows from each scene that survived the
       ``num_frames_to_eval`` truncation.

    :param ground_truth_file: Path to the AICity MTMC GT text file.
    :param prediction_file: Path to the AICity MTMC submission text
        file.
    :param scene_id_to_name: Mapping ``{scene_id_str: scene_name}`` for
        the scenes to evaluate.  Use
        :func:`spatialai_data_utils.datasets.aicity25.load_default_scene_id_to_name`
        for the 2025 edition's scenes (17-20) or
        :func:`spatialai_data_utils.datasets.aicity26.load_default_scene_id_to_name`
        for the 2026 edition's scenes (23-25).
    :param output_dir: Where to write intermediate per-(scene, class)
        split files and TrackEval scratch artefacts.  When ``None`` a
        temp dir is created and removed on return — useful for
        notebooks / CI runs that only care about the in-memory
        results.
    :param num_cores: Number of cores forwarded to TrackEval's
        ``NUM_PARALLEL_CORES``.  Has near-zero impact because we run
        TrackEval once per (scene, class) pair with a single
        sequence.
    :param num_frames_to_eval: Frame-count truncation per scene
        (0-indexed exclusive upper bound).  Defaults to the official
        validation server's ``9000``.  Combined with ``frame_start``
        this defines an arbitrary half-open window
        ``[frame_start, num_frames_to_eval)`` per scene.
    :param eval_type: ``"bbox"`` for 3D-IoU matching (the official
        AICity MTMC metric) or ``"location"`` for centre-distance
        matching.
    :param fps: FPS written into TrackEval's per-sequence
        ``seqinfo.ini``.  Cosmetic for single-sequence runs.
    :param quiet: Suppress TrackEval's per-class ``INFO`` records and
        per-class metric tables (kept ``True`` by default because
        the orchestrator already emits its own per-(scene, class)
        progress logs).
    :param class_id_to_name: Optional ``{class_id: class_name}``
        spec table to validate against.  Defaults to the AICity'25
        spec (six classes, IDs 0-5).  Pass the AICity'26 spec table
        (which adds ``PalletTruck`` at ID 6) to evaluate 2026
        submissions without dropping the new class.
    :param frame_start: 0-indexed inclusive lower bound for
        ``frame_id``.  Defaults to ``0`` (the official validation
        server's behaviour).  Set this together with
        ``num_frames_to_eval`` to evaluate a non-prefix slice — e.g.
        ``frame_start=4500, num_frames_to_eval=9000`` runs HOTA on
        the second half of a 9000-frame scene.  Must be in
        ``[0, num_frames_to_eval)``; an out-of-range value raises
        ``ValueError``.
    :return: A nested dict with keys ``"eval_type"``,
        ``"num_frames_to_eval"``, ``"scene_id_to_name"``,
        ``"per_scene_object_counts"``, ``"per_scene_per_class"``,
        ``"per_scene"``, and ``"final"``.  Metric values are stored
        as ratios in ``[0, 1]``;
        :func:`save_aicity_mtmc_results` multiplies them by 100
        on disk for human readability, matching the convention used
        by the official validation server.
    """
    if not scene_id_to_name:
        raise ValueError("scene_id_to_name is empty; nothing to evaluate.")

    _temp_dir_handle: Optional[tempfile.TemporaryDirectory] = None
    if output_dir is None:
        _temp_dir_handle = tempfile.TemporaryDirectory(prefix="aicity_mtmc_eval_")
        output_dir = _temp_dir_handle.name
        logger.info("No output_dir specified; using temp dir %s", output_dir)
    else:
        os.makedirs(output_dir, exist_ok=True)

    try:
        split_root = os.path.join(output_dir, "split")
        os.makedirs(split_root, exist_ok=True)

        logger.info(
            "Splitting GT (%s) and predictions (%s) into per-scene/per-class "
            "MOT files under %s ...",
            ground_truth_file, prediction_file, split_root,
        )
        gt_counts = split_aicity_mtmc_per_scene_per_class(
            ground_truth_file, split_root, "gt.txt",
            scene_id_to_name, num_frames_to_eval, is_pred=False,
            class_id_to_name=class_id_to_name,
            frame_start=frame_start,
        )
        pred_counts = split_aicity_mtmc_per_scene_per_class(
            prediction_file, split_root, "pred.txt",
            scene_id_to_name, num_frames_to_eval, is_pred=True,
            class_id_to_name=class_id_to_name,
            frame_start=frame_start,
        )

        scene_object_counts: Dict[str, int] = {
            scene: sum(class_counts.values())
            for scene, class_counts in gt_counts.items()
        }
        pred_object_counts: Dict[str, int] = {
            scene: sum(class_counts.values())
            for scene, class_counts in pred_counts.items()
        }
        logger.info("GT rows per scene (post-truncation): %s", scene_object_counts)
        logger.info("Pred rows per scene (post-truncation): %s", pred_object_counts)

        # --- Per-(scene, class) HOTA + per-scene aggregation. ---
        per_scene_per_class: Dict[str, Dict[str, Optional[Dict[str, float]]]] = {}
        per_scene: Dict[str, Dict[str, float]] = {}

        scene_names = sorted(
            set(gt_counts) | set(pred_counts),
            key=lambda s: (scene_object_counts.get(s, 0) == 0, s),
        )

        for scene_name in scene_names:
            scene_dir = os.path.join(split_root, scene_name)
            if not os.path.isdir(scene_dir):
                continue
            class_results: Dict[str, Optional[Dict[str, float]]] = {}

            class_names = sorted(
                set(gt_counts.get(scene_name, {}).keys())
                | set(pred_counts.get(scene_name, {}).keys())
            )

            for class_name in class_names:
                class_dir = os.path.join(scene_dir, class_name)
                gt_file = os.path.join(class_dir, "gt.txt")
                pred_file = os.path.join(class_dir, "pred.txt")
                if not (os.path.exists(gt_file) and os.path.exists(pred_file)):
                    logger.info(
                        "  %s/%s: skipping (missing %s)",
                        scene_name, class_name,
                        "GT" if not os.path.exists(gt_file) else "pred",
                    )
                    class_results[class_name] = None
                    continue

                t0 = time.time()
                metrics = _run_hota_for_scene_class(
                    gt_file, pred_file,
                    work_dir=os.path.join(class_dir, "trackeval"),
                    eval_type=eval_type,
                    num_cores=num_cores,
                    seq_name=scene_name,
                    seq_length=num_frames_to_eval,
                    fps=fps,
                    quiet=quiet,
                )
                elapsed = time.time() - t0
                class_results[class_name] = metrics
                if metrics is None:
                    logger.info(
                        "  %s/%s: FAILED (%.1fs)",
                        scene_name, class_name, elapsed,
                    )
                else:
                    logger.info(
                        "  %s/%s: HOTA=%.2f DetA=%.2f AssA=%.2f LocA=%.2f (%.1fs)",
                        scene_name, class_name,
                        metrics["HOTA"] * 100, metrics["DetA"] * 100,
                        metrics["AssA"] * 100, metrics["LocA"] * 100,
                        elapsed,
                    )

            per_scene_per_class[scene_name] = class_results

            valid = [m for m in class_results.values() if m is not None]
            if not valid:
                logger.warning(
                    "Scene %s: no class evaluated; excluding from final mean.",
                    scene_name,
                )
                continue
            per_scene[scene_name] = {
                f: float(np.mean([m[f] for m in valid])) for f in HOTA_FIELDS
            }

        # --- GT-count-weighted final aggregate. ---
        final: Dict[str, float] = {
            f: _weighted_average(
                scene_object_counts,
                {s: m[f] for s, m in per_scene.items()},
            )
            for f in HOTA_FIELDS
        }

        return {
            "eval_type": eval_type,
            "num_frames_to_eval": num_frames_to_eval,
            "scene_id_to_name": dict(scene_id_to_name),
            "per_scene_object_counts": scene_object_counts,
            "per_scene_per_class": {
                scene: {
                    cls: (None if m is None else {f: m[f] for f in HOTA_FIELDS})
                    for cls, m in classes.items()
                }
                for scene, classes in per_scene_per_class.items()
            },
            "per_scene": per_scene,
            "final": final,
        }
    finally:
        if _temp_dir_handle is not None:
            _temp_dir_handle.cleanup()


# ---------------------------------------------------------------------------
# Presentation / persistence
# ---------------------------------------------------------------------------


def print_aicity_mtmc_summary(results: Dict[str, Any]) -> None:
    """Log a compact two-section summary table for an evaluation result.

    Section 1 lists per-(scene, class) results — one line per class
    with a metric value, or ``--`` when the (scene, class) pair was
    skipped (missing GT or pred file, or an unexpected TrackEval
    failure).  Section 2 lists the per-scene means, the per-scene
    GT-row weight used by the validation server, and the final
    weighted aggregate.

    All metric values are scaled to the 0-100 range expected by the
    official leaderboard.  Output goes to this module's logger, so
    callers control whether it's printed (e.g.
    ``logging.basicConfig(level=logging.INFO)``).

    :param results: A dict as returned by
        :func:`run_aicity_mtmc_evaluation`.
    """
    per_scene_per_class = results["per_scene_per_class"]
    per_scene = results["per_scene"]
    per_scene_object_count = results["per_scene_object_counts"]
    final = results["final"]

    fields = HOTA_FIELDS
    scene_col_w = 20
    class_col_w = 22
    header = (
        "  " + "Scene".ljust(scene_col_w) + "Class".ljust(class_col_w)
        + "".join(f"{f:>9s}" for f in fields)
    )
    sep = "  " + "-" * (len(header) - 2)

    logger.info("")
    logger.info("=" * len(header))
    logger.info("  Per-(scene, class) HOTA results")
    logger.info("=" * len(header))
    logger.info(header)
    logger.info(sep)

    for scene_name in sorted(per_scene_per_class):
        scene_classes = per_scene_per_class[scene_name]
        scene_label = scene_name
        for cls_name in sorted(scene_classes):
            metrics = scene_classes[cls_name]
            row = (
                "  "
                + scene_label.ljust(scene_col_w)
                + cls_name.ljust(class_col_w)
            )
            if metrics is None:
                # Pad the metric columns with "--" placeholders so the
                # row width matches the data rows.
                row += "".join(f"{'--':>9s}" for _ in fields)
            else:
                row += "".join(f"{metrics[f] * 100:9.2f}" for f in fields)
            logger.info(row)
            scene_label = ""  # blank for continuation rows
        logger.info(sep)

    logger.info("")
    logger.info("=" * len(header))
    logger.info("  Per-scene HOTA (mean across classes) and weighted aggregate")
    logger.info("=" * len(header))
    weight_col_w = 10
    weight_header = (
        "  " + "Scene".ljust(scene_col_w) + f"{'Weight':>{weight_col_w}s}"
        + "".join(f"{f:>9s}" for f in fields)
    )
    weight_sep = "  " + "-" * (len(weight_header) - 2)
    logger.info(weight_header)
    logger.info(weight_sep)
    for scene_name in sorted(per_scene):
        weight = per_scene_object_count.get(scene_name, 0)
        metrics = per_scene[scene_name]
        values_str = "".join(f"{metrics[f] * 100:9.2f}" for f in fields)
        logger.info(
            f"  {scene_name:<{scene_col_w}s}"
            f"{weight:{weight_col_w}d}{values_str}"
        )
    logger.info(weight_sep)
    total_weight = sum(
        per_scene_object_count.get(s, 0) for s in per_scene
    )
    final_values = "".join(f"{final[f] * 100:9.2f}" for f in fields)
    logger.info(
        f"  {'WEIGHTED FINAL':<{scene_col_w}s}"
        f"{total_weight:{weight_col_w}d}{final_values}"
    )
    logger.info("")


def save_aicity_mtmc_results(
    results: Dict[str, Any],
    output_dir: str,
) -> str:
    """Persist evaluation *results* to ``<output_dir>/aicity_mtmc_hota_summary.json``.

    The on-disk JSON multiplies every metric by 100 (matching the 0-100
    scale used by the official leaderboard and by
    :func:`print_aicity_mtmc_summary`).  The in-memory *results*
    dict is **not** mutated.

    :param results: A dict as returned by
        :func:`run_aicity_mtmc_evaluation`.
    :param output_dir: Directory the JSON file is written under (will
        be created if missing).
    :return: Absolute path to the written JSON file.
    """
    os.makedirs(output_dir, exist_ok=True)
    summary_path = os.path.join(output_dir, _SUMMARY_JSON_NAME)

    per_scene_per_class = results["per_scene_per_class"]
    per_scene = results["per_scene"]
    final = results["final"]

    disk_payload = {
        "eval_type": results["eval_type"],
        "num_frames_to_eval": results["num_frames_to_eval"],
        "scene_id_to_name": results["scene_id_to_name"],
        "per_scene_object_counts": results["per_scene_object_counts"],
        "per_scene_per_class": {
            scene: {
                cls: (None if m is None
                      else {f: round(m[f] * 100, 4) for f in HOTA_FIELDS})
                for cls, m in classes.items()
            }
            for scene, classes in per_scene_per_class.items()
        },
        "per_scene": {
            scene: {f: round(m[f] * 100, 4) for f in HOTA_FIELDS}
            for scene, m in per_scene.items()
        },
        "final": {f: round(final[f] * 100, 4) for f in HOTA_FIELDS},
    }

    with open(summary_path, "w") as fp:
        json.dump(disk_payload, fp, indent=2)
    logger.info("Wrote summary metrics to: %s", summary_path)

    return summary_path

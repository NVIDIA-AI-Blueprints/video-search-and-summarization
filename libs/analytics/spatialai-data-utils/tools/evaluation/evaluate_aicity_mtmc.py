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
CLI wrapper around the AICity Challenge MTMC evaluation API.

All of the actual evaluation logic lives in
:mod:`spatialai_data_utils.eval.tracking.aicity_mtmc_eval`; this script
is a thin argparse + logging shell that wires the CLI flags into
``run_aicity_mtmc_evaluation`` / ``print_aicity_mtmc_summary`` /
``save_aicity_mtmc_results`` so the same pipeline is callable from a
shell and from Python notebooks / CI scripts.

The evaluation handles both the 2025 and 2026 editions of the AICity
Challenge MTMC track — pick the edition with ``--edition``; the
**default is ``2026``** (the current edition).  The class table and
default scene mapping for each edition live in the per-edition sibling
packages
:mod:`spatialai_data_utils.datasets.aicity25` /
:mod:`spatialai_data_utils.datasets.aicity26`; this CLI imports both
and selects between them based on ``--edition``.

Example (2026 default)::

    python tools/evaluation/evaluate_aicity_mtmc.py \\
        --ground_truth_file  data/aicity26/ground_truth/ground_truth.txt \\
        --input_file         /path/to/your_2026_submission.txt \\
        --output_dir         /tmp/aicity_mtmc_eval \\
        --quiet

Example (2025)::

    python tools/evaluation/evaluate_aicity_mtmc.py \\
        --edition            2025 \\
        --ground_truth_file  data/aicity25/ground_truth/ground_truth.txt \\
        --input_file         data/aicity25/v0.6.0/aicity25_submissions_all/R101_iter_4684_conf05/track1_fixed.txt \\
        --output_dir         /tmp/aicity_mtmc_eval \\
        --quiet

When ``--scene_id_2_scene_name_file`` is omitted the tool falls back to
the chosen edition's bundled mapping (``17``-``20`` →
``Warehouse_017``-``Warehouse_020`` for 2025; ``23``-``25`` →
``Warehouse_023``-``Warehouse_025`` for 2026).  Pass
``--scene_id_2_scene_name_file`` explicitly to evaluate a custom
subset of either edition.
"""

import argparse
import logging
import time
from typing import Dict, Tuple

from spatialai_data_utils.datasets import aicity25, aicity26
from spatialai_data_utils.datasets.aicity25.spec import (
    CLASS_ID_TO_NAME as AICITY25_CLASS_ID_TO_NAME,
)
from spatialai_data_utils.datasets.aicity26.spec import (
    CLASS_ID_TO_NAME as AICITY26_CLASS_ID_TO_NAME,
)
from spatialai_data_utils.utils.filesystem_utils import (
    load_json_from_file,
    validate_readable_file,
)
from spatialai_data_utils.eval.tracking.aicity_mtmc_eval import (
    print_aicity_mtmc_summary,
    run_aicity_mtmc_evaluation,
    save_aicity_mtmc_results,
)


logger = logging.getLogger(__name__)


# Per-edition spec bundle: the dataset sub-package (providing the
# default scene mapping helpers) plus the spec's class-id table.
_EDITIONS: Dict[str, Tuple[object, Dict[int, str]]] = {
    "2025": (aicity25, AICITY25_CLASS_ID_TO_NAME),
    "2026": (aicity26, AICITY26_CLASS_ID_TO_NAME),
}


def parse_args() -> argparse.Namespace:
    """Parse the CLI arguments for the AICity MTMC evaluator."""
    parser = argparse.ArgumentParser(
        description=(
            "AICity Challenge MTMC (Multi-Camera 3D People Tracking) "
            "evaluation. Computes per-scene HOTA / DetA / AssA / LocA "
            "on the official MTMC submission text format and reports "
            "the GT-object-count-weighted mean used by the competition "
            "validation server."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--edition",
        choices=sorted(_EDITIONS.keys()),
        default="2026",
        help="AICity Challenge edition to evaluate.  Selects the spec's "
        "class-id table and the default scene mapping.  '2025' = "
        "six classes (IDs 0-5) and scenes 17-20; '2026' = same six "
        "classes plus PalletTruck at ID 6 and scenes 23-25.  "
        "Default: 2026 (the current edition); pass --edition 2025 "
        "to evaluate against the original six-class table.",
    )
    parser.add_argument(
        "--ground_truth_file",
        type=validate_readable_file,
        required=True,
        help="Path to the AICity MTMC ground-truth text file.",
    )
    parser.add_argument(
        "--input_file",
        type=validate_readable_file,
        required=True,
        help="Path to the AICity MTMC prediction text file "
        "(a single submission, in the official space-separated "
        "11-column format).",
    )
    parser.add_argument(
        "--scene_id_2_scene_name_file",
        type=validate_readable_file,
        default=None,
        help="Optional JSON mapping {scene_id_str: scene_name} for the "
        "scenes to evaluate. Scenes outside this mapping are "
        "dropped from GT and rejected from predictions. When "
        "omitted, falls back to the bundled default mapping for "
        "the chosen --edition.",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Optional output directory. When set, the per-(scene, "
        "class) split files, TrackEval scratch artefacts, and "
        "the final aicity_mtmc_hota_summary.json are written "
        "here. When omitted, a tempdir is used and removed at "
        "the end of the run (no JSON summary is persisted).",
    )
    parser.add_argument(
        "--num_cores",
        type=int,
        default=1,
        help="Number of cores forwarded to TrackEval. Has negligible "
        "effect here because we run TrackEval once per "
        "(scene, class) pair with a single sequence. Default: 1.",
    )
    parser.add_argument(
        "--frame_start",
        type=int,
        default=0,
        help="0-indexed inclusive lower bound for frame_id (per scene). "
        "Defaults to 0 (the official validation server's behaviour). "
        "Combined with --num_frames_to_eval this defines an arbitrary "
        "half-open frame window [frame_start, num_frames_to_eval) -- "
        "e.g. --frame_start 4500 --num_frames_to_eval 9000 evaluates "
        "the second half of a 9000-frame scene; --frame_start 0 "
        "--num_frames_to_eval 4500 evaluates the first half.",
    )
    parser.add_argument(
        "--num_frames_to_eval",
        type=int,
        default=9000,
        help="Frame-count truncation per scene (0-indexed exclusive "
        "upper bound). Matches the official validation server "
        "default. Default: 9000.",
    )
    parser.add_argument(
        "--eval_type",
        choices=["bbox", "location"],
        default="bbox",
        help="HOTA matching function: 'bbox' (3D IoU, the official "
        "AICity MTMC metric) or 'location' (centre distance, "
        "useful for ablation only). Default: bbox.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="FPS written into TrackEval's per-sequence seqinfo.ini "
        "(cosmetic for single-sequence runs). Default: 30.0.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress TrackEval's per-class INFO logs (keeps the "
        "summary table readable).",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%y/%m/%d %H:%M:%S",
        level=logging.INFO,
    )
    args = parse_args()

    edition_pkg, class_id_to_name = _EDITIONS[args.edition]
    logger.info(
        "Evaluating against AICity'%s spec (class IDs %s).",
        args.edition,
        sorted(class_id_to_name.keys()),
    )

    if args.scene_id_2_scene_name_file is not None:
        scene_id_to_name = load_json_from_file(args.scene_id_2_scene_name_file)
        if not isinstance(scene_id_to_name, dict):
            raise ValueError(
                f"scene-id mapping file {args.scene_id_2_scene_name_file} "
                f"must contain a JSON object, got "
                f"{type(scene_id_to_name).__name__}."
            )
        scene_id_to_name = {str(k): str(v) for k, v in scene_id_to_name.items()}
    else:
        scene_id_to_name = edition_pkg.load_default_scene_id_to_name()
        logger.info(
            "Using packaged AICity'%s scene-id mapping from %s: %s",
            args.edition,
            edition_pkg.get_default_scene_id_to_name_path(),
            scene_id_to_name,
        )

    start_time = time.time()
    if args.frame_start < 0:
        raise ValueError(
            f"--frame_start must be >= 0, got {args.frame_start}."
        )
    if args.frame_start >= args.num_frames_to_eval:
        raise ValueError(
            f"--frame_start ({args.frame_start}) must be strictly less "
            f"than --num_frames_to_eval ({args.num_frames_to_eval}); "
            f"otherwise the frame window is empty."
        )
    if args.frame_start > 0:
        logger.info(
            "Evaluating on frame window [%d, %d) per scene.",
            args.frame_start, args.num_frames_to_eval,
        )

    results = run_aicity_mtmc_evaluation(
        ground_truth_file=args.ground_truth_file,
        prediction_file=args.input_file,
        scene_id_to_name=scene_id_to_name,
        output_dir=args.output_dir,
        num_cores=args.num_cores,
        num_frames_to_eval=args.num_frames_to_eval,
        eval_type=args.eval_type,
        fps=args.fps,
        quiet=args.quiet,
        class_id_to_name=class_id_to_name,
        frame_start=args.frame_start,
    )

    print_aicity_mtmc_summary(results)
    if args.output_dir is not None:
        save_aicity_mtmc_results(results, args.output_dir)

    final = results["final"]
    logger.info(
        "Final weighted: HOTA=%.4f DetA=%.4f AssA=%.4f LocA=%.4f (in 0-100 scale)",
        final["HOTA"] * 100,
        final["DetA"] * 100,
        final["AssA"] * 100,
        final["LocA"] * 100,
    )
    logger.info("Total time: %.1f seconds", time.time() - start_time)


if __name__ == "__main__":
    main()

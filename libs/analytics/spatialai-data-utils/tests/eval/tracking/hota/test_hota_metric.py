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

"""Coverage supplement for ``hota.metrics.hota.HOTA`` — pins the
empty-tracker-but-non-empty-gt timestep branch in ``eval_sequence``
and the matplotlib ``plot_single_tracker_results`` helper."""

import os

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402

from spatialai_data_utils.eval.tracking.hota.metrics import HOTA  # noqa: E402


def _data_with_empty_tracker_timestep():
    """One sequence with two timesteps: t=0 has gt only (no tracker),
    t=1 has both. Exercises the
    ``if len(tracker_ids_t) == 0: ... HOTA_FN += len(gt_ids_t); continue``
    branch inside the per-timestep loop."""
    return {
        "num_timesteps": 2,
        "num_gt_dets": 2,
        "num_tracker_dets": 1,
        "num_gt_ids": 1,
        "num_tracker_ids": 1,
        "gt_ids": [np.array([0]), np.array([0])],
        "tracker_ids": [np.array([], dtype=int), np.array([0])],
        "similarity_scores": [np.zeros((1, 0)), np.array([[1.0]])],
    }


def test_eval_sequence_empty_tracker_timestep_accumulates_to_hota_fn():
    """At t=0 there's one gt and no tracker -> HOTA_FN counter ticks
    for every alpha threshold."""
    res = HOTA().eval_sequence(_data_with_empty_tracker_timestep())
    # FN for every alpha should reflect the dropped gt det at t=0.
    assert (res["HOTA_FN"] >= 1).all()


def test_plot_single_tracker_results_writes_pdf_and_png(tmp_path):
    """``plot_single_tracker_results`` matplotlib path — writes a PDF
    and a same-stem PNG under ``output_folder``. We don't pin pixel
    contents; just assert the files exist."""
    metric = HOTA()
    # Build a results dict with valid arrays for every float_array field.
    res = {
        f: np.full(len(metric.array_labels), 0.5) for f in metric.float_array_fields
    }
    out_root = tmp_path / "out"
    metric.plot_single_tracker_results(
        {"COMBINED_SEQ": res},
        tracker="trkA", cls="person", output_folder=str(out_root),
    )
    assert (out_root / "person_plot.pdf").is_file()
    assert (out_root / "person_plot.png").is_file()

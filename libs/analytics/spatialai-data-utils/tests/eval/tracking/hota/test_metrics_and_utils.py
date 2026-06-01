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

"""Tests for the HOTA metric classes (Count, Identity, CLEAR, HOTA)
and the supporting ``hota.utils`` helpers.

The four metric classes share the same ``data`` dict contract — a
per-timestep description of ``gt_ids`` / ``tracker_ids`` /
``similarity_scores`` plus a handful of summary counts. We exercise
each one against three canonical mini-fixtures (perfect match, empty
tracker, empty GT) so the early-return branches and the main scoring
path all execute, then drive the ``combine_*`` helpers (sequences,
class-averaged, det-averaged, with and without ``ignore_empty_classes``).

``hota.utils`` covers config / I/O helpers used by the wider HOTA
runner; we exercise the public surface end-to-end (init_config
default + override paths, validate_metrics_list happy and duplicate-
error paths, write/load round-trip).
"""

import os

import numpy as np
import pytest

from spatialai_data_utils.eval.tracking.hota import utils
from spatialai_data_utils.eval.tracking.hota.metrics import (
    CLEAR,
    HOTA,
    Count,
    Identity,
)
from spatialai_data_utils.eval.tracking.hota.metrics._base_metric import _BaseMetric
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException


# ---------------------------------------------------------------------------
# Shared data fixtures
# ---------------------------------------------------------------------------


def _perfect_match_data(n_timesteps=3):
    """One gt id, one tracker id, perfect overlap on every timestep."""
    return {
        "num_timesteps": n_timesteps,
        "num_gt_dets": n_timesteps,
        "num_tracker_dets": n_timesteps,
        "num_gt_ids": 1,
        "num_tracker_ids": 1,
        "gt_ids": [np.array([0]) for _ in range(n_timesteps)],
        "tracker_ids": [np.array([0]) for _ in range(n_timesteps)],
        "similarity_scores": [np.array([[1.0]]) for _ in range(n_timesteps)],
    }


def _empty_tracker_data(n_timesteps=2, num_gt_dets=2):
    return {
        "num_timesteps": n_timesteps,
        "num_gt_dets": num_gt_dets,
        "num_tracker_dets": 0,
        "num_gt_ids": 1,
        "num_tracker_ids": 0,
        "gt_ids": [np.array([0]) for _ in range(n_timesteps)],
        "tracker_ids": [np.array([], dtype=int) for _ in range(n_timesteps)],
        "similarity_scores": [np.zeros((1, 0)) for _ in range(n_timesteps)],
    }


def _empty_gt_data(n_timesteps=2, num_tracker_dets=2):
    return {
        "num_timesteps": n_timesteps,
        "num_gt_dets": 0,
        "num_tracker_dets": num_tracker_dets,
        "num_gt_ids": 0,
        "num_tracker_ids": 1,
        "gt_ids": [np.array([], dtype=int) for _ in range(n_timesteps)],
        "tracker_ids": [np.array([0]) for _ in range(n_timesteps)],
        "similarity_scores": [np.zeros((0, 1)) for _ in range(n_timesteps)],
    }


# ---------------------------------------------------------------------------
# Count metric
# ---------------------------------------------------------------------------


def test_count_eval_sequence_returns_raw_counts():
    res = Count().eval_sequence(_perfect_match_data(n_timesteps=4))
    assert res == {"Dets": 4, "GT_Dets": 4, "IDs": 1, "GT_IDs": 1, "Frames": 4}


def test_count_combine_sequences_sums_across_sequences():
    metric = Count()
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    b = metric.eval_sequence(_perfect_match_data(n_timesteps=3))
    combined = metric.combine_sequences({"seqA": a, "seqB": b})
    assert combined == {"Dets": 5, "GT_Dets": 5, "IDs": 2, "GT_IDs": 2}


def test_count_combine_classes_class_and_det_averaged_both_sum():
    metric = Count()
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    b = metric.eval_sequence(_perfect_match_data(n_timesteps=3))
    assert metric.combine_classes_class_averaged({"A": a, "B": b}) == \
           metric.combine_classes_det_averaged({"A": a, "B": b})


# ---------------------------------------------------------------------------
# Identity metric
# ---------------------------------------------------------------------------


def test_identity_perfect_match_idf1_is_one():
    res = Identity({"PRINT_CONFIG": False}).eval_sequence(_perfect_match_data())
    assert res["IDF1"] == pytest.approx(1.0)
    assert res["IDR"] == pytest.approx(1.0)
    assert res["IDP"] == pytest.approx(1.0)


def test_identity_empty_tracker_yields_only_idfn():
    res = Identity({"PRINT_CONFIG": False}).eval_sequence(_empty_tracker_data())
    assert res["IDFN"] == 2
    assert res["IDTP"] == 0 and res["IDFP"] == 0


def test_identity_empty_gt_yields_only_idfp():
    res = Identity({"PRINT_CONFIG": False}).eval_sequence(_empty_gt_data())
    assert res["IDFP"] == 2
    assert res["IDTP"] == 0 and res["IDFN"] == 0


def test_identity_combine_sequences_recomputes_idf1_from_summed_counts():
    metric = Identity({"PRINT_CONFIG": False})
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    b = metric.eval_sequence(_perfect_match_data(n_timesteps=3))
    combined = metric.combine_sequences({"A": a, "B": b})
    assert combined["IDTP"] == 5
    assert combined["IDF1"] == pytest.approx(1.0)


def test_identity_combine_classes_class_averaged_with_and_without_ignore_empty():
    metric = Identity({"PRINT_CONFIG": False})
    good = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    empty = metric.eval_sequence(_empty_tracker_data(n_timesteps=1, num_gt_dets=0))
    # Without ignore -> empty class drags the average down (mean of 1 and 0).
    res_default = metric.combine_classes_class_averaged({"A": good, "B": empty})
    # With ignore -> only the non-empty class contributes -> stays at 1.0.
    res_ignore = metric.combine_classes_class_averaged(
        {"A": good, "B": empty}, ignore_empty_classes=True,
    )
    assert res_ignore["IDF1"] == pytest.approx(1.0)
    assert res_default["IDF1"] < res_ignore["IDF1"] + 1e-9


def test_identity_combine_classes_det_averaged_uses_summed_counts():
    metric = Identity({"PRINT_CONFIG": False})
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    b = metric.eval_sequence(_perfect_match_data(n_timesteps=3))
    combined = metric.combine_classes_det_averaged({"A": a, "B": b})
    assert combined["IDF1"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CLEAR metric
# ---------------------------------------------------------------------------


def test_clear_perfect_match_mota_is_one_and_mt_counts_track():
    res = CLEAR({"PRINT_CONFIG": False}).eval_sequence(_perfect_match_data())
    assert res["MOTA"] == pytest.approx(1.0)
    assert res["MT"] == 1
    assert res["ML"] == 0
    assert res["CLR_TP"] == 3 and res["CLR_FN"] == 0 and res["CLR_FP"] == 0
    assert res["IDSW"] == 0


def test_clear_empty_tracker_marks_all_gt_as_ml():
    res = CLEAR({"PRINT_CONFIG": False}).eval_sequence(_empty_tracker_data())
    assert res["CLR_FN"] == 2 and res["CLR_TP"] == 0
    assert res["ML"] == 1
    assert res["MLR"] == pytest.approx(1.0)


def test_clear_empty_gt_counts_predictions_as_fp():
    res = CLEAR({"PRINT_CONFIG": False}).eval_sequence(_empty_gt_data())
    assert res["CLR_FP"] == 2 and res["CLR_TP"] == 0


def test_clear_idsw_counted_when_gt_id_switches_tracker_ids():
    """gt id 0 is matched to tracker id 0 at t=0 and to tracker id 1
    at t=1. The Hungarian matching prefers continuity (1000-bonus on
    matches with the previous tracker id), but at t=1 the previous-
    tracker (0) is absent so it must switch — that's an IDSW."""
    data = {
        "num_timesteps": 2,
        "num_gt_dets": 2,
        "num_tracker_dets": 2,
        "num_gt_ids": 1,
        "num_tracker_ids": 2,
        "gt_ids": [np.array([0]), np.array([0])],
        "tracker_ids": [np.array([0]), np.array([1])],
        "similarity_scores": [np.array([[1.0]]), np.array([[1.0]])],
    }
    res = CLEAR({"PRINT_CONFIG": False}).eval_sequence(data)
    assert res["IDSW"] == 1


def test_clear_no_gt_in_timestep_counts_predictions_as_fp():
    """First timestep has no GT but two predictions — those become
    FPs without affecting matching."""
    data = {
        "num_timesteps": 2,
        "num_gt_dets": 1,
        "num_tracker_dets": 3,
        "num_gt_ids": 1,
        "num_tracker_ids": 2,
        "gt_ids": [np.array([], dtype=int), np.array([0])],
        "tracker_ids": [np.array([0, 1]), np.array([0])],
        "similarity_scores": [np.zeros((0, 2)), np.array([[1.0]])],
    }
    res = CLEAR({"PRINT_CONFIG": False}).eval_sequence(data)
    assert res["CLR_FP"] == 2
    assert res["CLR_TP"] == 1


def test_clear_no_tracker_in_timestep_counts_gts_as_fn():
    data = {
        "num_timesteps": 2,
        "num_gt_dets": 3,
        "num_tracker_dets": 1,
        "num_gt_ids": 2,
        "num_tracker_ids": 1,
        "gt_ids": [np.array([0, 1]), np.array([0])],
        "tracker_ids": [np.array([], dtype=int), np.array([0])],
        "similarity_scores": [np.zeros((2, 0)), np.array([[1.0]])],
    }
    res = CLEAR({"PRINT_CONFIG": False}).eval_sequence(data)
    assert res["CLR_FN"] >= 2


def test_clear_combine_sequences_recomputes_final_fields():
    metric = CLEAR({"PRINT_CONFIG": False})
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    b = metric.eval_sequence(_perfect_match_data(n_timesteps=3))
    combined = metric.combine_sequences({"A": a, "B": b})
    assert combined["MOTA"] == pytest.approx(1.0)
    assert combined["CLR_TP"] == 5


def test_clear_combine_classes_class_and_det_averaged():
    metric = CLEAR({"PRINT_CONFIG": False})
    good = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    res_class = metric.combine_classes_class_averaged({"A": good, "B": good})
    res_det = metric.combine_classes_det_averaged({"A": good, "B": good})
    assert res_class["MOTA"] == pytest.approx(1.0)
    assert res_det["MOTA"] == pytest.approx(1.0)


def test_clear_combine_classes_class_averaged_ignore_empty_skips_zero_classes():
    metric = CLEAR({"PRINT_CONFIG": False})
    good = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    empty = metric.eval_sequence(
        _empty_tracker_data(n_timesteps=1, num_gt_dets=0)
    )
    res = metric.combine_classes_class_averaged(
        {"A": good, "B": empty}, ignore_empty_classes=True,
    )
    assert res["MOTA"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# HOTA metric — eval_sequence path is covered by test_3d_iou_and_hota.py;
# focus here on the combine_* helpers and the empty-data early returns.
# ---------------------------------------------------------------------------


def test_hota_empty_tracker_returns_loca_one():
    res = HOTA().eval_sequence(_empty_tracker_data())
    assert res["LocA(0)"] == 1.0
    assert (res["LocA"] == 1.0).all()


def test_hota_empty_gt_returns_loca_one():
    res = HOTA().eval_sequence(_empty_gt_data())
    assert res["LocA(0)"] == 1.0
    assert (res["LocA"] == 1.0).all()


def test_hota_combine_sequences_aggregates_across_two_runs():
    metric = HOTA()
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    b = metric.eval_sequence(_perfect_match_data(n_timesteps=3))
    combined = metric.combine_sequences({"A": a, "B": b})
    np.testing.assert_allclose(combined["HOTA"], np.ones_like(combined["HOTA"]))
    np.testing.assert_array_equal(combined["HOTA_TP"], a["HOTA_TP"] + b["HOTA_TP"])


def test_hota_combine_classes_det_averaged_recomputes_final_fields():
    metric = HOTA()
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    b = metric.eval_sequence(_perfect_match_data(n_timesteps=3))
    combined = metric.combine_classes_det_averaged({"A": a, "B": b})
    np.testing.assert_allclose(combined["HOTA"], np.ones_like(combined["HOTA"]))


def test_hota_combine_classes_class_averaged_default_and_ignore_empty():
    metric = HOTA()
    good = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    # An "empty" class for HOTA: zero gt and tracker dets but valid shapes.
    empty_input = {
        "num_timesteps": 1, "num_gt_dets": 0, "num_tracker_dets": 0,
        "num_gt_ids": 0, "num_tracker_ids": 0,
        "gt_ids": [np.array([], dtype=int)],
        "tracker_ids": [np.array([], dtype=int)],
        "similarity_scores": [np.zeros((0, 0))],
    }
    empty = metric.eval_sequence(empty_input)
    res = metric.combine_classes_class_averaged({"A": good, "B": empty})
    res_ignore = metric.combine_classes_class_averaged(
        {"A": good, "B": empty}, ignore_empty_classes=True,
    )
    # With ignore, the good class alone contributes -> HOTA stays at 1.0.
    np.testing.assert_allclose(res_ignore["HOTA"], np.ones_like(res_ignore["HOTA"]))
    # Without ignore, the empty class drags the average down.
    assert (res["HOTA"] <= res_ignore["HOTA"] + 1e-9).all()


# ---------------------------------------------------------------------------
# _BaseMetric helper methods
# ---------------------------------------------------------------------------


def test_base_metric_get_name_returns_class_name():
    assert Count.get_name() == "Count"
    assert Identity.get_name() == "Identity"
    assert HOTA.get_name() == "HOTA"


def test_base_metric_summary_results_returns_named_dict():
    metric = Count()
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    table_res = {"COMBINED_SEQ": metric.combine_sequences({"A": a})}
    summary = metric.summary_results(table_res)
    assert set(summary.keys()) == set(metric.summary_fields)


def test_base_metric_detailed_results_round_trip_for_array_fields():
    metric = HOTA()
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    table_res = {"COMBINED_SEQ": metric.combine_sequences({"A": a})}
    details = metric.detailed_results(table_res)
    # For HOTA, every float_array_field gets per-alpha entries +
    # an AUC entry, every float_field gets a flat entry.
    keys = details["COMBINED_SEQ"].keys()
    for field in metric.float_fields:
        assert field in keys
    for field in metric.float_array_fields:
        assert f"{field}___AUC" in keys


def test_base_metric_print_table_emits_combined_row(capsys):
    metric = Count()
    a = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    table_res = {"A": a, "COMBINED_SEQ": metric.combine_sequences({"A": a})}
    metric.print_table(table_res, tracker="trk", cls="person")
    captured = capsys.readouterr().out
    assert "COMBINED" in captured
    assert "Count: trk-person" in captured


# ---------------------------------------------------------------------------
# hota.utils
# ---------------------------------------------------------------------------


def test_init_config_returns_default_when_config_is_none(capsys):
    """``init_config(None, default)`` returns the default verbatim and
    skips the PRINT_CONFIG branch since ``name`` is None."""
    out = utils.init_config(None, {"PRINT_CONFIG": False, "T": 0.5})
    assert out == {"PRINT_CONFIG": False, "T": 0.5}
    assert capsys.readouterr().out == ""


def test_init_config_overlays_partial_config_onto_defaults():
    out = utils.init_config({"PRINT_CONFIG": False, "T": 0.7},
                            default_config={"PRINT_CONFIG": True, "T": 0.5, "X": 1})
    assert out["T"] == 0.7
    assert out["PRINT_CONFIG"] is False
    assert out["X"] == 1  # picked up from default


def test_init_config_prints_when_named_and_print_config_true(capsys):
    utils.init_config({"PRINT_CONFIG": True, "T": 0.7},
                      default_config={"PRINT_CONFIG": True, "T": 0.5},
                      name="MyMetric")
    out = capsys.readouterr().out
    assert "MyMetric Config:" in out
    assert "T" in out


def test_get_code_path_points_to_an_existing_directory():
    path = utils.get_code_path()
    assert os.path.isdir(path)


def test_validate_metrics_list_returns_unique_metric_names():
    names = utils.validate_metrics_list([Count(), HOTA()])
    assert names == ["Count", "HOTA"]


def test_validate_metrics_list_rejects_duplicate_metric_names():
    with pytest.raises(TrackEvalException, match="multiple metrics of the same name"):
        utils.validate_metrics_list([Count(), Count()])


def test_write_and_load_round_trip_for_summary_and_detailed(tmp_path):
    """``write_summary_results`` + ``write_detailed_results`` produce
    files in the expected layout; ``load_detail`` reads a detailed
    CSV back into the same dict shape."""
    metric = Count()
    seq_res = metric.eval_sequence(_perfect_match_data(n_timesteps=2))
    table = {"seqA": seq_res, "COMBINED_SEQ": metric.combine_sequences({"seqA": seq_res})}

    out_folder = str(tmp_path / "out")
    os.makedirs(out_folder, exist_ok=True)

    summary = metric.summary_results(table)
    utils.write_summary_results([summary], cls="person", output_folder=out_folder)
    summary_path = os.path.join(out_folder, "person_summary.txt")
    assert os.path.isfile(summary_path)
    with open(summary_path) as f:
        text = f.read().splitlines()
    # First line is field names, second is values, both space-separated.
    assert "Dets" in text[0]

    details = metric.detailed_results(table)
    utils.write_detailed_results([details], cls="person", output_folder=out_folder)
    detail_path = os.path.join(out_folder, "person_detailed.csv")
    loaded = utils.load_detail(detail_path)
    assert "COMBINED_SEQ" in loaded
    assert "seqA" in loaded


def test_track_eval_exception_subclasses_exception():
    assert issubclass(TrackEvalException, Exception)
    with pytest.raises(TrackEvalException):
        raise TrackEvalException("boom")


# ===========================================================
# Coverage supplement for ``hota.metrics._base_metric`` and
# ``hota.utils`` — pins the remaining branches:
#
# * ``_BaseMetric.plot_single_tracker_results`` for both ``plottable``
#   flag states,
# * ``_summary_row`` for the float_array / float / integer fields and
#   the NotImplementedError fall-through,
# * ``detailed_results`` raise on field-name/data length mismatch,
# * ``validate_metrics_list`` duplicate-field-name raise.
# ===========================================================


# ---------------------------------------------------------------------------
# _BaseMetric — plot_single_tracker_results
# ---------------------------------------------------------------------------


class TestPlotSingleTrackerResultsDefault:
    def test_non_plottable_metric_silently_passes(self):
        """``Count.plottable == False`` so the default plot helper is
        a silent no-op (the ``else: pass`` branch in the base class)."""
        Count().plot_single_tracker_results({}, "trk", "out", "person")

    def test_plottable_metric_without_override_raises_not_implemented(self):
        """If a subclass sets ``plottable=True`` but inherits the
        default plot helper without overriding it, the base raises
        ``NotImplementedError``. (The real HOTA subclass overrides
        the method — exercise the un-overridden contract via the
        base class directly.)"""
        class _PlottableMetric(_BaseMetric):
            def __init__(self):
                super().__init__()
                self.plottable = True

            def eval_sequence(self, data):  # pragma: no cover - abstract stub
                pass

            def combine_sequences(self, all_res):  # pragma: no cover
                pass

            def combine_classes_class_averaged(self, all_res, ignore_empty_classes=False):  # pragma: no cover
                pass

            def combine_classes_det_averaged(self, all_res):  # pragma: no cover
                pass

        m = _PlottableMetric()
        with pytest.raises(NotImplementedError):
            m.plot_single_tracker_results({}, "trk", "out", "person")


# ---------------------------------------------------------------------------
# _BaseMetric — _summary_row branches
# ---------------------------------------------------------------------------


def test_summary_row_handles_float_array_and_integer_fields():
    """HOTA mixes float_array fields and integer_array fields — exercise
    both via the ``summary_results`` path."""
    metric = HOTA()
    # Hand-built results dict with the right shapes (float arrays per
    # alpha threshold + a couple of summary scalar floats).
    res = {f: np.full(len(metric.array_labels), 0.5) for f in metric.float_array_fields}
    res.update({f: np.zeros(len(metric.array_labels)) for f in metric.integer_array_fields})
    res.update({f: 0.5 for f in metric.float_fields})
    summary = metric.summary_results({"COMBINED_SEQ": res})
    # Values are scaled by 100 and formatted as 1.5g strings.
    for f in metric.float_array_fields:
        assert summary[f] == "50"


def test_summary_row_raises_for_unknown_field_type():
    """Adding a summary field that lives in none of the three typed
    lists (float_array / float / integer) triggers
    ``NotImplementedError`` from the default ``_summary_row``. Must
    still satisfy the iteration over the pre-existing summary_fields,
    so populate ``res`` with all of them."""
    metric = Count()  # only has integer_fields by default
    metric.summary_fields = [*metric.summary_fields, "mystery_field"]
    res = {f: 1 for f in metric.summary_fields}
    with pytest.raises(NotImplementedError, match="Summary function"):
        metric.summary_results({"COMBINED_SEQ": res})


# ---------------------------------------------------------------------------
# _BaseMetric — detailed_results size mismatch
# ---------------------------------------------------------------------------


def test_detailed_results_raises_when_row_size_does_not_match_fields():
    """When the per-sequence ``_detailed_row`` produces a different
    number of values than the declared ``detailed_fields``, the
    helper raises ``TrackEvalException``. In normal use the two are
    constructed from the same field lists so the lengths match; force
    a mismatch by overriding ``_detailed_row``."""
    metric = Count()
    # Replace the row builder to return a too-short list.
    metric._detailed_row = lambda res: [1, 2]
    res = {f: 1 for f in metric.float_fields + metric.integer_fields}
    with pytest.raises(TrackEvalException, match="different sizes"):
        metric.detailed_results({"COMBINED_SEQ": res})


# ---------------------------------------------------------------------------
# utils.validate_metrics_list — duplicate-field raise
# ---------------------------------------------------------------------------


def test_validate_metrics_list_rejects_metrics_with_duplicate_field_names():
    """Two metrics with overlapping field names must raise. Build a
    second metric class that shares the ``Identity`` field set."""
    class _CopyIdentity(Identity):
        @classmethod
        def get_name(cls):
            return "CopyIdentity"

    metric_a = Identity({"PRINT_CONFIG": False})
    metric_b = _CopyIdentity({"PRINT_CONFIG": False})
    with pytest.raises(TrackEvalException, match="multiple metrics with fields"):
        utils.validate_metrics_list([metric_a, metric_b])

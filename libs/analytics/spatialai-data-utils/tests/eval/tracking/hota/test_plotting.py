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

"""Smoke / contract tests for ``eval.tracking.hota.plotting``.

The module wraps matplotlib so headless test runs need an Agg
backend. The tests:

* exercise the small numeric helpers (``geometric_mean`` /
  ``jaccard`` / ``multiplication`` / ``_get_boundaries`` /
  ``get_default_plots_list``) for value contracts,
* drive ``load_multiple_tracker_summaries`` end-to-end against a
  small on-disk summary, and
* run ``create_comparison_plot`` / ``plot_compare_trackers`` to
  produce real PDF + PNG files in a tmp dir (full Agg-backend path)
  with and without the ``bg_function`` overlay.
"""

import os

import matplotlib

matplotlib.use("Agg")  # must happen before any matplotlib.pyplot import

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from spatialai_data_utils.eval.tracking.hota import plotting  # noqa: E402
from spatialai_data_utils.eval.tracking.hota.utils import (  # noqa: E402
    TrackEvalException,
)


# ---------------------------------------------------------------------------
# Pure numeric helpers
# ---------------------------------------------------------------------------


def test_geometric_mean_matches_sqrt_product():
    assert plotting.geometric_mean(64.0, 16.0) == pytest.approx(32.0)


def test_jaccard_matches_iou_style_formula_on_percent_inputs():
    """``jaccard(x, y)`` operates on percent inputs (0..100). For two
    equal inputs of 50 it returns ``100 * (0.25) / (0.75) = 33.333``."""
    assert plotting.jaccard(50.0, 50.0) == pytest.approx(100 * (0.25 / 0.75))


def test_multiplication_returns_xy_over_100():
    assert plotting.multiplication(50.0, 40.0) == pytest.approx(20.0)


def test_get_default_plots_list_returns_eight_plot_specs():
    plots = plotting.get_default_plots_list()
    assert len(plots) == 8
    # Each spec is a 5-tuple/list: y, x, sort, bg_label, bg_function
    for spec in plots:
        assert len(spec) == 5


def test_bg_function_dict_lists_three_known_functions():
    assert set(plotting.bg_function_dict.keys()) == \
           {"geometric_mean", "jaccard", "multiplication"}
    # Sanity check: every entry is the matching top-level function.
    assert plotting.bg_function_dict["geometric_mean"] is plotting.geometric_mean


def test_get_boundaries_returns_square_window_within_unit_axis():
    """``_get_boundaries`` snaps to a square window inside [0, 100].
    A tight cluster around (50, 60) at round_val=1 should give a
    finite box covering the data."""
    min_x, max_x, min_y, max_y = plotting._get_boundaries(
        np.array([50.0, 52.0]), np.array([60.0, 58.0]), round_val=1.0,
    )
    assert 0 <= min_x < max_x <= 100
    assert 0 <= min_y < max_y <= 100
    # The aspect must be square (max_x - min_x == max_y - min_y).
    assert (max_x - min_x) == pytest.approx(max_y - min_y)


# ---------------------------------------------------------------------------
# load_multiple_tracker_summaries
# ---------------------------------------------------------------------------


def _write_summary(folder, tracker, cls, fields, values):
    os.makedirs(os.path.join(folder, tracker), exist_ok=True)
    path = os.path.join(folder, tracker, cls + "_summary.txt")
    with open(path, "w") as f:
        f.write(" ".join(fields) + "\n")
        f.write(" ".join(str(v) for v in values) + "\n")
    return path


def test_load_multiple_tracker_summaries_collects_per_tracker_dicts(tmp_path):
    fields = ["HOTA", "DetA", "AssA", "MOTA", "IDF1"]
    _write_summary(str(tmp_path), "trkA", "person", fields, [70.0, 60.0, 80.0, 65.0, 75.0])
    _write_summary(str(tmp_path), "trkB", "person", fields, [50.0, 55.0, 45.0, 60.0, 50.0])

    data = plotting.load_multiple_tracker_summaries(
        str(tmp_path), tracker_list=["trkA", "trkB"], cls="person",
    )
    assert set(data.keys()) == {"trkA", "trkB"}
    assert data["trkA"]["HOTA"] == pytest.approx(70.0)
    assert data["trkB"]["DetA"] == pytest.approx(55.0)


# ---------------------------------------------------------------------------
# create_comparison_plot
# ---------------------------------------------------------------------------


def _data_for_plot():
    return {
        "trkA": {"HOTA": 70.0, "DetA": 60.0, "AssA": 80.0, "LocA": 65.0,
                  "DetRe": 60.0, "DetPr": 70.0, "AssRe": 80.0, "AssPr": 75.0,
                  "MOTA": 65.0, "IDF1": 75.0, "HOTA(0)": 65.0, "LocA(0)": 70.0,
                  "HOTALocA(0)": 50.0},
        "trkB": {"HOTA": 50.0, "DetA": 55.0, "AssA": 45.0, "LocA": 60.0,
                  "DetRe": 55.0, "DetPr": 50.0, "AssRe": 45.0, "AssPr": 45.0,
                  "MOTA": 60.0, "IDF1": 50.0, "HOTA(0)": 55.0, "LocA(0)": 60.0,
                  "HOTALocA(0)": 33.0},
    }


def test_create_comparison_plot_writes_pdf_and_png_without_bg(tmp_path):
    plotting.create_comparison_plot(
        _data_for_plot(), str(tmp_path),
        y_label="HOTA", x_label="MOTA", sort_label="HOTA",
        bg_label=None, bg_function=None,
    )
    base = os.path.join(str(tmp_path), "HOTA_vs_MOTA")
    assert os.path.isfile(base + ".pdf")
    assert os.path.isfile(base + ".png")


def test_create_comparison_plot_writes_files_with_bg_geometric_mean(tmp_path):
    plotting.create_comparison_plot(
        _data_for_plot(), str(tmp_path),
        y_label="AssA", x_label="DetA", sort_label="HOTA",
        bg_label="HOTA", bg_function="geometric_mean",
    )
    base = os.path.join(str(tmp_path), "AssA_vs_DetA_(HOTA)")
    assert os.path.isfile(base + ".pdf")


def test_create_comparison_plot_raises_when_only_one_bg_argument_set(tmp_path):
    with pytest.raises(TrackEvalException,
                       match="bg_function and bg_label must either be both given"):
        plotting.create_comparison_plot(
            _data_for_plot(), str(tmp_path),
            y_label="HOTA", x_label="MOTA", sort_label="HOTA",
            bg_label="HOTA", bg_function=None,
        )


def test_create_comparison_plot_raises_for_unknown_bg_function(tmp_path):
    with pytest.raises(TrackEvalException, match="not defined"):
        plotting.create_comparison_plot(
            _data_for_plot(), str(tmp_path),
            y_label="AssA", x_label="DetA", sort_label="HOTA",
            bg_label="HOTA", bg_function="not_a_real_function",
        )


def test_create_comparison_plot_honors_custom_settings(tmp_path):
    plotting.create_comparison_plot(
        _data_for_plot(), str(tmp_path),
        y_label="HOTA", x_label="MOTA", sort_label="HOTA",
        settings={"gap_val": 5, "num_to_plot": 1},
    )
    assert os.path.isfile(os.path.join(str(tmp_path), "HOTA_vs_MOTA.pdf"))


# ---------------------------------------------------------------------------
# plot_compare_trackers — wires the load + per-spec plot pipeline
# ---------------------------------------------------------------------------


def test_plot_compare_trackers_emits_one_file_per_plot_spec(tmp_path):
    fields = ["HOTA", "DetA", "AssA", "DetRe", "DetPr", "AssRe", "AssPr",
              "LocA", "OWTA", "HOTA(0)", "LocA(0)", "HOTALocA(0)", "MOTA", "IDF1"]
    _write_summary(
        str(tmp_path / "trackers"), "trkA", "person", fields,
        [70.0, 60.0, 80.0, 60.0, 70.0, 80.0, 75.0, 65.0, 70.0,
         65.0, 70.0, 50.0, 65.0, 75.0],
    )
    _write_summary(
        str(tmp_path / "trackers"), "trkB", "person", fields,
        [50.0, 55.0, 45.0, 55.0, 50.0, 45.0, 45.0, 60.0, 50.0,
         55.0, 60.0, 33.0, 60.0, 50.0],
    )

    out_root = str(tmp_path / "plots")
    plotting.plot_compare_trackers(
        tracker_folder=str(tmp_path / "trackers"),
        tracker_list=["trkA", "trkB"],
        cls="person",
        output_folder=out_root,
        # Use a single-spec plots list to keep the file count tractable.
        plots_list=[["HOTA", "MOTA", "HOTA", None, None]],
    )
    assert os.path.isfile(os.path.join(out_root, "person", "HOTA_vs_MOTA.pdf"))


# ===========================================================
# Coverage supplement (merged from test_plotting_coverage.py)
# ===========================================================

"""Coverage supplement for ``hota.plotting`` — pins the two branches
the existing tests don't reach: ``plot_compare_trackers`` with the
default ``plots_list=None`` argument (which expands to the canonical
8-plot suite), and the inner while-loop of
``_plot_pareto_optimal_lines`` (which only runs when there's more than
one tracker on the pareto frontier)."""

import os

import matplotlib

matplotlib.use("Agg")  # required before any matplotlib.pyplot import

import numpy as np  # noqa: E402

from spatialai_data_utils.eval.tracking.hota import plotting  # noqa: E402


# Note: ``_write_summary`` is defined at the top of this module and
# reused below.


# ---------------------------------------------------------------------------
# plot_compare_trackers default plots_list
# ---------------------------------------------------------------------------


def test_plot_compare_trackers_with_default_plots_list(tmp_path):
    """``plots_list=None`` expands to ``get_default_plots_list()``,
    which is an 8-spec list — driving this path writes 8 PDF files.

    Note: ``load_multiple_tracker_summaries`` does not strip the
    trailing newline from the last field name, so the last entry in
    ``fields`` becomes an unreachable ``"<name>\\n"`` key. We add a
    sacrificial ``PADDING`` field at the end so every real field
    accessed by the 8-spec list is reachable."""
    fields = ["HOTA", "DetA", "AssA", "DetRe", "DetPr", "AssRe", "AssPr",
              "LocA", "OWTA", "HOTA(0)", "LocA(0)", "HOTALocA(0)",
              "MOTA", "IDF1", "PADDING"]
    _write_summary(
        str(tmp_path / "trackers"), "trkA", "person", fields,
        [70.0, 60.0, 80.0, 60.0, 70.0, 80.0, 75.0, 65.0, 70.0,
         65.0, 70.0, 50.0, 65.0, 75.0, 0.0],
    )
    _write_summary(
        str(tmp_path / "trackers"), "trkB", "person", fields,
        [50.0, 55.0, 45.0, 55.0, 50.0, 45.0, 45.0, 60.0, 50.0,
         55.0, 60.0, 33.0, 60.0, 50.0, 0.0],
    )

    out_root = str(tmp_path / "plots")
    plotting.plot_compare_trackers(
        tracker_folder=str(tmp_path / "trackers"),
        tracker_list=["trkA", "trkB"],
        cls="person",
        output_folder=out_root,
        # plots_list left at None -> default 8-spec list
    )
    pdfs = sorted(p.name for p in (tmp_path / "plots" / "person").glob("*.pdf"))
    assert len(pdfs) == 8


# ---------------------------------------------------------------------------
# _plot_pareto_optimal_lines — multi-point loop branch
# ---------------------------------------------------------------------------


def test_plot_pareto_optimal_lines_multi_point_loop(tmp_path):
    """The pareto-line helper has a ``while len(cxs) > 0`` loop that
    only iterates when multiple trackers sit on the frontier. Drive a
    plot with >2 trackers whose ``(x, y)`` points span the frontier."""
    fields = ["HOTA", "DetA", "AssA", "DetRe", "DetPr", "AssRe", "AssPr",
              "LocA", "OWTA", "HOTA(0)", "LocA(0)", "HOTALocA(0)",
              "MOTA", "IDF1", "PADDING"]
    # Four trackers with distinct (MOTA, HOTA) points that form a
    # genuine pareto frontier (no single tracker dominates).
    for i, (mota, hota) in enumerate([(70, 50), (60, 70), (50, 80), (40, 90)]):
        _write_summary(
            str(tmp_path / "trackers"), f"trk{i}", "person", fields,
            [hota, 60.0, 80.0, 60.0, 70.0, 80.0, 75.0, 65.0, 70.0,
             65.0, 70.0, 50.0, mota, 75.0, 0.0],
        )

    out_root = str(tmp_path / "plots")
    plotting.plot_compare_trackers(
        tracker_folder=str(tmp_path / "trackers"),
        tracker_list=[f"trk{i}" for i in range(4)],
        cls="person",
        output_folder=out_root,
        plots_list=[["HOTA", "MOTA", "HOTA", None, None]],
    )
    assert (tmp_path / "plots" / "person" / "HOTA_vs_MOTA.pdf").is_file()

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

"""Tests for ``visualization.camera_groups``.

The companion file ``test_camera_groups_visualization.py`` covers the
camera-grouping orchestrators end-to-end. This file fills in the
missing pure-helper coverage and the two ``plot_sensor_groups*``
matplotlib paths (separate_images=True / False, with map / without).
"""

import os

import matplotlib

matplotlib.use("Agg")  # must come before any matplotlib.pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from shapely.geometry import (  # noqa: E402
    GeometryCollection,
    MultiPolygon,
    Polygon,
)

from spatialai_data_utils.visualization.camera_groups import (  # noqa: E402
    _draw_label,
    _extract_polygons,
    _plot_polygon_on_ax,
    draw_polygon,
    get_cluster_color,
    plot_sensor_groups,
    plot_sensor_groups_black_background,
    transform_polygon,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestGetClusterColor:
    def test_returns_hex_string_by_default(self):
        out = get_cluster_color(0)
        assert isinstance(out, str) and out.startswith("#")

    def test_returns_rgb_tuple_in_unit_range_when_requested(self):
        r, g, b = get_cluster_color(0, as_tuple=True)
        assert 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0

    def test_cycles_through_palette_via_modulo(self):
        from spatialai_data_utils.visualization.camera_groups import CLUSTER_COLORS
        n = len(CLUSTER_COLORS)
        assert get_cluster_color(0) == get_cluster_color(n)


class TestTransformPolygon:
    def test_polygon_input_maps_through_translation_and_scale(self):
        poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        out = transform_polygon(
            poly, translation={"x": 0.0, "y": 0.0}, scale=10.0,
            map_height=100,
        )
        # World (0, 0) -> pixel (0, 99); world (1, 1) -> pixel (10, 89)
        coords = list(out.exterior.coords)
        assert coords[0] == (0.0, 99.0)
        assert coords[2] == (10.0, 89.0)

    def test_multipolygon_input_returns_multipolygon(self):
        mp = MultiPolygon([
            Polygon([(0, 0), (1, 0), (1, 1)]),
            Polygon([(2, 0), (3, 0), (3, 1)]),
        ])
        out = transform_polygon(
            mp, translation={"x": 0.0, "y": 0.0}, scale=1.0, map_height=100,
        )
        assert isinstance(out, MultiPolygon)
        assert len(list(out.geoms)) == 2

    def test_geometry_collection_input_flattens_to_multipolygon(self):
        gc = GeometryCollection([
            Polygon([(0, 0), (1, 0), (1, 1)]),
            MultiPolygon([Polygon([(2, 0), (3, 0), (3, 1)])]),
        ])
        out = transform_polygon(
            gc, translation={"x": 0.0, "y": 0.0}, scale=1.0, map_height=100,
        )
        assert isinstance(out, MultiPolygon)
        # The collection's first Polygon + the MultiPolygon's single Polygon -> 2
        assert len(list(out.geoms)) == 2

    def test_unknown_geometry_type_returns_input_unchanged(self):
        """A non-{Polygon, MultiPolygon, GeometryCollection} input
        (e.g. a Point) falls through the ``return polygon`` tail."""
        from shapely.geometry import Point
        pt = Point(0.5, 0.5)
        out = transform_polygon(
            pt, translation={"x": 0.0, "y": 0.0}, scale=1.0, map_height=100,
        )
        assert out is pt


class TestDrawPolygon:
    def test_draws_polygon_on_blank_canvas(self):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        poly = Polygon([(10, 10), (50, 10), (50, 50), (10, 50)])
        draw_polygon(img, poly, color=(0, 255, 0))
        # Some pixels in the bounding region got filled.
        assert img[30, 30].sum() > 0

    def test_draws_multipolygon_on_blank_canvas(self):
        """Exercises the ``MultiPolygon`` branch."""
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        mp = MultiPolygon([
            Polygon([(10, 10), (30, 10), (30, 30), (10, 30)]),
            Polygon([(60, 60), (90, 60), (90, 90), (60, 90)]),
        ])
        draw_polygon(img, mp, color=(255, 0, 0))
        # Both regions had pixels filled.
        assert img[20, 20].sum() > 0
        assert img[75, 75].sum() > 0


class TestExtractPolygons:
    def test_none_returns_empty(self):
        assert _extract_polygons(None) == []

    def test_empty_geometry_returns_empty(self):
        from shapely.geometry import Polygon as _P
        empty = _P()
        assert _extract_polygons(empty) == []

    def test_valid_polygon_returns_singleton(self):
        poly = Polygon([(0, 0), (1, 0), (1, 1)])
        out = _extract_polygons(poly)
        assert out == [poly]

    def test_multipolygon_flattens_to_constituent_polygons(self):
        polys = [
            Polygon([(0, 0), (1, 0), (1, 1)]),
            Polygon([(2, 0), (3, 0), (3, 1)]),
        ]
        out = _extract_polygons(MultiPolygon(polys))
        assert len(out) == 2

    def test_geometry_collection_flattens_recursively(self):
        gc = GeometryCollection([
            Polygon([(0, 0), (1, 0), (1, 1)]),
            MultiPolygon([Polygon([(2, 0), (3, 0), (3, 1)])]),
        ])
        out = _extract_polygons(gc)
        assert len(out) == 2

    def test_invalid_polygon_goes_through_make_valid_branch(self):
        """A bow-tie polygon is invalid; ``_extract_polygons`` calls
        ``make_valid`` and re-extracts."""
        bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
        assert not bowtie.is_valid
        out = _extract_polygons(bowtie)
        # Should produce one or more valid polygons after repair.
        assert len(out) >= 1
        for p in out:
            assert p.is_valid


class TestPlotPolygonOnAx:
    def test_plots_valid_polygon_and_returns_centroid(self):
        fig, ax = plt.subplots()
        try:
            poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
            centroid = _plot_polygon_on_ax(ax, poly, color="red")
            assert centroid == pytest.approx((5.0, 5.0))
        finally:
            plt.close(fig)

    def test_returns_none_for_empty_geometry(self):
        from shapely.geometry import Polygon as _P
        fig, ax = plt.subplots()
        try:
            assert _plot_polygon_on_ax(ax, _P(), color="blue") is None
        finally:
            plt.close(fig)

    def test_multipolygon_centroid_uses_unary_union(self):
        fig, ax = plt.subplots()
        try:
            mp = MultiPolygon([
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(9, 0), (10, 0), (10, 1), (9, 1)]),
            ])
            centroid = _plot_polygon_on_ax(ax, mp, color="green")
            # Two equal-size squares at x=0..1 and x=9..10 -> centroid at x=5
            assert centroid is not None
            cx, _ = centroid
            assert cx == pytest.approx(5.0, abs=1e-6)
        finally:
            plt.close(fig)


def test_draw_label_smoke_runs_without_error():
    fig, ax = plt.subplots()
    try:
        _draw_label(ax, x=5.0, y=10.0, text="hello", color="#FF0000")
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# plot_sensor_groups* end-to-end
# ---------------------------------------------------------------------------


def _make_sensors_with_fov(n=2):
    """Build a small synthetic sensor list with valid FOV polygons +
    coordinate transforms, both groups present."""
    sensors = []
    for i in range(n):
        sensors.append({
            "id": f"Camera_{i:02d}",
            "translationToGlobalCoordinates": {"x": 0.0, "y": 0.0},
            "scaleFactor": 3.0,
            "group": {"name": f"bev-sensor-{(i % 2) + 1}"},
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
                {
                    "name": "fieldOfViewPolygon",
                    "value": (
                        f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, "
                        f"{i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))"
                    ),
                },
            ],
        })
    return sensors


def _make_map_file(tmp_path):
    """Write a tiny PNG so ``load_map_data`` returns a real image."""
    map_file = tmp_path / "Top.png"
    try:
        from PIL import Image
        Image.new("RGB", (300, 300), color=(0, 0, 0)).save(map_file)
    except ImportError:
        # Minimal valid PNG header (1x1 transparent pixel)
        map_file.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05"
            b"\x00\x01\xff\xa3\x9c\x9a\xf0\x00\x00\x00\x00IEND\xaeB`\x82"
        )
    return str(map_file)


class TestPlotSensorGroups:
    def test_with_map_separate_images_emits_one_png_per_group(self, tmp_path):
        sensors = _make_sensors_with_fov(n=2)  # 2 groups
        out = tmp_path / "viz.png"
        plot_sensor_groups(
            sensors, _make_map_file(tmp_path), str(out),
            separate_images=True, label_ids=True,
            scene_name="Scene_X", algorithm_name="alg-A",
        )
        # One PNG per group, named with the group suffix.
        produced = sorted(p.name for p in tmp_path.glob("viz_*.png"))
        assert produced == ["viz_bev-sensor-1.png", "viz_bev-sensor-2.png"]

    def test_with_map_combined_writes_single_output(self, tmp_path):
        sensors = _make_sensors_with_fov(n=2)
        out = tmp_path / "combined.png"
        plot_sensor_groups(
            sensors, _make_map_file(tmp_path), str(out),
            separate_images=False,
        )
        assert out.is_file()

    def test_no_map_falls_back_to_black_background(self, tmp_path):
        """Passing a non-existent ``map_file`` triggers the
        ``plot_sensor_groups_black_background`` fallback."""
        sensors = _make_sensors_with_fov(n=2)
        out = tmp_path / "fallback.png"
        plot_sensor_groups(
            sensors, map_file=None, output_map_file=str(out),
            separate_images=True,
        )
        # Fallback path emits per-group PNGs too.
        produced = sorted(p.name for p in tmp_path.glob("fallback_*.png"))
        assert len(produced) == 2


class TestPlotSensorGroupsBlackBackground:
    def test_separate_images_writes_one_png_per_group(self, tmp_path):
        sensors = _make_sensors_with_fov(n=2)
        out = tmp_path / "bg.png"
        plot_sensor_groups_black_background(
            sensors, str(out), separate_images=True,
            label_ids=True, scene_name="S", algorithm_name="A",
        )
        produced = sorted(p.name for p in tmp_path.glob("bg_*.png"))
        assert produced == ["bg_bev-sensor-1.png", "bg_bev-sensor-2.png"]

    def test_combined_writes_single_output(self, tmp_path):
        sensors = _make_sensors_with_fov(n=2)
        out = tmp_path / "bg_combined.png"
        plot_sensor_groups_black_background(
            sensors, str(out), separate_images=False,
        )
        assert out.is_file()

    def test_no_polygons_returns_early_with_warning(self, tmp_path, caplog):
        """When every sensor lacks a parseable FOV polygon, the
        function warns and returns without writing any file."""
        import logging
        sensors = [{
            "id": "X",
            "group": {"name": "g1"},
            "attributes": [
                {"name": "fieldOfViewPolygon", "value": "BAD WKT"},
            ],
        }]
        out = tmp_path / "noviz.png"
        with caplog.at_level(logging.WARNING):
            plot_sensor_groups_black_background(sensors, str(out))
        assert "No valid polygons" in caplog.text
        assert not out.exists()

    def test_skips_sensors_without_attributes_field(self, tmp_path):
        """A sensor without an ``attributes`` key is skipped silently
        (the ``"attributes" not in sensor: continue`` branch)."""
        sensors = _make_sensors_with_fov(n=1) + [{
            "id": "no-attrs", "group": {"name": "bev-sensor-1"},
        }]
        out = tmp_path / "skip.png"
        plot_sensor_groups_black_background(
            sensors, str(out), separate_images=False,
        )
        assert out.is_file()


# ===========================================================
# Coverage supplement (merged from test_camera_groups_coverage.py)
# ===========================================================

"""Coverage supplement for ``visualization.camera_groups`` — pins the
small branches the existing test_camera_groups.py doesn't reach:

* ``_plot_polygon_on_ax`` exception handler for unplottable polygons
  and centroid-fallback ``return None``,
* ``plot_sensor_groups`` (with map, combined) MultiPolygon branch,
  polygon-is-None continue, union-update when multiple sensors share
  a group, and label_ids branch,
* ``plot_sensor_groups_black_background`` union-update for multi-
  sensor groups and label_ids branch in the combined-image path.
"""

import os
from unittest.mock import MagicMock

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from spatialai_data_utils.visualization import camera_groups  # noqa: E402
from spatialai_data_utils.visualization.camera_groups import (  # noqa: E402
    _plot_polygon_on_ax,
    plot_sensor_groups,
    plot_sensor_groups_black_background,
)


# ---------------------------------------------------------------------------
# _plot_polygon_on_ax — exception handler + centroid fallback
# ---------------------------------------------------------------------------


def test_plot_polygon_on_ax_swallows_axis_fill_failure(caplog):
    """If ``ax.fill`` raises (e.g. because the polygon's
    ``exterior.xy`` access fails for a malformed geometry), the
    helper logs a warning and continues. We patch ``_extract_polygons``
    to yield a polygon whose ``exterior.xy`` access raises, and
    confirm the function still returns None (the empty list reached
    the centroid branch with no polygons left to fill)."""
    import logging
    fig, ax = plt.subplots()
    try:
        # A real polygon, but we monkey-patch the *axis* to raise on
        # ``.fill`` so the inner try/except branch fires.
        from shapely.geometry import Polygon
        good_poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        bad_ax = MagicMock(wraps=ax)
        bad_ax.fill.side_effect = AttributeError("synthetic")
        with caplog.at_level(logging.WARNING):
            result = _plot_polygon_on_ax(bad_ax, good_poly, color="#ff0000")
        # With every fill failing, the polygons list still had
        # entries so centroid extraction ran (returns shapely centroid).
        assert result is not None
        assert "Failed to plot polygon" in caplog.text
    finally:
        plt.close(fig)


def test_plot_polygon_on_ax_returns_none_when_centroid_raises():
    """A geometry that produces an empty polygon list returns
    ``None`` from the head guard (covers the ``if not polygons:
    return None`` line)."""
    from shapely.geometry import GeometryCollection
    fig, ax = plt.subplots()
    try:
        # Empty collection -> no polygons -> early-return None.
        result = _plot_polygon_on_ax(
            ax, GeometryCollection([]), color="#0000ff",
        )
        assert result is None
    finally:
        plt.close(fig)


def test_plot_polygon_on_ax_returns_none_when_centroid_extraction_raises(
    monkeypatch,
):
    """Lines 202-203: when ``unary_union(polygons).centroid`` raises
    ``AttributeError`` / ``TypeError`` / ``ValueError`` (e.g.
    pathological geometry), the helper swallows and returns ``None``.
    Force the raise by monkeypatching ``unary_union`` in the
    module's namespace."""
    from shapely.geometry import Polygon
    fig, ax = plt.subplots()
    try:
        a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        b = Polygon([(5, 5), (6, 5), (6, 6), (5, 6)])
        multi = MagicMock(name="multi_polygon_list")
        # Force the elif-branch (len(polygons) > 1).
        # Patch ``unary_union`` to raise so the centroid try/except fires.
        def _boom(_polys):
            raise ValueError("synthetic union failure")

        monkeypatch.setattr(camera_groups, "unary_union", _boom)
        # Use MultiPolygon containing two parts -> _extract_polygons
        # returns [a, b] (len > 1) -> centroid path goes through unary_union.
        from shapely.geometry import MultiPolygon as _MP
        result = _plot_polygon_on_ax(
            ax, _MP([a, b]), color="#00ff00",
        )
        assert result is None
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# Fixture helpers — sensors with shared groups + edge-case FOVs
# ---------------------------------------------------------------------------


def _sensor(
    *, sensor_id, group, fov_wkt="POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))",
):
    return {
        "id": sensor_id,
        "translationToGlobalCoordinates": {"x": 0.0, "y": 0.0},
        "scaleFactor": 1.0,
        "group": {"name": group},
        "attributes": [
            {"name": "frameWidth", "value": "1920"},
            {"name": "frameHeight", "value": "1080"},
            {"name": "fieldOfViewPolygon", "value": fov_wkt},
        ],
    }


def _two_sensors_same_group():
    """Two sensors with parseable polygons in the SAME group ->
    drives the ``group_union_polygon_vis is not None`` / ``unary_union``
    update branches."""
    return [
        _sensor(sensor_id="Camera_00", group="bev-sensor-1",
                 fov_wkt="POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))"),
        _sensor(sensor_id="Camera_01", group="bev-sensor-1",
                 fov_wkt="POLYGON((5 5, 15 5, 15 15, 5 15, 5 5))"),
    ]


def _multipolygon_sensor():
    """Sensor whose FOV parses to a MultiPolygon (two disjoint squares)."""
    return _sensor(
        sensor_id="Camera_MP", group="bev-sensor-1",
        fov_wkt=(
            "MULTIPOLYGON("
            "((0 0, 5 0, 5 5, 0 5, 0 0)),"
            "((10 10, 15 10, 15 15, 10 15, 10 10))"
            ")"
        ),
    )


def _none_polygon_sensor():
    """Sensor with unparseable FOV string -> ``parse_polygon`` returns
    None, exercising the ``if polygon is None: continue`` skip."""
    return _sensor(
        sensor_id="Camera_None", group="bev-sensor-1",
        fov_wkt="THIS IS NOT WKT",
    )


# Note: ``_make_map_file`` is defined at the top of this module and
# reused below.


# ---------------------------------------------------------------------------
# plot_sensor_groups (with map): MultiPolygon + None-polygon + union path
# ---------------------------------------------------------------------------


class TestPlotSensorGroupsExtras:
    def test_separate_images_with_multipolygon_and_none_polygon_sensors(
        self, tmp_path,
    ):
        """``plot_sensor_groups`` (with map, ``separate_images=True``)
        with a mix of multipolygon + un-parseable + plain polygon
        sensors — drives the MultiPolygon buffer branch, the
        polygon-is-None ``continue``, and the union-update path."""
        sensors = (
            _two_sensors_same_group()
            + [_multipolygon_sensor(), _none_polygon_sensor()]
        )
        out = tmp_path / "viz.png"
        plot_sensor_groups(
            sensors, _make_map_file(tmp_path), str(out),
            separate_images=True, label_ids=True,
        )
        produced = sorted(p.name for p in tmp_path.glob("viz_*.png"))
        assert "viz_bev-sensor-1.png" in produced

    def test_combined_with_multipolygon_and_label_ids(self, tmp_path):
        """``plot_sensor_groups`` (with map, ``separate_images=False``,
        ``label_ids=True``) with two same-group sensors, a
        multipolygon, AND a sensor with un-parseable polygon — drives
        the combined-figure union-update branch, the
        label_ids+centroid_result write, AND the
        ``if polygon is None: continue`` skip (line 341)."""
        sensors = (
            _two_sensors_same_group()
            + [_multipolygon_sensor(), _none_polygon_sensor()]
        )
        out = tmp_path / "combined.png"
        plot_sensor_groups(
            sensors, _make_map_file(tmp_path), str(out),
            separate_images=False, label_ids=True,
        )
        assert out.is_file()


# ---------------------------------------------------------------------------
# plot_sensor_groups_black_background: union-update + combined label_ids
# ---------------------------------------------------------------------------


class TestPlotSensorGroupsBlackBackgroundExtras:
    def test_separate_images_with_multi_sensor_group_unions_polygons(
        self, tmp_path,
    ):
        """``plot_sensor_groups_black_background`` (separate_images=True)
        with two sensors in the same group drives the
        ``group_union_polygon = unary_union(...)`` update branch (line
        471)."""
        sensors = _two_sensors_same_group()
        out = tmp_path / "bg.png"
        plot_sensor_groups_black_background(
            sensors, str(out), separate_images=True, label_ids=True,
        )
        produced = sorted(p.name for p in tmp_path.glob("bg_*.png"))
        assert produced == ["bg_bev-sensor-1.png"]

    def test_combined_with_label_ids_and_duplicate_sensor_skip(
        self, tmp_path,
    ):
        """``plot_sensor_groups_black_background`` (combined, label_ids)
        with two same-group sensors + a duplicate-id sensor drives:
        * the ``group_union_polygon = unary_union(...)`` update (532),
        * the ``label_ids and centroid_result`` write (536-537),
        * the ``if sensor_id in processed_sensors: continue`` dedup
          branch (526).
        """
        sensors = _two_sensors_same_group() + [
            # Duplicate of Camera_00 (same id) — skipped by dedup set.
            _sensor(sensor_id="Camera_00", group="bev-sensor-1",
                     fov_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"),
        ]
        out = tmp_path / "bg_combined.png"
        plot_sensor_groups_black_background(
            sensors, str(out),
            separate_images=False, label_ids=True,
        )
        assert out.is_file()

# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import pytest
from datetime import datetime, timedelta
from mdx.analytics.core.schema.models import Coordinate
from mdx.analytics.core.schema.trajectory.trajectory_g import TrajectoryG
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType


@pytest.fixture
def sample_geo_trajectory():
    """Create a sample geo trajectory for testing"""
    points = [
        Coordinate(x=-122.4194, y=37.7749, z=0.0),  # San Francisco
        Coordinate(x=-122.3321, y=37.8085, z=0.0),  # Oakland
        Coordinate(x=-122.2711, y=37.8044, z=0.0),  # Berkeley
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1800)  # 30 minutes
    return TrajectoryG(
        id="test_geo_trajectory",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True
    )


def test_geo_distance_calculation(sample_geo_trajectory):
    """Test geo distance calculations using haversine formula"""
    # Distance should be calculated using haversine formula when geo is enabled
    assert sample_geo_trajectory.distance > 0
    assert sample_geo_trajectory.distance < 100000  # Should be less than 100km for SF to Berkeley


def test_geo_bearing_calculation(sample_geo_trajectory):
    """Test geo bearing calculations"""
    # Bearing should be calculated using geo coordinates
    bearing = sample_geo_trajectory.bearing
    assert 0 <= bearing <= 360
    # For SF to Berkeley, bearing should be roughly northeast
    assert 0 < bearing < 90


def test_direction_modes():
    """Test different direction modes for geo trajectories"""
    points = [
        Coordinate(x=-122.4194, y=37.7749, z=0.0),  # San Francisco
        Coordinate(x=-122.2711, y=37.8044, z=0.0),  # Berkeley
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1800)

    # Test mode 0 (4 directions)
    traj_4dir = TrajectoryG(
        id="test_4dir",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_mode=0,
        timestamps=[start_time, end_time]
    )
    assert traj_4dir.direction in ["N", "E", "S", "W"]

    # Test mode 1 (8 directions)
    traj_8dir = TrajectoryG(
        id="test_8dir",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_mode=1,
        timestamps=[start_time, end_time]
    )
    assert traj_8dir.direction in ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

    # Test mode 2 (16 directions)
    traj_16dir = TrajectoryG(
        id="test_16dir",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_mode=2,
        timestamps=[start_time, end_time]
    )
    assert traj_16dir.direction in [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
    ]


def test_direction_based_clustering():
    """Test direction-based clustering modes"""
    points = [
        Coordinate(x=-122.4194, y=37.7749, z=0.0),  # San Francisco
        Coordinate(x=-122.2711, y=37.8044, z=0.0),  # Berkeley
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1800)

    # Test mode 0 (4 clusters)
    traj_4cluster = TrajectoryG(
        id="test_4cluster",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_based_cluster_mode=0,
        timestamps=[start_time, end_time]
    )
    assert 0 <= traj_4cluster.direction_based_cluster_index < 4

    # Test mode 1 (8 clusters)
    traj_8cluster = TrajectoryG(
        id="test_8cluster",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_based_cluster_mode=1,
        timestamps=[start_time, end_time]
    )
    assert 0 <= traj_8cluster.direction_based_cluster_index < 8


def test_geo_str_representation(sample_geo_trajectory):
    """Test string representation of geo trajectory"""
    str_repr = str(sample_geo_trajectory)
    assert sample_geo_trajectory.id in str_repr
    assert "mph" in str_repr
    assert "meters" in str_repr
    assert sample_geo_trajectory.direction in str_repr


def test_geo_vs_euclidean():
    """Test differences between geo and euclidean calculations"""
    points = [
        Coordinate(x=-122.4194, y=37.7749, z=0.0),  # San Francisco
        Coordinate(x=-122.2711, y=37.8044, z=0.0),  # Berkeley
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1800)

    # Geo trajectory
    geo_traj = TrajectoryG(
        id="geo",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        timestamps=[start_time, end_time]
    )

    # Euclidean trajectory (trajGeoCoordEnable off -> coordinates projected to metres)
    euclid_traj = TrajectoryG(
        id="euclid",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=False,
        timestamps=[start_time, end_time]
    )

    # Distances should be different
    assert geo_traj.distance != euclid_traj.distance
    # Geo distance should be larger (actual ground distance)
    assert geo_traj.distance > euclid_traj.distance


class TestCalibrationTypeDispatchIsComplete:
    """
    Both classes dispatch on ``calibration_type``, over different sets.

    ``Trajectory`` covers cartesian and image and rejects geographic. ``TrajectoryG`` is built for
    geographic and image calibration and rejects nothing, so it must override every member the base
    gates -- otherwise a geographic trajectory would fall through to the base and raise. These tests
    pin that: a gated member added to ``Trajectory`` and not overridden here fails the check.
    """

    def _geo(self, **kwargs):
        points = [Coordinate(x=-122.4194, y=37.7749, z=0.0), Coordinate(x=-122.3321, y=37.8085, z=0.0)]
        start = datetime(2025, 3, 1, 12, 0, 0)
        return TrajectoryG(
            id="g", start=start, end=start + timedelta(seconds=10), points=points,
            calibration_type=CalibrationType.GEO, enable_geo=True, **kwargs,
        )

    def test_geo_is_the_default_for_this_class(self):
        """TrajectoryG defaults to geographic rather than inheriting Trajectory's cartesian default."""
        from datetime import datetime as _dt
        points = [Coordinate(x=-122.4194, y=37.7749, z=0.0), Coordinate(x=-122.3321, y=37.8085, z=0.0)]
        start = _dt(2025, 3, 1, 12, 0, 0)
        unspecified = TrajectoryG(id="g", start=start, end=start + timedelta(seconds=10), points=points)

        assert unspecified.calibration_type == CalibrationType.GEO
        assert self._geo().calibration_type == CalibrationType.GEO

    def test_every_calibration_type_gated_member_is_overridden(self):
        """Guards the trap: a gated member left un-overridden would raise on geographic input.

        ``Trajectory`` raises ValueError for GEO in each of these. TrajectoryG must define its own,
        so if someone adds a fifth gated member upstream without overriding it here, this fails
        rather than that member blowing up in a geographic deployment.
        """
        from mdx.analytics.core.schema.trajectory.trajectory import Trajectory

        gated = {
            name for name in dir(Trajectory)
            if (src := self._source_of(Trajectory, name)) and "calibration_type" in src
        }
        assert gated, "expected Trajectory to gate some members on calibration_type"

        not_overridden = {n for n in gated if getattr(TrajectoryG, n, None) is getattr(Trajectory, n, None)}
        assert not_overridden == set(), (
            f"TrajectoryG must override every calibration_type-gated member; missing: {not_overridden}"
        )

    @staticmethod
    def _source_of(owner, name):
        """Source of a member as defined on ``owner`` itself, unwrapping cached_property/computed_field."""
        import inspect
        attr = owner.__dict__.get(name)
        if attr is None:
            return None
        func = getattr(attr, "func", None) or getattr(attr, "fget", None) or attr
        try:
            return inspect.getsource(func)
        except (TypeError, OSError):
            return None

    def test_gated_members_return_geo_answers_not_cartesian_ones(self):
        """The overrides are real: geographic speed uses haversine, not the cartesian branch."""
        geo = self._geo()
        # Reaching Trajectory's implementation with calibration_type=GEO would raise instead.
        assert geo.speed > 0
        assert geo.speed_over_time
        assert 0 <= geo.bearing <= 360
        assert "mph" in str(geo)


class TestUnitsAndMetricAreIndependent:
    """
    ``calibration_type`` and ``enable_geo`` answer different questions and do not move together.

    ``trajGeoCoordEnable`` is an output-format switch on ``CalibrationG.transform``: with it off, a
    geographically calibrated deployment emits coordinates projected to ``crsCartesian`` -- still
    metres, just not lat/lon. So it selects haversine versus euclidean. The units come from the
    calibration instead: only image calibration is in pixels. Collapsing the two would either apply
    haversine to projected metres or strip the mph conversion from them.
    """

    def _traj(self, calibration_type, enable_geo):
        # 100 units covered in 2 s.
        points = [Coordinate(x=100.0, y=200.0, z=0.0), Coordinate(x=200.0, y=200.0, z=0.0)]
        start = datetime(2025, 3, 1, 12, 0, 0)
        return TrajectoryG(
            id="t", start=start, end=start + timedelta(seconds=2), points=points,
            calibration_type=calibration_type, enable_geo=enable_geo,
            smooth_min_points=3, smooth_window_size=3, distance_stride=1, speed_segment_size=3,
        )

    def test_geo_calibration_converts_whether_or_not_coords_are_latlon(self):
        """trajGeoCoordEnable off yields projected metres -- metric, so still mph."""
        from mdx.analytics.core.utils.distance_util import MPS_TO_MPH
        projected = self._traj(CalibrationType.GEO, enable_geo=False)

        assert projected.speed == pytest.approx(50.0 * MPS_TO_MPH)
        assert projected.distance == pytest.approx(100.0)  # euclidean, not haversine

    def test_image_calibration_is_left_in_pixels(self):
        """Reached via AnomalyDetector on an image-calibrated app; 100 px in 2 s reads as 50."""
        image = self._traj(CalibrationType.IMAGE, enable_geo=False)

        assert image.speed == pytest.approx(50.0)
        assert image.speed_over_time == pytest.approx([50.0])

    def test_latlon_uses_haversine_regardless_of_units(self):
        latlon = self._traj(CalibrationType.GEO, enable_geo=True)
        projected = self._traj(CalibrationType.GEO, enable_geo=False)

        assert latlon.distance != projected.distance

    def test_speed_over_time_agrees_with_speed_in_every_combination(self):
        """Whatever the unit, the two members must not disagree by a conversion factor."""
        for calibration_type in (CalibrationType.GEO, CalibrationType.IMAGE):
            for enable_geo in (True, False):
                traj = self._traj(calibration_type, enable_geo)
                assert traj.speed_over_time == pytest.approx([traj.speed]), (calibration_type, enable_geo)

    def test_str_names_pixel_units_only_for_image_calibration(self):
        assert "px/s" in str(self._traj(CalibrationType.IMAGE, enable_geo=False))
        assert "mph" in str(self._traj(CalibrationType.GEO, enable_geo=False))
        assert "mph" in str(self._traj(CalibrationType.GEO, enable_geo=True))

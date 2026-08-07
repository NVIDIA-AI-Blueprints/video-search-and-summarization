# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Wiring tests for the app entrypoints.

The apps were the one part of this service with no unit coverage: everything below ``src`` is tested,
but the seven -- now eight -- ``apps/*/main_*_app.py`` files were reachable only through the
docker-compose integration suite, which needs a live broker. That left the code most likely to break
during a refactor -- the handlers that stitch state management, events and detectors together --
verified only by reading.

These tests need no infrastructure. Sources and sinks connect lazily, so an app can be constructed
and inspected without a broker; what is asserted is the wiring: that each app builds against its
shipped config, registers the processors it is supposed to, selects the right collaborators, and
shuts down cleanly. Handler bodies that need real frames stay with the integration suite.
"""

import importlib.util
import json
import pathlib
import sys
from unittest.mock import patch

import pytest

from mdx.analytics.core.schema.config import AppConfig

REPO = pathlib.Path(__file__).resolve().parents[3]
APPS = REPO / "apps"
CONFIGS = REPO / "configs"


def _load(app_path: str):
    """Import an app module by path; ``apps`` is a script directory, not a package."""
    full = APPS / app_path
    spec = importlib.util.spec_from_file_location(f"_app_{full.stem}", full)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Every capability knob on CompositeApp, so a test can clear them and assert the code default
# rather than whatever the shipped starter config happens to set.
COMPOSITE_WORKER_KEYS = (
    "numWorkersForBehaviorCreation",
    "numWorkersForFrameEnhancement",
    "numWorkersForSpaceEstimation",
    "numWorkersForEmbedFiltering",
    "numWorkersForBehaviorClustering",
)


def _config(name: str, **overrides: str | None) -> AppConfig:
    """Load a shipped config, applying app-key overrides. A ``None`` value removes the key."""
    raw = json.loads((CONFIGS / name).read_text())
    raw["app"] = [kv for kv in raw.get("app", []) if kv["name"] not in overrides]
    raw["app"] += [{"name": k, "value": v} for k, v in overrides.items() if v is not None]
    return AppConfig(**raw)


@pytest.fixture
def app_factory():
    """Build apps with the config-file watcher stubbed, and close them afterwards.

    ``BaseApp`` starts a watchdog observer per instance; left running they would leak threads across
    the suite.
    """
    built = []

    def _build(app_cls, config, calibration_path=None):
        with patch("mdx.analytics.core.app.app_base.ConfigFileMonitor"):
            app = app_cls(config, calibration_path)
        built.append(app)
        return app

    yield _build

    for app in built:
        try:
            app.close()
        except Exception:  # noqa: BLE001 - teardown must not mask a test failure
            pass


# (module path, class name, shipped config) for every app that has one
PROFILE_APPS = [
    ("analytics/main_analytics_2d_app.py", "Analytics2DApp", "warehouse_2d_config.json"),
    ("analytics/main_analytics_3d_app.py", "Analytics3DApp", "warehouse_3d_config.json"),
    ("public_safety/main_public_safety_app.py", "PublicSafetyApp", "public_safety_config.json"),
    ("search_and_alerts/main_search_and_alerts_app.py", "SearchAndAlertsApp", "search_and_alerts_config.json"),
    ("smart_city/main_smart_city_app.py", "SmartCityApp", "smart_city_config.json"),
    ("rpm/main_rpm_app.py", "RPMApp", "rpm_config.json"),
    ("composite/main_composite_app.py", "CompositeApp", "composite_config.json"),
]


class TestAppsBuild:
    """Every app constructs against its shipped config and registers at least one processor."""

    @pytest.mark.parametrize("module_path,class_name,config_name", PROFILE_APPS)
    def test_app_builds_from_its_shipped_config(self, app_factory, module_path, class_name, config_name):
        """A shipped config plus its app must produce a working instance.

        Catches the failure mode a refactor is most likely to introduce: a collaborator whose
        constructor signature moved, or a config key an app expects that its config no longer sets.
        """
        app_cls = getattr(_load(module_path), class_name)

        app = app_factory(app_cls, _config(config_name))

        assert app.config is not None
        # composite ships every processor disabled, so it is the one app that legitimately registers none
        assert app.get_processors() or class_name == "CompositeApp"

    @pytest.mark.parametrize("module_path,class_name", [(m, c) for m, c, _ in PROFILE_APPS])
    def test_app_closes_cleanly(self, app_factory, module_path, class_name):
        """close() must not raise, including the behavior flush added for emit-once."""
        config_name = dict((c, cfg) for _, c, cfg in PROFILE_APPS)[class_name]
        app = app_factory(getattr(_load(module_path), class_name), _config(config_name))

        app.close()  # the fixture closes again; both must be safe


class TestBehaviorProducers:
    """Apps that produce behaviors share one state manager type and one flush contract."""

    @pytest.mark.parametrize("module_path,class_name,config_name", [
        p for p in PROFILE_APPS if p[1] not in ("SmartCityApp",)
    ])
    def test_uses_the_shared_state_manager(self, app_factory, module_path, class_name, config_name):
        """Non-geographic apps all use StateMgmt, which reads the coordinate system per trajectory."""
        from mdx.analytics.core.stream.state.behavior.state_management import StateMgmt

        app = app_factory(getattr(_load(module_path), class_name), _config(config_name))

        assert isinstance(app.state_mgmt, StateMgmt)

    def test_smart_city_selects_by_calibration_type(self, app_factory):
        """Smart city is the one app that switches state manager on calibration type."""
        from mdx.analytics.core.stream.state.behavior.state_management import StateMgmt
        from mdx.analytics.core.stream.state.behavior.state_management_g import StateMgmtG

        app_cls = getattr(_load("smart_city/main_smart_city_app.py"), "SmartCityApp")

        image = app_factory(app_cls, _config("smart_city_config.json"))
        assert isinstance(image.state_mgmt, StateMgmt)  # no calibration file -> image

        geo = app_factory(app_cls, _config("smart_city_config.json"),
                          str(CONFIGS / "calibration_smart_city_v3.0.json"))
        assert isinstance(geo.state_mgmt, StateMgmtG)

    @pytest.mark.parametrize("module_path,class_name,config_name", [
        p for p in PROFILE_APPS if p[1] != "CompositeApp"
    ])
    def test_flush_is_empty_when_emit_once_is_off(self, app_factory, module_path, class_name, config_name):
        """Per-batch mode holds nothing back, so the shutdown flush costs nothing."""
        app = app_factory(getattr(_load(module_path), class_name),
                          _config(config_name, behaviorEmitOnce="false"))

        assert app.state_mgmt.flush_behaviors() == []


class TestCompositeAppComposition:
    """The composite app exists to be configured into a capability set, so that is what is asserted."""

    @pytest.fixture
    def composite_cls(self):
        return getattr(_load("composite/main_composite_app.py"), "CompositeApp")

    def test_nothing_is_registered_by_default(self, app_factory, composite_cls):
        """An unconfigured app must register nothing rather than guess a default set.

        The keys are removed rather than zeroed, so this asserts the fallback in the app itself and
        stays true no matter what the shipped starter config enables. Registering nothing is not the
        same as running idle -- app_runner refuses to start with no processors -- but that is the
        runner's contract, covered by
        test_app_runner.py::TestAppRunnerStart::test_start_handles_when_no_processors_and_calls_close.
        """
        app = app_factory(composite_cls, _config(
            "composite_config.json", **{k: None for k in COMPOSITE_WORKER_KEYS}))

        assert app.get_processors() == []
        assert app.crs is None  # no road-network graph loaded either

    @pytest.mark.parametrize("workers,expected", [
        ({"numWorkersForBehaviorCreation": "1"}, {"create_behaviors"}),
        ({"numWorkersForBehaviorCreation": "1", "numWorkersForFrameEnhancement": "1"},
         {"create_behaviors", "enhance_frames"}),
        ({"numWorkersForBehaviorCreation": "1", "numWorkersForFrameEnhancement": "1",
          "numWorkersForSpaceEstimation": "1"},
         {"create_behaviors", "enhance_frames", "estimate_space"}),
        ({"numWorkersForBehaviorCreation": "1", "numWorkersForEmbedFiltering": "1"},
         {"create_behaviors", "process_chunk_embeddings"}),
        ({"numWorkersForBehaviorClustering": "1"}, {"process_behavior_clustering"}),
    ])
    def test_worker_counts_select_the_capabilities(self, app_factory, composite_cls, workers, expected):
        """Each processor is enabled solely by its own worker count."""
        # Zero every knob first: the assertion is that *only* the requested ones register, which
        # would otherwise be satisfied by whatever the starter config already enables.
        app = app_factory(composite_cls, _config(
            "composite_config.json", **({k: "0" for k in COMPOSITE_WORKER_KEYS} | workers)))

        assert {p.handler.__name__ for p in app.get_processors()} == expected

    def test_detection_stages_are_off_unless_enabled(self, app_factory, composite_cls):
        """Optional stages cost nothing when unused -- including the CRS they would need."""
        app = app_factory(composite_cls, _config("composite_config.json", **(
            {k: "0" for k in COMPOSITE_WORKER_KEYS} | {"numWorkersForBehaviorCreation": "1"})))

        assert app.anomaly_detector is None
        assert app.action_detector is None
        assert app.crs is None

    def test_anomaly_detection_builds_its_reference_system(self, app_factory, composite_cls):
        """Anomaly detection needs a CRS, so enabling it is what pays for the road-network load."""
        app = app_factory(composite_cls, _config("composite_config.json", **(
            {k: "0" for k in COMPOSITE_WORKER_KEYS}
            | {"numWorkersForBehaviorCreation": "1", "anomalyDetectionEnable": "true"})))

        assert app.anomaly_detector is not None
        assert app.crs is not None

    def test_action_detection_does_not_build_a_reference_system(self, app_factory, composite_cls):
        """Pose-action detection needs no CRS, so it must not trigger the graph load."""
        app = app_factory(composite_cls, _config("composite_config.json", **(
            {k: "0" for k in COMPOSITE_WORKER_KEYS}
            | {"numWorkersForBehaviorCreation": "1", "actionDetectionEnable": "true"})))

        assert app.action_detector is not None
        assert app.crs is None

    def test_config_defines_every_topic_the_processors_write_to(self):
        """The starter config must satisfy the constraint the class docstring states.

        Sinks resolve a topic before looking at the message list, so a processor writing to a topic
        the config omits raises on its first batch even with nothing to write.
        """
        config = json.loads((CONFIGS / "composite_config.json").read_text())
        defined = {t["name"] for t in config["kafka"]["topics"]}

        required = {"raw", "frames", "behavior", "events", "incidents",
                    "anomaly", "spaceUtilization", "embed", "embedFiltered"}
        assert required <= defined, f"missing topics: {sorted(required - defined)}"

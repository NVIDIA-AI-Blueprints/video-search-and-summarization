#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prove the slow-timeout lint fails on the edits it exists to catch.

A guard on this branch has twice certified the exact broken configuration it
was written to reject, so every rule in ``check_gateway_slow_timeouts.py`` is
exercised from the broken side as well as the clean one -- above all the two
edits nothing else would notice: a raised ``timeout server`` reverted to the
default, and a second front door onto a slow service that inherits it.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_gateway_slow_timeouts.py")
SPEC = importlib.util.spec_from_file_location("check_gateway_slow_timeouts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)


def _template(directory: str, *, default: str = "120s", vlm: str | None = "600s", extra: str = "") -> Path:
    """A minimal template carrying one registered slow backend."""
    path = Path(directory) / "haproxy.cfg.template"
    override = f"    timeout server {vlm}\n" if vlm is not None else ""
    path.write_text(
        "defaults\n"
        "    mode http\n"
        f"    timeout server {default}\n"
        "    timeout tunnel 3600s\n"
        "\n"
        "backend bk_rtvi_cv_strip\n"
        '    server s1 "${RTVI_CV_SERVICE_HOST}:${RTVI_CV_SERVICE_PORT}" check\n'
        "\n"
        "backend bk_rtvi_vlm_strip\n"
        + override
        + '    server s1 "${RTVI_VLM_SERVICE_HOST}:${RTVI_VLM_SERVICE_PORT}" check\n'
        + extra
    )
    return path


def _helm(directory: str, *, chart_default: str = '""', profile: str | None = '"600s"') -> Path:
    """A Helm tree shaped like the real one: chart default plus a profile override."""
    root = Path(directory) / "helm"
    chart = root / "services/rtvi/charts/rtvi-vlm"
    chart.mkdir(parents=True)
    (chart / "values.yaml").write_text(f"service:\n  port: 8000\ningressTimeoutServer: {chart_default}\n")

    override = f"    ingressTimeoutServer: {profile}\n" if profile is not None else ""
    profiles = root / "developer-profiles/dev-profile-base"
    profiles.mkdir(parents=True)
    (profiles / "values.yaml").write_text("rtvi:\n  vss-rtvi-vlm:\n    enabled: true\n" + override)
    return root


def _nested_helm(
    directory: str,
    *,
    umbrella: str = "true",
    service: str | None = "true",
    sub_feature: bool = False,
    chart_default: str = '""',
    profile: str | None = '"600s"',
) -> Path:
    """A profile shaped like the real ones: a service nested under its umbrella.

    ``_helm`` above omits the umbrella's own ``enabled``, which is the half of
    the shape that decides whether the service is installed at all.
    """
    root = Path(directory) / "helm"
    chart = root / "services/rtvi/charts/rtvi-vlm"
    chart.mkdir(parents=True)
    (chart / "values.yaml").write_text(f"service:\n  port: 8000\ningressTimeoutServer: {chart_default}\n")

    lines = ["rtvi:", f"  enabled: {umbrella}", "  vss-rtvi-vlm:"]
    if service is not None:
        lines.append(f"    enabled: {service}")
    if profile is not None:
        lines.append(f"    ingressTimeoutServer: {profile}")
    if sub_feature:
        lines += ["    waitForKafka:", "      enabled: true"]

    profiles = root / "developer-profiles/dev-profile-base"
    profiles.mkdir(parents=True)
    (profiles / "values.yaml").write_text("\n".join(lines) + "\n")
    return root


class SanityTest(unittest.TestCase):
    """The registry is data; these tests pin it to the fixture above."""

    def setUp(self) -> None:
        self._original = dict(LINT.SLOW_BACKENDS)
        self._original_fast = dict(LINT.DEFAULT_TIMEOUT_BACKENDS)
        LINT.SLOW_BACKENDS.clear()
        LINT.SLOW_BACKENDS["bk_rtvi_vlm_strip"] = {
            "why": "a caption on a loaded RT-VLM outlasts the default",
            "helm": "services/rtvi/charts/rtvi-vlm",
            "key": "vss-rtvi-vlm",
        }
        LINT.DEFAULT_TIMEOUT_BACKENDS.clear()
        LINT.DEFAULT_TIMEOUT_BACKENDS["bk_rtvi_cv_strip"] = "its client bounds itself at read=120.0"

    def tearDown(self) -> None:
        LINT.SLOW_BACKENDS.clear()
        LINT.SLOW_BACKENDS.update(self._original)
        LINT.DEFAULT_TIMEOUT_BACKENDS.clear()
        LINT.DEFAULT_TIMEOUT_BACKENDS.update(self._original_fast)


class TimeParsingTest(unittest.TestCase):
    def test_haproxy_units(self) -> None:
        self.assertEqual(600.0, LINT.seconds("600s"))
        self.assertEqual(3600.0, LINT.seconds("1h"))
        self.assertEqual(600.0, LINT.seconds("10m"))
        self.assertEqual(120.0, LINT.seconds('"120s"'))

    def test_a_bare_number_is_milliseconds(self) -> None:
        """`timeout server 600` is 0.6s. Reading it as 600 would invert the check."""
        self.assertEqual(0.6, LINT.seconds("600"))

    def test_a_non_time_is_not_a_time(self) -> None:
        self.assertIsNone(LINT.seconds('""'))
        self.assertIsNone(LINT.seconds("soon"))


class CleanTemplateTest(SanityTest):
    def test_a_raised_backend_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual([], LINT.scan(_template(directory), _helm(directory)))

    def test_raising_the_value_further_still_passes(self) -> None:
        """The point is a floor, not a frozen integer: 900s must not fail."""
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(
                _template(directory, vlm="900s"),
                _helm(directory, profile='"900s"'),
            )
            self.assertEqual([], failures)

    def test_raising_the_default_instead_passes(self) -> None:
        """The threshold is read from `defaults`, so a 60s default is compared to."""
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(
                _template(directory, default="60s", vlm="300s"),
                _helm(directory, profile='"300s"'),
            )
            self.assertEqual([], failures)


class RevertedTimeoutTest(SanityTest):
    """The edit this guard exists for: the override goes away."""

    def test_a_deleted_timeout_fails_naming_the_backend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory, vlm=None), _helm(directory))
            joined = " ".join(failures)
            self.assertIn("bk_rtvi_vlm_strip", joined)
            self.assertIn("inherits the 120s default", joined)

    def test_a_timeout_lowered_to_the_default_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory, vlm="120s"), _helm(directory))
            joined = " ".join(failures)
            self.assertIn("bk_rtvi_vlm_strip", joined)
            self.assertIn("at or below the 120s default", joined)

    def test_a_timeout_below_the_default_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory, vlm="30s"), _helm(directory))
            self.assertIn("at or below the 120s default", " ".join(failures))

    def test_a_bare_millisecond_value_reads_as_a_revert(self) -> None:
        """`timeout server 600` looks like a raise and is 0.6s."""
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory, vlm="600"), _helm(directory))
            self.assertIn("at or below the 120s default", " ".join(failures))

    def test_a_retired_backend_still_in_the_registry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _template(directory)
            path.write_text(path.read_text().replace("bk_rtvi_vlm_strip", "bk_renamed"))
            failures = LINT.scan(path, _helm(directory))
            self.assertIn("no longer exists in the template", " ".join(failures))


class UnregisteredRaiseTest(SanityTest):
    """A raised timeout with no entry means the registry has fallen behind."""

    def test_an_unregistered_raised_backend_fails(self) -> None:
        extra = "\nbackend bk_new_model_strip\n    timeout server 900s\n    server s1 \"${NEW_HOST}:8000\" check\n"
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory, extra=extra), _helm(directory))
            joined = " ".join(failures)
            self.assertIn("bk_new_model_strip", joined)
            self.assertIn("not in SLOW_BACKENDS", joined)


class AliasParityTest(SanityTest):
    """Derived from the config: two front doors onto one service must agree."""

    def test_an_alias_inheriting_the_default_fails(self) -> None:
        extra = (
            "\nbackend bk_rtvi_vlm_alias_strip\n"
            '    server s1 "${RTVI_VLM_SERVICE_HOST}:${RTVI_VLM_SERVICE_PORT}" check\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory, extra=extra), _helm(directory))
            joined = " ".join(failures)
            self.assertIn("bk_rtvi_vlm_alias_strip", joined)
            self.assertIn("aliases of one service", joined)
            self.assertIn("would be the one that fails", joined)

    def test_an_alias_carrying_the_same_timeout_passes_parity(self) -> None:
        extra = (
            "\nbackend bk_rtvi_vlm_alias_strip\n"
            "    timeout server 600s\n"
            '    server s1 "${RTVI_VLM_SERVICE_HOST}:${RTVI_VLM_SERVICE_PORT}" check\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory, extra=extra), _helm(directory))
            self.assertNotIn("aliases of one service", " ".join(failures))


class DeliberateDefaultTest(SanityTest):
    """A route recorded as fast cannot quietly acquire an override."""

    def test_raising_a_recorded_fast_route_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _template(directory)
            path.write_text(
                path.read_text().replace(
                    "backend bk_rtvi_cv_strip\n",
                    "backend bk_rtvi_cv_strip\n    timeout server 3600s\n",
                )
            )
            failures = LINT.scan(path, _helm(directory))
            joined = " ".join(failures)
            self.assertIn("bk_rtvi_cv_strip", joined)
            self.assertIn("read=120.0", joined)


class HelmParityTest(SanityTest):
    """Docker and Kubernetes publish the same routes; both edges or neither."""

    def test_a_profile_that_enables_the_service_without_a_timeout_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory), _helm(directory, profile=None))
            joined = " ".join(failures)
            self.assertIn("dev-profile-base", joined)
            self.assertIn("sets no ingressTimeoutServer", joined)

    def test_a_chart_default_covers_a_profile_that_sets_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(
                _template(directory),
                _helm(directory, chart_default='"600s"', profile=None),
            )
            self.assertEqual([], failures)

    def test_a_helm_timeout_below_the_docker_one_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory), _helm(directory, profile='"300s"'))
            self.assertIn("below the 600s the Docker edge gives", " ".join(failures))

    def test_a_chart_with_no_knob_at_all_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            helm = _helm(directory)
            chart = helm / "services/rtvi/charts/rtvi-vlm/values.yaml"
            chart.write_text("service:\n  port: 8000\n")
            failures = LINT.scan(_template(directory), helm)
            self.assertIn("declares no ingressTimeoutServer", " ".join(failures))

    def test_a_disabled_service_is_not_held_to_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            helm = _helm(directory, profile=None)
            profile = helm / "developer-profiles/dev-profile-base/values.yaml"
            profile.write_text("rtvi:\n  vss-rtvi-vlm:\n    enabled: false\n")
            failures = LINT.scan(_template(directory), helm)
            self.assertNotIn("dev-profile-base", " ".join(failures))

    def test_a_missing_helm_tree_is_reported_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory), Path(directory) / "absent")
            self.assertIn("parity is unchecked", " ".join(failures))


class NestedEnablementTest(SanityTest):
    """Enablement is nested, and reading it flat gets the wrong answer.

    Every profile in this repo nests a service under its umbrella chart, and
    nests sub-feature blocks with an ``enabled`` of their own underneath *that*:
    ``agent.vss-agent.waitForDependencies.enabled``,
    ``rtvi.vss-rtvi-cv.engineCache.enabled``, ``infra.kafka.topicJob.enabled``.
    Attributing an ``enabled: true`` to the nearest block by bare name, with no
    regard for what encloses it, reads ``warehouse-2d-app`` -- which sets
    ``agent.enabled: false`` and still carries ``agent.vss-va-mcp.enabled:
    true`` -- as installing a service it does not install.
    """

    def test_an_umbrella_switched_off_does_not_enable_its_subcharts(self) -> None:
        """The warehouse-2d-app shape. Chart default covers the service, so a
        spurious profile entry is the only thing that could fail this."""
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(
                _template(directory),
                _nested_helm(directory, umbrella="false", profile=None, chart_default='"900s"'),
            )
            self.assertEqual([], failures)

    def test_an_umbrella_switched_off_is_not_read_as_a_profile_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(
                _template(directory),
                _nested_helm(directory, umbrella="false", profile=None),
            )
            self.assertNotIn("dev-profile-base", " ".join(failures))

    def test_an_umbrella_switched_on_still_holds_its_subcharts(self) -> None:
        """Non-vacuity: the ancestor rule must not switch the check off wholesale."""
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(
                _template(directory),
                _nested_helm(directory, umbrella="true", profile=None),
            )
            joined = " ".join(failures)
            self.assertIn("dev-profile-base", joined)
            self.assertIn("sets no ingressTimeoutServer", joined)

    def test_a_sub_feature_flag_does_not_displace_the_service(self) -> None:
        """``waitForKafka.enabled: true`` is not an enablement of the service,
        and must not take the service's override with it."""
        with tempfile.TemporaryDirectory() as directory:
            helm = _nested_helm(directory, sub_feature=True, profile='"600s"')
            _, enabled = LINT.helm_declarations(helm)
            self.assertEqual([600.0], [override for _, override in enabled["vss-rtvi-vlm"]])
            self.assertEqual([], LINT.scan(_template(directory), helm))

    def test_two_blocks_sharing_a_name_do_not_share_a_record(self) -> None:
        """Keyed by path, so an unrelated block of the same name cannot blank
        the override of the one that is switched on."""
        with tempfile.TemporaryDirectory() as directory:
            helm = _nested_helm(directory, profile='"600s"')
            profile = helm / "developer-profiles/dev-profile-base/values.yaml"
            profile.write_text(
                profile.read_text() + 'retired:\n  vss-rtvi-vlm:\n    ingressTimeoutServer: ""\n'
            )
            _, enabled = LINT.helm_declarations(helm)
            self.assertEqual([600.0], [override for _, override in enabled["vss-rtvi-vlm"]])
            self.assertEqual([], LINT.scan(_template(directory), helm))


class NonVacuityTest(SanityTest):
    """A lint that recognises nothing passes forever."""

    def test_a_template_with_no_defaults_timeout_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _template(directory)
            path.write_text(path.read_text().replace("    timeout server 120s\n", ""))
            failures = LINT.scan(path, _helm(directory))
            self.assertIn("no threshold to compare against", " ".join(failures))

    def test_an_empty_registry_fails(self) -> None:
        LINT.SLOW_BACKENDS.clear()
        with tempfile.TemporaryDirectory() as directory:
            failures = LINT.scan(_template(directory), _helm(directory))
            self.assertIn("asserts nothing", " ".join(failures))

    def test_an_unrecognised_template_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "haproxy.cfg.template"
            path.write_text("defaults\n    timeout server 120s\n")
            failures = LINT.scan(path, _helm(directory))
            self.assertIn("no backends recognised", " ".join(failures))


class TheRealTreeTest(unittest.TestCase):
    """The shipped configuration, and proof this lint is in scope on it."""

    def test_the_shipped_tree_passes(self) -> None:
        self.assertEqual([], LINT.scan(LINT.TEMPLATE))

    def test_the_docker_default_is_the_threshold_being_used(self) -> None:
        default, line, backends = LINT.parse_template(LINT.TEMPLATE)
        self.assertEqual(120.0, default)
        self.assertGreater(line, 0)
        self.assertIn("bk_rtvi_vlm_strip", backends)

    def test_the_route_this_guard_was_written_for_is_actually_raised(self) -> None:
        """/rtvi-vlm now carries agent, alert-bridge and lvs-server VLM traffic.

        Asserted as "above the default" rather than "600", so a later 900s is
        not a failure -- but a revert to 120s is.
        """
        default, _, backends = LINT.parse_template(LINT.TEMPLATE)
        for name in ("bk_rtvi_vlm_strip", "bk_llm_strip", "bk_lvs_strip"):
            with self.subTest(backend=name):
                self.assertIsNotNone(backends[name]["timeout"], name)
                self.assertGreater(backends[name]["timeout"], default, name)

    def test_the_registry_and_the_template_agree(self) -> None:
        _, _, backends = LINT.parse_template(LINT.TEMPLATE)
        for name in LINT.SLOW_BACKENDS:
            self.assertIn(name, backends)
        for name in LINT.DEFAULT_TIMEOUT_BACKENDS:
            self.assertIn(name, backends)
            self.assertIsNone(backends[name]["timeout"], name)

    def test_a_disabled_umbrella_in_the_shipped_tree_is_read_as_disabled(self) -> None:
        """warehouse-2d-app sets ``agent.enabled: false`` and still carries
        ``agent.vss-va-mcp.enabled: true`` and
        ``agent.vss-agent.waitForDependencies.enabled: true`` under it.

        Pinned against the real file rather than only a fixture, because the
        fixture is what a later edit would keep passing while the tree drifted.
        """
        _, enabled = LINT.helm_declarations(LINT.HELM)
        warehouse = "warehouse-2d-app"
        installers = [str(path) for path, _ in enabled.get("vss-va-mcp", [])]
        self.assertFalse(
            [path for path in installers if warehouse in path],
            f"vss-va-mcp read as installed by {warehouse}, whose agent umbrella is disabled",
        )
        agents = [str(path) for path, _ in enabled.get("vss-agent", [])]
        self.assertFalse([path for path in agents if warehouse in path])

    def test_the_shipped_tree_still_has_something_to_gate(self) -> None:
        """The assertion above is only worth anything while the shape exists."""
        source = (LINT.HELM / "industry-profiles/warehouse-operations/warehouse-2d-app/values.yaml").read_text()
        self.assertIn("waitForDependencies", source)
        self.assertRegex(source, r"(?m)^agent:\n  enabled: false$")

    def test_the_helm_side_is_actually_being_read(self) -> None:
        chart_defaults, enabled = LINT.helm_declarations(LINT.HELM)
        self.assertIn("rtvi-vlm", chart_defaults)
        self.assertIn("video-summarization", chart_defaults)
        self.assertIn("vss-rtvi-vlm", enabled)
        self.assertTrue(enabled["vss-rtvi-vlm"])

    def test_every_helm_profile_that_enables_rtvi_vlm_raises_the_timeout(self) -> None:
        """The gap this guard found: dev-profile-base enabled it with no timeout."""
        chart_defaults, enabled = LINT.helm_declarations(LINT.HELM)
        default = chart_defaults["rtvi-vlm"]
        _, _, backends = LINT.parse_template(LINT.TEMPLATE)
        docker = backends["bk_rtvi_vlm_strip"]["timeout"]
        for path, override in enabled["vss-rtvi-vlm"]:
            with self.subTest(profile=str(path)):
                effective = override if override is not None else default
                self.assertIsNotNone(effective, str(path))
                self.assertGreaterEqual(effective, docker, str(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)

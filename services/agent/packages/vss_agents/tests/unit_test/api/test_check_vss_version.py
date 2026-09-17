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

"""Tests for ``services/agent/scripts/check_vss_version.py``.

Two layers. The unit tests exercise the range parser and comparison directly.
The CLI tests run the script as a subprocess against a throwaway HTTP server,
with ``-S`` and a scrubbed ``sys.path``, which is also what proves the
stdlib-only, copy-and-run property the benchmark team relies on.
"""

from collections.abc import Iterator
import http.server
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "check_vss_version.py"
SKILLS_DIR = Path(__file__).resolve().parents[7] / "skills" / "benchmarking"

# The two values the shipped deployments default to: Helm renders a prerelease,
# Compose a bare release. Every range must treat them identically, which is the
# whole reason prerelease is ignored for range satisfaction.
HELM_DEFAULT = "3.3.0-65576357eb80"
COMPOSE_DEFAULT = "3.3.0"

EXIT_OK = 0
EXIT_INDETERMINATE = 1
EXIT_USAGE = 2
EXIT_INCOMPATIBLE = 3


def _load() -> ModuleType:
    """Import the checker by path; it is stdlib-only, so this needs no extras."""
    spec = importlib.util.spec_from_file_location("check_vss_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load()


# ── Range parser ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec, expected",
    [
        (">=3.3.0", [(">=", (3, 3, 0))]),
        (">3.3.0", [(">", (3, 3, 0))]),
        ("<4.0.0", [("<", (4, 0, 0))]),
        ("<=3.4.1", [("<=", (3, 4, 1))]),
        ("==3.3.0", [("==", (3, 3, 0))]),
        (">=3.2.0,<4.0.0", [(">=", (3, 2, 0)), ("<", (4, 0, 0))]),
        ("  >=3.2.0 , <4.0.0  ", [(">=", (3, 2, 0)), ("<", (4, 0, 0))]),
        ("==0.0.0", [("==", (0, 0, 0))]),
    ],
)
def test_parse_requirement_accepts_the_grammar(spec: str, expected: list) -> None:
    assert checker.parse_requirement(spec) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "",  # empty range
        "   ",  # whitespace-only range
        ",",  # nothing but a separator
        ">=3.3.0,",  # stray trailing comma
        "3.3.0",  # no comparator
        ">=3.3",  # not MAJOR.MINOR.PATCH
        ">=3.3.0.1",  # four components
        ">=v3.3.0",  # SemVer has no leading v
        ">=03.3.0",  # leading zero
        ">=3.3.0-rc.1",  # prerelease in a bound: would imply we honour it
        ">=3.3.0+build.42",  # build metadata in a bound
        "=>3.3.0",  # comparator typo
        "~=3.3.0",  # PEP 440, not this grammar
        ">= 3.3.0 <4.0.0",  # space-separated instead of comma-separated
        ">=",  # comparator with no bound
    ],
)
def test_parse_requirement_rejects_malformed_ranges(spec: str) -> None:
    with pytest.raises(ValueError):
        checker.parse_requirement(spec)


def test_parse_requirement_error_names_the_problem() -> None:
    with pytest.raises(ValueError, match="empty"):
        checker.parse_requirement("")
    with pytest.raises(ValueError, match="comparator"):
        checker.parse_requirement("3.3.0")


# ── Precedence and comparison ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "version, expected",
    [
        ("3.3.0", (3, 3, 0)),
        ("3.3.0-65576357eb80", (3, 3, 0)),
        ("3.3.0-rc.1+build.42", (3, 3, 0)),
        ("10.2.30", (10, 2, 30)),
        ("0.0.0", (0, 0, 0)),
    ],
)
def test_precedence_drops_prerelease_and_build(version: str, expected: tuple) -> None:
    assert checker.precedence(version) == expected


@pytest.mark.parametrize("version", ["3.3", "v3.3.0", "03.3.0", "develop-latest", ""])
def test_precedence_rejects_non_semver(version: str) -> None:
    with pytest.raises(ValueError):
        checker.precedence(version)


def test_numeric_not_lexicographic_ordering() -> None:
    """A string compare would put 3.10.0 below 3.9.0 and break every upper bound."""
    assert checker.satisfies("3.10.0", checker.parse_requirement(">=3.9.0"))
    assert not checker.satisfies("3.9.0", checker.parse_requirement(">=3.10.0"))


@pytest.mark.parametrize(
    "spec, satisfied, unsatisfied",
    [
        (">=3.3.0", ["3.3.0", "3.3.1", "3.4.0", "4.0.0"], ["3.2.9", "3.2.0", "2.9.9"]),
        (">3.3.0", ["3.3.1", "3.4.0"], ["3.3.0", "3.2.9"]),
        ("<4.0.0", ["3.9.9", "3.3.0", "0.0.1"], ["4.0.0", "4.0.1", "5.0.0"]),
        ("<=3.3.0", ["3.3.0", "3.2.9"], ["3.3.1", "3.4.0"]),
        ("==3.3.0", ["3.3.0"], ["3.3.1", "3.2.0"]),
        (">=3.2.0,<4.0.0", ["3.2.0", "3.3.0", "3.9.9"], ["3.1.9", "4.0.0"]),
    ],
)
def test_comparator_boundaries(spec: str, satisfied: list[str], unsatisfied: list[str]) -> None:
    """Each comparator is checked exactly at its boundary, not just near it."""
    requirement = checker.parse_requirement(spec)
    for version in satisfied:
        assert checker.satisfies(version, requirement), f"{version} should satisfy {spec}"
    for version in unsatisfied:
        assert not checker.satisfies(version, requirement), f"{version} should not satisfy {spec}"


# ── The prerelease decision ──────────────────────────────────────────────────


@pytest.mark.parametrize("spec", [">=3.3.0", ">=3.2.0,<4.0.0", "==3.3.0", "<=3.3.0", "<4.0.0", ">3.2.0"])
def test_helm_and_compose_defaults_are_indistinguishable(spec: str) -> None:
    """The point of ignoring prerelease.

    Under strict semver.org precedence ``3.3.0-65576357eb80 < 3.3.0``, so
    ``>=3.3.0`` would pass on the Compose default and fail on the Helm one. Here
    a prerelease of X.Y.Z counts as X.Y.Z, so the same skill behaves the same on
    both deployment paths.
    """
    requirement = checker.parse_requirement(spec)
    assert checker.satisfies(HELM_DEFAULT, requirement) == checker.satisfies(COMPOSE_DEFAULT, requirement)


def test_both_defaults_satisfy_a_ge_330_range() -> None:
    """Specifically: the stock Helm deployment is not rejected by '>=3.3.0'."""
    requirement = checker.parse_requirement(">=3.3.0")
    assert checker.satisfies(COMPOSE_DEFAULT, requirement)
    assert checker.satisfies(HELM_DEFAULT, requirement)


# ── Reading the range out of a SKILL.md ──────────────────────────────────────

SHIPPED_SKILLS = [
    ("benchmark-video-summarization", ">=3.2.0,<4.0.0"),
    ("benchmark-vlm-qa", ">=3.3.0,<4.0.0"),
    ("vss-evaluate-caption-accuracy", ">=3.2.0,<4.0.0"),
]


@pytest.mark.parametrize("skill, expected_range", SHIPPED_SKILLS)
def test_shipped_skills_declare_a_parseable_range(skill: str, expected_range: str) -> None:
    requirement, raw, skill_version = checker.requirement_from_skill(SKILLS_DIR / skill / "SKILL.md")
    assert raw == expected_range
    assert requirement == checker.parse_requirement(expected_range)
    assert skill_version != "unknown"


@pytest.mark.parametrize("skill, _expected_range", SHIPPED_SKILLS)
def test_both_deployment_defaults_satisfy_every_shipped_skill(skill: str, _expected_range: str) -> None:
    """Both values a stock deployment reports are accepted by all three skills."""
    requirement, _raw, _version = checker.requirement_from_skill(SKILLS_DIR / skill / "SKILL.md")
    assert checker.satisfies(COMPOSE_DEFAULT, requirement)
    assert checker.satisfies(HELM_DEFAULT, requirement)


def _write_skill(tmp_path: Path, body: str) -> Path:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(body, encoding="utf-8")
    return skill_md


def test_missing_field_is_indeterminate_not_a_silent_pass(tmp_path: Path) -> None:
    """A skill that declares nothing must not be treated as compatible."""
    skill_md = _write_skill(tmp_path, '---\nname: x\nmetadata:\n  version: "3.2.0"\n---\nbody\n')
    with pytest.raises(checker.IndeterminateError, match="declares no 'requires-vss'"):
        checker.requirement_from_skill(skill_md)


def test_malformed_field_is_indeterminate(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, '---\nmetadata:\n  requires-vss: "not a range"\n---\n')
    with pytest.raises(checker.IndeterminateError, match="cannot be parsed"):
        checker.requirement_from_skill(skill_md)


def test_empty_field_is_indeterminate(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, '---\nmetadata:\n  requires-vss: ""\n---\n')
    with pytest.raises(checker.IndeterminateError, match="declares no 'requires-vss'"):
        checker.requirement_from_skill(skill_md)


def test_absent_file_is_indeterminate(tmp_path: Path) -> None:
    with pytest.raises(checker.IndeterminateError, match="cannot read"):
        checker.requirement_from_skill(tmp_path / "nope" / "SKILL.md")


def test_no_front_matter_is_indeterminate(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, "# Just a heading\n")
    with pytest.raises(checker.IndeterminateError, match="front matter"):
        checker.requirement_from_skill(skill_md)


def test_unterminated_front_matter_is_indeterminate(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, '---\nmetadata:\n  requires-vss: ">=3.2.0"\n')
    with pytest.raises(checker.IndeterminateError, match="unterminated"):
        checker.requirement_from_skill(skill_md)


def test_field_is_read_at_top_level_too(tmp_path: Path) -> None:
    """The spec permits the field at the top level; these skills nest it."""
    skill_md = _write_skill(tmp_path, '---\nrequires-vss: ">=3.2.0,<4.0.0"\nversion: "1.0.0"\n---\n')
    requirement, raw, skill_version = checker.requirement_from_skill(skill_md)
    assert raw == ">=3.2.0,<4.0.0"
    assert requirement == checker.parse_requirement(raw)
    assert skill_version == "1.0.0"


def test_commented_out_field_is_not_read(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, '---\nmetadata:\n  # requires-vss: ">=9.0.0"\n---\n')
    with pytest.raises(checker.IndeterminateError, match="declares no 'requires-vss'"):
        checker.requirement_from_skill(skill_md)


# ── End-to-end through the real CLI ──────────────────────────────────────────


class _VersionHandler(http.server.BaseHTTPRequestHandler):
    """Serves whatever the test class attributes say, on /api/v1/version."""

    status = 200
    body = b'{"service":"vss","version":"3.3.0"}'
    origin = ""

    def do_GET(self) -> None:
        if self.path != "/api/v1/version":
            self.send_error(404, "Not Found")
            return
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: object) -> None:
        """Silence the per-request stderr logging."""


@pytest.fixture
def server() -> Iterator[type[_VersionHandler]]:
    """A real HTTP server, so the CLI exercises urllib rather than a mock."""
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _VersionHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _VersionHandler.status = 200
    _VersionHandler.body = b'{"service":"vss","version":"3.3.0"}'
    _VersionHandler.origin = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield _VersionHandler
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the checker the way an operator does — and prove it needs no packages.

    ``-S`` skips site-packages and ``PYTHONPATH`` is cleared, so any
    third-party import the script grew would fail here rather than in
    production on a bare ``python3``.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(
        [sys.executable, "-S", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_cli_runs_without_site_packages(server: type[_VersionHandler]) -> None:
    """The stdlib-only, copy-and-run property, asserted rather than assumed."""
    result = run_cli(server.origin)
    assert result.returncode == EXIT_OK, result.stderr
    assert result.stdout.strip() == "3.3.0"
    assert "ModuleNotFoundError" not in result.stderr


@pytest.mark.parametrize("version", [COMPOSE_DEFAULT, HELM_DEFAULT])
def test_cli_compatible_exits_zero(server: type[_VersionHandler], version: str) -> None:
    """JIRA outcome 1: compatible -> exit 0, print the version.

    Both deployment defaults, against the same range, through the real CLI.
    """
    server.body = json.dumps({"service": "vss", "version": version}).encode()
    result = run_cli(server.origin, "--require", ">=3.3.0,<4.0.0")
    assert result.returncode == EXIT_OK, result.stderr
    assert result.stdout.strip() == version


@pytest.mark.parametrize("skill, _range", SHIPPED_SKILLS)
@pytest.mark.parametrize("version", [COMPOSE_DEFAULT, HELM_DEFAULT])
def test_cli_compatible_against_each_shipped_skill(
    server: type[_VersionHandler], skill: str, _range: str, version: str
) -> None:
    server.body = json.dumps({"service": "vss", "version": version}).encode()
    result = run_cli(server.origin, "--skill", str(SKILLS_DIR / skill / "SKILL.md"))
    assert result.returncode == EXIT_OK, result.stderr
    assert result.stdout.strip() == version


def test_cli_incompatible_exits_three(server: type[_VersionHandler]) -> None:
    """JIRA outcome 2: incompatible -> distinct non-zero code and a clear message."""
    result = run_cli(server.origin, "--require", ">=4.0.0,<5.0.0")
    assert result.returncode == EXIT_INCOMPATIBLE
    assert "incompatible" in result.stderr
    # The message must name the deployed version, the range, and what to do.
    assert "3.3.0" in result.stderr
    assert ">=4.0.0,<5.0.0" in result.stderr
    assert "deploy a VSS release inside that range" in result.stderr


def test_cli_incompatible_names_the_skill_version(server: type[_VersionHandler], tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, '---\nmetadata:\n  version: "9.9.9"\n  requires-vss: "==1.0.0"\n---\n')
    result = run_cli(server.origin, "--skill", str(skill_md))
    assert result.returncode == EXIT_INCOMPATIBLE
    assert "9.9.9" in result.stderr
    assert "==1.0.0" in result.stderr


def test_cli_503_is_indeterminate(server: type[_VersionHandler]) -> None:
    """JIRA outcome 3, via a deployment that cannot report its version."""
    server.status = 503
    server.body = b'{"detail":"The deployed VSS version is unavailable."}'
    result = run_cli(server.origin, "--require", ">=3.2.0,<4.0.0")
    assert result.returncode == EXIT_INDETERMINATE
    assert "cannot determine compatibility" in result.stderr
    assert "503" in result.stderr
    assert "VSS_DEPLOYMENT_VERSION" in result.stderr


def test_cli_404_is_indeterminate_and_says_the_deployment_is_too_old(
    server: type[_VersionHandler],
) -> None:
    """A 404 means the deployment predates the endpoint — not a generic HTTP error."""
    result = run_cli(f"{server.origin}/nowhere", "--require", ">=3.2.0,<4.0.0")
    assert result.returncode == EXIT_INDETERMINATE
    assert "404" in result.stderr
    assert "predates the version endpoint" in result.stderr


def test_cli_404_is_indeterminate_without_a_range(server: type[_VersionHandler]) -> None:
    """The 404 message is the same when no range was asked for."""
    result = run_cli(f"{server.origin}/nowhere")
    assert result.returncode == EXIT_INDETERMINATE
    assert "predates the version endpoint" in result.stderr


def test_cli_unreachable_is_indeterminate() -> None:
    # Port 1 on loopback: nothing listens, and the refusal is immediate.
    result = run_cli("http://127.0.0.1:1", "--require", ">=3.2.0,<4.0.0", "--timeout", "2")
    assert result.returncode == EXIT_INDETERMINATE
    assert "not reachable" in result.stderr


def test_cli_non_json_is_indeterminate(server: type[_VersionHandler]) -> None:
    server.body = b"<html>not json</html>"
    result = run_cli(server.origin, "--require", ">=3.2.0,<4.0.0")
    assert result.returncode == EXIT_INDETERMINATE
    assert "did not return JSON" in result.stderr


def test_cli_wrong_shape_is_indeterminate(server: type[_VersionHandler]) -> None:
    server.body = b'{"service":"something-else","version":"3.3.0"}'
    result = run_cli(server.origin, "--require", ">=3.2.0,<4.0.0")
    assert result.returncode == EXIT_INDETERMINATE
    assert "expected" in result.stderr


def test_cli_non_semver_version_is_indeterminate(server: type[_VersionHandler]) -> None:
    server.body = b'{"service":"vss","version":"develop-latest"}'
    result = run_cli(server.origin, "--require", ">=3.2.0,<4.0.0")
    assert result.returncode == EXIT_INDETERMINATE
    assert "Semantic Versioning" in result.stderr


def test_cli_malformed_range_is_indeterminate(server: type[_VersionHandler]) -> None:
    result = run_cli(server.origin, "--require", "~=3.3.0")
    assert result.returncode == EXIT_INDETERMINATE
    assert "cannot be parsed" in result.stderr


def test_cli_empty_range_is_indeterminate(server: type[_VersionHandler]) -> None:
    result = run_cli(server.origin, "--require", "")
    assert result.returncode == EXIT_INDETERMINATE
    assert "empty" in result.stderr


def test_cli_skill_without_the_field_is_indeterminate(server: type[_VersionHandler], tmp_path: Path) -> None:
    """The case that must never be a silent pass."""
    skill_md = _write_skill(tmp_path, '---\nname: x\nmetadata:\n  version: "3.2.0"\n---\n')
    result = run_cli(server.origin, "--skill", str(skill_md))
    assert result.returncode == EXIT_INDETERMINATE
    assert "requires-vss" in result.stderr


def test_cli_rejects_both_range_sources(server: type[_VersionHandler], tmp_path: Path) -> None:
    """--skill and --require are mutually exclusive, so a run cannot enforce two."""
    result = run_cli(server.origin, "--skill", str(tmp_path / "SKILL.md"), "--require", ">=3.0.0")
    assert result.returncode == EXIT_USAGE


def test_cli_exit_codes_are_all_distinct() -> None:
    """The contract the docs publish and preflight.sh branches on."""
    codes = (checker.EXIT_OK, checker.EXIT_INDETERMINATE, checker.EXIT_USAGE, checker.EXIT_INCOMPATIBLE)
    assert codes == (EXIT_OK, EXIT_INDETERMINATE, EXIT_USAGE, EXIT_INCOMPATIBLE)
    assert len(set(codes)) == len(codes)
    assert checker.EXIT_OK == 0

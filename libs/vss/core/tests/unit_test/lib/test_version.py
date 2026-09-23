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
"""Tests for the reported-version contract: installed metadata, rendered as SemVer."""

import importlib.metadata

import pytest

from vss_core.version import SEMVER_PATTERN
from vss_core.version import library_version
from vss_core.version import pep440_to_semver
from vss_core.version import resolve_deployment_version


def _installed(value: str | None, monkeypatch) -> None:
    """Pin what ``nvidia-vss-core`` metadata says, or that there is none.

    Never read the real metadata: whether this test process happens to run
    against an installed package or a source tree on ``PYTHONPATH`` would
    otherwise decide the result.
    """

    def _version(name: str) -> str:
        assert name == "nvidia-vss-core"
        if value is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return value

    monkeypatch.setattr(importlib.metadata, "version", _version)


@pytest.mark.parametrize(
    ("pep440", "expected"),
    [
        # hatch-vcs `no-guess-dev` off a release tag: what a develop checkout
        # produced before the first pre-release tag of the next line.
        ("3.2.1.post1.dev1519+gc85c4a4e8", "3.2.1-dev.1519+gc85c4a4e8"),
        ("3.2.1.post1.dev1519+gc85c4a4e8.dirty", "3.2.1-dev.1519+gc85c4a4e8.dirty"),
        # Sitting on the tag: already SemVer, returned untouched.
        ("3.2.1", "3.2.1"),
        # hatch-vcs off a pre-release tag (v3.3.0rc0): on it, and past it.
        ("3.3.0rc0", "3.3.0-rc0"),
        ("3.3.0rc0.post1.dev20+g73f724482", "3.3.0-rc0.dev.20+g73f724482"),
        ("3.3.0a1.post1.dev2+gabcdef0.dirty", "3.3.0-a1.dev.2+gabcdef0.dirty"),
        ("3.3.0b2", "3.3.0-b2"),
        # The image stamp: <release line>+tree.<sha>, release or pre-release.
        ("3.3.0+tree.c85c4a4e8", "3.3.0+tree.c85c4a4e8"),
        ("3.3.0rc0+tree.c85c4a4e8", "3.3.0-rc0+tree.c85c4a4e8"),
        # Already-SemVer input is never re-derived, prerelease and all.
        ("3.3.0-rc.1+build.42", "3.3.0-rc.1+build.42"),
    ],
)
def test_pep440_becomes_semver(pep440: str, expected: str) -> None:
    assert pep440_to_semver(pep440) == expected
    assert SEMVER_PATTERN.fullmatch(expected)


def test_semver_precedence_orders_the_rendered_forms() -> None:
    """dev builds < the pre-release < the release, by the SemVer rules alone.

    Pre-release identifiers compare left to right, numerically when numeric,
    and a longer set of identifiers ranks higher than its prefix; a version
    with no pre-release ranks above every pre-release. ``rc0`` beats
    ``rc0.dev.20`` under the *first* of those, so the check is done on the
    identifier lists rather than by string comparison.
    """

    def identifiers(version: str) -> list[str]:
        pre = version.split("+", 1)[0].split("-", 1)
        return pre[1].split(".") if len(pre) == 2 else []

    assert identifiers("3.3.0-rc0.dev.20+g73f724482") == ["rc0", "dev", "20"]
    assert identifiers("3.3.0-rc0") == ["rc0"]
    assert identifiers("3.3.0") == []
    # rc0.dev.20 is rc0 with extra identifiers -> ranks below rc0 (prefix rule).
    assert identifiers("3.3.0-rc0.dev.20")[:1] == identifiers("3.3.0-rc0")
    assert len(identifiers("3.3.0-rc0.dev.20")) > len(identifiers("3.3.0-rc0"))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "3.2",
        "v3.2.1",
        "not-a-version",
        "3.2.1.dev",
        "1!3.2.1",
        # Unnormalised pre-release spellings hatch-vcs never emits.
        "3.3.0alpha1",
        "3.3.0-rc0",  # SemVer, not PEP 440 -- accepted, but via the SemVer path
    ],
)
def test_unconvertible_versions_are_rejected(value: str) -> None:
    """Returning ``None`` beats guessing: the caller reports no version instead."""
    if value == "3.3.0-rc0":
        assert pep440_to_semver(value) == value
        return
    assert pep440_to_semver(value) is None


def test_installed_metadata_is_normalised_to_semver(monkeypatch) -> None:
    """Installed metadata is PEP 440, and the endpoint's contract is SemVer."""
    _installed("3.3.0rc0.post1.dev12+gc85c4a4e8", monkeypatch)

    assert library_version() == "3.3.0-rc0.dev.12+gc85c4a4e8"


def test_installed_build_stamp_keeps_its_release_line(monkeypatch) -> None:
    """The build stamps `<release line>+tree.<sha>`; precedence ignores the metadata."""
    _installed("3.3.0rc0+tree.c85c4a4e8", monkeypatch)

    assert library_version() == "3.3.0-rc0+tree.c85c4a4e8"


def test_library_version_is_none_when_not_installed(monkeypatch) -> None:
    """A source tree on ``PYTHONPATH`` has no metadata to read."""
    _installed(None, monkeypatch)

    assert library_version() is None


def test_unnormalisable_metadata_is_none(monkeypatch) -> None:
    """An unusable stamp reports nothing rather than a version nobody can match."""
    _installed("not-a-version", monkeypatch)

    assert library_version() is None


def test_local_build_stamp_is_reported_as_what_it_is(monkeypatch) -> None:
    """A bare `docker build` stamps 0.0.0+local: valid, and unable to satisfy any range."""
    _installed("0.0.0+local", monkeypatch)

    assert library_version() == "0.0.0+local"


def test_deployment_version_is_the_installed_version(monkeypatch) -> None:
    """Nothing outranks the installed library and nothing stands in for it."""
    _installed("3.3.0rc0+tree.c85c4a4e8", monkeypatch)
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "9.9.9")

    assert resolve_deployment_version() == "3.3.0-rc0+tree.c85c4a4e8"


def test_nothing_installed_resolves_to_none(monkeypatch) -> None:
    _installed(None, monkeypatch)

    assert resolve_deployment_version() is None

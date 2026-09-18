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
"""Tests for the reported-version contract and its derivation from git."""

from pathlib import Path
import subprocess

import pytest

from vss_core import version as version_module
from vss_core.version import SEMVER_PATTERN
from vss_core.version import describe_version
from vss_core.version import pep440_to_semver
from vss_core.version import resolve_deployment_version


@pytest.fixture(autouse=True)
def _no_deployment_env(monkeypatch) -> None:
    for name in version_module.DEPLOYMENT_VERSION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository: the derivation reads `git describe`, so drive git."""
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "commit", "--allow-empty", "--no-gpg-sign", "-m", "initial")
    return tmp_path


@pytest.mark.parametrize(
    ("pep440", "expected"),
    [
        # hatch-vcs `no-guess-dev` off a release tag, which is what a develop
        # checkout of this repo produces.
        ("3.2.1.post1.dev1519+gc85c4a4e8", "3.2.1-dev.1519+gc85c4a4e8"),
        ("3.2.1.post1.dev1519+gc85c4a4e8.dirty", "3.2.1-dev.1519+gc85c4a4e8.dirty"),
        # Sitting on the tag: already SemVer, returned untouched.
        ("3.2.1", "3.2.1"),
        # Already-SemVer input is never re-derived, prerelease and all.
        ("3.3.0-rc.1+build.42", "3.3.0-rc.1+build.42"),
    ],
)
def test_pep440_becomes_semver(pep440: str, expected: str) -> None:
    assert pep440_to_semver(pep440) == expected
    assert SEMVER_PATTERN.fullmatch(expected)


@pytest.mark.parametrize("value", ["", "3.2", "v3.2.1", "not-a-version", "3.2.1.dev", "1!3.2.1"])
def test_unconvertible_versions_are_rejected(value: str) -> None:
    """Returning ``None`` beats guessing: the caller reports no version instead."""
    assert pep440_to_semver(value) is None


def test_describe_on_a_release_tag_is_the_bare_release(repo: Path) -> None:
    _git(repo, "tag", "v3.2.1")

    assert describe_version(repo) == "3.2.1"


def test_describe_off_a_tag_carries_distance_and_sha(repo: Path) -> None:
    _git(repo, "tag", "v3.2.1")
    _git(repo, "commit", "--allow-empty", "--no-gpg-sign", "-m", "later")

    derived = describe_version(repo)

    assert derived is not None
    assert SEMVER_PATTERN.fullmatch(derived)
    assert derived.startswith("3.2.1-dev.1+g")


def test_describe_marks_a_dirty_tree(repo: Path) -> None:
    """Build metadata says the tree had uncommitted changes, so results are unpinnable."""
    _git(repo, "tag", "v3.2.1")
    (repo / "changed.txt").write_text("edited", encoding="utf-8")
    _git(repo, "add", "changed.txt")

    derived = describe_version(repo)

    assert derived is not None
    assert derived.endswith(".dirty")
    assert SEMVER_PATTERN.fullmatch(derived)


def test_describe_uses_the_last_tag_reached_not_the_next_release(repo: Path) -> None:
    """A commit heading for 3.3.0 derives 3.2.1-dev.N, because 3.3.0 is not a fact yet."""
    _git(repo, "tag", "v3.2.1")
    _git(repo, "commit", "--allow-empty", "--no-gpg-sign", "-m", "heading for 3.3.0")

    derived = describe_version(repo)

    assert derived is not None
    assert derived.startswith("3.2.1-")


def test_describe_ignores_non_release_tags(repo: Path) -> None:
    """The `v[0-9]*` match is the one the hatch-vcs configs use."""
    _git(repo, "tag", "v3.2.1")
    _git(repo, "commit", "--allow-empty", "--no-gpg-sign", "-m", "later")
    _git(repo, "tag", "some-feature-tag")

    derived = describe_version(repo)

    assert derived is not None
    assert derived.startswith("3.2.1-dev.1+g")


def test_describe_returns_none_without_a_release_tag(repo: Path) -> None:
    assert describe_version(repo) is None


def test_describe_returns_none_outside_a_repository(tmp_path: Path) -> None:
    """The container case: sources are copied in, ``.git`` is not."""
    assert describe_version(tmp_path) is None


def test_configured_version_wins_over_the_derived_one(repo: Path, monkeypatch) -> None:
    _git(repo, "tag", "v3.2.1")
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "3.3.0")

    assert resolve_deployment_version(repo) == "3.3.0"


def test_legacy_variable_is_consulted_before_git(repo: Path, monkeypatch) -> None:
    _git(repo, "tag", "v3.2.1")
    monkeypatch.setenv("VSS_AGENT_VERSION", "3.3.0-65576357eb80")

    assert resolve_deployment_version(repo) == "3.3.0-65576357eb80"


def test_invalid_configured_version_does_not_fall_back_to_git(repo: Path, monkeypatch) -> None:
    """A deployment that states a version wrongly gets that reported, not hidden."""
    _git(repo, "tag", "v3.2.1")
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "develop-latest")

    assert resolve_deployment_version(repo) is None


def test_unconfigured_deployment_falls_back_to_git(repo: Path) -> None:
    _git(repo, "tag", "v3.2.1")

    assert resolve_deployment_version(repo) == "3.2.1"


def test_nothing_anywhere_resolves_to_none(tmp_path: Path) -> None:
    assert resolve_deployment_version(tmp_path) is None

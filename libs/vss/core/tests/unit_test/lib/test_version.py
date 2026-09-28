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

import importlib.metadata
from pathlib import Path
import subprocess

import pytest

from vss_core import version as version_module
from vss_core.version import SEMVER_PATTERN
from vss_core.version import describe_version
from vss_core.version import library_version
from vss_core.version import pep440_to_semver
from vss_core.version import resolve_deployment_version


@pytest.fixture(autouse=True)
def _no_deployment_env(monkeypatch) -> None:
    monkeypatch.delenv(version_module.DEPLOYMENT_VERSION_ENV_VAR, raising=False)


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


def test_installed_metadata_is_normalised_to_semver(monkeypatch) -> None:
    """Installed metadata is PEP 440, and the endpoint's contract is SemVer."""
    _installed("3.3.0.post1.dev12+gc85c4a4e8", monkeypatch)

    assert library_version() == "3.3.0-dev.12+gc85c4a4e8"


def test_installed_build_stamp_keeps_its_release_line(monkeypatch) -> None:
    """The build stamps `<release line>+tree.<sha>`; precedence ignores the metadata."""
    _installed("3.3.0+tree.c85c4a4e8", monkeypatch)

    assert library_version() == "3.3.0+tree.c85c4a4e8"


def test_library_version_is_none_when_not_installed(monkeypatch) -> None:
    """A source tree on ``PYTHONPATH`` has no metadata to read."""
    _installed(None, monkeypatch)

    assert library_version() is None


def test_unnormalisable_metadata_is_none(monkeypatch) -> None:
    """An unusable stamp reports nothing rather than a version nobody can match."""
    _installed("not-a-version", monkeypatch)

    assert library_version() is None


def test_override_wins_over_the_installed_version(repo: Path, monkeypatch) -> None:
    """The override exists to correct a wrong stamp, so it has to outrank it."""
    _installed("3.3.0", monkeypatch)
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "3.4.0")

    assert resolve_deployment_version(repo) == "3.4.0"


def test_unconfigured_deployment_reports_the_installed_version(repo: Path, monkeypatch) -> None:
    _git(repo, "tag", "v3.2.1")
    _installed("3.3.0+tree.c85c4a4e8", monkeypatch)

    assert resolve_deployment_version(repo) == "3.3.0+tree.c85c4a4e8"


def test_invalid_override_does_not_fall_through(repo: Path, monkeypatch) -> None:
    """An operator correcting a stamp wrongly sees that, not the stamp they replaced."""
    _git(repo, "tag", "v3.2.1")
    _installed("3.3.0", monkeypatch)
    monkeypatch.setenv("VSS_DEPLOYMENT_VERSION", "develop-latest")

    assert resolve_deployment_version(repo) is None


def test_git_is_reached_only_without_an_installed_version(repo: Path, monkeypatch) -> None:
    _git(repo, "tag", "v3.2.1")
    _installed(None, monkeypatch)

    assert resolve_deployment_version(repo) == "3.2.1"


def test_nothing_anywhere_resolves_to_none(tmp_path: Path, monkeypatch) -> None:
    _installed(None, monkeypatch)

    assert resolve_deployment_version(tmp_path) is None

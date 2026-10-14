# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resource boundaries and the Docker Compose publication contract; no remote calls."""

import importlib.util
import io
import json
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "artifact", Path(__file__).parents[1] / "scripts/promoted_artifact.py"
)
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)
REF = "nv-metropolis-dev/vss-developer/vss-blueprints-compose-base:nightly-20261010"


@pytest.fixture
def renderer(monkeypatch):
    def execute(command, **kwargs):
        if command[0] == "ngc":
            assert command[-1] == REF and "--org" not in command
            assert kwargs["env"]["NGC_CLI_API_KEY"] == "test-only-key"
            assert "test-only-key" not in command
            root = Path(command[-2]) / "package/deploy/docker"
            (root / "services").mkdir(parents=True)
            (root / "containers.env").write_text("VSS_CONTAINER_TAG=published-pin\n")
            profile = root / "developer-profiles/dev-profile-base"
            profile.mkdir(parents=True)
            for name in (".env", "overrides.env", "generated.env"):
                (profile / name).write_text("# published coordinate layer\n")
            return Mock(returncode=0)
        assert "--no-env-resolution" in command and "--format" in command
        assert "VSS_CONTAINER_TAG" not in kwargs["env"]
        assert "VSS_RT_CV_IMAGE" not in kwargs["env"]
        return Mock(
            stdout=json.dumps(
                {
                    "services": {
                        name: {
                            "image": "nvcr.io/org/team/image:published-pin",
                            "environment": {"SECRET": "never-persist"},
                        }
                        for name in artifact.SERVICES
                    }
                }
            )
        )

    monkeypatch.setenv("NGC_API_KEY", "test-only-key")
    monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
    monkeypatch.setenv("VSS_CONTAINER_TAG", "borrowed-source-pin")
    monkeypatch.setenv("VSS_RT_CV_IMAGE", "borrowed-source-image")
    mock = Mock(side_effect=execute)
    monkeypatch.setattr(artifact.subprocess, "run", mock)
    return mock


def test_exact_resource_uses_published_layers_and_keeps_only_safe_provenance(
    tmp_path, renderer
):
    report = artifact.acquire(REF, tmp_path / "verified", tmp_path / "recipe.yaml")
    assert report["artifact_ref"] == REF and report["verified"]
    assert [Path(p).name for p in report["env_files"]] == [
        "containers.env",
        ".env",
        "overrides.env",
        "generated.env",
    ]
    assert all(Path(p).is_file() for p in report["env_files"])
    assert "never-persist" not in json.dumps(report)
    assert len(report["compose_tree_sha256"]) == len(report["download_sha256"]) == 64
    with pytest.raises(ValueError, match="fresh"):
        artifact.acquire(REF, tmp_path / "verified", tmp_path / "recipe.yaml")


@pytest.mark.parametrize(
    "pin",
    [
        "image:latest",
        "image:develop-latest",
        "image:${TAG}",
        "image",
        "registry:5000/path/image",
    ],
)
def test_invalid_image_pin_never_verifies(tmp_path, renderer, pin):
    original = renderer.side_effect

    def execute(command, **kwargs):
        result = original(command, **kwargs)
        if command[0] != "ngc":
            result.stdout = json.dumps(
                {"services": {name: {"image": pin} for name in artifact.SERVICES}}
            )
        return result

    renderer.side_effect = execute
    with pytest.raises(ValueError):
        artifact.acquire(REF, tmp_path / "verified", tmp_path / "recipe.yaml")
    assert not (tmp_path / "verified").exists()


@pytest.mark.parametrize("format", ["tar", "zip"])
@pytest.mark.parametrize("unsafe", ["../escape", "link"])
def test_archive_escape_and_links_are_rejected(tmp_path, format, unsafe):
    path = tmp_path / ("bundle." + format)
    if format == "tar":
        with tarfile.open(path, "w") as bundle:
            member = tarfile.TarInfo(unsafe)
            member.size = 1
            if unsafe == "link":
                member.type, member.linkname, member.size = (
                    tarfile.SYMTYPE,
                    "../escape",
                    0,
                )
            bundle.addfile(member, io.BytesIO(b"x"))
    else:
        with zipfile.ZipFile(path, "w") as bundle:
            member = zipfile.ZipInfo(unsafe)
            if unsafe == "link":
                member.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(member, b"x")
    with pytest.raises((ValueError, tarfile.FilterError)):
        artifact.unpack(path, tmp_path / "unpacked")
    assert not (tmp_path / "escape").exists()


def test_failed_download_does_not_leave_verified_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(
        artifact.subprocess,
        "run",
        Mock(side_effect=subprocess.CalledProcessError(1, ["ngc"])),
    )
    with pytest.raises(subprocess.CalledProcessError):
        artifact.acquire(REF, tmp_path / "verified", tmp_path / "recipe.yaml")
    assert not (tmp_path / "verified").exists()


def test_size_limits_prevent_extraction(tmp_path, monkeypatch):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("file", b"xx")
    monkeypatch.setattr(artifact, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="limits"):
        artifact.unpack(path, tmp_path / "unpacked")

# SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wdm_router_dockerfile_removes_setuptools_from_runtime():
    dockerfile = (REPO_ROOT / "envoy" / "Dockerfile.wdm-router").read_text(encoding="utf-8")

    assert "setuptools==78.1.1" in dockerfile
    assert "setuptools>=78.1.1,<81" in dockerfile
    assert "setuptools==63.2.0" not in dockerfile
    assert "python3 -m pip uninstall -y setuptools wheel pip" in dockerfile
    assert "/root/.cache/uv" in dockerfile


def test_wdm_router_downloads_protobuf_sdist_without_build_isolation():
    dockerfile = (REPO_ROOT / "envoy" / "Dockerfile.wdm-router").read_text(encoding="utf-8")

    assert 'grep -v "^protobuf==" /wdm/requirements.txt' in dockerfile
    assert "pip3 download protobuf==3.20.0" in dockerfile
    assert "--no-build-isolation" in dockerfile


def test_runtime_requirements_do_not_pin_setuptools():
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "setuptools==" not in requirements
    assert '"setuptools==' not in pyproject


def test_wdm_router_creates_usr_local_bin_before_envoy_symlink():
    dockerfile = (REPO_ROOT / "envoy" / "Dockerfile.wdm-router").read_text(encoding="utf-8")

    assert "mkdir -p /usr/local/bin && \\\n    ln -sf /usr/bin/envoy /usr/local/bin/envoy" in dockerfile


def test_runtime_dependency_pins_remediate_non_protobuf_cves():
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    sdr_spec = (REPO_ROOT / "sdr.spec").read_text(encoding="utf-8")
    sdr_mw_spec = (REPO_ROOT / "sdr-mw.spec").read_text(encoding="utf-8")

    for dependency_file in (requirements, pyproject):
        assert "redis==4.4.4" in dependency_file
        assert "werkzeug==3.1.8" in dependency_file
        assert "redis==4.4.2" not in dependency_file
        assert "werkzeug==2.3.8" not in dependency_file
        assert "envoy-reader" not in dependency_file

    assert "envoy_reader" not in sdr_spec
    assert "envoy_reader" not in sdr_mw_spec
    assert "PyJWT" not in pyproject
    assert "protobuf==3.20.0" in requirements
    assert '"protobuf==3.20.0"' in pyproject

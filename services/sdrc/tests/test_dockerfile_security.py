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

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wdm_router_dockerfile_removes_setuptools_from_runtime():
    dockerfile = (REPO_ROOT / "envoy" / "Dockerfile.wdm-router").read_text(encoding="utf-8")

    assert "setuptools==78.1.1" in dockerfile
    assert "setuptools==63.2.0" not in dockerfile
    assert "python3 -m pip uninstall -y uv setuptools wheel pip" in dockerfile
    assert "/root/.cache/uv" in dockerfile
    assert "uv==0.11.26" in dockerfile
    assert "uv==0.11.24" not in dockerfile


def test_wdm_router_does_not_mirror_compliance_artifacts():
    dockerfile = (REPO_ROOT / "envoy" / "Dockerfile.wdm-router").read_text(encoding="utf-8")

    assert "pip3 download" not in dockerfile
    assert "/wdm/wheels" not in dockerfile
    assert "/wdm/ThirdPartySourceCodes" not in dockerfile
    executable_lines = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "COPY ./3rdParty_Licenses.md /wdm/ThirdPartyLicences.txt" not in executable_lines
    assert "COPY ./3rdParty_Licenses.md /wdm/3rdParty_Licenses.md" in executable_lines
    assert "CI license injection anchor: COPY ./3rdParty_Licenses.md /wdm/ThirdPartyLicences.txt" in dockerfile


def test_runtime_requirements_do_not_pin_setuptools():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"setuptools==' not in pyproject


def test_wdm_router_creates_usr_local_bin_before_envoy_symlink():
    dockerfile = (REPO_ROOT / "envoy" / "Dockerfile.wdm-router").read_text(encoding="utf-8")

    assert "mkdir -p /usr/local/bin && \\\n    ln -sf /usr/bin/envoy /usr/local/bin/envoy" in dockerfile


def test_runtime_dependency_pins_remediate_non_protobuf_cves():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    sdr_spec = (REPO_ROOT / "sdr.spec").read_text(encoding="utf-8")
    sdr_mw_spec = (REPO_ROOT / "sdr-mw.spec").read_text(encoding="utf-8")

    assert '"redis==4.4.4"' in pyproject
    assert '"werkzeug==3.1.8"' in pyproject
    assert "redis==4.4.2" not in pyproject
    assert "werkzeug==2.3.8" not in pyproject
    assert "envoy-reader" not in pyproject

    assert "envoy_reader" not in sdr_spec
    assert "envoy_reader" not in sdr_mw_spec
    assert "PyJWT" not in pyproject
    assert '"protobuf==3.20.0"' in pyproject


def test_dockerfile_does_not_copy_requirements_txt():
    dockerfile = (REPO_ROOT / "envoy" / "Dockerfile.wdm-router").read_text(encoding="utf-8")

    assert "requirements.txt" not in dockerfile
    assert not (REPO_ROOT / "requirements.txt").exists()

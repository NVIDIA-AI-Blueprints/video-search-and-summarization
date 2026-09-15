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

"""``deploy_docker-compose.yml`` — the documented single-service quick start.

That Compose file mounts this repository's ``config.yaml`` and
``alert_request_defaults.yaml`` over the image's own copies, which makes the
mounted files and the image two halves of one contract. A pinned release tag
broke it once: the defaults file dropped ``vss_params`` for 3.3 while the image
stayed at 3.2.0, whose loader still required that section, so the file the
quick start mounts was one the image would reject.

The two assertions below are what that failure needed:

* the image tracks the same variables as the profile deployments, so it cannot
  fall behind the files next to it, and
* every mounted file loads under the loader in this tree.

Neither runs Docker, so both run wherever the unit suite does.
"""

import os
import sys

import pytest
import yaml

SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPO_ROOT = os.path.abspath(os.path.join(SERVICE_ROOT, "..", ".."))

QUICKSTART_COMPOSE = os.path.join(SERVICE_ROOT, "deploy_docker-compose.yml")
PROFILE_COMPOSE = os.path.join(REPO_ROOT, "deploy", "docker", "services", "alert", "compose.yml")

sys.path.insert(0, os.path.join(SERVICE_ROOT, "src"))

from schemas.config import AlertsDefaultsConfigLoader  # noqa: E402


def _service(compose_path):
    with open(compose_path) as handle:
        return yaml.safe_load(handle)["services"]["alert-bridge"]


def _mount_defaults(service):
    """Host-side default of each bind mount, keyed by container path.

    Mounts are written ``${OVERRIDE:-./file}:/container/path``; the default is
    what an operator who sets no override gets, and the only side of the mount
    this repository controls.
    """
    mounts = {}
    for volume in service.get("volumes", []):
        host, _, container = volume.rpartition(":")
        if not host.startswith("${") or ":-" not in host:
            continue
        mounts[container] = host.split(":-", 1)[1].rstrip("}")
    return mounts


QUICKSTART = _service(QUICKSTART_COMPOSE)
MOUNTS = _mount_defaults(QUICKSTART)


def test_the_image_matches_the_profile_deployments():
    """The quick start and the profiles run the same build of the service.

    Pinning a literal tag here is what let the image fall behind the config
    schema. Sharing the variables means a release bump moves both at once.
    """
    assert os.path.isfile(PROFILE_COMPOSE), (
        f"no {PROFILE_COMPOSE} — this test needs a full repo checkout, not a "
        f"copy of services/alert alone"
    )
    assert QUICKSTART["image"] == _service(PROFILE_COMPOSE)["image"], (
        "deploy_docker-compose.yml no longer resolves to the same image as "
        "deploy/docker/services/alert/compose.yml; a quick start on a "
        "different build can reject the config schema mounted into it"
    )


def test_both_config_files_are_mounted():
    """Guards the guard: an empty mount set would pass everything below."""
    assert set(MOUNTS) == {"/app/config.yaml", "/app/alert_request_defaults.yaml"}, (
        f"unexpected quick-start mounts: {MOUNTS}. Add the new one to this "
        f"test, or drop the one that went away"
    )


@pytest.mark.parametrize("container_path", sorted(MOUNTS))
def test_the_mounted_file_exists(container_path):
    host_path = os.path.join(SERVICE_ROOT, MOUNTS[container_path])

    assert os.path.isfile(host_path), (
        f"{container_path} defaults to {MOUNTS[container_path]}, which does "
        f"not exist under services/alert — the quick start would bind-mount a "
        f"directory Docker creates for it"
    )


def test_the_mounted_defaults_load(monkeypatch):
    """The mounted file against the loader that reads it.

    This is the direct check on the schema half of the contract: required
    sections present and non-empty, constraints satisfied, schema version
    understood.
    """
    host_path = os.path.join(SERVICE_ROOT, MOUNTS["/app/alert_request_defaults.yaml"])
    monkeypatch.setenv("ALERT_BRIDGE_DEFAULTS_FILE", host_path)

    config = AlertsDefaultsConfigLoader().load_defaults()

    assert config.vlm_params, "vlm_params is required and must not be empty"
    assert config.request_defaults, "request_defaults is required and must not be empty"


def test_the_mounted_service_config_parses():
    host_path = os.path.join(SERVICE_ROOT, MOUNTS["/app/config.yaml"])

    with open(host_path) as handle:
        config = yaml.safe_load(handle)

    assert config.get("event_bridge", {}).get("sourceType") == "kafka", (
        "the quick start mounts a config whose event_bridge.sourceType is not "
        "kafka; Kafka is the only supported source since Redis was removed, "
        "and any other value fails startup"
    )

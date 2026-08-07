# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Fixtures for the Tier-2 full-warehouse e2e test.

Resolves the warehouse-3d-app-mv3dt deployment (compose file + env file), and —
when --e2e-deploy is set — optionally runs setup_e2e_env.sh, brings the stack up
on the sample dataset, and tears it down afterwards. Without --e2e-deploy the
test verifies an already-running deployment (and does not tear it down).
"""

import glob
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
E2E_DIR = Path(__file__).resolve().parent
# First-run TensorRT engine builds (RT-DETR + BodyPose3DNet) can take several minutes.
DEPLOY_TIMEOUT_S = int(os.getenv("MV3DT_E2E_DEPLOY_TIMEOUT_S", "2400"))


def pytest_addoption(parser):
    parser.addoption("--deploy-root", default=os.getenv("MV3DT_DEPLOY_ROOT"),
                     help="vss-warehouse-compose .../deployments dir to run compose from.")
    parser.addoption("--compose-rel", default=os.getenv(
        "MV3DT_COMPOSE_REL", "warehouse/warehouse-3d-app-mv3dt/compose.yml"),
        help="Compose file path relative to --deploy-root.")
    parser.addoption("--env-rel", default=os.getenv("MV3DT_ENV_REL", "warehouse/.env"),
                     help="Env file path relative to --deploy-root.")
    parser.addoption("--e2e-deploy", action="store_true", default=False,
                     help="Bring the stack up/down in-test (else verify a running deployment).")
    parser.addoption("--e2e-run-setup", action="store_true", default=False,
                     help="Run setup_e2e_env.sh before deploy (needs NGC_CLI_API_KEY/HOST_IP/HARDWARE_PROFILE).")
    parser.addoption("--e2e-keep-up", action="store_true", default=False,
                     help="Do not tear the stack down after the test.")


@dataclass
class Deployment:
    deploy_root: Path
    compose_rel: str
    env_rel: str
    env: dict           # parsed env-file values
    num_streams: int
    broker: str         # "kafka" or "redis"


def _discover_deploy_root(opt) -> Path | None:
    if opt:
        return Path(opt)
    # Common location after setup_e2e_env.sh.
    for pat in ("vss-warehouse-compose_v*/deployments", "*/vss-warehouse-compose_v*/deployments"):
        hits = sorted(glob.glob(str(REPO_ROOT / pat)))
        if hits:
            return Path(hits[-1])
    return None


def _parse_env_file(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


@pytest.fixture(scope="session")
def deployment(request) -> Deployment:
    run_setup = request.config.getoption("--e2e-run-setup")
    if run_setup:
        logger.info("Running setup_e2e_env.sh ...")
        subprocess.run(["bash", str(E2E_DIR / "setup_e2e_env.sh")], check=True)

    deploy_root = _discover_deploy_root(request.config.getoption("--deploy-root"))
    if not deploy_root or not deploy_root.exists():
        pytest.skip(
            "warehouse-3d-app-mv3dt deployment not found. Run tests/e2e/setup_e2e_env.sh "
            "or pass --deploy-root / MV3DT_DEPLOY_ROOT."
        )

    compose_rel = request.config.getoption("--compose-rel")
    env_rel = request.config.getoption("--env-rel")
    if not (deploy_root / compose_rel).exists():
        pytest.skip(f"compose file {deploy_root / compose_rel} not found")

    env = _parse_env_file(deploy_root / env_rel)
    num_streams = int(env.get("NUM_STREAMS", "4") or "4")
    broker = "redis" if "redis" in env.get("BP_PROFILE", "kafka").lower() else "kafka"
    return Deployment(deploy_root, compose_rel, env_rel, env, num_streams, broker)


def _compose(deployment: Deployment, *args, check=True, timeout=None):
    cmd = ["docker", "compose", "-f", deployment.compose_rel, "--env-file", deployment.env_rel, *args]
    return subprocess.run(cmd, cwd=str(deployment.deploy_root), check=check,
                          capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="session")
def deployed_stack(request, deployment):
    """Bring the stack up (if --e2e-deploy) and yield the Deployment; tear down after."""
    do_deploy = request.config.getoption("--e2e-deploy")
    keep_up = request.config.getoption("--e2e-keep-up")

    if do_deploy:
        logger.info("Deploying warehouse-3d-app-mv3dt from %s", deployment.deploy_root)
        _compose(deployment, "up", "--detach", "--build", "--force-recreate",
                 timeout=DEPLOY_TIMEOUT_S)
    try:
        yield deployment
    finally:
        if do_deploy and not keep_up:
            logger.info("Tearing down warehouse-3d-app-mv3dt")
            _compose(deployment, "down", "-v", check=False, timeout=600)

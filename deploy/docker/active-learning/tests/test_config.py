# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pre-deploy checks: the Compose configuration is complete and pull-only.

These need Docker Compose but not a running stack, so they are safe to run
before ``docker compose up``.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest
import yaml
from conftest import DEPLOY_DIR, HOST, LOOPBACK, mount_source, run

REGISTRY = "nvcr.io/0691049440816435/mdx"
# Images the team publishes to REGISTRY; every one must be used by the stack.
TEAM_IMAGES = {
    "al-archiver",
    "aoi-client",
    "deft-aoi-active-learning-backend",
    "deft-aoi-inference",
    "deft-service",
    "dfw-model-manager",
    "eval-set-worker",
    "foundational-flywheel-server-deft",
    "kpi-worker",
    "snapshot-worker",
}
SECRETS_TEMPLATE = ".env.secrets.example"
# Where every data-flywheel-* service mounts the shared renderer and template.
DFW_RENDERER = "/opt/data-flywheel/render_config.py"
DFW_TEMPLATE = "/opt/data-flywheel/config/config.deft.yaml"
# Same pattern as deft-service's parse_kpi_target(); a kpi_target it cannot
# match is silently treated as "no target".
DEFT_KPI_TARGET_RE = re.compile(r"far\s*[<≤]=?\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)


def parse_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def required_secret_keys() -> list[str]:
    """Keys listed under the '# Required: ...' sections of .env.secrets.example."""
    keys, required = [], False
    for line in (DEPLOY_DIR / SECRETS_TEMPLATE).read_text().splitlines():
        if line.startswith("# Required"):
            required = True
        elif line.startswith("# Optional"):
            required = False
        elif required and "=" in line and not line.startswith("#"):
            keys.append(line.split("=", 1)[0].strip())
    return keys


def split_image(image: str) -> tuple[str, str]:
    """Return (repository basename, tag) for an image reference."""
    name, _, tag = image.rpartition("/")[2].partition(":")
    return name, tag


def dfw_services(services: dict[str, dict]) -> dict[str, dict]:
    """Services that render the Data Flywheel config at startup."""
    return {
        name: svc
        for name, svc in services.items()
        if any(v["target"] == DFW_RENDERER for v in svc.get("volumes") or [])
    }


def test_secrets_file_has_every_required_value():
    secrets_file = DEPLOY_DIR / ".env.secrets"
    assert secrets_file.is_file(), (
        f"Missing .env.secrets: install -m 600 {SECRETS_TEMPLATE} .env.secrets, then fill it in"
    )
    keys = required_secret_keys()
    assert keys, f"No '# Required' keys found in {SECRETS_TEMPLATE}"
    secrets = parse_env_file(secrets_file)
    missing = [key for key in keys if not secrets.get(key)]
    assert not missing, f"Required values are empty in .env.secrets: {missing}"


def test_compose_config_resolves(services):
    assert services, "docker compose config resolved no services"


def test_deft_runtime_dir_is_an_absolute_host_directory(services):
    path = services["deft-service"]["environment"]["DEFT_RUNTIME_DIR"]
    # deft-service mounts it at the same path, and so do the TAO containers it
    # launches, so it must be an absolute path on the Docker host.
    assert os.path.isabs(path) and "~" not in path and "$" not in path, (
        f"DEFT_RUNTIME_DIR must be an absolute host path (no ~ or $HOME): {path!r}"
    )
    docker_is_local = HOST in (None, *LOOPBACK) and not os.environ.get("DOCKER_HOST", "").startswith(
        ("tcp://", "ssh://")
    )
    if docker_is_local:
        assert Path(path).is_dir(), (
            f"DEFT_RUNTIME_DIR does not exist; create it as your user: mkdir -p {path}"
        )


def test_no_service_builds_from_source(services):
    building = sorted(name for name, svc in services.items() if "build" in svc)
    assert not building, (
        f"Services with a build block would silently build when a pull fails: {building}"
    )


def test_team_images_come_from_mdx_registry(services):
    wrong, used = [], set()
    for name, svc in services.items():
        repo, tag = split_image(svc["image"])
        if repo not in TEAM_IMAGES:
            continue
        used.add(repo)
        if not svc["image"].startswith(f"{REGISTRY}/") or tag in ("", "latest", "local"):
            wrong.append(f"{name}: {svc['image']}")
    assert not wrong, f"Expected pinned {REGISTRY}/<image>:<tag> references:\n" + "\n".join(wrong)
    assert used == TEAM_IMAGES, f"Team images not used by any service: {sorted(TEAM_IMAGES - used)}"


def test_bundle_env_files_agree_on_shared_variables():
    """Each bundle interpolates with its own env file, so a variable set in two
    of them can drift apart without any error. .env.secrets overrides all of them."""
    seen: dict[str, dict[str, str]] = {}
    for env_file in sorted(DEPLOY_DIR.glob("includes/*/.env.*")):
        for key, value in parse_env_file(env_file).items():
            seen.setdefault(key, {})[str(env_file.relative_to(DEPLOY_DIR))] = value
    conflicts = {key: files for key, files in seen.items() if len(set(files.values())) > 1}
    assert not conflicts, f"Bundle env files disagree: {conflicts}"


def test_data_flywheel_services_share_one_config(services):
    users = dfw_services(services)
    assert users, f"No service mounts {DFW_RENDERER}"
    for target in (DFW_RENDERER, DFW_TEMPLATE):
        sources = {name: mount_source(svc, target) for name, svc in users.items()}
        assert len(set(sources.values())) == 1, f"{target} comes from different files: {sources}"


def test_data_flywheel_config_renders_for_every_service(services, tmp_path):
    """render_config.py fails fast on a missing or empty variable, which would
    crash-loop the container; render it here with each service's environment."""
    failures = []
    for name, svc in sorted(dfw_services(services).items()):
        spec = importlib.util.spec_from_file_location("render_config", mount_source(svc, DFW_RENDERER))
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        environ = {k: v for k, v in (svc.get("environment") or {}).items() if v is not None}
        try:
            renderer.render_template(
                Path(mount_source(svc, DFW_TEMPLATE)), tmp_path / f"{name}.yaml", environ=environ
            )
        except (TypeError, ValueError) as exc:
            failures.append(f"{name}: {exc}")
    assert not failures, "Data Flywheel config does not render:\n" + "\n".join(failures)


def test_far_targets_agree(services):
    """The offline gate, the online gate and DEFT's training target are the
    same FAR-at-recall=100% target and must stay in step."""
    template = mount_source(next(iter(dfw_services(services).values())), DFW_TEMPLATE)
    job = yaml.safe_load(Path(template).read_text())["aoi_job_config"]
    deft_target = job["deft"]["config"]["pipeline"]["kpi_target"]
    match = DEFT_KPI_TARGET_RE.search(deft_target or "")
    assert match, (
        f"deft-service cannot parse kpi_target {deft_target!r} and would run without a "
        "target; write it as 'FAR < 10% at recall=100%'"
    )
    far = float(match.group(1))
    gates = {
        "eval.kpi_target": job["eval"]["kpi_target"],
        "promotion_policy.eval_harness.online_target": job["promotion_policy"]["eval_harness"]["online_target"],
    }
    wrong = {
        name: rules
        for name, rules in gates.items()
        if [(r["op"], float(r["threshold"])) for r in rules if r["metric"] == "FAR_pct"] != [("<", far)]
    }
    assert not wrong, f"FAR_pct gates disagree with deft kpi_target {deft_target!r}: {wrong}"


@pytest.mark.registry
def test_team_images_exist_in_registry(services):
    images = sorted({s["image"] for s in services.values() if s["image"].startswith(REGISTRY)})
    missing = []
    for image in images:
        result = run(["docker", "manifest", "inspect", image], timeout=60)
        if result.returncode != 0:
            missing.append(f"{image}: {result.stderr.strip()}")
    assert not missing, "Not pullable (check `docker login nvcr.io`):\n" + "\n".join(missing)

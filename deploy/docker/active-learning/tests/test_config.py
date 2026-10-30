# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pre-deploy checks: the Compose configuration is complete and pull-only.

These need Docker Compose but not a running stack, so they are safe to run
before ``docker compose up``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import DEPLOY_DIR, run

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


def parse_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def required_secret_keys() -> list[str]:
    """Keys listed under '# Required' in env.secrets.example."""
    keys, required = [], False
    for line in (DEPLOY_DIR / "env.secrets.example").read_text().splitlines():
        if line.strip() == "# Required":
            required = True
        elif line.strip() == "# Optional":
            required = False
        elif required and "=" in line and not line.startswith("#"):
            keys.append(line.split("=", 1)[0].strip())
    return keys


def split_image(image: str) -> tuple[str, str]:
    """Return (repository basename, tag) for an image reference."""
    name, _, tag = image.rpartition("/")[2].partition(":")
    return name, tag


def test_secrets_file_has_every_required_value():
    secrets_file = DEPLOY_DIR / ".env.secrets"
    assert secrets_file.is_file(), (
        "Missing .env.secrets: install -m 600 env.secrets.example .env.secrets, then fill it in"
    )
    secrets = parse_env_file(secrets_file)
    missing = [key for key in required_secret_keys() if not secrets.get(key)]
    assert not missing, f"Required values are empty in .env.secrets: {missing}"


def test_compose_config_resolves(services):
    assert services, "docker compose config resolved no services"


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


def test_image_overrides_agree_across_env_files():
    """The top-level .env silently overrides the per-stack env files, so they must match."""
    seen: dict[str, dict[str, str]] = {}
    for env_file in [DEPLOY_DIR / ".env", *sorted(DEPLOY_DIR.glob("includes/*/.env.*"))]:
        for key, value in parse_env_file(env_file).items():
            if key.endswith("_IMAGE"):
                seen.setdefault(key, {})[str(env_file.relative_to(DEPLOY_DIR))] = value
    conflicts = {key: files for key, files in seen.items() if len(set(files.values())) > 1}
    assert not conflicts, f"Image overrides disagree between env files: {conflicts}"


@pytest.mark.registry
def test_team_images_exist_in_registry(services):
    images = sorted({s["image"] for s in services.values() if s["image"].startswith(REGISTRY)})
    missing = []
    for image in images:
        result = run(["docker", "manifest", "inspect", image], timeout=60)
        if result.returncode != 0:
            missing.append(f"{image}: {result.stderr.strip()}")
    assert not missing, "Not pullable (check `docker login nvcr.io`):\n" + "\n".join(missing)

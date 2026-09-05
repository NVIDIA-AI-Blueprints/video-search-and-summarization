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

"""Shared contracts, timeouts and helpers for the flow backends."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

# Timeouts (seconds). Mirror the upstream defaults documented in
# vss_agents/api/video_ingest.py so an eval failure reflects real deployment
# behaviour rather than a client-side cutoff.
UPLOAD_URL_TIMEOUT = 30
UPLOAD_TIMEOUT = 900
COMPLETE_TIMEOUT = 900
SEARCH_TIMEOUT = 300
VST_LIST_TIMEOUT = 15

# The upstream skill documents this exact constant for uploads whose real
# capture time is unknown; the eval's ground truth is expressed relative to it.
DEFAULT_UPLOAD_TIMESTAMP = "2025-01-01T00:00:00"

CONTENT_TYPES = {".mp4": "video/mp4", ".mkv": "video/x-matroska"}


class QueryBackend(Protocol):
    """Runs one query and returns (raw results, latency in seconds)."""

    name: str

    def search(self, query: str) -> tuple[list[dict[str, Any]], float]: ...

    def describe(self) -> dict[str, Any]: ...


class IngestBackend(Protocol):
    """Uploads one video and returns a per-file record."""

    name: str

    def upload(self, video_path: Path) -> dict[str, Any]: ...

    def describe(self) -> dict[str, Any]: ...


def base_record(video_path: Path) -> dict[str, Any]:
    """Per-file record skeleton, matching what ``_aggregate_upload_stats`` reads."""
    try:
        file_size_mb: float | None = video_path.stat().st_size / (1024 * 1024)
    except OSError:
        file_size_mb = None
    return {
        "video_name": video_path.stem,
        "video_path": str(video_path),
        "file_size_mb": round(file_size_mb, 2) if file_size_mb is not None else None,
        "timestamp": datetime.now().isoformat(),
    }


def finish_record(record: dict[str, Any], latency_s: float) -> dict[str, Any]:
    """Stamp latency and derived throughput onto a per-file record."""
    record["upload_latency_s"] = round(latency_s, 3)
    size_mb = record.get("file_size_mb")
    if size_mb and latency_s > 0:
        record["upload_speed_mbps"] = round((size_mb * 8) / latency_s, 2)
    return record


def elapsed_since(start: float) -> float:
    """Seconds since ``start`` (a ``time.time()`` reading)."""
    return time.time() - start


# =============================================================================
# CLI discovery
# =============================================================================

#: The VSS checkout holding the ``vss`` CLI. Found by walking up to the directory
#: containing ``services/agent`` rather than by counting path components: this
#: skill sits five levels down (skills/benchmarking/<skill>/scripts/flows), and a
#: fixed ``parents[N]`` silently resolved to ``skills/`` when the eval moved here
#: from ci-vss-oss. Walking up survives the next move too.
REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "services/agent/pyproject.toml").exists()),
    Path(__file__).resolve().parents[4],
)

#: Kept as an alias: in ci-vss-oss the product was a submodule beside the eval,
#: so the two differed. Here the eval ships inside the product and they are the
#: same directory. Callers that ask for either get the checkout with the CLI.
SUBMODULE_ROOT = REPO_ROOT

#: Port serving the unified path-prefix origin (/vst, /elasticsearch,
#: /rtvi-embed, ...) that ``vss configure`` probes. The agent's own port does
#: NOT route those prefixes -- observed on both 10.86.12.161 and 10.87.88.126,
#: where the agent answers on 8000 and the unified origin on 7777.
DEFAULT_VSS_ORIGIN_PORT = 7777


def has_cli_package(repo_root: Path | str) -> bool:
    """True when a checkout actually ships the ``vss`` CLI package."""
    return (Path(repo_root) / "services/agent/packages/vss_cli").is_dir()


def default_vss_cmd(repo_root: str) -> list[str]:
    """The project-local CLI invocation the upstream skill mandates.

    ``--extra cli`` is required: the base distribution ships the libraries,
    while the ``nvidia-vss-cli`` extra ships the ``vss`` executable.
    """
    project = f"{repo_root.rstrip('/')}/services/agent"
    return ["uv", "run", "--project", project, "--no-dev", "--extra", "cli", "vss"]


def resolve_vss_cmd(
    explicit_cmd: str | None = None,
    repo_root: str | None = None,
) -> tuple[list[str], str]:
    """Work out how to invoke ``vss``. Returns ``(argv_prefix, how)``.

    Order of preference, most explicit first:

    1. ``--vss-cmd``          -- caller knows exactly what they want
    2. ``--vss-repo-root``    -- a checkout the caller named
    3. the vendored submodule -- the CI case, when its pin ships the CLI
    4. ``vss`` on PATH        -- a standalone ``pip install`` of nvidia-vss-cli

    Raises ``FileNotFoundError`` listing every candidate tried, because "no
    CLI found" and "the CLI is broken" need different fixes and a generic
    message sends people down the wrong one.
    """
    if explicit_cmd:
        return shlex.split(explicit_cmd), "--vss-cmd"

    if repo_root:
        if not has_cli_package(repo_root):
            raise FileNotFoundError(
                f"--vss-repo-root {repo_root} has no services/agent/packages/vss_cli.\n"
                "  That checkout predates the core/agents/cli split, so it cannot "
                "provide the vss CLI."
            )
        return default_vss_cmd(repo_root), f"--vss-repo-root {repo_root}"

    if has_cli_package(SUBMODULE_ROOT):
        return default_vss_cmd(str(SUBMODULE_ROOT)), f"repo checkout {SUBMODULE_ROOT}"

    on_path = shutil.which("vss")
    if on_path:
        return [on_path], f"PATH ({on_path})"

    raise FileNotFoundError(
        "Could not find a vss CLI. Tried, in order:\n"
        f"  1. --vss-cmd                 (not given)\n"
        f"  2. --vss-repo-root           (not given)\n"
        f"  3. submodule                 {SUBMODULE_ROOT}\n"
        f"     -> {'no services/agent/packages/vss_cli (pin predates the CLI split)'}\n"
        f"  4. vss on PATH               (not found)\n\n"
        "Fix by either:\n"
        "  * passing --vss-repo-root <checkout with services/agent/packages/vss_cli>, or\n"
        "  * installing the CLI standalone (needs Python 3.13/3.14):\n"
        "      pip install <checkout>/services/agent/packages/vss_core \\\n"
        "                  <checkout>/services/agent/packages/vss_cli"
    )


def preflight_vss_cmd(vss_cmd: list[str], timeout: int = 300) -> str:
    """Prove the CLI runs before the eval commits to hundreds of queries.

    A stale virtualenv exits 1 with ModuleNotFoundError on every invocation.
    Discovering that on query 1 of 121 wastes a run; discovering it here costs
    one subprocess.
    """
    try:
        proc = subprocess.run(
            [*vss_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"vss command is not executable: {shlex.join(vss_cmd)}\n  {e}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"vss --version timed out after {timeout}s: {shlex.join(vss_cmd)}") from e

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise RuntimeError(
            f"vss --version exited {proc.returncode}: {detail}\n"
            f"  command: {shlex.join(vss_cmd)}\n"
            "  A stale virtualenv is the usual cause -- reinstall it, or point "
            "--vss-repo-root at a current checkout."
        )
    return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else "unknown"


def vss_origin_for(endpoint: str, port: int = DEFAULT_VSS_ORIGIN_PORT) -> str:
    """The origin ``vss configure`` should probe, derived from the agent endpoint.

    The CLI discovers services by path prefix on ONE origin, and the agent's
    port does not route them -- pointing ``vss configure`` at the agent finds
    1/7 services and every search then exits 4.
    """
    parsed = urlparse(endpoint)
    return f"{parsed.scheme or 'http'}://{parsed.hostname}:{port}"


def configured_base_url() -> str | None:
    """The origin currently recorded in ``~/.vss/config.json``, if any."""
    config = Path.home() / ".vss" / "config.json"
    if not config.is_file():
        return None
    try:
        return json.loads(config.read_text()).get("base_url")
    except (json.JSONDecodeError, OSError):
        return None


def ensure_vss_configured(vss_cmd: list[str], base_url: str, timeout: int = 300) -> dict[str, Any]:
    """Point the CLI at ``base_url`` unless it already is.

    ``vss configure`` writes ~/.vss/config.json, which lives outside this repo,
    so this reports what it is doing rather than changing user state silently.
    """
    current = configured_base_url()
    if current and current.rstrip("/") == base_url.rstrip("/"):
        return {"ran": False, "base_url": current, "reason": "already configured"}

    if current:
        print(f"  vss is configured for {current}; re-pointing at {base_url}")
    else:
        print(f"  vss is not configured; pointing it at {base_url}")

    proc = subprocess.run(
        [*vss_cmd, "configure", "--base-url", base_url],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    for line in output.splitlines():
        if line.strip():
            print(f"    {line}")
    if proc.returncode != 0:
        raise RuntimeError(f"vss configure exited {proc.returncode} for {base_url}")
    return {"ran": True, "base_url": base_url, "previous": current}

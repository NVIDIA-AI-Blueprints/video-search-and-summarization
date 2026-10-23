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

"""Finding the ``vss`` CLI, calling ``search run``, and reading what comes back.

This is the interface under test. The agent's ``POST /api/v1/search`` no longer
exists -- it is absent from ``services/agent`` entirely -- so the CLI is what a
caller has.

What that costs the measurement
-------------------------------
Every query is a process. ``uv run --project libs/vss vss`` starts a Python
interpreter, imports the CLI and the search library, and exits; startup alone
was measured at about 1.3 s against an 8.9 s query. At concurrency 20 the load
generator is starting twenty interpreters at a time on one host.

So a throughput number from this harness is bounded by two things, and only one
of them is the cluster. :func:`measure_spawn_cost` times a bare ``vss
--version`` so the report can state the floor rather than leave a reader to
assume the service was the limit. Treat a run whose per-query latency
approaches the spawn cost as measuring this machine.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .routing import cli_subcommand

logger = logging.getLogger(__name__)

#: What the CLI's exit codes mean. Used to turn a number into a sentence; the
#: policy below does not depend on the mapping being complete.
EXIT_MEANINGS = {
    0: "success",
    2: "invalid input (unknown flag or bad value)",
    3: "backend unreachable",
    4: "configuration -- not configured, foreign config, or a required service absent",
    5: "not found -- a searched index does not exist (nothing ingested yet)",
}


class CliNotFoundError(RuntimeError):
    """No usable ``vss`` executable, or the one found does not run."""


class EnvironmentFault(RuntimeError):
    """The CLI reported a state no retry fixes. Aborts the whole sweep."""


def explain_exit(code: int) -> str:
    return EXIT_MEANINGS.get(code, f"exit {code}")


def is_environment_fault(code: int) -> bool:
    """Whether this exit code means the run cannot continue.

    These are states no amount of retrying fixes: a flag the deployment's CLI
    does not know, a backend that is down, a CLI pointed at the wrong origin,
    an index that was never created. A sweep that treated them as per-request
    failures would spend hours producing a wall of zeros and report them as
    measurements.

    Deliberately narrower than "any non-zero exit". A query that simply finds
    nothing is a legitimate result at this layer and must stay countable.
    """
    return code in {2, 3, 4, 5}


#: Exit codes a cell can also produce by overloading a healthy backend: the CLI
#: reports a client timeout under load as "backend unreachable".
OVERLOAD_EXIT_CODES = frozenset({3})


def is_overload(codes: set[int], backend_was_up: bool) -> bool:
    """Whether a cell's faults read as overload rather than a dead backend.

    Only when every fault is a backend-unreachable exit and something showed
    the backend answering this cell -- a passed health gate or a request that
    succeeded. A backend that really died is then caught by the next cell's
    health gate, which aborts the sweep.
    """
    return bool(codes) and codes <= OVERLOAD_EXIT_CODES and backend_was_up


def clean_cli_stderr(text: str) -> str:
    """Drop ``uv`` environment warnings that precede the CLI's own message."""
    return "\n".join(
        line for line in text.splitlines()
        if not (line.startswith("warning: `VIRTUAL_ENV=") or "use `--active`" in line)
    ).strip()


# ---------------------------------------------------------------------------
# Finding and checking the CLI
# ---------------------------------------------------------------------------


def has_cli_package(repo_root: Path | str) -> bool:
    """True when a checkout actually ships the ``vss`` CLI package."""
    return (Path(repo_root) / "libs/vss/cli").is_dir()


def default_vss_cmd(repo_root: Path | str) -> list[str]:
    """The project-local CLI invocation. ``libs/vss`` exposes ``vss`` directly."""
    return ["uv", "run", "--project", f"{str(repo_root).rstrip('/')}/libs/vss", "vss"]


def resolve_vss_cmd(
    explicit_cmd: str | None = None,
    repo_root: Path | str | None = None,
) -> tuple[list[str], str]:
    """Work out how to invoke ``vss``. Returns ``(argv_prefix, how)``.

    Most explicit first: an exact command, then a named checkout, then ``vss``
    on PATH. The error lists everything tried, because "no CLI found" and "the
    CLI is broken" need different fixes.
    """
    if explicit_cmd:
        return shlex.split(explicit_cmd), "cli.vss_cmd"

    if repo_root:
        if not has_cli_package(repo_root):
            raise CliNotFoundError(
                f"cli.vss_repo_root {repo_root} has no libs/vss/cli. Point it at "
                "the root of a current VSS checkout."
            )
        return default_vss_cmd(repo_root), f"cli.vss_repo_root ({repo_root})"

    on_path = shutil.which("vss")
    if on_path:
        return [on_path], f"PATH ({on_path})"

    raise CliNotFoundError(
        "Could not find a vss CLI. Tried, in order:\n"
        "  1. cli.vss_cmd        (not set)\n"
        "  2. cli.vss_repo_root  (not set, or no libs/vss/cli under it)\n"
        "  3. vss on PATH        (not found)\n\n"
        "Set cli.vss_repo_root to a checkout containing libs/vss/cli, or "
        "install the CLI standalone."
    )


def preflight(vss_cmd: list[str], timeout_sec: int = 300) -> str:
    """Prove the CLI runs before the sweep commits to thousands of queries.

    A stale virtualenv exits non-zero with ModuleNotFoundError on every
    invocation. Finding that out on request 1 of 20,000 wastes a sweep; finding
    it out here costs one subprocess.
    """
    try:
        proc = subprocess.run(
            [*vss_cmd, "--version"],
            capture_output=True, check=False, text=True, timeout=timeout_sec,
        )
    except FileNotFoundError as e:
        raise CliNotFoundError(
            f"vss is not executable: {shlex.join(vss_cmd)}\n  {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise CliNotFoundError(
            f"vss --version timed out after {timeout_sec}s: {shlex.join(vss_cmd)}"
        ) from e

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise CliNotFoundError(
            f"vss --version exited {proc.returncode}: {detail}\n"
            f"  command: {shlex.join(vss_cmd)}\n"
            "  A stale virtualenv is the usual cause."
        )
    output = (proc.stdout or proc.stderr or "").strip()
    return output.splitlines()[0] if output else "unknown"


def measure_spawn_cost(
    vss_cmd: list[str],
    samples: int = 3,
    timeout_sec: int = 300,
) -> float:
    """Median seconds for the CLI to start and exit doing nothing.

    This is the floor under every latency this harness reports, and at high
    concurrency it is also what the host is actually busy doing. Recorded so
    the report can say so instead of attributing it to the cluster.
    """
    timings: list[float] = []
    for _ in range(max(1, samples)):
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                [*vss_cmd, "--version"],
                capture_output=True, check=False, text=True, timeout=timeout_sec,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            timings.append(time.perf_counter() - started)
    if not timings:
        return 0.0
    timings.sort()
    return timings[len(timings) // 2]


def supports_flag(
    vss_cmd: list[str],
    flag: str,
    path: str = "event",
    timeout_sec: int = 120,
) -> bool:
    """Whether ``vss search run <path>`` accepts ``flag``. ``path`` is a benchmark path.

    The deployment's CLI comes from its image, not from this checkout, so
    "newer than the flag" is a normal state. Click rejects an unknown option
    with exit 2, which :func:`is_environment_fault` correctly treats as fatal
    -- meaning one unsupported optional flag would abort the sweep on request
    one. A single ``--help`` turns that into a downgrade.

    Unparseable output reports *absent*: omitting an optional flag measures
    slightly the wrong thing, sending an unsupported one measures nothing.
    """
    try:
        proc = subprocess.run(
            [*vss_cmd, "search", "run", cli_subcommand(path), "--help"],
            capture_output=True, check=False, text=True, timeout=timeout_sec,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    return flag in (proc.stdout or "") or flag in (proc.stderr or "")


def require_origin(cli_config: dict[str, Any]) -> str:
    """The origin ``vss configure`` probes. Configured, never inferred.

    A sibling skill derives this as port 7777 on the agent's host, because
    under Docker Compose that is where the HAProxy unified origin listens.
    Helm puts the same path prefixes behind an Ingress at
    ``global.externalHost``, which shares neither the host nor the port with
    the agent's Service -- so there is nothing here to derive from, and a
    derived value would be an address nothing serves. Every search would exit
    4, hours after ingest.
    """
    origin = str(cli_config.get("origin_url") or "").strip()
    if not origin:
        raise CliNotFoundError(
            "cli.origin_url is not set, and it cannot be inferred: this skill "
            "targets a Helm deployment, where the unified origin is the "
            "profile's Ingress rather than a port on any one Service.\n"
            "  Set it to the ingress host, e.g.\n"
            '    origin_url: "http://vss-search.<NODE_IP>.nip.io"\n'
            "  Find it with: kubectl get ingress -n <namespace>\n"
            "  Check it serves the prefixes the CLI needs:\n"
            "    curl -sf <origin>/elasticsearch/_cat/indices && "
            "curl -sf <origin>/vst/api/v1/sensor/version"
        )
    return origin.rstrip("/")


#: The CLI reads its config from here instead of ``~/.vss`` when set.
CONFIG_HOME_ENV = "VSS_CONFIG_HOME"


def user_config_home() -> Path:
    """Where the CLI reads its config outside this benchmark, as the CLI resolves it."""
    override = os.environ.get(CONFIG_HOME_ENV, "").strip()
    return Path(override) if override else Path.home() / ".vss"


def _read_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise CliNotFoundError(f"cannot read {path}: {e}") from e
    if not isinstance(payload.get("services"), dict) or not payload["services"]:
        raise CliNotFoundError(
            f"{path} records no services. Re-run `vss configure --base-url <origin>`."
        )
    return payload


def _write_config(home: Path, payload: dict[str, Any]) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    target = home / "config.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def prepare_sweep_config(
    vss_cmd: list[str],
    base_url: str,
    sweep_home: Path,
    overrides: dict[str, str],
    configure: bool = True,
    timeout_sec: int = 300,
) -> dict[str, Any]:
    """Write the CLI config every request of this sweep runs with.

    ``vss configure`` discovers the services behind ``base_url`` into
    ``sweep_home`` (``VSS_CONFIG_HOME``) rather than into ``~/.vss``, so the
    machine's own CLI config is never changed. ``configure=False`` copies the
    user's config instead, as it stands.

    ``overrides`` then repoints named services. Discovery goes through the
    profile's HAProxy Ingress, and a benchmark looking for a saturation point
    cannot have a shared proxy inside every measured request. The CLI reads each
    URL verbatim, so swapping in ClusterIPs leaves the rest of discovery --
    models, indices -- as it was. A name the deployment does not expose is
    refused: ignoring a typo would leave that leg on the ingress unnoticed.
    """
    target = sweep_home / "config.json"
    if configure:
        sweep_home.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                [*vss_cmd, "configure", "--base-url", base_url],
                capture_output=True, check=False, text=True, timeout=timeout_sec,
                env={**os.environ, CONFIG_HOME_ENV: str(sweep_home)},
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise CliNotFoundError(f"vss configure could not run for {base_url}: {e}") from e
        if proc.returncode != 0:
            detail = ((proc.stderr or "") + (proc.stdout or "")).strip()[:400]
            raise CliNotFoundError(
                f"vss configure exited {proc.returncode} for {base_url}: {detail}"
            )
        payload = _read_config(target)
    else:
        payload = _read_config(user_config_home() / "config.json")

    services = payload["services"]
    unknown = sorted(n for n in overrides if not isinstance(services.get(n), dict))
    if unknown:
        raise CliNotFoundError(
            f"cli.service_urls names {', '.join(unknown)}, which the deployment "
            f"does not expose. It has: {', '.join(sorted(services))}"
        )
    applied = {name: str(url).rstrip("/") for name, url in overrides.items()}
    for name, url in applied.items():
        services[name]["url"] = url
    _write_config(sweep_home, payload)
    return {
        "configured": configure,
        "applied": applied,
        "config_path": str(target),
        "base_url": payload.get("base_url", ""),
        "service_urls": {
            name: str(info.get("url", ""))
            for name, info in services.items() if isinstance(info, dict)
        },
        "has_rt_vlm": bool((services.get("rt_vlm") or {}).get("url")),
    }


def write_critic_off_config(sweep_home: Path, dest_dir: Path) -> Path:
    """Copy the sweep's CLI config into ``dest_dir`` without the ``rt_vlm`` route.

    The CLI skips the critic when no RT-VLM route is configured, and has no
    other way to be told to. Run with ``VSS_CONFIG_HOME=dest_dir`` it searches
    the same services by the same routes, minus verification.
    """
    payload = _read_config(sweep_home / "config.json")
    payload["services"].pop("rt_vlm", None)
    return _write_config(dest_dir, payload)


def probe_service_urls(urls: dict[str, str], timeout_sec: float = 5.0) -> dict[str, str]:
    """Which service URLs do not accept a TCP connection, with the reason.

    ClusterIPs change on every clean redeploy, and a stale one otherwise
    surfaces hours later as a timeout, or not at all if the address was reused.
    A connect is all that is checked: no service shares a health path.
    """
    import socket
    from urllib.parse import urlsplit

    failures: dict[str, str] = {}
    for name, url in urls.items():
        parts = urlsplit(url)
        if not parts.hostname:
            failures[name] = f"not a URL: {url!r}"
            continue
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            with socket.create_connection((parts.hostname, port), timeout=timeout_sec):
                pass
        except OSError as e:
            failures[name] = f"{parts.hostname}:{port} -- {e}"
    return failures


# ---------------------------------------------------------------------------
# Building the command and reading the answer
# ---------------------------------------------------------------------------


def build_argv(
    vss_cmd: list[str],
    plan: dict[str, Any],
    top_k: int,
    critic_eval_count: int | None = None,
    min_cosine_similarity: float | None = None,
    merge_adjacent: bool = False,
    original_query: str = "",
) -> list[str]:
    """The exact command for one search.

    Each sub-action accepts only its own fields -- ``run embed`` has no
    ``--attribute`` and ``run object`` has no ``--query`` -- so the arguments
    are selected by path rather than passed unconditionally. Sending a flag the
    path does not define exits 2, which aborts the sweep.
    """
    path = cli_subcommand(str(plan["path"]))
    argv = [*vss_cmd, "search", "run", path]

    if path in ("embed", "fusion", "tag"):
        argv += ["--query", str(plan.get("query") or "")]
    if path in ("attribute", "fusion"):
        for attribute in plan.get("attributes") or []:
            argv += ["--attribute", str(attribute)]
    if path == "object":
        for object_id in plan.get("object_ids") or []:
            argv += ["--object-id", str(object_id)]

    # Retrieval never reads this; the critic does. Without it the critic judges
    # hits against the decomposed query, which is a good retrieval request and
    # a poor question to verify against.
    if original_query:
        argv += ["--original-query", original_query]

    argv += ["--source-type", str(plan.get("source_type") or "video_file")]
    # The sweep's value, never the plan's. ``top_k`` is a sweep axis, and a
    # decomposition that happened to emit one would pin a cell to a value it
    # was not asked to measure -- silently, since the run_id would still say 20.
    argv += ["--top-k", str(top_k)]

    if critic_eval_count is not None:
        argv += ["--critic-eval-count", str(critic_eval_count)]
    for source in plan.get("video_sources") or []:
        argv += ["--video-source", str(source)]
    if plan.get("timestamp_start"):
        argv += ["--timestamp-start", str(plan["timestamp_start"])]
    if plan.get("timestamp_end"):
        argv += ["--timestamp-end", str(plan["timestamp_end"])]
    if min_cosine_similarity is not None and path in ("embed", "fusion"):
        argv += ["--min-cosine-similarity", str(min_cosine_similarity)]
    # Merging contiguous same-sensor windows is on by default. Off matches what
    # the agent's search returned, and keeps the hit count comparable with the
    # top_k that was asked for.
    if not merge_adjacent:
        argv.append("--no-merge-adjacent")

    argv.append("--raw")
    return argv


def parse_cli_output(
    stdout: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Read ``(hits, search_messages, timings)`` out of ``--raw`` output.

    ``timings`` is ``SearchOutput.timings``: per-stage wall clock measured
    inside the CLI process, ``{}`` from a CLI too old to report it.

    The output is **NDJSON**: one JSON document per line, with lifecycle events
    interleaved before the result. Parsing the buffer as a single document
    raises "Extra data" -- which, swallowed, silently discards most of a run.
    It did exactly that once: 106 of 121 results dropped, reported as mAP
    0.0000.

    So: try the whole buffer first, because a single-document response is still
    valid, then fall back to line by line and take the first document carrying
    ``data``. Lifecycle events -- an ``event`` key with no ``data`` -- are
    skipped rather than treated as empty results.
    """
    text = (stdout or "").strip()
    if not text:
        raise ValueError("the CLI produced no output")

    candidates: list[Any] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidates.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not candidates:
        raise ValueError(f"the CLI produced no JSON: {text[:200]!r}")

    for payload in candidates:
        # Lifecycle events carry ``event`` and no ``data``.
        if not isinstance(payload, dict) or "data" not in payload:
            continue
        hits = payload.get("data") or []
        messages = [str(m) for m in (payload.get("search_messages") or [])]
        timings = payload.get("timings")
        return (
            hits if isinstance(hits, list) else [],
            messages,
            timings if isinstance(timings, dict) else {},
        )

    raise ValueError(
        f"no CLI output document carried a 'data' field: {text[:200]!r}"
    )

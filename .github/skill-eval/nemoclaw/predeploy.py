#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deploy a VSS profile through the orchestrator MCP, before the agent turn.

Runs **on the Brev box**, after `deploy_vss_orchestrator.ipynb` has started the
MCP server, and follows the documented tool sequence from the blueprint docs
(`vss-orchestrator-mcp.html`):

    profiles -> prereqs -> docker_generate -> docker_up -> poll docker_status

This is the same path the NemoClaw agent takes when a human asks it to "deploy
the VSS alerts profile in verification mode" -- the harness just makes the calls
itself so the model under test is not scored on, or blocked by, deployment.

Deliberately NOT `dev-profile.sh`: that script is a third deploy path nothing in
the NemoClaw flow uses, so driving it here would introduce drift against what
the agent would have produced. The MCP is the documented path.

Usage (on the box, from the repo root):

    python3 .github/skill-eval/nemoclaw/predeploy.py \\
        --profile alerts --profile-mode verification \\
        --env-override LLM_MODE=remote --env-override VLM_MODE=remote
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# The four profiles `vss_orchestrator__docker_generate` accepts. Matches the
# union of profiles the eval corpus actually needs, so an unknown value here is
# an adapter bug rather than a missing MCP capability.
SUPPORTED_PROFILES = ("base", "lvs", "search", "alerts")

# `profile_mode` is meaningful only for `alerts` (docs: "required for alerts
# only"). `remote-all` is NOT a profile_mode -- it is LLM/VLM placement and must
# be expressed as env overrides; see `_split_deploy_mode`.
PROFILE_MODES = ("verification", "real-time")

# Placement modes that map to env overrides instead of `profile_mode`.
_PLACEMENT_OVERRIDES = {
    "remote-all": {"LLM_MODE": "remote", "VLM_MODE": "remote"},
    "remote-llm": {"LLM_MODE": "remote"},
    "remote-vlm": {"VLM_MODE": "remote"},
}

# `vss_orchestrator__docker_status` validates `tail_lines <= 20` server-side and
# rejects anything larger, which failed run 33588684082 on both legs. Note
# `orchestrator_mcp_helper.poll_compose_op` defaults to 200 and would hit the
# same wall -- another reason this module polls itself rather than calling it.
MAX_TAIL_LINES = 20


def _repo_dir() -> Path:
    return Path(
        os.environ.get("VSS_REPO_DIR")
        or Path.home() / "video-search-and-summarization"
    ).resolve()


def _load_helper(repo: Path) -> Any:
    """Import the checked-in MCP client helper by path.

    Mirrors how `deploy_vss_orchestrator.ipynb` loads it -- the file lives under
    `deploy/docker/scripts/`, which is not an importable package.
    """
    path = repo / "deploy" / "docker" / "scripts" / "orchestrator_mcp_helper.py"
    if not path.is_file():
        raise RuntimeError(f"orchestrator MCP helper not found at {path}")
    spec = importlib.util.spec_from_file_location("orchestrator_mcp_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mcp_url() -> str:
    """Same construction the orchestrator notebook uses."""
    scheme = (
        "https"
        if (os.environ.get("ORCHESTRATOR_ENABLE_HTTPS", "false").lower() == "true")
        else "http"
    )
    port = os.environ.get("VSS_ORCHESTRATOR_MCP_PORT", "9988")
    if not port.isdigit() or not 1024 <= int(port) <= 65535:
        raise ValueError(f"invalid VSS_ORCHESTRATOR_MCP_PORT: {port!r}")
    host = os.environ.get("VSS_ORCHESTRATOR_MCP_HOST", "")
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"{scheme}://{host}:{port}/mcp"


def _split_deploy_mode(
    profile: str, deploy_mode: str | None
) -> tuple[str | None, dict[str, str]]:
    """Map a spec's `deploy_mode` onto (profile_mode, env_overrides).

    `deploy_mode` is overloaded in the eval specs: `verification` / `real-time`
    are alerts pipeline modes and belong in `profile_mode`, while `remote-all`
    and friends describe LLM/VLM placement and have no `profile_mode`
    representation at all -- they are env overrides. Passing `remote-all` as
    `profile_mode` makes `docker_generate` reject the call.
    """
    mode = (deploy_mode or "").strip()
    if not mode:
        return None, {}
    if mode in PROFILE_MODES:
        if profile != "alerts":
            # Fail loud rather than silently dropping it: a non-alerts spec
            # asking for a pipeline mode means the spec or adapter is wrong.
            raise ValueError(
                f"deploy_mode={mode!r} is only valid for the alerts profile, "
                f"got profile={profile!r}"
            )
        return mode, {}
    if mode in _PLACEMENT_OVERRIDES:
        return None, dict(_PLACEMENT_OVERRIDES[mode])
    raise ValueError(
        f"unsupported deploy_mode {mode!r}; expected one of "
        f"{', '.join((*PROFILE_MODES, *_PLACEMENT_OVERRIDES))}"
    )


def _as_env_override_list(overrides: dict[str, str]) -> list[str]:
    return [f"{k}={v}" for k, v in sorted(overrides.items())]



def _bind_sources(resolved: Any) -> list[str]:
    """Absolute host bind sources in a docker_read payload.

    `docker_read` returns `compose_yaml_content` as a RAW YAML STRING (see
    services/agent/.../orchestrator/tools.py::_docker_read), not a nested
    structure. An earlier version of this parsed the payload as JSON and
    required quote-delimited paths, so it matched nothing and silently created
    no directories -- run 33611712532 failed on sdrc/log exactly as before.

    Parsed as text rather than with a YAML loader: the pre-deploy runs under
    `uv --isolated --no-project`, which has no third-party deps available.

    Both compose forms appear in the tree:
        - /abs/src:/dst[:ro]          short
        - type: bind                  long
          source: /abs/src
    """
    if isinstance(resolved, dict):
        text = "\n".join(
            str(resolved.get(k) or "")
            for k in ("compose_yaml_content", "env_content")
        ) or json.dumps(resolved)
    else:
        text = str(resolved)

    found: set[str] = set()
    # short form: a list item whose value is `/src:/dst` (optional :mode)
    found.update(re.findall(r'^\s*-\s+"?(/[^\s:"]+):/[^\s"]*"?\s*$', text, re.M))
    # long form: an explicit `source:` key
    found.update(re.findall(r'^\s*source:\s*"?(/[^\s"]+)"?\s*$', text, re.M))

    repo = str(_repo_dir())
    out = []
    for f in sorted(found):
        f = f.rstrip("/")
        # Scope guard -- we mkdir these, so never escape the checkout/.mdx_data.
        if f and "*" not in f and (f.startswith(repo + "/") or "/.mdx_data/" in f):
            out.append(f)
    return out


def _precreate_bind_sources(call, tools, compose_id: str) -> int:
    """`mkdir -p` every bind source the resolved compose needs.

    Docker auto-creates a missing host path for SHORT-syntax binds but not for
    long-form `type: bind` (create_host_path defaults false). The harness also
    scrubs untracked files from the checkout before every leg, so runtime dirs
    such as `services/infra/sdrc/log` are absent by deploy time.

    Four separate runs died one directory at a time -- vst_data, vst_video,
    nginx_logs, sdrc/log -- because each fix extended a hand-maintained list.
    Reading the RESOLVED compose ends the whole class: absolute, relative and
    ${VAR}-derived sources alike, including any added later.

    Best-effort by design -- if the read or parse fails, fall through and let
    docker_up report the real error rather than masking it with our own.
    """
    try:
        resolved = call(tools.DOCKER_READ,
                        arguments={"docker_compose_id": compose_id},
                        show_response=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[predeploy] docker_read failed ({exc}); skipping bind pre-create",
              flush=True)
        return 0
    sources = _bind_sources(resolved)
    made = 0
    for src in sources:
        # Only create DIRECTORY bind sources. A dot in the basename means a file
        # (`render-config.sh`, `.wdm-env`), and mkdir there would mount an empty
        # directory over a script the container needs -- quieter and worse than
        # the missing-path error. Erring toward not-creating keeps the status quo
        # for those rather than regressing them.
        if "." in Path(src).name:
            continue
        try:
            path = Path(src)
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                made += 1
        except OSError as exc:
            print(f"[predeploy] could not create bind source {src}: {exc}", flush=True)
    print(f"[predeploy] bind sources: {len(sources)} referenced, {made} created",
          flush=True)
    return made


def predeploy(
    profile: str,
    *,
    deploy_mode: str | None = None,
    placement_mode: str | None = None,
    extra_env_overrides: dict[str, str] | None = None,
    poll_sleep_s: int = 30,
    tail_lines: int = MAX_TAIL_LINES,
) -> dict[str, Any]:
    """Run the documented deploy sequence and return the terminal status.

    `deploy_mode` and `placement_mode` are DIFFERENT axes and both may apply at
    once. The alerts adapter emits both: `deploy_mode` is the pipeline mode
    (`verification` / `real-time` -> `profile_mode`), while `mode` is LLM/VLM
    placement (`remote-all` -> env overrides). Folding them into one argument
    would silently drop whichever came second.
    """
    tail_lines = max(1, min(tail_lines, MAX_TAIL_LINES))
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(
            f"unsupported profile {profile!r}; expected one of "
            f"{', '.join(SUPPORTED_PROFILES)}"
        )

    repo = _repo_dir()
    agent_dir = repo / "services" / "agent"
    if not agent_dir.is_dir():
        raise RuntimeError(f"agent dir not found at {agent_dir}")
    helper = _load_helper(repo)
    mcp_url = _mcp_url()

    healthy, message = helper.check_mcp_health(mcp_url, agent_dir)
    if not healthy:
        raise RuntimeError(f"orchestrator MCP is not healthy at {mcp_url}: {message}")

    call = lambda tool, **kw: helper.require_success(  # noqa: E731
        helper.tool_call(tool, mcp_url=mcp_url, agent_dir=agent_dir, **kw),
        str(tool),
    )
    tools = helper.OrchestratorTool

    # 1. profiles -- validates the name against what this MCP build supports
    #    before we spend time on prereqs, and records the list in the log.
    call(tools.PROFILES, arguments={})

    # 2. prereqs -- Docker / GPU / container-toolkit / disk checks.
    call(tools.PREREQS, arguments={})

    # 3. docker_generate -- resolve env + compose artifacts.
    profile_mode, placement = _split_deploy_mode(profile, deploy_mode)
    if placement_mode:
        extra_placement, _ = _split_deploy_mode(profile, placement_mode)
        if extra_placement:
            raise ValueError(
                f"placement_mode={placement_mode!r} resolved to a pipeline mode; "
                "pass it as deploy_mode instead"
            )
        placement = {**placement, **_PLACEMENT_OVERRIDES.get(placement_mode, {})}
    overrides = {**placement, **(extra_env_overrides or {})}
    generate_args: dict[str, Any] = {
        "profile": profile,
        "env_overrides": _as_env_override_list(overrides),
    }
    if profile_mode:
        generate_args["profile_mode"] = profile_mode
    generated = call(tools.DOCKER_GENERATE, arguments=generate_args)
    compose_id = generated.get("docker_compose_id")
    if not compose_id:
        raise RuntimeError(
            f"docker_generate returned no docker_compose_id: "
            f"{json.dumps(generated, indent=2)}"
        )

    # 3b. Pre-create every bind source the resolved compose needs. See
    #     _precreate_bind_sources for why this replaces patching dir lists.
    _precreate_bind_sources(call, tools, compose_id)

    # 4. docker_up -- returns an ops id to poll.
    up = call(
        tools.DOCKER_UP,
        arguments={
            "docker_compose_id": compose_id,
            "build": True,
            "force_recreate": False,
            "pull_always": False,
        },
    )
    ops_id = up.get("docker_compose_ops_id")
    if not ops_id:
        raise RuntimeError(
            f"docker_up returned no docker_compose_ops_id: {json.dumps(up, indent=2)}"
        )

    # 5. Poll docker_status. NOT helper.poll_compose_op(): that returns as soon
    #    as `running` is false and never inspects `exit_code`, so a failed
    #    deploy comes back looking like a successful one. The docs define ready
    #    as `running == false` AND `exit_code == 0`, so assert both here.
    started = time.monotonic()
    while True:
        status = call(
            tools.DOCKER_STATUS,
            arguments={"docker_compose_ops_id": ops_id, "tail_lines": tail_lines},
        )
        if not status.get("running", False):
            break
        time.sleep(poll_sleep_s)

    exit_code = status.get("exit_code")
    if exit_code != 0:
        raise RuntimeError(
            f"VSS pre-deploy failed: profile={profile} mode={deploy_mode or '-'} "
            f"exit_code={exit_code!r} after {int(time.monotonic() - started)}s\n"
            f"{json.dumps(status, indent=2)[-4000:]}"
        )
    print(
        f"[predeploy] VSS profile {profile!r} ready "
        f"(mode={deploy_mode or '-'}, {int(time.monotonic() - started)}s)",
        flush=True,
    )
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=SUPPORTED_PROFILES)
    parser.add_argument(
        "--deploy-mode",
        default="",
        help="Spec deploy_mode: verification | real-time (alerts) or a "
             "placement mode such as remote-all.",
    )
    parser.add_argument(
        "--placement-mode",
        default="",
        help="LLM/VLM placement: remote-all | remote-llm | remote-vlm. "
             "Separate axis from --deploy-mode; the alerts adapter sets both.",
    )
    parser.add_argument(
        "--env-override",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra env override passed to docker_generate. Repeatable.",
    )
    parser.add_argument("--poll-sleep-sec", type=int, default=30)
    args = parser.parse_args(argv)

    extra: dict[str, str] = {}
    for item in args.env_override:
        if "=" not in item:
            parser.error(f"--env-override must be KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        extra[key.strip()] = value

    try:
        predeploy(
            args.profile,
            deploy_mode=args.deploy_mode,
            placement_mode=args.placement_mode,
            extra_env_overrides=extra,
            poll_sleep_s=args.poll_sleep_sec,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

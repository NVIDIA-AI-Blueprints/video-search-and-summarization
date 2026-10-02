#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Worker-side NIM lifecycle. Standard library only; copied to the VSS worker.

Discover model-specific NIMs in nvcr.io, pin the resolved manifest digest,
validate CPU architecture, then serve coding harnesses through LiteLLM and
NemoClaw through NIM's native API. No resource sizing or hosted fallback is
performed.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROXY_PORT = 18400
SWITCHYARD_REF = "cef42319255e6d6788fd6287fedde0a0f115065e"
ROUTER_PORT = 18430
LITELLM_VERSION = "1.103.0"
LABEL = "vss.skill-eval.nim-owner"
STARTUP_BUDGET_SEC = 5400
_START_DEADLINE: float | None = None
SPARK_NODE_ID = "extnode-3I3rYbpIyfB6TcEXWk2k0wabSR8"
SPARK_NODE_NAME = "Spark-ba-WiFi"
MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


class NimError(RuntimeError):
    pass


def validate_model_id(model: str) -> str:
    # NVIDIA Inference sometimes prefixes its NIM provider name. No arbitrary
    # provider stripping: hosted proprietary models must fail, never substitute.
    canonical = model.removeprefix("nvidia_nim/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*", canonical):
        raise ValueError(
            f"Invalid local NIM image ID {model!r}; expected publisher/model "
            "(optionally prefixed by nvidia_nim/), for example "
            "nvidia/nemotron-3.5-lightning-30b-a3b. Use the model-specific "
            "NIM image ID, not a hosted Inference Hub model ID."
        )
    return canonical


def architecture(machine: str) -> str:
    aliases = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    if machine not in aliases:
        raise NimError(f"Unsupported worker architecture: {machine}")
    return aliases[machine]


def unique_models(routes: list[dict]) -> list[str]:
    return list(dict.fromkeys(validate_model_id(r["model"]) for r in routes))


def tool_parser_args(model: str) -> str | None:
    """Enable tool calling for known vLLM-backed model-specific NIMs."""
    if model.startswith(("meta/llama-3.1-", "meta/llama-3.3-")):
        return "--enable-auto-tool-choice --tool-call-parser llama3_json"
    if model == "nvidia/nemotron-3.5-lightning-30b-a3b":
        return (
            "--reasoning-parser nemotron_v3 "
            "--enable-auto-tool-choice --tool-call-parser qwen3_coder"
        )
    if model == "nvidia/nemotron-3-ultra-550b-a55b":
        return (
            "--reasoning-parser-plugin ultra_v3_reasoning_parser.py "
            "--reasoning-parser ultra_v3 "
            "--enable-auto-tool-choice --tool-call-parser qwen3_coder"
        )
    return None


def request_json(url: str, headers: dict | None = None, payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers=headers or {})
    timeout = 60
    if _START_DEADLINE is not None:
        if time.monotonic() >= _START_DEADLINE:
            raise NimError(f"Local NIM startup exceeded its {STARTUP_BUDGET_SEC:,}-second budget")
        timeout = min(timeout, max(1, int(_START_DEADLINE - time.monotonic())))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}, response.headers


def registry_error(exc: urllib.error.HTTPError, model: str):
    if exc.code == 404:
        raise NimError(f"No NIM image available for {model}") from None
    if exc.code in (401, 403):
        raise NimError(
            f"NGC access denied for {model}; check NGC credentials and image entitlement"
        ) from None
    raise NimError(f"NGC registry failed for {model}: HTTP {exc.code}") from None


def resolve_image(model: str, arch: str, key: str) -> dict:
    """Discover released tags and an architecture-compatible manifest.

    The Spark-specific Qwen repository is a packaging variant of the same
    model, not a model substitution. Try it first on arm64. Unknown tags,
    prereleases, and non-Linux images are never silently selected.
    """
    canonical = validate_model_id(model)
    repos = [f"nim/{canonical}"]
    if arch == "arm64" and canonical == "qwen/qwen3-32b":
        repos.insert(0, "nim/qwen/qwen3-32b-dgx-spark")
    found = False
    for repo in repos:
        basic = base64.b64encode(f"$oauthtoken:{key}".encode()).decode()
        # nvcr.io's Registry v2 challenge advertises this realm for image
        # pulls. The NGC API's authn.nvidia.com/token endpoint is a separate
        # service and can reject an otherwise valid container-registry key.
        auth_url = "https://nvcr.io/proxy_auth?" + urllib.parse.urlencode(
            {
                "service": "registry",
                "scope": f"repository:{repo}:pull",
            }
        )
        try:
            auth, _ = request_json(auth_url, {"Authorization": f"Basic {basic}"})
            headers = {
                "Authorization": f"Bearer {auth['token']}",
                "Accept": MANIFEST_TYPES,
            }
            listing, _ = request_json(f"https://nvcr.io/v2/{repo}/tags/list", headers)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            registry_error(exc, canonical)
        tags = listing.get("tags") or []

        # Prefer released semver tags, including NVIDIA's documented variant
        # suffix. Resolve the tag to a digest so later pulls cannot drift.
        def version(tag):
            match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-variant)?", tag)
            return tuple(map(int, match.groups())) if match else ()

        releases = sorted(
            (t for t in tags if version(t)), key=lambda t: (version(t), t), reverse=True
        )
        if "latest" in tags:
            releases.append("latest")
        found = found or bool(tags)
        for tag in releases:
            try:
                manifest, mh = request_json(
                    f"https://nvcr.io/v2/{repo}/manifests/{tag}", headers
                )
                if "manifests" in manifest:
                    matches = [
                        m
                        for m in manifest["manifests"]
                        if m.get("platform", {}).get("architecture") == arch
                        and m.get("platform", {}).get("os") == "linux"
                    ]
                    if not matches:
                        continue
                    digest = matches[0]["digest"]
                else:
                    config, _ = request_json(
                        f"https://nvcr.io/v2/{repo}/blobs/{manifest['config']['digest']}",
                        headers,
                    )
                    if (
                        config.get("architecture") != arch
                        or config.get("os") != "linux"
                    ):
                        continue
                    digest = mh.get("Docker-Content-Digest")
                if not digest or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                    raise NimError(
                        f"NGC returned an invalid image digest for {canonical}"
                    )
                return {
                    "model": canonical,
                    "image": f"nvcr.io/{repo}@{digest}",
                    "tag": tag,
                    "architecture": arch,
                }
            except urllib.error.HTTPError as exc:
                registry_error(exc, canonical)
    if found:
        raise NimError(f"No released NIM image for {canonical} supports linux/{arch}")
    raise NimError(f"No model-specific NIM image available for {canonical}")


def docker(
    *args: str, timeout: int = 120, input_text: str | None = None, check: bool = True
):
    if _START_DEADLINE is not None:
        timeout = min(timeout, max(1, int(_START_DEADLINE - time.monotonic())))
        if time.monotonic() >= _START_DEADLINE:
            raise NimError(f"Local NIM startup exceeded its {STARTUP_BUDGET_SEC:,}-second budget")
    result = subprocess.run(
        ["docker", *args],
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode:
        # Docker diagnostics are useful but never include the registry key.
        error = result.stderr[-1500:]
        for name in ("NGC_API_KEY", "NGC_CLI_API_KEY", "SKILL_EVAL_ROUTER_UPSTREAM_API_KEY"):
            if os.environ.get(name):
                error = error.replace(os.environ[name], "[REDACTED]")
        raise NimError(f"Docker {args[0]} failed: {error}")
    return result


def owner_paths(owner: str):
    if not re.fullmatch(r"[a-f0-9]{24}", owner):
        raise NimError("Invalid local NIM owner")
    root = Path.home() / ".cache" / "skill-eval-nim" / owner
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def worker_host() -> str:
    """Return the worker address reachable from the NemoClaw sandbox."""
    route = subprocess.run(
        ["ip", "-j", "route", "get", "1.1.1.1"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    host = json.loads(route.stdout)[0]["prefsrc"]
    import ipaddress

    ipaddress.ip_address(host)
    return host


def wait_ready(url: str, token: str, timeout: int = 900, container: str | None = None):
    deadline = min(time.monotonic() + timeout, _START_DEADLINE or float("inf"))
    while time.monotonic() < deadline:
        try:
            return request_json(url, {"Authorization": f"Bearer {token}"})[0]
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise NimError(
                    f"Local inference authentication failed: HTTP {exc.code}"
                ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if container:
            state = docker(
                "inspect", "--format", "{{.State.Running}} {{.State.OOMKilled}} {{.State.ExitCode}}",
                container, check=False,
            )
            if state.returncode or not state.stdout.startswith("true "):
                logs = docker("logs", "--tail", "40", container, check=False)
                detail = (logs.stderr or logs.stdout or state.stderr or "")[-2500:]
                for name in ("NGC_API_KEY", "NGC_CLI_API_KEY", "SKILL_EVAL_ROUTER_UPSTREAM_API_KEY"):
                    if os.environ.get(name):
                        detail = detail.replace(os.environ[name], "[REDACTED]")
                raise NimError(
                    f"Local NIM container stopped before readiness "
                    f"(state={state.stdout.strip() or 'missing'}): {detail}"
                )
        time.sleep(3)
    raise NimError(f"Local NIM readiness timed out: {url}")


def start(plan: dict):
    root = owner_paths(plan["owner"])
    marker = root / "ready.json"
    if marker.exists():
        # Same leg, later task: share the existing services. A new chain's
        # Docker reset removes them, in which case rebuild below.
        containers = docker(
            "ps", "-q", "--filter", f"label={LABEL}={plan['owner']}"
        ).stdout.split()
        previous = json.loads(marker.read_text())
        config_file = root / "proxy.json"
        previous_key = (
            json.loads(config_file.read_text())
            .get("general_settings", {})
            .get("master_key")
            if config_file.exists()
            else None
        )
        same_plan = (
            previous.get("roles") == plan["routes"]
            and previous.get("switchyard") == plan.get("switchyard")
            and previous_key == plan["token"]
        )
        expected = len(unique_models(plan["routes"])) + 1 + bool(plan.get("switchyard"))
        if same_plan and len(containers) == expected:
            for i, model in enumerate(unique_models(plan["routes"])):
                base = f"http://127.0.0.1:{PROXY_PORT + 10 + i}/v1"
                wait_ready(
                    f"{base}/health/ready", "", 30,
                    container=f"skill-eval-nim-{plan['owner']}-{i}",
                )
                served, _ = request_json(f"{base}/models")
                if [entry.get("id") for entry in served.get("data", [])] != [
                    item["served_model"] for item in previous["models"]
                    if item["model"] == model
                ]:
                    raise NimError(f"Reused local NIM changed served model: {model}")
            wait_ready(
                f"http://127.0.0.1:{PROXY_PORT}/health/liveliness", plan["token"], 30
            )
            if plan.get("switchyard"):
                request_json(f"http://127.0.0.1:{ROUTER_PORT}/v1/stats")
                smoke_switchyard(plan)
            smoke_routes(plan, worker_host() if any(
                route["runtime"] == "nemoclaw" for route in plan["routes"]
            ) else None)
            publish(root)
            configure_nemoclaw(json.loads(marker.read_text()))
            return
    marker.unlink(missing_ok=True)
    (root / "deployment.json").unlink(missing_ok=True)
    key = os.environ.get("NGC_API_KEY") or os.environ.get("NGC_CLI_API_KEY")
    if plan["routes"] and not key:
        raise NimError("Local NIM requires NGC_CLI_API_KEY or NGC_API_KEY on the worker")
    if key:
        os.environ["NGC_API_KEY"] = key
    arch = architecture(platform.machine())
    resolved = [
        resolve_image(model, arch, key) for model in unique_models(plan["routes"])
    ]
    # Persist sanitized resolution evidence even if deployment fails later.
    (root / "deployment.json").write_text(
        json.dumps(
            {"models": resolved, "roles": plan["routes"], "architecture": arch},
            indent=2,
        )
    )
    # Scope login credentials to this operation, never modify the user's Docker config.
    with tempfile.TemporaryDirectory(prefix="nim-docker-") as config:
        old = os.environ.get("DOCKER_CONFIG")
        os.environ["DOCKER_CONFIG"] = config
        try:
            if resolved:
                docker(
                    "login", "nvcr.io", "-u", "$oauthtoken", "--password-stdin",
                    input_text=key,
                )
            for item in resolved:
                docker("pull", item["image"], timeout=1500)
        finally:
            if old is None:
                os.environ.pop("DOCKER_CONFIG", None)
            else:
                os.environ["DOCKER_CONFIG"] = old
    cleanup(plan["owner"], remove_files=False)
    models = []
    nemoclaw_route = next(
        (r for r in plan["routes"] if r["runtime"] == "nemoclaw"), None
    )
    nemoclaw_model = (
        validate_model_id(nemoclaw_route["model"]) if nemoclaw_route else None
    )
    host = worker_host() if nemoclaw_route or plan.get("switchyard") else None
    for i, item in enumerate(resolved):
        port = PROXY_PORT + 10 + i
        direct_nemoclaw = item["model"] == nemoclaw_model
        name = f"skill-eval-nim-{plan['owner']}-{i}"
        cache = (
            Path.home()
            / ".cache"
            / "skill-eval-nim-models"
            / hashlib.sha256(item["model"].encode()).hexdigest()[:20]
        )
        cache.mkdir(parents=True, exist_ok=True)
        cache.chmod(0o1777)
        nim_args = [
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"{LABEL}={plan['owner']}",
            "--gpus",
            "all",
            "--shm-size=16g",
            "-e",
            "NGC_API_KEY",
        ]
        parser_args = tool_parser_args(item["model"])
        if "-dgx-spark@" in item["image"]:
            # The 1.14 DGX Spark variants enable tool calls with this switch;
            # they do not consume the standard NIM_PASSTHROUGH_ARGS setting.
            nim_args.extend(("-e", "TOOL_CALL_PARSER=1"))
            if "qwen3-32b-dgx-spark@" in item["image"]:
                # Its default 8K window cannot hold the Codex skill-eval
                # system prompt (9.7K tokens before the first tool call).
                nim_args.extend(("-e", "NIM_MAX_MODEL_LEN=32768"))
        elif parser_args:
            nim_args.extend(("-e", f"NIM_PASSTHROUGH_ARGS={parser_args}"))
        nim_args.extend((
            "-p",
            f"127.0.0.1:{port}:8000",
            "-v",
            f"{cache}:/opt/nim/.cache",
            item["image"],
        ))
        if direct_nemoclaw:
            # Keep the selected ID on NIM for discovery. NemoClaw reaches it
            # through the authenticated proxy, never this loopback port.
            nim_args.extend(("--served-model-name", nemoclaw_route["model"]))
        docker(*nim_args)
        base = f"http://127.0.0.1:{port}/v1"
        wait_ready(f"{base}/health/ready", "", 4800, container=name)
        served, _ = request_json(f"{base}/models")
        names = [m["id"] for m in served.get("data", [])]
        # The model-specific repository establishes identity; the server's
        # single advertised ID may use original publisher capitalization.
        if len(names) != 1:
            raise NimError(
                f"Expected one served model from {item['image']}; received {names}"
            )
        if direct_nemoclaw and names[0] != nemoclaw_route["model"]:
            raise NimError(
                f"Local NIM advertised {names[0]!r}, not the selected "
                f"NemoClaw model {nemoclaw_route['model']!r}"
            )
        for route in plan["routes"]:
            if validate_model_id(route["model"]) == item["model"]:
                models.append(
                    {
                        "model_name": route["model"],
                        "litellm_params": {
                            "model": f"nvidia_nim/{names[0]}",
                            "api_base": base,
                            "api_key": "local-nim",
                        },
                    }
                )
        item["served_model"] = names[0]
    if plan.get("switchyard"):
        start_switchyard(plan, root, resolved)
        models.append({
            "model_name": plan["switchyard"]["route"],
            "litellm_params": {
                "model": f"openai/{plan['switchyard']['route']}",
                "api_base": f"http://127.0.0.1:{ROUTER_PORT}/v1",
                "api_key": "local-router",
            },
        })
    # JSON is valid YAML; no templating of arbitrary model strings into shell.
    proxy_config = {
        "model_list": list({m["model_name"]: m for m in models}.values()),
        "litellm_settings": {"drop_params": True},
        "general_settings": {"master_key": plan["token"]},
    }
    config_file = root / "proxy.json"
    config_file.write_text(json.dumps(proxy_config))
    config_file.chmod(0o600)
    docker(
        "run",
        "-d",
        "--name",
        f"skill-eval-nim-{plan['owner']}-proxy",
        "--label",
        f"{LABEL}={plan['owner']}",
        "--network",
        "host",
        "-v",
        f"{config_file}:/config.yaml:ro",
        "python:3.12-slim",
        "sh",
        "-c",
        f"pip install --disable-pip-version-check 'litellm[proxy]=={LITELLM_VERSION}' && exec litellm --config /config.yaml --host 0.0.0.0 --port {PROXY_PORT}",
        timeout=300,
    )
    wait_ready(f"http://127.0.0.1:{PROXY_PORT}/health/liveliness", plan["token"], 300)
    # Exercise each harness protocol, so a healthy server with an incompatible
    # API cannot produce an apparently successful deployment.
    smoke_routes(plan, host)
    if plan.get("switchyard"):
        smoke_switchyard(plan)
    evidence = {"models": resolved, "roles": plan["routes"], "architecture": arch}
    if plan.get("switchyard"):
        evidence["switchyard"] = plan["switchyard"]
    if nemoclaw_route or plan.get("switchyard"):
        evidence["nemoclaw_endpoint"] = f"http://{host}:{PROXY_PORT}/v1"
    marker.write_text(json.dumps(evidence, indent=2))
    configure_nemoclaw(evidence)
    publish(root)


def switchyard_config(
    frontier_model: str,
    operational_model: str,
    operational_deployment: str,
    local_port: int | None,
    route: str,
) -> str:
    """Route the selected operational model from its selected deployment."""
    for model in (frontier_model, operational_model, route):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", model):
            raise NimError(f"Invalid Switchyard model or route ID: {model!r}")
    if operational_deployment not in {"local-nim", "hosted-nvidia-inference", "nvidia-inference"}:
        raise NimError(f"Unsupported Switchyard operational deployment: {operational_deployment}")
    if operational_deployment == "local-nim" and local_port is None:
        raise NimError("Switchyard local NIM port is missing")
    local_client = (
        f'[llm_clients.local]\nformat = "openai_chat"\n'
        f'base_url = "http://127.0.0.1:{local_port}/v1"\n\n'
        if operational_deployment == "local-nim" else ""
    )
    efficient_client = "local" if operational_deployment == "local-nim" else "frontier"
    return f'''schema_version = 1

{local_client}[llm_clients.frontier]
format = "openai_chat"
base_url = "https://inference-api.nvidia.com/v1"
api_key_env = "SWITCHYARD_FRONTIER_API_KEY"

[targets.capable]
id = {json.dumps(frontier_model)}
llm_client = "frontier"

[targets.efficient]
id = {json.dumps(operational_model)}
llm_client = {json.dumps(efficient_client)}

[routes.stage]
id = {json.dumps(route)}
type = "stage_router"
capable_target = "capable"
efficient_target = "efficient"
picker = "efficient_first"
confidence_threshold = 0.5

[routes.probe_efficient]
id = "switchyard/probe-efficient"
type = "passthrough"
target = "efficient"

[routes.probe_frontier]
id = "switchyard/probe-frontier"
type = "passthrough"
target = "capable"
'''


def start_switchyard(plan: dict, root: Path, resolved: list[dict]) -> None:
    settings = plan["switchyard"]
    key = os.environ.get("SKILL_EVAL_ROUTER_UPSTREAM_API_KEY")
    if not key or "\n" in key or "\r" in key:
        raise NimError("Switchyard requires a single-line frontier API key")
    deployment = settings["operational_deployment"]
    local_model = next(
        (item for item in resolved if item["model"] == validate_model_id(settings["operational_model"])),
        None,
    ) if deployment == "local-nim" else None
    if deployment == "local-nim" and local_model is None:
        raise NimError("Switchyard selected local NIM was not deployed")
    local_port = PROXY_PORT + 10 + resolved.index(local_model) if local_model else None
    served_model = local_model["served_model"] if local_model else settings["operational_model"]
    image = f"skill-eval-switchyard:{SWITCHYARD_REF[:12]}"
    if docker("image", "inspect", image, check=False).returncode:
        source = Path.home() / ".cache" / "skill-eval-switchyard-source"
        source.parent.mkdir(parents=True, exist_ok=True)
        if not (source / ".git").is_dir():
            subprocess.run(
                ["git", "clone", "--filter=blob:none", "https://github.com/NVIDIA-NeMo/Switchyard.git", str(source)],
                check=True, capture_output=True, text=True, timeout=300,
            )
        for command in (["fetch", "origin", SWITCHYARD_REF], ["checkout", "-f", "--detach", SWITCHYARD_REF]):
            subprocess.run(
                ["git", "-C", str(source), *command],
                check=True, capture_output=True, text=True, timeout=300,
            )
        actual = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        if actual != SWITCHYARD_REF:
            raise NimError("Switchyard source pin drifted")
        docker("build", "-t", image, str(source), timeout=1800)
    config_file = root / "switchyard.toml"
    config_file.write_text(switchyard_config(
        settings["frontier_model"], served_model, deployment, local_port, settings["route"]
    ))
    key_file = root / "frontier.key"
    key_file.write_text(key)
    key_file.chmod(0o600)
    routing_log = root / "switchyard-routing.jsonl"
    routing_log.write_text("")
    routing_log.chmod(0o600)
    router = f"skill-eval-nim-{plan['owner']}-switchyard"
    docker(
        "run", "-d", "--name", router, "--label", f"{LABEL}={plan['owner']}",
        "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{config_file}:/etc/switchyard/config.toml:ro",
        "-v", f"{key_file}:/run/switchyard/frontier.key:ro",
        "-v", f"{routing_log}:/var/log/switchyard/routing.jsonl",
        "--entrypoint", "/bin/sh", image, "-c",
        "export SWITCHYARD_FRONTIER_API_KEY=\"$(cat /run/switchyard/frontier.key)\"; "
        f"exec switchyard-server --config /etc/switchyard/config.toml --host 127.0.0.1 --port {ROUTER_PORT} "
        "--routing-log-file /var/log/switchyard/routing.jsonl",
        timeout=60,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            request_json(f"http://127.0.0.1:{ROUTER_PORT}/v1/stats")
            return
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    raise NimError("Switchyard router did not become ready")


def smoke_switchyard(plan: dict) -> None:
    for route in ("switchyard/probe-efficient", "switchyard/probe-frontier", plan["switchyard"]["route"]):
        try:
            request_json(
                f"http://127.0.0.1:{ROUTER_PORT}/v1/chat/completions",
                {"Content-Type": "application/json"},
                {"model": route, "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 16},
            )
        except urllib.error.HTTPError as exc:
            raise NimError(f"Switchyard {route} smoke failed: HTTP {exc.code}") from None
    try:
        request_json(
            f"http://127.0.0.1:{PROXY_PORT}/v1/chat/completions",
            {"Authorization": f"Bearer {plan['token']}", "Content-Type": "application/json"},
            {"model": plan["switchyard"]["route"],
             "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 16},
        )
    except urllib.error.HTTPError as exc:
        raise NimError(f"Authenticated Switchyard proxy smoke failed: HTTP {exc.code}") from None


def smoke_routes(plan: dict, host: str | None):
    """Check the same authenticated endpoints the evaluated harnesses use."""
    for route in plan["routes"]:
        runtime = route["runtime"]
        schema = {"type": "object", "properties": {}}
        if runtime == "claude-code":
            path, body = (
                "messages",
                {
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 16,
                    "tools": [
                        {
                            "name": "probe",
                            "description": "Check status",
                            "input_schema": schema,
                        }
                    ],
                    "tool_choice": {"type": "auto"},
                },
            )
        elif runtime == "codex":
            path, body = "responses", {
                "input": "Say OK",
                "max_output_tokens": 16,
                "tools": [
                    {
                        "type": "function",
                        "name": "probe",
                        "description": "Check status",
                        "parameters": schema,
                    }
                ],
                "tool_choice": "auto",
            }
        else:
            path, body = (
                "chat/completions",
                {
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 16,
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "probe",
                                "description": "Check status",
                                "parameters": schema,
                            },
                        }
                    ],
                    "tool_choice": "auto",
                },
            )
        try:
            smoke_base = (
                f"http://{host}:{PROXY_PORT}/v1"
                if runtime == "nemoclaw"
                else f"http://127.0.0.1:{PROXY_PORT}/v1"
            )
            request_json(
                f"{smoke_base}/{path}",
                {
                    "Authorization": f"Bearer {plan['token']}",
                    "x-api-key": plan["token"],
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                {"model": route["model"], **body},
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read(1500).decode(errors="replace")
            for secret in (plan["token"], os.environ.get("NGC_API_KEY"), os.environ.get("NGC_CLI_API_KEY"), os.environ.get("SKILL_EVAL_ROUTER_UPSTREAM_API_KEY")):
                if secret:
                    detail = detail.replace(secret, "[REDACTED]")
            raise NimError(
                f"Local NIM {runtime} protocol smoke failed for {route['model']}: "
                f"HTTP {exc.code}: {detail}"
            ) from None


def configure_nemoclaw(evidence: dict):
    if evidence.get("nemoclaw_endpoint"):
        with (Path.home() / ".eval_env").open("a") as handle:
            handle.write(
                "\nexport NEMOCLAW_ENDPOINT_URL="
                + shlex.quote(evidence["nemoclaw_endpoint"])
                + "\n"
            )


def collect_logs(plan: dict):
    root = owner_paths(plan["owner"])
    publish(root)
    target = Path("/logs/artifacts/local-nim")
    names = docker(
        "ps",
        "-a",
        "--filter",
        f"label={LABEL}={plan['owner']}",
        "--format",
        "{{.Names}}",
        check=False,
    ).stdout.split()
    for name in names:
        if not re.fullmatch(r"skill-eval-nim-[a-f0-9]{24}-(?:[0-9]+|proxy|switchyard)", name):
            continue
        result = docker("logs", "--tail", "150", name, check=False)
        logs = result.stdout + result.stderr
        for value in (
            plan.get("token"),
            os.environ.get("NGC_API_KEY"),
            os.environ.get("NGC_CLI_API_KEY"),
            os.environ.get("SKILL_EVAL_ROUTER_UPSTREAM_API_KEY"),
        ):
            if value:
                logs = logs.replace(value, "[REDACTED]")
        (target / f"{name}.log").write_text(logs)
    routing_log = root / "switchyard-routing.jsonl"
    if routing_log.is_file():
        (target / "switchyard-routing.jsonl").write_text(routing_log.read_text())


def publish(root: Path):
    target = Path("/logs/artifacts/local-nim")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("deployment.json", "ready.json"):
        if (root / name).exists():
            (target / name).write_text((root / name).read_text())


def cleanup(owner: str, remove_files: bool = True):
    root = owner_paths(owner)
    result = docker("ps", "-aq", "--filter", f"label={LABEL}={owner}", check=False)
    if result.returncode:
        raise NimError("Unable to enumerate local NIM containers for cleanup")
    for container in result.stdout.split():
        docker("rm", "-f", container)
    if remove_files:
        for name in ("proxy.json", "ready.json", "frontier.key", "switchyard.toml", "switchyard-routing.jsonl"):
            (root / name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "cleanup"))
    parser.add_argument("--plan")
    parser.add_argument("--owner")
    args = parser.parse_args()
    global _START_DEADLINE
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if args.action == "cleanup":
        root = owner_paths(args.owner)
        pid_file = root / "startup.pid"
        with contextlib.suppress(FileNotFoundError, ProcessLookupError):
            if pid_file.exists():
                pid = int(pid_file.read_text())
                proc = Path(f"/proc/{pid}/cmdline")
                # Only stop our exact helper, never a recycled unrelated PID.
                expected = f"skill-eval-nim-{args.owner}.py".encode()
                if expected in proc.read_bytes():
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(20):
                        if not proc.exists():
                            break
                        time.sleep(0.5)
                    if expected in proc.read_bytes():
                        os.kill(pid, signal.SIGKILL)
        cleanup(args.owner)
        Path(f"/tmp/skill-eval-nim-{args.owner}.json").unlink(missing_ok=True)
        return
    plan = json.loads(Path(args.plan).read_text())
    root = owner_paths(plan["owner"])
    pid_file = root / "startup.pid"
    pid_file.write_text(str(os.getpid()))
    _START_DEADLINE = time.monotonic() + STARTUP_BUDGET_SEC
    try:
        start(plan)
        try:
            collect_logs(plan)
        except (OSError, ValueError, NimError, subprocess.SubprocessError):
            print("Local NIM diagnostic collection incomplete", file=sys.stderr)
    except BaseException as exc:
        # Collect diagnostics before removing any successfully started NIMs.
        _START_DEADLINE = None
        target = Path("/logs/artifacts/local-nim")
        target.mkdir(parents=True, exist_ok=True)
        message = f"{type(exc).__name__}: {exc}"
        for secret in (
            plan["token"],
            os.environ.get("NGC_API_KEY"),
            os.environ.get("NGC_CLI_API_KEY"),
            os.environ.get("SKILL_EVAL_ROUTER_UPSTREAM_API_KEY"),
        ):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        (target / "error.txt").write_text(message)
        try:
            collect_logs(plan)
        except (OSError, ValueError, NimError, subprocess.SubprocessError):
            print("Local NIM diagnostic collection incomplete", file=sys.stderr)
        try:
            cleanup(plan["owner"])
        finally:
            Path(args.plan).unlink(missing_ok=True)
        raise
    finally:
        pid_file.unlink(missing_ok=True)
    Path(args.plan).unlink(missing_ok=True)


if __name__ == "__main__":
    main()

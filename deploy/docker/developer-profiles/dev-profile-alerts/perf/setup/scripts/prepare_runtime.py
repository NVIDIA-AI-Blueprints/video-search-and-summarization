#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Resolve a perf config entry and build the per-run files for alert perf.

Two subcommands, both used by the perf/scripts/*.sh stage hooks:

``resolve``
    Print ``export KEY=VALUE`` lines for one ``perf_configs`` entry so a shell
    hook can ``eval`` them (PERF_HW_PROFILE, PERF_CV_MODEL, ...).

``prepare``
    Write everything a run needs under ``--work-dir`` without touching tracked
    files in the checkout:

    * ``verifier-config.yml``   profile verifier config + defaults.yaml overrides
    * ``deepstream-configs/``   profile DeepStream configs + low-latency inserts
    * ``videos/``               N copies (hard links) of the scenario video, one
                                per stream, served by NVStreamer
    * ``perf-runtime.env``      last --env-file for docker compose; overrides
                                generated.env
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

PERF_DIR = Path(__file__).resolve().parents[2]  # deploy/docker/developer-profiles/dev-profile-alerts/perf
PROFILE_DIR = PERF_DIR.parent
DEPLOY_DIR = PROFILE_DIR.parents[1]
REPO_ROOT = DEPLOY_DIR.parents[1]

_TAG = "[alert-perf-prepare]"


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #
def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def find_perf_config(config_id: str, perf_configs_path: Path) -> Dict[str, Any]:
    entries = load_yaml(perf_configs_path).get("perf_configs") or []
    for entry in entries:
        if entry.get("id") == config_id:
            return entry
    known = ", ".join(e.get("id", "?") for e in entries)
    raise SystemExit(f"{_TAG} ERROR: config id '{config_id}' not in {perf_configs_path} (known: {known})")


def scenario_stream_counts(scenario: Dict[str, Any]) -> List[int]:
    counts: List[int] = []
    for video in scenario.get("videos") or []:
        counts.extend(int(c) for c in video.get("stream_counts") or [1])
    return counts or [1]


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #
_RESOLVE_FIELDS = {
    "PERF_COMPOSE_PATH": ("composePath", ""),
    "PERF_NODE_LABEL": ("nodeLabel", ""),
    "PERF_IMAGE_ARCH": ("imageArch", "amd64"),
    "PERF_HW_PROFILE": ("hardwareProfile", "H100"),
    "PERF_CV_MODEL": ("cvModel", "RTDETR"),
    "PERF_CV_GPUS": ("cvGpus", "0"),
    "PERF_VLM_GPUS": ("vlmGpus", "1"),
    "PERF_VLM_PRECISION": ("vlmPrecision", "bf16"),
    "PERF_LLM_MODE": ("llmMode", "local_shared"),
}


def cmd_resolve(args: argparse.Namespace) -> int:
    entry = find_perf_config(args.config_id, args.perf_configs)
    for env_key, (field, default) in _RESOLVE_FIELDS.items():
        value = str(entry.get(field, default))
        print(f"export {env_key}={shlex.quote(value)}")
    print(f"export PERF_CONFIG_ENABLED={shlex.quote(str(entry.get('enabled', True)).lower())}")
    return 0


# --------------------------------------------------------------------------- #
# prepare
# --------------------------------------------------------------------------- #
def set_dotted(doc: Dict[str, Any], dotted: str, value: Any) -> None:
    node = doc
    parts = dotted.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def write_verifier_config(work_dir: Path, overrides: Dict[str, Any]) -> Path:
    """Copy the profile verifier config and apply dotted-key overrides.

    PyYAML drops comments but keeps ``${VAR}`` placeholders as plain scalars,
    so the Alert Bridge env-substitute step behaves exactly as with the
    shipped file.
    """
    prefix = os.environ.get("VLM_AS_VERIFIER_CONFIG_FILE_PREFIX", "").strip("'\"")
    source = PROFILE_DIR / "vlm-as-verifier" / "configs" / f"{prefix}config.yml"
    doc = load_yaml(source)
    for key, value in (overrides or {}).items():
        set_dotted(doc, key, value)
    target = work_dir / "verifier-config.yml"
    header = (
        f"# GENERATED by {Path(__file__).name} for alert perf runs. Do not edit.\n"
        f"# Source: {source.relative_to(REPO_ROOT)} + configs/defaults.yaml verifier_config_overrides\n"
    )
    target.write_text(header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=10**6),
                      encoding="utf-8")
    print(f"{_TAG} wrote {target}")
    return target


def write_deepstream_configs(work_dir: Path, inserts: Dict[str, Iterable[Dict[str, str]]]) -> Path:
    source = PROFILE_DIR / "deepstream" / "configs"
    target = work_dir / "deepstream-configs"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    for file_name, rules in (inserts or {}).items():
        path = target / file_name
        lines = path.read_text(encoding="utf-8").splitlines()
        for rule in rules:
            after, new_line = rule["after"], rule["line"]
            if new_line in (ln.strip() for ln in lines):
                continue
            try:
                idx = next(i for i, ln in enumerate(lines) if ln.strip() == after)
            except StopIteration:
                raise SystemExit(f"{_TAG} ERROR: '{after}' not found in {path.name}; update defaults.yaml")
            lines.insert(idx + 1, new_line)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{_TAG} wrote {target}")
    return target


def fetch_video(url: str, cache_dir: Path) -> Path:
    local = os.environ.get("PERF_ALERT_VIDEO_PATH", "").strip()
    if local:
        path = Path(local).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"{_TAG} ERROR: PERF_ALERT_VIDEO_PATH not found: {path}")
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / Path(url.split("?")[0]).name
    if not path.is_file():
        print(f"{_TAG} downloading {url}")
        tmp = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=300) as resp, tmp.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
        tmp.rename(path)
    return path


def stage_videos(work_dir: Path, source: Path, count: int) -> Path:
    """One file per stream; NVStreamer creates one RTSP stream per file."""
    videos = work_dir / "videos"
    if videos.exists():
        shutil.rmtree(videos)
    videos.mkdir(parents=True)
    for i in range(1, count + 1):
        dst = videos / f"perf_stream_{i:03d}{source.suffix}"
        try:
            os.link(source, dst)
        except OSError:
            shutil.copy2(source, dst)
    print(f"{_TAG} staged {count} stream video(s) in {videos}")
    return videos


def parse_artifact_ref(ref: str) -> Optional[str]:
    """Return an image reference for the alert service, or None.

    The pipeline passes ARTIFACT_REF=nvcr.io/<org>/<team>/<name>:<version>.
    Only image artifacts change what we deploy.
    """
    ref = (ref or "").strip()
    if not ref or "$" in ref:
        return None
    return ref


def write_runtime_env(work_dir: Path, values: Dict[str, str]) -> Path:
    path = work_dir / "perf-runtime.env"
    lines = [f"# GENERATED by {Path(__file__).name}. Last --env-file; overrides generated.env."]
    for key, value in values.items():
        if any(ch in str(value) for ch in "\n\r"):
            raise SystemExit(f"{_TAG} ERROR: newline in value for {key}")
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{_TAG} wrote {path}")
    return path


def cmd_prepare(args: argparse.Namespace) -> int:
    entry = find_perf_config(args.config_id, args.perf_configs)
    config = load_yaml(args.config)
    defaults = load_yaml(args.defaults)
    scenarios = config.get("test_scenarios") or {}
    if args.scenario not in scenarios:
        raise SystemExit(f"{_TAG} ERROR: scenario '{args.scenario}' not in {args.config}")
    scenario = scenarios[args.scenario]

    work_dir: Path = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    write_verifier_config(work_dir, defaults.get("verifier_config_overrides") or {})
    write_deepstream_configs(work_dir, defaults.get("deepstream_config_inserts") or {})

    max_streams = max(scenario_stream_counts(scenario))
    first_video = (scenario.get("videos") or [{}])[0]
    video = fetch_video(first_video.get("url", ""), work_dir / "video-cache")
    videos_dir = stage_videos(work_dir, video, max_streams)

    precision = str(entry.get("vlmPrecision", "bf16"))
    model = (defaults.get("vlm_precision_models") or {}).get(precision) or {}
    if not model.get("VLM_NAME") or not model.get("RTVI_VLM_MODEL_PATH"):
        raise SystemExit(f"{_TAG} ERROR: no checkpoint for vlmPrecision '{precision}' in defaults.yaml")

    env: Dict[str, str] = {
        "PERF_DIR": str(PERF_DIR),
        "PERF_WORK_DIR": str(work_dir),
        "MODE": "2d_cv",
        "MODEL_NAME_2D": str(entry.get("cvModel", "RTDETR")),
        "NUM_SENSORS": str(max_streams),
        "RT_CV_DEVICE_ID": str(entry.get("cvGpus", "0")).split(",")[0],
        "VLM_AS_VERIFIER_CONFIG_FILE": str(work_dir / "verifier-config.yml"),
        "NVSTREAMER_ALERTS_VIDEO_DIR": str(videos_dir),
        "VLM_NAME": model["VLM_NAME"],
        "RTVI_VLM_MODEL_PATH": model["RTVI_VLM_MODEL_PATH"],
        # generated.env interpolates this from VLM_NAME when it is read, so it
        # must be restated after VLM_NAME changes.
        "RTVI_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": model["VLM_NAME"],
    }
    env.update({k: str(v) for k, v in (defaults.get("runtime_env") or {}).items()})
    image = parse_artifact_ref(args.artifact_ref or os.environ.get("ARTIFACT_REF", ""))
    if image and os.environ.get("ARTIFACT_TYPE", "image") == "image":
        env["PERF_ALERT_IMAGE"] = image
    write_runtime_env(work_dir, env)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--perf-configs", type=Path, default=PERF_DIR / "configs" / "perf-configs.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser("resolve", help="print exports for one perf_configs entry")
    p_resolve.add_argument("--config-id", required=True)
    p_resolve.set_defaults(func=cmd_resolve)

    p_prepare = sub.add_parser("prepare", help="build per-run configs, videos and env file")
    p_prepare.add_argument("--config-id", required=True)
    p_prepare.add_argument("--scenario", required=True)
    p_prepare.add_argument("--work-dir", type=Path, required=True)
    p_prepare.add_argument("--artifact-ref", default="")
    p_prepare.add_argument("--config", type=Path, default=PERF_DIR / "configs" / "config.yaml")
    p_prepare.add_argument("--defaults", type=Path, default=PERF_DIR / "configs" / "defaults.yaml")
    p_prepare.set_defaults(func=cmd_prepare)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

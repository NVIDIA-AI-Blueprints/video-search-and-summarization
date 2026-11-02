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
"""The detector's throwaway environment (X5): CPU only, pinned, outside the repository. It never downloads weights.

    python scripts/v2_detector_env.py plan                # default: print the commands, change nothing
    python scripts/v2_detector_env.py install --yes       # create the venv and install the pinned packages
    python scripts/v2_detector_env.py check               # offline: the env imports and its torch is a CPU build
    uv run --frozen python scripts/v2_detector_env.py estimate [--seconds-per-frame S]   # RA1 frames and CPU time

The environment lives in ``$EVALV2_DETECTOR_HOME`` (default ``~/.cache/evalv2-detector``): ``venv/`` and, once
installed, ``env-manifest.json`` (the resolved packages, the requirements' SHA-256, the size on disk). Delete the
directory to throw it away. torch comes only from the PyTorch CPU index; everything else from
``v2_detector_requirements.txt``. The weights are the separate, approved step ``v2_detector_weights.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENV_VERSION = "evalv2-detector-env-v1"
PYTHON_VERSION = "3.12"
TORCH_VERSION = "2.14.0"  # eval's uv.lock version, as a CPU build
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
REQUIREMENTS = Path(__file__).resolve().with_name("v2_detector_requirements.txt")
HOME_ENV = "EVALV2_DETECTOR_HOME"
MANIFEST_NAME = "env-manifest.json"
# Measured 2026-11-01 on the evaluation laptop (Intel i7-1255U, WSL2, 7 GB): a randomly initialised OWLv2 B/16 at
# 960 px (the real model's architecture and FLOPs, no weights downloaded) took 8.8-9.1 s per frame at 6 or 12
# threads, batch 1, four text queries.
MEASURED_SECONDS_PER_FRAME = 9.0
CHECK_CODE = ("import json, platform, numpy, PIL, scipy, safetensors, tokenizers, torch, transformers, huggingface_hub; "
              "print(json.dumps({'python': platform.python_version(), 'torch': torch.__version__, "
              "'torch_cuda': torch.version.cuda, 'transformers': transformers.__version__, "
              "'tokenizers': tokenizers.__version__, 'huggingface_hub': huggingface_hub.__version__, "
              "'safetensors': safetensors.__version__, 'numpy': numpy.__version__, 'pillow': PIL.__version__}))")

Runner = Callable[..., subprocess.CompletedProcess[str]]


class EnvRefused(RuntimeError):
    """The environment step will not run (no --yes, or an environment is already there)."""


def detector_home(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(HOME_ENV, "").strip()
    return Path(env) if env else Path.home() / ".cache" / "evalv2-detector"


def venv_python(home: Path) -> Path:
    return home / "venv" / "bin" / "python"


def commands(home: Path) -> list[list[str]]:
    """The install, in order: a fresh venv, torch from the CPU index only, then the pinned rest from PyPI."""
    python = str(venv_python(home))
    return [["uv", "venv", "--python", PYTHON_VERSION, str(home / "venv")],
            ["uv", "pip", "install", "--python", python, "--index-url", TORCH_INDEX, f"torch=={TORCH_VERSION}"],
            ["uv", "pip", "install", "--python", python, "-r", str(REQUIREMENTS)]]


def requirements_sha256() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def plan(home: Path) -> dict[str, Any]:
    return {"env": ENV_VERSION, "home": str(home), "torch": f"torch=={TORCH_VERSION} from {TORCH_INDEX} (CPU only)",
            "requirements": str(REQUIREMENTS), "requirements_sha256": requirements_sha256(),
            "commands": [" ".join(c) for c in commands(home)],
            "weights": "not downloaded here: scripts/v2_detector_weights.py, with the owner's approval marker",
            "installed": (home / MANIFEST_NAME).is_file()}


def check(home: Path, run: Runner = subprocess.run) -> dict[str, Any]:
    """Offline: the environment imports every package and its torch is a CPU build (no CUDA)."""
    python = venv_python(home)
    if not python.is_file():
        raise EnvRefused(f"no environment at {home} (run: install --yes)")
    done = run([str(python), "-I", "-c", CHECK_CODE], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise EnvRefused(f"the environment does not import: {done.stderr.strip()[-300:]}")
    versions = json.loads(done.stdout)
    return {**versions, "cpu_only": versions.get("torch_cuda") is None}


def _size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def install(home: Path, *, yes: bool, run: Runner = subprocess.run) -> dict[str, Any]:
    """Create the environment and record it; refuses without ``yes`` and over an existing environment."""
    if not yes:
        raise EnvRefused("installing downloads packages (about 250 MB, no weights): pass --yes once it is approved")
    if (home / MANIFEST_NAME).exists() or (home / "venv").exists():
        raise EnvRefused(f"{home} already holds an environment; delete it first to rebuild")
    home.mkdir(parents=True, exist_ok=True)
    for command in commands(home):
        done = run(command, check=False)
        if done.returncode != 0:
            raise EnvRefused(f"{' '.join(command)} exited {done.returncode}")
    versions = check(home, run)
    if not versions["cpu_only"]:
        raise EnvRefused(f"torch {versions['torch']} is not a CPU build")
    freeze = run(["uv", "pip", "freeze", "--python", str(venv_python(home))], capture_output=True, text=True,
                 check=False)
    manifest = {"env": ENV_VERSION, "created_at": datetime.now(UTC).isoformat(), "home": str(home),
                "platform": platform.platform(), "torch_index": TORCH_INDEX, "requirements_sha256":
                requirements_sha256(), "versions": versions, "freeze": (freeze.stdout or "").splitlines(),
                "bytes": _size(home / "venv")}
    (home / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def estimate(seconds_per_frame: float, halves: Sequence[str]) -> dict[str, Any]:
    """RA1's detector frames per split half (tracked manifest and split; no private data) and the CPU time."""
    eval_dir = Path(__file__).resolve().parents[1]
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    from evalv2.datasets import registry
    from evalv2.references.detector import frames_needed

    clips = {c["incident_id"]: c["duration_seconds"] for c in
             json.loads(registry.resolve("dataset-manifest-v1").path.read_text(encoding="utf-8"))["clips"]}
    split = json.loads(registry.resolve("split-v2").path.read_text(encoding="utf-8"))
    out: dict[str, Any] = {"seconds_per_frame": seconds_per_frame, "halves": {}}
    for half in halves:
        counts = frames_needed({clip: clips[clip] for clip in split[half]})
        out["halves"][half] = {**counts, "cpu_hours": round(counts["frames"] * seconds_per_frame / 3600, 1)}
    return out


def main(argv: Sequence[str] | None = None, *, run: Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", nargs="?", default="plan", choices=("plan", "install", "check", "estimate"))
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--yes", action="store_true", help="install: confirm the (approved) package download")
    parser.add_argument("--seconds-per-frame", type=float, default=MEASURED_SECONDS_PER_FRAME)
    parser.add_argument("--halves", default="dev,test")
    args = parser.parse_args(argv)
    home = detector_home(args.home)
    try:
        if args.action == "plan":
            result = plan(home)
        elif args.action == "install":
            result = install(home, yes=args.yes, run=run)
        elif args.action == "check":
            result = check(home, run)
        else:
            result = estimate(args.seconds_per_frame, [h.strip() for h in args.halves.split(",") if h.strip()])
    except EnvRefused as exc:
        print(json.dumps({"ok": False, "refused": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

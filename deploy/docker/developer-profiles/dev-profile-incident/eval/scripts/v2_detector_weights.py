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
"""The detector weights download (X5): a separate step that refuses to run without the owner's approval marker.

    <env>/venv/bin/python scripts/v2_detector_weights.py download --approval FILE [--home DIR] [--dry-run]
    <env>/venv/bin/python scripts/v2_detector_weights.py verify --weights-dir DIR

Run it with the detector environment's interpreter (``v2_detector_env.py``), which has ``huggingface_hub``; eval's
environment never imports it. ``download``:

1. refuses unless ``--approval`` names an ``evalv2-detector-weights-approval-v1`` marker for
   ``google/owlv2-base-patch16-ensemble`` with the approver, the date, a revision and a download ceiling;
2. reads the repository's metadata at that revision (the only network call before the files): the commit, the
   licence (must be Apache-2.0), and each file's size and hash;
3. selects ``model.safetensors`` and the small JSON and text files (never ``pytorch_model.bin`` or other formats),
   refusing when they pass the ceiling or when the marker's ``expected_sha256`` differs from the Hub's;
4. downloads them into ``<home>/weights/<repo>@<commit>/`` and re-hashes each: an LFS file must have the Hub's
   SHA-256 (and the marker's, when given), a small file its git blob id;
5. only then writes ``evalv2-weights.lock.json``, which the runner re-checks before every load.

``--dry-run`` prints the steps without importing ``huggingface_hub``. ``verify`` re-hashes a download against its
lock, offline.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_detector import (  # the runner's lock format and checks, shared
    LOCK_NAME,
    LOCK_VERSION,
    MODEL_LICENSE,
    MODEL_REPO,
    WEIGHTS_FILE,
    DetectorFailure,
    sha256_file,
    verify_lock,
)

APPROVAL_VERSION = "evalv2-detector-weights-approval-v1"
ALLOW = ("model.safetensors", "*.json", "*.txt")
HOME_ENV = "EVALV2_DETECTOR_HOME"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^([0-9a-f]{40}|main)$")


class Refused(RuntimeError):
    """The download will not run, or what it fetched does not verify; no lock is written."""


def read_approval(path: Path | None) -> dict[str, Any]:
    """The owner's approval marker for this exact repository, with a revision and a download ceiling."""
    if path is None:
        raise Refused("refusing: the weights download needs the owner's approval marker (--approval FILE)")
    try:
        data = Path(path).read_bytes()
        marker = json.loads(data)
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(f"approval marker {path} is unreadable: {exc}") from exc
    problems = []
    if not isinstance(marker, dict) or marker.get("approval") != APPROVAL_VERSION:
        raise Refused(f"{path} is not an {APPROVAL_VERSION} marker")
    if marker.get("repo_id") != MODEL_REPO:
        problems.append(f"the marker approves {marker.get('repo_id')!r}, not {MODEL_REPO!r}")
    if not _REVISION.match(str(marker.get("revision", ""))):
        problems.append("revision must be a 40-character commit hash or 'main'")
    for key in ("approved_by", "approved_at"):
        if not str(marker.get(key) or "").strip():
            problems.append(f"the marker must name {key}")
    ceiling = marker.get("max_download_mb")
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling <= 0:
        problems.append("max_download_mb must be a positive integer")
    expected = marker.get("expected_sha256")
    if expected is not None and not _SHA256.match(str(expected)):
        problems.append("expected_sha256, when given, is the weights file's 64-character SHA-256")
    if problems:
        raise Refused("; ".join(problems))
    return {**marker, "sha256": hashlib.sha256(data).hexdigest()}


class HubClient:
    """The only code here that touches the network: Hugging Face Hub metadata and file downloads."""

    def __init__(self) -> None:
        import huggingface_hub

        self._hub = huggingface_hub
        self.version = huggingface_hub.__version__

    def info(self, repo_id: str, revision: str) -> dict[str, Any]:
        info = self._hub.HfApi().model_info(repo_id, revision=revision, files_metadata=True)
        card = info.card_data
        license_name = (card.get("license") if card is not None else None) or next(
            (tag.split(":", 1)[1] for tag in info.tags or [] if tag.startswith("license:")), None)
        return {"commit": info.sha, "license": license_name,
                "files": [{"name": s.rfilename, "bytes": s.size, "blob_id": s.blob_id,
                           "lfs_sha256": s.lfs.sha256 if s.lfs is not None else None} for s in info.siblings or []]}

    def download(self, repo_id: str, filename: str, revision: str, local_dir: Path) -> Path:
        return Path(self._hub.hf_hub_download(repo_id, filename, revision=revision, local_dir=str(local_dir)))


def git_blob_sha1(path: Path) -> str:
    """The git object id of a small (non-LFS) file, as the Hub reports it."""
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def select_files(info: Mapping[str, Any], approval: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The files to fetch, checked against the licence, the ceiling and the marker's expected SHA-256."""
    if str(info.get("license") or "").lower() != MODEL_LICENSE:
        raise Refused(f"the repository's licence is {info.get('license')!r}, not {MODEL_LICENSE}")
    if not re.match(r"^[0-9a-f]{40}$", str(info.get("commit") or "")):
        raise Refused("the Hub reported no commit for the revision")
    files = [f for f in info["files"] if any(fnmatch.fnmatch(f["name"], pattern) for pattern in ALLOW)
             and "/" not in f["name"]]
    weights = [f for f in files if f["name"] == WEIGHTS_FILE]
    if len(weights) != 1 or not weights[0]["lfs_sha256"]:
        raise Refused(f"the revision has no LFS {WEIGHTS_FILE}")
    if any(f["bytes"] is None for f in files):
        raise Refused("the Hub did not report every file's size")
    total = sum(int(f["bytes"]) for f in files)
    if total > int(approval["max_download_mb"]) * 1024 * 1024:
        raise Refused(f"the files are {total} bytes, over the approved {approval['max_download_mb']} MB")
    expected = approval.get("expected_sha256")
    if expected and weights[0]["lfs_sha256"] != expected:
        raise Refused(f"the Hub's {WEIGHTS_FILE} is {weights[0]['lfs_sha256']}, not the approved {expected}")
    return sorted(files, key=lambda f: f["name"])


def download(approval: Mapping[str, Any], home: Path, hub: Any) -> dict[str, Any]:
    """Fetch, verify every file, then write the lock; raises :class:`Refused` (no lock) on any difference."""
    info = hub.info(MODEL_REPO, str(approval["revision"]))
    files = select_files(info, approval)
    commit = info["commit"]
    target = home / "weights" / f"{MODEL_REPO.replace('/', '--')}@{commit}"
    if (target / LOCK_NAME).exists():
        raise Refused(f"{target} already holds a verified download (verify it instead)")
    locked: dict[str, dict[str, Any]] = {}
    for entry in files:
        path = hub.download(MODEL_REPO, entry["name"], commit, target)
        size, digest = path.stat().st_size, sha256_file(path)
        if size != entry["bytes"]:
            raise Refused(f"{entry['name']}: {size} bytes, the Hub reported {entry['bytes']}")
        if entry["lfs_sha256"]:
            if digest != entry["lfs_sha256"]:
                raise Refused(f"{entry['name']}: sha256 {digest}, the Hub reported {entry['lfs_sha256']}")
        elif git_blob_sha1(path) != entry["blob_id"]:
            raise Refused(f"{entry['name']}: its git blob id is not the Hub's {entry['blob_id']}")
        locked[entry["name"]] = {"bytes": size, "sha256": digest, "hub_sha256": entry["lfs_sha256"],
                                 "git_blob_id": entry["blob_id"]}
    weights_sha256 = locked[WEIGHTS_FILE]["sha256"]
    if approval.get("expected_sha256") and weights_sha256 != approval["expected_sha256"]:
        raise Refused(f"the downloaded {WEIGHTS_FILE} is {weights_sha256}, not the approved one")
    lock = {"lock": LOCK_VERSION, "repo_id": MODEL_REPO, "revision": commit,
            "requested_revision": approval["revision"], "license": info["license"], "files": locked,
            "weights_file": WEIGHTS_FILE, "weights_sha256": weights_sha256, "approval_sha256": approval["sha256"],
            "approved_by": approval["approved_by"], "approved_at": approval["approved_at"],
            "downloaded_at": datetime.now(UTC).isoformat(), "huggingface_hub": getattr(hub, "version", None)}
    temporary = target / f".{LOCK_NAME}.tmp"
    temporary.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target / LOCK_NAME)
    return {"weights_dir": str(target), **lock}


def main(argv: Sequence[str] | None = None, *, hub_factory: Callable[[], Any] = HubClient) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    fetch = sub.add_parser("download")
    fetch.add_argument("--approval", type=Path, default=None, help=f"an {APPROVAL_VERSION} marker")
    fetch.add_argument("--home", type=Path, default=None, help=f"default ${HOME_ENV} or ~/.cache/evalv2-detector")
    fetch.add_argument("--dry-run", action="store_true")
    check = sub.add_parser("verify")
    check.add_argument("--weights-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            lock = verify_lock(args.weights_dir)
            result: dict[str, Any] = {"ok": True, "weights_sha256": lock["weights_sha256"],
                                      "revision": lock["revision"], "files": sorted(lock["files"])}
        else:
            approval = read_approval(args.approval)
            home = args.home or Path(os.environ.get(HOME_ENV, "").strip() or Path.home() / ".cache" / "evalv2-detector")
            if args.dry_run:
                result = {"dry_run": True, "repo_id": MODEL_REPO, "revision": approval["revision"],
                          "allow": list(ALLOW), "target": str(home / "weights"), "max_download_mb":
                          approval["max_download_mb"], "expected_sha256": approval.get("expected_sha256")}
            else:
                result = download(approval, home, hub_factory())
    except (Refused, DetectorFailure) as exc:
        print(json.dumps({"ok": False, "refused": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Last-green developer-channel controller (I-10 of the container release flow).

The last-green channel is the committed, immutable record of the most recent
release set that passed the full nightly acceptance matrix. It lives in
``deploy/docker/last-green.lock.json``; moving aliases (e.g. a
``develop-latest`` tag) are at most a convenience derived from it, never the
source of truth.

Rules enforced here:

* Only a verified acceptance **PASS for the exact release-set ID** advances
  the lock. The result payload must carry the tested digests, and every
  built/mirrored digest must equal the release set's digest — a pipeline that
  tested different bytes cannot advance the channel.
* **FAIL changes nothing** (action ``hold``): the previous last-green set
  stays in place.
* **Rollback is one operation**: the lock keeps a bounded history and
  ``rollback`` restores the previous accepted set.

Subcommands: ``validate-result``, ``advance``, ``rollback``.

The acceptance-result payload contract (produced by the GitLab ci-vss-oss
acceptance pipeline once I-09 lands; the controller workflow stays dormant
until then):

    {
      "schema_version": 1,
      "release_set_id": "sha256:...",
      "result": "PASS" | "FAIL",
      "failure_class": null | "product" | "infrastructure",
      "tested_images": [{"name": ..., "image": ..., "tag": ..., "digest": ...}],
      "profile_matrix": ["base", "lvs", "search", "alerts"],
      "evidence_url": "...",
      "pipeline_url": "..."
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_set import DIGEST_RE, compute_release_set_id  # noqa: E402

LOCK_FILE = Path("deploy/docker/last-green.lock.json")
HISTORY_LIMIT = 10

RELEASE_SET_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_RESULT_FIELDS = (
    "schema_version",
    "release_set_id",
    "result",
    "tested_images",
    "profile_matrix",
    "pipeline_url",
)


def validate_result(payload: dict) -> list[str]:
    """Validate the acceptance-result payload contract."""
    problems: list[str] = []
    for field in REQUIRED_RESULT_FIELDS:
        if field not in payload:
            problems.append(f"missing required field {field!r}")
    if problems:
        return problems

    if payload["schema_version"] != 1:
        problems.append("schema_version must be 1")
    if not RELEASE_SET_ID_RE.match(payload["release_set_id"]):
        problems.append("release_set_id must be sha256:<64 hex>")
    if payload["result"] not in ("PASS", "FAIL"):
        problems.append("result must be PASS or FAIL")
    if payload["result"] == "FAIL" and payload.get("failure_class") not in (
        "product",
        "infrastructure",
    ):
        problems.append("FAIL results must classify failure_class")
    if not payload["profile_matrix"]:
        problems.append("profile_matrix must be non-empty")
    for item in payload["tested_images"]:
        name = item.get("name", "<missing>")
        if not item.get("name"):
            problems.append("tested_images entry missing name")
        if not DIGEST_RE.match(item.get("digest") or ""):
            problems.append(f"tested_images[{name}]: malformed digest")
    if payload["result"] == "PASS" and not payload["tested_images"]:
        problems.append("a PASS must list the tested images")
    return problems


def check_digest_parity(payload: dict, release_set: dict) -> list[str]:
    """Every image the release set pins by digest must have been tested at
    exactly that digest."""
    problems: list[str] = []
    if payload["release_set_id"] != release_set.get("release_set_id"):
        problems.append(
            f"release-set ID mismatch: result is for {payload['release_set_id']}, "
            f"manifest is {release_set.get('release_set_id')}"
        )
    recomputed = compute_release_set_id(release_set)
    if recomputed != release_set.get("release_set_id"):
        problems.append("release-set manifest failed integrity recomputation")

    tested = {item["name"]: item.get("digest") for item in payload["tested_images"]}
    for entry in release_set.get("images", []):
        if entry.get("strategy") not in ("build", "mirror"):
            continue
        name = entry["name"]
        if name not in tested:
            problems.append(f"{name}: pinned by digest but never tested")
        elif tested[name] != entry.get("digest"):
            problems.append(
                f"{name}: tested digest {tested[name]} != release-set digest "
                f"{entry.get('digest')}"
            )
    return problems


def empty_lock() -> dict:
    return {
        "schema_version": 1,
        "release_set_id": None,
        "source_commit": None,
        "advanced_at": None,
        "images": [],
        "history": [],
    }


def load_lock(path: Path) -> dict:
    if not path.exists():
        return empty_lock()
    return json.loads(path.read_text())


def advance_lock(
    lock: dict, payload: dict, release_set: dict, timestamp: str
) -> tuple[str, dict, list[str]]:
    """Return ``(action, new_lock, problems)`` where action is ``advance`` |
    ``hold`` | ``reject``. Pure function; callers handle I/O."""
    problems = validate_result(payload)
    if problems:
        return "reject", lock, problems

    if payload["result"] == "FAIL":
        # Failure never mutates the channel.
        return "hold", lock, []

    problems = check_digest_parity(payload, release_set)
    if problems:
        return "reject", lock, problems

    if lock.get("release_set_id") == payload["release_set_id"]:
        return "hold", lock, []  # already current; idempotent

    new_lock = {
        "schema_version": 1,
        "release_set_id": release_set["release_set_id"],
        "source_commit": release_set["source"]["commit"],
        "advanced_at": timestamp,
        "acceptance": {
            "pipeline_url": payload.get("pipeline_url"),
            "evidence_url": payload.get("evidence_url"),
            "profile_matrix": payload.get("profile_matrix"),
        },
        "images": release_set["images"],
        "history": (
            [
                {
                    "release_set_id": lock["release_set_id"],
                    "source_commit": lock.get("source_commit"),
                    "advanced_at": lock.get("advanced_at"),
                    "acceptance": lock.get("acceptance"),
                    "images": lock.get("images", []),
                }
            ]
            if lock.get("release_set_id")
            else []
        )
        + lock.get("history", []),
    }
    new_lock["history"] = new_lock["history"][:HISTORY_LIMIT]
    return "advance", new_lock, []


def rollback_lock(lock: dict) -> tuple[dict, list[str]]:
    """Restore the previous accepted set (one tested operation)."""
    history = lock.get("history", [])
    if not history:
        return lock, ["nothing to roll back to: history is empty"]
    previous, *rest = history
    restored = {
        "schema_version": 1,
        "release_set_id": previous["release_set_id"],
        "source_commit": previous.get("source_commit"),
        "advanced_at": previous.get("advanced_at"),
        "acceptance": previous.get("acceptance"),
        "images": previous.get("images", []),
        "history": rest,
    }
    return restored, []


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def cmd_validate_result(args: argparse.Namespace) -> int:
    payload = json.loads(args.file.read_text())
    problems = validate_result(payload)
    if problems:
        print("FAIL: invalid acceptance result:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"OK: valid acceptance result ({payload['result']}).")
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    payload = json.loads(args.result.read_text())
    release_set = json.loads(args.release_set.read_text())
    lock = load_lock(args.lock)
    action, new_lock, problems = advance_lock(
        lock, payload, release_set, args.timestamp
    )
    if action == "reject":
        print("FAIL: acceptance result rejected:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if action == "hold":
        print(
            "HOLD: channel unchanged "
            f"(current: {lock.get('release_set_id') or '<none>'})."
        )
        return 0
    _write_json(args.lock, new_lock)
    print(
        f"ADVANCED: last-green is now {new_lock['release_set_id']} "
        f"(source {new_lock['source_commit'][:12]})."
    )
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    lock = load_lock(args.lock)
    restored, problems = rollback_lock(lock)
    if problems:
        print("FAIL: " + "; ".join(problems), file=sys.stderr)
        return 1
    _write_json(args.lock, restored)
    print(f"ROLLED BACK: last-green is now {restored['release_set_id']}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-result", help="validate a result payload")
    validate.add_argument("--file", type=Path, required=True)

    advance = sub.add_parser("advance", help="advance or hold the channel")
    advance.add_argument("--result", type=Path, required=True)
    advance.add_argument("--release-set", type=Path, required=True)
    advance.add_argument("--lock", type=Path, default=LOCK_FILE)
    advance.add_argument(
        "--timestamp", required=True, help="ISO-8601 UTC, e.g. from date -u"
    )

    rollback = sub.add_parser("rollback", help="restore the previous accepted set")
    rollback.add_argument("--lock", type=Path, default=LOCK_FILE)

    args = parser.parse_args()
    return {
        "validate-result": cmd_validate_result,
        "advance": cmd_advance,
        "rollback": cmd_rollback,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

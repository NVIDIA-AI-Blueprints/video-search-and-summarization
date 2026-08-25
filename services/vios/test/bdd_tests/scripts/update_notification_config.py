# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Apply the BDD webhook receivers to a VST notification_config.json.

The receivers, including every custom-body template test case, are declared in
data/webhook_bdd_config.json (the single source of truth shared with
tests/notification/test_webhook_custom_body.py). This script merges them into
the deployed notification config:

- webhooks.enabled is set to true.
- One webhook item per event group, matched by its BDD id (wh-001..wh-003);
  created when absent, enabled when present. Other items are left untouched.
- Inside a BDD item, requests are matched by URL: managed requests are
  rewritten in place, stale requests pointing at the BDD receiver base URL are
  pruned, and any other receiver is preserved.

Usage (from test/bdd_tests):

    python3 scripts/update_notification_config.py \
        --notification-config ../../deployment/stream-processing/docker-compose/configs/notification_config.json

VST reads this file at startup, so restart VST after applying it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

BDD_TESTS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BDD_CONFIG = BDD_TESTS_DIR / "data" / "webhook_bdd_config.json"
DEFAULT_NOTIFICATION_CONFIG = (
    BDD_TESTS_DIR.parent.parent
    / "deployment"
    / "stream-processing"
    / "docker-compose"
    / "configs"
    / "notification_config.json"
)


def build_desired_requests(bdd_config: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Return the managed request entries keyed by event group."""
    base_url = bdd_config["receiver_base_url"]
    defaults = bdd_config["request_defaults"]
    receivers = list(bdd_config["standard_receivers"])
    cases = bdd_config["custom_body_cases"]
    receivers.extend(cases["valid"])
    receivers.extend(cases["invalid"])

    desired: Dict[str, List[Dict[str, Any]]] = {}
    for receiver in receivers:
        request = {
            "url": base_url + receiver["path"],
            "method": receiver["method"],
            **json.loads(json.dumps(defaults)),
        }
        # A case may override the shared defaults (e.g. receiver-specific
        # static query_params) or carry per-request extras.
        for key in ("headers", "query_params", "camera_type", "body", "user_defined_metadata"):
            if key in receiver:
                request[key] = receiver[key]
        desired.setdefault(receiver["event_group"], []).append(request)
    return desired


def sync_item_requests(
    item: Dict[str, Any], desired: List[Dict[str, Any]], base_url: str
) -> None:
    """Rewrite managed requests by URL, prune stale BDD URLs, keep the rest."""
    desired_by_url = {request["url"]: request for request in desired}
    existing = item.get("request", [])
    if not isinstance(existing, list):
        existing = []

    merged: List[Dict[str, Any]] = []
    for request in existing:
        url = request.get("url") if isinstance(request, dict) else None
        if url in desired_by_url:
            merged.append(desired_by_url.pop(url))
        elif isinstance(url, str) and url.startswith(base_url + "/bdd/webhooks/"):
            continue  # stale managed receiver, e.g. a renamed test case
        else:
            merged.append(request)  # user-configured receiver, preserved
    merged.extend(desired_by_url.values())
    item["request"] = merged


def apply(bdd_config: Dict[str, Any], notification_config: Dict[str, Any]) -> List[str]:
    """Merge the BDD receivers into the notification config; return a summary."""
    summary: List[str] = []
    webhooks = notification_config.setdefault("webhooks", {})
    webhooks["enabled"] = True
    items = webhooks.setdefault("items", [])

    desired_requests = build_desired_requests(bdd_config)
    base_url = bdd_config["receiver_base_url"]

    for event_group, group in bdd_config["event_groups"].items():
        item = next((entry for entry in items if entry.get("id") == group["id"]), None)
        if item is None:
            item = {"enabled": True, "id": group["id"], "camera_status_change": event_group}
            items.append(item)
            summary.append(f"created item {group['id']} ({event_group})")
        else:
            summary.append(f"updated item {group['id']} ({event_group})")
        item["enabled"] = True
        item["camera_status_change"] = event_group
        # The in-process BDD receiver does not verify signatures; an unresolved
        # {{secrets.*}} placeholder only adds a failure path.
        item.pop("auth", None)
        sync_item_requests(item, desired_requests.get(event_group, []), base_url)
        summary.append(
            f"  {group['id']}: {len(item['request'])} receivers "
            f"({len(desired_requests.get(event_group, []))} BDD-managed)"
        )
    return summary


def check_webhook_paths(bdd_config: Dict[str, Any]) -> List[str]:
    """Warn when config.json webhook_paths diverges from the standard receivers."""
    config_path = BDD_TESTS_DIR / "config.json"
    try:
        test_config = json.loads(config_path.read_text())
        paths = test_config["tests"]["notification_tests"]["test_parameters"]["webhook_paths"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        return [f"warning: could not cross-check {config_path}: {exc}"]

    declared = {receiver["path"] for receiver in bdd_config["standard_receivers"]}
    missing = sorted(set(paths.values()) - declared)
    if missing:
        return [
            "warning: webhook_paths in config.json not declared as standard_receivers "
            f"in {DEFAULT_BDD_CONFIG.name}: {missing}"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--bdd-config",
        type=Path,
        default=DEFAULT_BDD_CONFIG,
        help="Receiver declarations (default: data/webhook_bdd_config.json)",
    )
    parser.add_argument(
        "--notification-config",
        type=Path,
        default=DEFAULT_NOTIFICATION_CONFIG,
        help="VST notification config to update (default: the docker-compose config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merged config to stdout instead of writing the file",
    )
    args = parser.parse_args()

    try:
        bdd_config = json.loads(args.bdd_config.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read BDD config {args.bdd_config}: {exc}", file=sys.stderr)
        return 1
    try:
        notification_config = json.loads(args.notification_config.read_text())
    except OSError as exc:
        print(f"error: cannot read {args.notification_config}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(
            f"error: {args.notification_config} is not valid JSON ({exc}); "
            "fix or restore it before applying the BDD receivers",
            file=sys.stderr,
        )
        return 1

    summary = apply(bdd_config, notification_config)
    rendered = json.dumps(notification_config, indent="\t") + "\n"
    json.loads(rendered)  # self-check before touching the file

    if args.dry_run:
        print(rendered)
    else:
        args.notification_config.write_text(rendered)

    for line in summary:
        print(line)
    for line in check_webhook_paths(bdd_config):
        print(line)
    if not args.dry_run:
        print(f"wrote {args.notification_config}")
        print("restart VST if it is running; the config is read at startup")
    return 0


if __name__ == "__main__":
    sys.exit(main())

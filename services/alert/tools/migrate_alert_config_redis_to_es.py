#!/usr/bin/env python3
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

"""One-time Redis → Elasticsearch migration for alert verification configs.

Context:
    Elasticsearch is now the source of truth for alert
    verification configuration. Before the persistence layer landed,
    operators who tuned alert processing via the API stored those
    settings in Redis only. When the new code boots against an empty ES
    index, hydration copies *from* ES *into* Redis, which would wipe the
    operator-tuned values.

    Run this script once, immediately after deploying the new code, to
    seed ES from the current Redis state so hydration has something to
    propagate back.

Properties:
    * Idempotent — uses ``set_if_absent`` so re-runs never clobber ES.
    * Read-only against Redis; no keys are deleted or modified.
    * Safe to run while the service is live, but prefer a maintenance
      window: concurrent PUTs to the API could race with the migration
      and leave the newer record in Redis only.

Usage::

    python -m tools.migrate_alert_config_redis_to_es [--config config.yaml] [--dry-run]

Exit codes:
    0   success (migrated + skipped + 0 errors)
    1   one or more records failed to migrate
    2   fatal configuration / connectivity error
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Dict, Iterator, Tuple

import yaml

from handlers.alert_config import ALERT_CONFIG_COLLECTION, ESAlertConfigStore
from handlers.alert_config.normalize import normalize_alert_type
from its_redis.dynamic_prompt_handler import RedisClient
from persistence import create_persistence_store

logger = logging.getLogger(__name__)


REDIS_KEY_PREFIX = "alert_config:"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed Elasticsearch with alert configs currently in Redis."
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Enumerate Redis records without writing to Elasticsearch.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Emit DEBUG-level logs.",
    )
    return parser.parse_args(argv)


def iter_redis_configs(
    redis_client, prefix: str = REDIS_KEY_PREFIX
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield ``(alert_type, record)`` for every ``prefix*`` key in Redis."""
    raw_keys = redis_client.keys(f"{prefix}*")
    for raw in raw_keys:
        key = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        record = redis_client.json().get(key)
        if not record:
            continue
        # Prefer the record's own ``alert_type`` so we never diverge from
        # what the running service stored; fall back to the key suffix
        # for legacy records that omitted the field.
        alert_type = record.get("alert_type") or key.split(":", 1)[1]
        normalized = normalize_alert_type(alert_type)
        # Stamp the field back into the record before yielding —
        # ``ESAlertConfigStore.get_all`` filters by the ``alert_type``
        # field on each doc, so a legacy record migrated as-is would
        # land in ES under the right doc id but get silently skipped
        # by every list / hydration call (visible only to get-by-id).
        # Migration would report success while leaving the record
        # half-invisible.
        if record.get("alert_type") != normalized:
            record = {**record, "alert_type": normalized}
        yield normalized, record


def migrate(
    redis_client,
    es_store: ESAlertConfigStore,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Copy Redis records into ES via ``set_if_absent``. Returns stats."""
    stats = {"total": 0, "migrated": 0, "skipped": 0, "errors": 0}
    for alert_type, record in iter_redis_configs(redis_client):
        stats["total"] += 1
        if dry_run:
            logger.info("[dry-run] would migrate alert_type=%s", alert_type)
            stats["migrated"] += 1
            continue
        try:
            created = es_store.set_if_absent(alert_type, record)
        except Exception:
            logger.exception("Migration failed for alert_type=%s", alert_type)
            stats["errors"] += 1
            continue
        if created:
            stats["migrated"] += 1
            logger.info("Migrated alert_type=%s", alert_type)
        else:
            stats["skipped"] += 1
            logger.info("Skipped alert_type=%s (already in ES)", alert_type)
    return stats


def _build_es_store(config_path: str) -> ESAlertConfigStore:
    with open(config_path, "r") as fh:
        app_config = yaml.safe_load(fh) or {}
    persistence = create_persistence_store(app_config)
    if persistence is None:
        raise RuntimeError(
            "Persistence layer is disabled or unreachable. Enable it in "
            f"{config_path} and ensure Elasticsearch is accessible before "
            "running this migration."
        )
    if not persistence.health():
        raise RuntimeError(
            "Elasticsearch ping failed — refusing to migrate against an "
            "unhealthy backend."
        )
    return ESAlertConfigStore(persistence)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        redis_client = RedisClient(args.config).redis_client
        es_store = _build_es_store(args.config)
    except Exception:
        logger.exception("Fatal setup error; migration aborted")
        return 2

    stats = migrate(redis_client, es_store, dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(
        "[%s] target collection=%s  total=%d migrated=%d skipped=%d errors=%d",
        mode, ALERT_CONFIG_COLLECTION,
        stats["total"], stats["migrated"], stats["skipped"], stats["errors"],
    )
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

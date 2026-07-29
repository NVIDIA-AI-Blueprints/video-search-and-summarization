#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate an atomic, one-time migration for the retired lease database."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from typing import Any

MIGRATION_NAME = "legacy-inventory-v1"
GPU_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_inventory(path: pathlib.Path) -> list[dict[str, Any]]:
    inventory = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(inventory, list):
        raise SystemExit("legacy lease inventory is not a JSON list")

    seen_gpu_ids: set[str] = set()
    for worker in inventory:
        if not isinstance(worker, dict):
            raise SystemExit("legacy lease inventory contains a non-object entry")
        gpu_id = worker.get("gpu_id")
        if not isinstance(gpu_id, str) or not GPU_ID_PATTERN.fullmatch(gpu_id):
            raise SystemExit(f"invalid legacy gpu_id: {gpu_id!r}")
        if gpu_id in seen_gpu_ids:
            raise SystemExit(f"duplicate legacy gpu_id: {gpu_id}")
        seen_gpu_ids.add(gpu_id)
        if worker.get("live"):
            raise SystemExit(f"refusing to migrate a live lease: {gpu_id}")
        generation = worker.get("generation")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            raise SystemExit(f"invalid generation for {gpu_id}")
        metadata = worker.get("metadata", {})
        if not isinstance(metadata, dict):
            raise SystemExit(f"invalid metadata for {gpu_id}")
    return inventory


def build_sql(inventory: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        inventory,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    source_sha256 = hashlib.sha256(canonical).hexdigest()
    statements = [
        "-- Generated one-time migration from the fenced legacy lease database.",
        "CREATE TABLE IF NOT EXISTS public.skill_eval_migrations (",
        "    migration_name text PRIMARY KEY,",
        "    source_sha256 text NOT NULL CHECK (length(source_sha256) = 64),",
        "    applied_at timestamptz NOT NULL DEFAULT statement_timestamp()",
        ");",
        "ALTER TABLE public.skill_eval_migrations OWNER TO skill_eval_owner;",
        "REVOKE ALL ON TABLE public.skill_eval_migrations FROM PUBLIC;",
        "REVOKE ALL ON TABLE public.skill_eval_migrations FROM skill_eval_lease, skill_eval_fence;",
        "BEGIN;",
        (
            "LOCK TABLE public.skill_eval_migrations, public.gpu_workers, "
            "public.gpu_leases IN ACCESS EXCLUSIVE MODE;"
        ),
        "DO $legacy_migration$",
        "DECLARE",
        "    applied_sha256 text;",
        "BEGIN",
        (
            "    SELECT source_sha256 INTO applied_sha256 "
            "FROM public.skill_eval_migrations "
            f"WHERE migration_name = {quote(MIGRATION_NAME)};"
        ),
        "    IF FOUND THEN",
        f"        IF applied_sha256 <> {quote(source_sha256)} THEN",
        "            RAISE EXCEPTION 'legacy migration already applied from a different snapshot';",
        "        END IF;",
        "        RETURN;",
        "    END IF;",
        "",
        "    IF EXISTS (SELECT 1 FROM public.gpu_workers) THEN",
        (
            "        RAISE EXCEPTION "
            "'refusing unmarked legacy migration into a non-empty worker inventory';"
        ),
        "    END IF;",
        "    IF EXISTS (",
        "        SELECT 1 FROM public.gpu_leases",
        "        WHERE owner_id IS NOT NULL",
        "           OR lease_token IS NOT NULL",
        "           OR lease_expires_at > statement_timestamp()",
        "    ) THEN",
        "        RAISE EXCEPTION 'refusing legacy migration while a lease may be active';",
        "    END IF;",
    ]

    for worker in inventory:
        gpu_id = worker["gpu_id"]
        enabled = "true" if worker.get("enabled") else "false"
        metadata = json.dumps(
            worker.get("metadata", {}),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        generation = worker["generation"]
        statements.extend(
            [
                "",
                (
                    "    INSERT INTO public.gpu_workers "
                    "(gpu_id, enabled, fence_ready, metadata)"
                ),
                (
                    f"    VALUES ({quote(gpu_id)}, {enabled}, false, "
                    f"{quote(metadata)}::jsonb);"
                ),
                "    UPDATE public.gpu_leases",
                f"    SET generation = {generation},",
                "        renewed_at = statement_timestamp(),",
                "        lease_expires_at = statement_timestamp()",
                f"    WHERE gpu_id = {quote(gpu_id)};",
            ]
        )

    statements.extend(
        [
            "",
            (
                "    INSERT INTO public.skill_eval_migrations "
                "(migration_name, source_sha256)"
            ),
            f"    VALUES ({quote(MIGRATION_NAME)}, {quote(source_sha256)});",
            "END",
            "$legacy_migration$;",
            "COMMIT;",
        ]
    )
    return "\n".join(statements) + "\n"


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"Usage: {sys.argv[0]} INVENTORY_JSON OUTPUT_SQL")
    inventory_path = pathlib.Path(sys.argv[1])
    output_path = pathlib.Path(sys.argv[2])
    output_path.write_text(
        build_sql(load_inventory(inventory_path)),
        encoding="utf-8",
    )
    output_path.chmod(0o600)


if __name__ == "__main__":
    main()

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Local benchmark provenance, without contacting inference or loading annotation crops."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def capture_provenance(
    cfg: Any, queries: list[str], query_ids: list[str | None] | None = None
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[5]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    def file_identity(filename):
        if not filename:
            return {"status": "not supplied"}
        path = Path(filename)
        if not path.is_file():
            return {"path": str(path), "status": "unavailable"}
        return {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    preprocessing: dict[str, Any] = {
        "status": "unknown; no ingestion preprocessing provenance supplied"
    }
    preprocessing_path = getattr(cfg, "preprocessing_provenance", None)
    if preprocessing_path:
        try:
            preprocessing = json.loads(Path(preprocessing_path).read_text())
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Invalid preprocessing provenance: {exc}") from exc
        if not isinstance(preprocessing, dict):
            raise SystemExit("Preprocessing provenance must be a JSON object")
    gallery_path = Path(cfg.gallery_gt) if cfg.gallery_gt else None
    if gallery_path and gallery_path.is_dir():
        dataset_identity = {
            name: file_identity(gallery_path / name)
            for name in (
                "gt/queries_gt.json",
                "additional_gt/clips_gt.json",
                "additional_gt/tracks_pas_attributes.json",
            )
        }
    else:
        dataset_identity = file_identity(cfg.gallery_gt)
        if not cfg.gallery_gt and cfg.data_dir and cfg.dataset is not None:
            from flows.dataset import DATASETS

            relative = DATASETS.get(cfg.dataset, {}).get(cfg.subset or "")
            if relative:
                dataset_identity = file_identity(Path(cfg.data_dir) / relative)
    source_hasher = hashlib.sha256()
    source_paths = [Path(__file__).resolve().parents[1] / "run_eval_flows.py"]
    for directory in (
        Path(__file__).resolve().parent,
        root / "libs/vss/cli/src",
        root / "libs/vss/core/src",
    ):
        source_paths.extend(directory.rglob("*.py"))
    for source in sorted(set(source_paths)):
        source_hasher.update(str(source.relative_to(root)).encode())
        source_hasher.update(b"\0")
        source_hasher.update(source.read_bytes())
        source_hasher.update(b"\0")
    source_identity = {
        "revision": revision.stdout.strip() if revision.returncode == 0 else "unknown",
        "implementation_sha256": source_hasher.hexdigest(),
    }
    return {
        "source_identity": source_identity,
        "dataset_identity": dataset_identity,
        "revision": revision.stdout.strip() if revision.returncode == 0 else "unknown",
        "working_tree_dirty": bool(dirty.stdout) if dirty.returncode == 0 else None,
        "dataset": cfg.dataset,
        "subset": cfg.subset,
        "query_domain": cfg.query_domain,
        "query_count": len(queries),
        "query_cohort_sha256": hashlib.sha256(
            json.dumps(
                list(zip(query_ids, queries)) if query_ids is not None else queries,
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
        "ingestion_identity": file_identity(cfg.ingestion_manifest or cfg.ingest_in),
        "deployed_models": {
            "status": "unknown; record configured deployment model IDs with the ingestion provenance"
        },
        "preprocessing": preprocessing,
        "preprocessing_comparison_requirement": "Re-ingest the unchanged gallery after preprocessing changes before attributing quality changes to them.",
    }

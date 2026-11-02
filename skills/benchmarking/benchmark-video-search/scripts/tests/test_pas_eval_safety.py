# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Destructive reset containment and deployed source-attribution contracts."""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import ingest_clip_gallery
import reset_clip_gallery


def test_owned_storage_accepts_real_directories_inside_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "data_log/elastic/data").mkdir(parents=True)
    resolved = reset_clip_gallery.owned_storage(runtime, "data_log/elastic/data")
    assert resolved == (runtime / "data_log/elastic/data").resolve()


@pytest.mark.parametrize(
    "link", ["data_log", "data_log/elastic", "data_log/elastic/data"]
)
def test_owned_storage_rejects_links_that_would_escape_runtime(tmp_path, link):
    runtime = tmp_path / "runtime"
    victim = tmp_path / "victim"
    (victim / "elastic/data").mkdir(parents=True)
    (victim / "data").mkdir(parents=True, exist_ok=True)
    target = runtime / link
    target.parent.mkdir(parents=True, exist_ok=True)
    # Point each path component at an outside directory with the same shape.
    target.symlink_to(
        {"data_log": victim, "data_log/elastic": victim / "elastic"}.get(
            link, victim / "data"
        )
    )
    with pytest.raises(ValueError, match="symbolic link"):
        reset_clip_gallery.owned_storage(runtime, "data_log/elastic/data")


def test_owned_storage_rejects_a_linked_runtime_and_the_runtime_itself(tmp_path):
    real = tmp_path / "real"
    (real / "data").mkdir(parents=True)
    linked = tmp_path / "runtime"
    linked.symlink_to(real)
    with pytest.raises(ValueError, match="symbolic link"):
        reset_clip_gallery.owned_storage(linked, "data")
    with pytest.raises(ValueError, match="inside the owned runtime"):
        reset_clip_gallery.owned_storage(real, ".")


def report(by_source, index="mdx-raw-2025-01-01"):
    return {
        "sensors": [{"sensor_id": "uuid-a", "name": "CHAD_1_086_1_4"}],
        "indices": {index: {"by_source": by_source}, "other-index": {"uuid": "x"}},
    }


def test_attribution_accepts_registered_ids_and_names():
    ingest_clip_gallery.require_registered_attribution(
        report({"CHAD_1_086_1_4": 150, "uuid-a": 3})
    )


@pytest.mark.parametrize("source", ["0", "None", "another-camera"])
@pytest.mark.parametrize("index", ["mdx-raw-2025-01-01", "mdx-behavior-2025-01-01"])
def test_attribution_rejects_slot_missing_or_foreign_sources(source, index):
    with pytest.raises(RuntimeError, match="unregistered sources"):
        ingest_clip_gallery.require_registered_attribution(
            report({"CHAD_1_086_1_4": 150, source: 2}, index)
        )


def test_attribution_ignores_indices_rt_cv_does_not_write():
    ingest_clip_gallery.require_registered_attribution(
        report({"None": 5}, "mdx-embed-filtered-2025-01-01")
    )

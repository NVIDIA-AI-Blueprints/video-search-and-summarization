"""Unit tests for the real-execution building blocks that don't need a GPU
or docker: KPI threshold sweep math, workspace CSV validation/leakage, and
SQLite store persistence across a simulated restart."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.execution import kpi
from app.execution.workspace import (
    Workspace,
    WorkspaceError,
    leakage_pairs,
    normalize_label,
    read_rows,
    write_rows,
)
from app.schemas.config_models import DataPoolConfig, ExperimentConfig, ModelConfig
from app.store import RunStore

# ---------------------------------------------------------------------------
# KPI math
# ---------------------------------------------------------------------------

def _write_inference_csv(path: Path, pass_scores: list[float], defect_scores: list[float]) -> None:
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["label", "siamese_score"])
        writer.writeheader()
        for s in pass_scores:
            writer.writerow({"label": "PASS", "siamese_score": s})
        for s in defect_scores:
            writer.writerow({"label": "Missing", "siamese_score": s})


def test_kpi_far_at_recall_100_uses_min_defect_score(tmp_path):
    p = tmp_path / "inference.csv"
    # 10 PASS spread 0.0..0.9; defects at 0.5/0.8/0.9 -> threshold=0.5,
    # PASS >= 0.5 are {0.5,0.6,0.7,0.8,0.9} = 5/10 = 50% FAR
    _write_inference_csv(p, [i / 10 for i in range(10)], [0.5, 0.8, 0.9])
    a = kpi.analyze(p)
    assert a.threshold_100 == pytest.approx(0.5)
    assert a.far_pct_100 == pytest.approx(50.0)
    assert a.n_pass == 10 and a.n_defect == 3


def test_kpi_auc_perfect_and_random(tmp_path):
    perfect = tmp_path / "perfect.csv"
    _write_inference_csv(perfect, [0.1, 0.2, 0.3], [0.7, 0.8, 0.9])
    assert kpi.analyze(perfect).auc == pytest.approx(1.0)

    coin = tmp_path / "coin.csv"
    _write_inference_csv(coin, [0.4, 0.6], [0.4, 0.6])  # fully tied/interleaved
    assert kpi.analyze(coin).auc == pytest.approx(0.5)


def test_kpi_requires_defects(tmp_path):
    p = tmp_path / "no_defects.csv"
    _write_inference_csv(p, [0.1, 0.2], [])
    with pytest.raises(ValueError):
        kpi.analyze(p)


# ---------------------------------------------------------------------------
# workspace CSV rules
# ---------------------------------------------------------------------------

def test_normalize_label_preserves_pass_sentinel():
    assert normalize_label("PASS") == "PASS"
    assert normalize_label(" Missing ") == "missing"
    assert normalize_label("Excess_Solder") == "excess_solder"


def test_workspace_allowed_root_accepts_descendant(tmp_path):
    """A deployment boundary permits only its own workspace tree."""
    allowed = tmp_path / "runtime"
    workspace = allowed / "run-1" / "workspace"
    workspace.mkdir(parents=True)

    assert Workspace.resolve(str(workspace), allowed_root=allowed).root == workspace


def test_workspace_allowed_root_rejects_sibling(tmp_path):
    """A sibling path cannot become a host bind through the Docker socket."""
    allowed = tmp_path / "runtime"
    allowed.mkdir()
    sibling = tmp_path / "other" / "workspace"
    sibling.mkdir(parents=True)

    with pytest.raises(WorkspaceError, match="outside the allowed workspace root"):
        Workspace.resolve(str(sibling), allowed_root=allowed)


def test_workspace_allowed_root_resolves_symlinks(tmp_path):
    """A symlink inside the boundary cannot escape to another host directory."""
    allowed = tmp_path / "runtime"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (allowed / "workspace-link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError, match="outside the allowed workspace root"):
        Workspace.resolve(str(allowed / "workspace-link"), allowed_root=allowed)


def test_leakage_detects_shared_rows():
    row = {"input_path": "a", "golden_path": "g", "label": "PASS", "object_name": "o", "boardname": "b"}
    other = dict(row, object_name="o2")
    assert leakage_pairs([row, other], [row]) == 1
    assert leakage_pairs([other], [row]) == 0


def test_write_then_read_roundtrip(tmp_path):
    p = tmp_path / "rows.csv"
    fields = ["input_path", "golden_path", "label", "object_name"]
    rows = [{"input_path": "x", "golden_path": "y", "label": "PASS", "object_name": "o", "_extra": "dropped"}]
    write_rows(p, fields, rows)
    got_fields, got_rows = read_rows(p)
    assert got_fields == fields
    assert got_rows[0]["input_path"] == "x"
    assert "_extra" not in got_rows[0]


# ---------------------------------------------------------------------------
# store persistence across restart
# ---------------------------------------------------------------------------

def _config() -> ExperimentConfig:
    return ExperimentConfig(
        model=ModelConfig(name="m", checkpoint="/tmp/ckpt.pth"),
        data_pool=DataPoolConfig(path="/tmp/ws"),
    )


def test_store_survives_restart_and_marks_orphans(tmp_path):
    db = str(tmp_path / "store.db")
    store1 = RunStore(db_path=db)
    record = store1.create(created_by="alice", workflow="aoi_active_learning", resolved_config=_config())
    record.state = "running"
    store1.save(record)

    # "restart": a fresh store over the same file must see the run and mark
    # the mid-flight one failed (its driving task died with the old process)
    store2 = RunStore(db_path=db)
    loaded = store2.get(record.id)
    assert loaded is not None
    assert loaded.created_by == "alice"
    assert store2.recover_orphans() == 1
    assert store2.get(record.id).state == "failed"
    assert "restarted" in store2.get(record.id).error.message


def test_store_idempotency_persists(tmp_path):
    db = str(tmp_path / "store.db")
    store1 = RunStore(db_path=db)
    record = store1.create(created_by="alice", workflow="aoi_active_learning", resolved_config=_config())
    store1.put_idempotent("key-1", "hash-1", record.id)

    store2 = RunStore(db_path=db)
    entry = store2.get_idempotent("key-1")
    assert entry is not None and entry.run_id == record.id and entry.body_hash == "hash-1"


def test_store_delete_removes_from_disk(tmp_path):
    db = str(tmp_path / "store.db")
    store1 = RunStore(db_path=db)
    record = store1.create(created_by="alice", workflow="aoi_active_learning", resolved_config=_config())
    store1.delete(record.id)
    store2 = RunStore(db_path=db)
    assert store2.get(record.id) is None


def test_sha256_file_matches_hashlib_and_none_for_non_file(tmp_path):
    import hashlib

    from app.execution.real_runner import _sha256_file

    ckpt = tmp_path / "model.pth"
    ckpt.write_bytes(b"changenet-weights-bytes")
    # Digest matches hashlib over the exact bytes MM downloads + verifies.
    assert _sha256_file(ckpt) == hashlib.sha256(b"changenet-weights-bytes").hexdigest()
    # None (not a crash) when the path is missing or a directory — single-file only.
    assert _sha256_file(tmp_path / "missing.pth") is None
    assert _sha256_file(tmp_path) is None

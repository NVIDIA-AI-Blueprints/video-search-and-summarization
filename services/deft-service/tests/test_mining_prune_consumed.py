"""Consumed candidates must leave the pool BEFORE the top-N cut.

`mining_pool.csv` is never rewritten, so a candidate that has already been
mined stays a nearest neighbour forever. Filtering it only after the top-N
truncation lets it retake its slot every iteration and be discarded again —
the shortlist fills with rows already in the training set and the stage mines
nothing while still reporting success. Observed live: 16 consecutive
zero-yield iterations (~12 h GPU) with 18 eligible candidates never ranked.

Run: uv run pytest tests/test_mining_prune_consumed.py
"""

from __future__ import annotations

import json
import types

import app.execution.aoi_pipeline as ap
from app.config import CONTAINER_WORKSPACE
from app.execution.workspace import Workspace, write_rows

FIELDS = ["input_path", "golden_path", "label", "object_name", "boardname"]


def _row(obj: str, img: str) -> dict:
    return {"input_path": img, "golden_path": "g", "label": "PASS",
            "object_name": obj, "boardname": "b"}


def test_consumed_candidate_does_not_occupy_the_shortlist(monkeypatch, tmp_path):
    ws = Workspace(tmp_path)
    (ws.root / "kpi/images/t").mkdir(parents=True)
    (ws.root / "kpi/images/t/T@1_SolderLight.jpg").write_bytes(b"x")
    ws.pool_dir.mkdir(parents=True, exist_ok=True)
    # NEAREST is already consumed; FAR is not. topn=1 => only one can be ranked.
    write_rows(ws.pool_csv, FIELDS, [_row("NEAR@1", "images/a.jpg"),
                                     _row("FAR@1", "images/b.jpg")])

    def fake_tao(args, **kw):
        pool_dir_c = ws.container(ws.pool_dir)
        json_path = args[-1].replace(CONTAINER_WORKSPACE, str(ws.root))
        with open(json_path, "w") as fh:
            json.dump({
                "targets": [{"path": "t", "emb": [1.0, 0.0]}],
                "pool": [
                    {"path": f"{pool_dir_c}/images/a.jpg", "emb": [1.0, 0.0]},    # sim 1.00
                    {"path": f"{pool_dir_c}/images/b.jpg", "emb": [0.97, 0.24]},  # sim ~0.97
                ],
            }, fh)

    ctx = types.SimpleNamespace(
        ws=ws,
        iter_dir=lambda name: tmp_path / name,
        current_iteration=2,
        mining_targets=[{"input_path": "kpi/images/t", "object_name": "T@1"}],
        tao=fake_tao,
        cfg=types.SimpleNamespace(mining=types.SimpleNamespace(min_similarity=0.9, topn=1)),
        csv_fieldnames=FIELDS,
        val_rows_norm=[],
        combined_rows=[{"input_path": "augmentation/mining_pool/images", "object_name": "NEAR@1",
                        "_provenance": "mining_pool"}],
        mined_rows_this_iter=[],
    )

    ap.data_mining(ctx)

    mined = [r["object_name"] for r in ctx.mined_rows_this_iter]
    assert mined == ["FAR@1"], (
        "the consumed nearest neighbour took the only top-N slot and was then "
        f"discarded, so nothing was mined: {mined}"
    )

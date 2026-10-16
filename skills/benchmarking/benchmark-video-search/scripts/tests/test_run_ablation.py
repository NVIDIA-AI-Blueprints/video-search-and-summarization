# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_ablation as abl


def test_extract_metric_flat_and_nested():
    assert abl.extract_metric({"mAP@10": 0.61}) == pytest.approx(0.61)
    assert abl.extract_metric({"overall": {"mAP@10": 0.55}}) == pytest.approx(0.55)
    assert abl.extract_metric({"summary": {"mAP@10": 0.5}}) == pytest.approx(0.5)
    assert abl.extract_metric({"pas": {"mAP@10": 0.42}}, "mAP@10") == pytest.approx(0.42)
    assert abl.extract_metric({"nope": {}}, "mAP@10") is None
    assert abl.extract_metric("not a dict", "mAP@10") is None


def test_ablation_table_sorts_by_abs_delta():
    runs = [
        {"name": "fusion_on", "value": 0.58, "mAP_scope": "observed-ranking; incomplete gallery coverage"},
        {"name": "critic_on", "value": 0.62, "mAP_scope": "full-gallery"},
        {"name": "threshold_0.3", "value": 0.50, "mAP_scope": "observed-ranking; incomplete gallery coverage"},
    ]
    rows = abl.ablation_table(0.60, runs, "mAP@10")
    deltas = [r["delta_vs_baseline"] for r in rows]
    # biggest |delta| first: -0.10 (threshold), +0.02 (critic), -0.02 (fusion)
    assert deltas == [-0.10, pytest.approx(-0.02), pytest.approx(0.02)]
    assert rows[0]["run"] == "threshold_0.3"
    assert rows[0]["mAP@10"] == 0.50


def test_load_run_reads_report(tmp_path):
    run_dir = tmp_path / "fusion_on"
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps({"overall": {"mAP@10": 0.58, "mAP_scope": "observed-ranking; incomplete gallery coverage"}}))
    run = abl.load_run(run_dir / "report.json", "mAP@10")
    assert run["name"] == "fusion_on"
    assert run["value"] == pytest.approx(0.58)


def test_cli_writes_csv_and_prints(tmp_path, capsys):
    base = tmp_path / "baseline" / "report.json"
    base.parent.mkdir()
    base.write_text(json.dumps({"overall": {"mAP@10": 0.60, "mAP_scope": "full-gallery"}}))
    for name, val in [("critic_on", 0.62), ("threshold_0.3", 0.50)]:
        d = tmp_path / name
        d.mkdir()
        (d / "report.json").write_text(json.dumps({"overall": {"mAP@10": val, "mAP_scope": "full-gallery"}}))
    out = tmp_path / "table.csv"
    rc = abl.main(["--baseline", str(base), "--run", str(tmp_path / "critic_on" / "report.json"),
                   "--run", str(tmp_path / "threshold_0.3" / "report.json"), "--out", str(out)])
    assert rc == 0
    text = out.read_text()
    assert "threshold_0.3" in text and "critic_on" in text
    captured = capsys.readouterr().out
    assert "baseline mAP@10=0.6" in captured
    assert "delta=-0.1" in captured or "delta=-0.10" in captured


def test_emit_configs_prints_snippet(capsys):
    rc = abl.main(["--baseline", "/dev/null", "--run", "/dev/null", "--emit-configs"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "baseline" in out and "fusion_on" in out and "run_eval_flows.py" in out


def test_missing_metric_exits_nonzero(tmp_path):
    base = tmp_path / "b" / "report.json"
    base.parent.mkdir()
    base.write_text(json.dumps({"overall": {"mAP": 0.3}}))
    run = tmp_path / "r" / "report.json"
    run.parent.mkdir()
    run.write_text(json.dumps({"overall": {"mAP@10": 0.5}}))
    rc = abl.main(["--baseline", str(base), "--run", str(run)])
    assert rc == 1

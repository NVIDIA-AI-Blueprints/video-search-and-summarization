# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import map_objects_to_tracks as m


def test_bbox_iou_basic():
    assert m.bbox_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert m.bbox_iou([0, 0, 10, 10], [10, 0, 20, 10]) == 0.0
    assert 0.0 < m.bbox_iou([0, 0, 10, 10], [5, 0, 15, 10]) < 1.0


def test_map_picks_highest_iou_in_same_clip():
    objects = [{"sensor_id": "s1", "object_id": "o1", "chunk_id": "A",
                "bbox": [0, 0, 10, 10]}]
    tracks = [
        {"track_uid": "A0", "chunk_id": "A", "bbox": [0, 0, 9, 9]},      # high IoU
        {"track_uid": "A1", "chunk_id": "A", "bbox": [5, 5, 50, 50]},    # low IoU
        {"track_uid": "B0", "chunk_id": "B", "bbox": [0, 0, 10, 10]},   # wrong clip
    ]
    mapping = m.map_objects_to_tracks(objects, tracks)
    assert mapping == [{"sensor_id": "s1", "object_id": "o1", "chunk_id": "A",
                         "track_uid": "A0", "iou": pytest.approx(0.81, abs=0.01)}]


def test_map_below_min_iou_is_unassigned():
    objects = [{"sensor_id": "s1", "object_id": "o1", "chunk_id": "A", "bbox": [0, 0, 10, 10]}]
    tracks = [{"track_uid": "A0", "chunk_id": "A", "bbox": [50, 50, 60, 60]}]  # no overlap
    mapping = m.map_objects_to_tracks(objects, tracks, min_iou=0.1)
    assert mapping[0]["track_uid"] is None
    assert mapping[0]["iou"] is None


def test_load_tracks_and_objects_roundtrip(tmp_path):
    tracks_path = tmp_path / "tracks.json"
    tracks_path.write_text(json.dumps({"tracks": [
        {"track_uid": "T1", "chunk_id": "C", "representative_frame": {"bbox_xyxy_pixels": [1, 1, 5, 5]}},
    ]}), encoding="utf-8")
    objects_path = tmp_path / "objects.jsonl"
    objects_path.write_text(
        json.dumps({"sensor_id": "s", "object_id": "o", "chunk_id": "C", "bbox": [1, 1, 5, 5]}) + "\n",
        encoding="utf-8")
    tracks = m.load_tracks(tracks_path)
    objects = m.load_objects(objects_path)
    assert tracks[0]["bbox"] == [1, 1, 5, 5]
    mapping = m.map_objects_to_tracks(objects, tracks)
    assert mapping[0]["track_uid"] == "T1"


def test_cli_writes_mapping(tmp_path, capsys):
    (tmp_path / "tracks.json").write_text(json.dumps({"tracks": [
        {"track_uid": "T", "chunk_id": "C", "representative_frame": {"bbox_xyxy_pixels": [0, 0, 4, 4]}}]}))
    (tmp_path / "objects.jsonl").write_text(
        json.dumps({"sensor_id": "s", "object_id": "o", "chunk_id": "C", "bbox": [0, 0, 4, 4]}) + "\n")
    out = tmp_path / "out.jsonl"
    rc = m.main(["--objects", str(tmp_path / "objects.jsonl"), "--tracks", str(tmp_path / "tracks.json"),
                 "--min-iou", "0.5", "--out", str(out)])
    assert rc == 0
    record = json.loads(out.read_text())
    assert record["track_uid"] == "T"
    assert "mapped 1/1" in capsys.readouterr().err

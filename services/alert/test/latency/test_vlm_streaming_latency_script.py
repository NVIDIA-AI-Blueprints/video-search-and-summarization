# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for ``test/latency/vlm_streaming_latency.py``.

Covers caption decoding into per-chunk latency rows (frame timestamps,
RT-VLM stage timings), incident / ES enrichment, and the CSV contract.

Run with: pytest test/latency/test_vlm_streaming_latency_script.py -v
"""

import csv
import os
import sys

import pytest

_SCRIPT_DIR = os.path.join(os.path.dirname(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import vlm_streaming_latency as vsl  # noqa: E402

try:
    nv_pb2, ext_pb2 = vsl.load_protos(vsl.DEFAULT_PROTO_PATH)
except ImportError:  # RT-VLM protos not present in this checkout
    nv_pb2 = ext_pb2 = None

needs_protos = pytest.mark.skipif(nv_pb2 is None, reason="RT-VLM protos not available")

FRAME_S = 1_790_620_233.721


def _caption(frames=(FRAME_S,), chunk_start=FRAME_S - 0.024, info=None):
    msg = nv_pb2.VisionLLM()
    msg.timestamp.seconds = int(chunk_start)
    msg.timestamp.nanos = int(round((chunk_start % 1) * 1e9))
    for idx, t in enumerate(frames):
        f = msg.frames.add()
        f.id = f"42:{idx}"
        f.timestamp.seconds = int(t)
        f.timestamp.nanos = int(round((t % 1) * 1e9))
    q = msg.llm.queries.add()
    q.response = "no"
    base = {
        "sensorId": "cam-1",
        "requestId": "req-1",
        "chunkIdx": "42",
        "incidentDetected": "false",
        "decodeLatencyMs": "0.800",
        "vlmLatencyMs": "88.000",
        "chunkLatencyMs": "90.000",
    }
    base.update(info or {})
    for k, v in base.items():
        msg.info[k] = v
    return msg


@needs_protos
class TestCaptionRow:
    def test_frame_to_kafka_uses_sampled_frame_time(self):
        row = vsl.caption_row(_caption(), kafka_ts=FRAME_S + 0.160, received_ts=FRAME_S + 0.162)

        assert row["frame_ts_source"] == "frame"
        assert row["frame_to_kafka_ms"] == pytest.approx(160.0, abs=0.01)
        assert row["kafka_to_consumer_ms"] == pytest.approx(2.0, abs=0.01)

    def test_stage_timings_and_remainder(self):
        row = vsl.caption_row(_caption(), kafka_ts=FRAME_S + 0.160, received_ts=FRAME_S + 0.161)

        assert row["rtvi_decode_ms"] == pytest.approx(0.8)
        assert row["rtvi_vlm_ms"] == pytest.approx(88.0)
        assert row["rtvi_chunk_ms"] == pytest.approx(90.0)
        assert row["frame_wait_publish_ms"] == pytest.approx(70.0, abs=0.01)

    def test_newest_frame_is_the_reference_for_multi_frame_chunks(self):
        row = vsl.caption_row(
            _caption(frames=(FRAME_S, FRAME_S + 0.5)), kafka_ts=FRAME_S + 0.660, received_ts=FRAME_S + 0.661
        )

        assert row["frame_count"] == 2
        assert row["frame_to_kafka_ms"] == pytest.approx(160.0, abs=0.01)

    def test_multi_frame_chunk_subtracts_only_the_model_step(self):
        # 30 s chunk: RT-VLM's decode/chunk timings span frame collection.
        msg = _caption(
            frames=(FRAME_S - 29.9, FRAME_S),
            info={"decodeLatencyMs": "29900.0", "vlmLatencyMs": "2600.0", "chunkLatencyMs": "32600.0"},
        )
        row = vsl.caption_row(msg, kafka_ts=FRAME_S + 2.7, received_ts=FRAME_S + 2.71)

        assert row["frame_to_kafka_ms"] == pytest.approx(2700.0, abs=0.01)
        assert row["chunk_start_to_kafka_ms"] == pytest.approx(32600.0, abs=0.01)
        assert row["frame_wait_publish_ms"] == pytest.approx(100.0, abs=0.01)

    def test_falls_back_to_chunk_start_without_frames(self):
        row = vsl.caption_row(
            _caption(frames=(), chunk_start=FRAME_S), kafka_ts=FRAME_S + 0.2, received_ts=FRAME_S + 0.2
        )

        assert row["frame_ts_source"] == "chunk_start"
        assert row["frame_to_kafka_ms"] == pytest.approx(200.0, abs=0.01)

    def test_missing_stage_timings_are_none(self):
        msg = _caption()
        del msg.info["chunkLatencyMs"]
        row = vsl.caption_row(msg, kafka_ts=FRAME_S + 0.160, received_ts=FRAME_S + 0.161)

        assert row["rtvi_chunk_ms"] is None
        assert row["frame_wait_publish_ms"] is None


@needs_protos
class TestEnrichment:
    def test_incident_and_es_timestamps(self):
        row = vsl.caption_row(_caption(), kafka_ts=FRAME_S + 0.160, received_ts=FRAME_S + 0.161)

        vsl.apply_incident(row, FRAME_S + 0.165)
        vsl.apply_es(row, FRAME_S + 1.165)

        assert row["incident"] is True
        assert row["frame_to_incident_kafka_ms"] == pytest.approx(165.0, abs=0.01)
        assert row["frame_to_es_ms"] == pytest.approx(1165.0, abs=0.01)
        assert row["kafka_to_es_ms"] == pytest.approx(1000.0, abs=0.01)

    def test_missing_indexed_at_leaves_es_columns_empty(self):
        row = vsl.caption_row(_caption(), kafka_ts=FRAME_S + 0.160, received_ts=FRAME_S + 0.161)

        vsl.apply_es(row, None)

        assert row["frame_to_es_ms"] is None


@needs_protos
def test_csv_has_one_row_per_chunk_with_iso_timestamps(tmp_path):
    rows = [
        vsl.caption_row(_caption(info={"chunkIdx": str(i)}), FRAME_S + 0.16, FRAME_S + 0.161)
        for i in range(3)
    ]
    path = tmp_path / "out.csv"

    vsl.write_csv(str(path), rows)

    with open(path) as f:
        out = list(csv.DictReader(f))
    assert [r["chunk_idx"] for r in out] == ["0", "1", "2"]
    assert list(out[0].keys()) == vsl.CSV_COLUMNS
    assert out[0]["frame_ts"].endswith("Z")
    assert out[0]["frame_to_es_ms"] == ""


@needs_protos
def test_errored_chunk_is_flagged():
    msg = _caption(info={"error": "Live decoder backlog exceeded the bounded decoder-to-VLM transport"})
    msg.llm.queries[0].response = ""

    row = vsl.caption_row(msg, kafka_ts=FRAME_S + 0.1, received_ts=FRAME_S + 0.1)

    assert row["error"].startswith("Live decoder backlog exceeded")


@needs_protos
def test_summary_excludes_errored_chunks_and_counts_gaps(capsys):
    ok = vsl.caption_row(_caption(info={"chunkIdx": "1"}), FRAME_S + 0.2, FRAME_S + 0.2)
    bad_msg = _caption(info={"chunkIdx": "2", "error": "Live decoder backlog exceeded"})
    bad_msg.llm.queries[0].response = ""
    bad = vsl.caption_row(bad_msg, FRAME_S + 0.01, FRAME_S + 0.01)
    later = vsl.caption_row(_caption(info={"chunkIdx": "5"}), FRAME_S + 0.2, FRAME_S + 0.2)

    vsl.print_summary([ok, bad, later], started=0.0, bootstrap="k:9092", es_note="")

    out = capsys.readouterr().out
    assert "Errored/dropped chunks: 1 (excluded)" in out
    assert "Missing chunk indices: 2" in out
    frame_line = next(l for l in out.splitlines() if "Frame -> Kafka (all)" in l)
    assert frame_line.split()[4] == "2"  # count excludes the errored chunk


def test_missing_chunks_per_request():
    rows = [{"request_id": "a", "chunk_idx": i} for i in ("1", "2", "4")] + [
        {"request_id": "b", "chunk_idx": i} for i in ("10", "11")
    ]

    assert vsl.missing_chunks(rows) == 1


def test_summarize_percentiles():
    s = vsl.summarize([float(v) for v in range(1, 101)])

    assert s["count"] == 100
    assert s["avg"] == pytest.approx(50.5)
    assert s["p50"] == pytest.approx(50.5)
    assert s["p90"] == pytest.approx(90.1)
    assert s["max"] == 100


@pytest.mark.parametrize("text,seconds", [("90s", 90), ("10m", 600), ("2h", 7200), ("1d", 86400)])
def test_parse_duration(text, seconds):
    assert vsl.parse_duration_to_seconds(text) == seconds


def test_bad_duration_is_an_argument_error():
    assert vsl.main(["--duration", "ten"]) == 2

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows import ingestion_validation as validation


@pytest.fixture
def template(tmp_path):
    file = tmp_path / "contract.json"
    file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "embedding": {
                    "model": "deployed-model",
                    "dimensions": 768,
                    "unit_normalized": True,
                },
                "document_limit": 50000,
            }
        )
    )
    return file


def test_decoded_counts_and_tool_identity_are_used(template, monkeypatch):
    run = Mock(
        side_effect=[
            SimpleNamespace(stdout="ffprobe version test\n"),
            SimpleNamespace(stdout='{"streams":[{"nb_read_frames":"150"}]}'),
        ]
    )
    monkeypatch.setattr(validation.subprocess, "run", run)
    contract, evidence = validation.prepare_contract(
        str(template), [template.parent / "clip.mp4"]
    )
    assert contract["frame_counts"] == {"clip": 150}
    assert evidence["ffprobe_version"] == "ffprobe version test"
    args = run.call_args.args[0]
    assert "-count_frames" in args
    assert "nb_read_frames" in args[args.index("-show_entries") + 1]


@pytest.mark.parametrize("count", ["N/A", "0", "-1", "1.5", True, 150, "９", "9" * 20])
def test_never_falls_back_to_duration_or_fps(template, monkeypatch, count):
    monkeypatch.setattr(
        validation.subprocess,
        "run",
        Mock(
            side_effect=[
                SimpleNamespace(stdout="version"),
                SimpleNamespace(
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "nb_read_frames": count,
                                    "nb_frames": "150",
                                    "duration": "5",
                                }
                            ]
                        }
                    )
                ),
            ]
        ),
    )
    with pytest.raises(ValueError, match="decoded frame count"):
        validation.prepare_contract(str(template), [Path("clip.mp4")])


@pytest.mark.parametrize(
    "mutation",
    [
        {"frame_counts": {"clip": 10}},
        {"document_limit": True},
        {"schema_version": True},
        {"embedding": {"model": "", "dimensions": 768, "unit_normalized": True}},
        {"embedding": {"model": "m", "dimensions": 0, "unit_normalized": True}},
        {
            "embedding": {
                "model": "m",
                "dimensions": 768,
                "unit_normalized": True,
                "norm_tolerance": float("nan"),
            }
        },
    ],
)
def test_bad_contract_fails_before_media_probe(template, monkeypatch, mutation):
    data = json.loads(template.read_text())
    data.update(mutation)
    template.write_text(json.dumps(data))
    run = Mock()
    monkeypatch.setattr(validation.subprocess, "run", run)
    with pytest.raises(ValueError):
        validation.prepare_contract(str(template), [Path("clip.mp4")])
    run.assert_not_called()


def test_media_probe_failure_propagates_before_ingestion(template, monkeypatch):
    monkeypatch.setattr(
        validation.subprocess, "run", Mock(side_effect=FileNotFoundError("ffprobe"))
    )
    with pytest.raises(FileNotFoundError):
        validation.prepare_contract(str(template), [Path("clip.mp4")])

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Release compatibility and stale-artifact rejection without model weights."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SOURCE = Path(__file__).parents[2] / "src/models/custom/samples/cosmos-embed1-release"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load("release_contract_test", "input_contract.py")
builder = load("release_builder_test", "create_triton_model_repo.py")


def test_manifest_rejects_modified_or_unlisted_files(tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"correct graph")
    (tmp_path / "MANIFEST.sha256").write_text(
        f"{contract.sha256(model)}  ./model.onnx\n"
    )
    assert contract.verify_release(tmp_path, [model]) == {
        "model.onnx": contract.sha256(model)
    }
    model.write_bytes(b"old checkpoint")
    with pytest.raises(ValueError, match="checksum"):
        contract.verify_release(tmp_path, [model])
    unknown = tmp_path / "unknown.onnx"
    unknown.write_bytes(b"unlisted")
    with pytest.raises(ValueError, match="checksum"):
        contract.verify_release(tmp_path, [unknown])


def test_engine_seal_rejects_modified_engine_and_configuration(tmp_path):
    config = tmp_path / "config.pbtxt"
    engine = tmp_path / "model.plan"
    config.write_text("config")
    engine.write_bytes(b"new engine")
    seal = {
        "contract": "cosmos-v1.2-bcthw-fp32-128-v1",
        "artifacts": {p.name: contract.sha256(p) for p in (config, engine)},
    }
    (tmp_path / "build.json").write_text(json.dumps(seal))
    assert contract.verify_build(tmp_path) == seal
    engine.write_bytes(b"old engine")
    with pytest.raises(ValueError, match="identity"):
        contract.verify_build(tmp_path)
    engine.write_bytes(b"new engine")
    config.write_text("incorrect output name")
    with pytest.raises(ValueError, match="identity"):
        contract.verify_build(tmp_path)


def test_normalized_embeddings_are_not_normalized_again():
    rows = np.zeros((2, 768), np.float32)
    rows[0, 2], rows[1, 4] = 1, 1
    assert contract.checked_embeddings(rows, 2) == rows.tolist()
    for bad in (rows * 2, rows[:, :767], rows[:1], rows * np.nan):
        with pytest.raises(ValueError):
            contract.checked_embeddings(bad, 2)


def test_text_padding_and_mask_integer_cast():
    class Tensor:
        def __init__(self, value):
            self.value = value

        def cpu(self):
            return self

        def numpy(self):
            return self.value

    def processor(**kwargs):
        assert kwargs == {
            "text": ["person walking", "red coat"],
            "return_tensors": "pt",
            "max_txt_len": 128,
        }
        return SimpleNamespace(
            input_ids=Tensor(np.ones((2, 128), dtype=np.int64)),
            attention_mask=Tensor(np.ones((2, 128), dtype=np.float32)),
        )

    result = contract.prepare_text_inputs(processor, ["person walking", "red coat"])
    assert all(v.dtype == np.int64 and v.shape == (2, 128) for v in result.values())
    with pytest.raises(ValueError):
        contract.prepare_text_inputs(processor, [""])


def test_triton_contract_uses_release_names_and_types():
    video = builder.configuration("video", 64)
    text = builder.configuration("text", 64)
    assert 'name: "videos" data_type: TYPE_FP32 dims: [3,8,448,448]' in video
    assert 'name: "video_embedding"' in video
    assert 'name: "text_embedding"' in text
    assert text.count("dims: [128]") == 2
    assert "max_batch_size: 64" in text


@pytest.mark.parametrize("width", [None, 768, 512])
def test_symbolic_release_output_width_is_checked_at_inference(monkeypatch, width):
    class Dimension:
        def __init__(self, value):
            self.dim_value = value or 0

        def HasField(self, name):
            return self.dim_value != 0

    def value(name, dtype, dimensions):
        return SimpleNamespace(
            name=name,
            type=SimpleNamespace(
                tensor_type=SimpleNamespace(
                    elem_type=dtype,
                    shape=SimpleNamespace(dim=[Dimension(d) for d in dimensions]),
                )
            ),
        )

    graph = SimpleNamespace(
        input=[value("videos", 1, [None, 3, 8, 448, 448])],
        output=[value("video_embedding", 1, [None, width])],
        initializer=[
            SimpleNamespace(
                external_data=[
                    SimpleNamespace(key="location", value=contract.VISION + "_data")
                ]
            )
        ],
    )
    checked = []
    monkeypatch.setitem(
        sys.modules,
        "onnx",
        SimpleNamespace(
            load=lambda *a, **k: SimpleNamespace(graph=graph),
            checker=SimpleNamespace(check_model=lambda path: checked.append(path)),
        ),
    )
    if width == 512:
        with pytest.raises(ValueError, match="embedding shape"):
            builder.graph_contract("vision.onnx", "video")
        assert not checked
    else:
        builder.graph_contract("vision.onnx", "video")
        assert checked == ["vision.onnx"]


def test_video_channels_time_scaling_and_batch_equivalence():
    torch = pytest.importorskip("torch")
    # Distinct channels and frame values expose a time/channel permutation error.
    frames = torch.zeros((8, 448, 448, 3), dtype=torch.uint8)
    frames[..., 0] = 255
    frames[..., 1] = 128
    for time in range(8):
        frames[time, :, :, 2] = time
    actual = contract.prepare_video_batch([frames, frames])
    assert actual.shape == (2, 3, 8, 448, 448)
    assert actual.dtype == torch.float32 and actual.is_contiguous()
    assert torch.all(actual[:, 0] == 1)
    assert torch.allclose(actual[:, 1], torch.full_like(actual[:, 1], 128 / 255))
    assert torch.allclose(actual[0, 2, :, 0, 0], torch.arange(8) / 255)
    assert torch.equal(actual[0], contract.prepare_video_batch([frames])[0])
    with pytest.raises(ValueError):
        contract.prepare_video_batch([frames.float()])
    with pytest.raises(ValueError):
        contract.prepare_video_batch([frames[:7]])
    with pytest.raises(ValueError):
        contract.prepare_video_batch([frames[..., :2]])

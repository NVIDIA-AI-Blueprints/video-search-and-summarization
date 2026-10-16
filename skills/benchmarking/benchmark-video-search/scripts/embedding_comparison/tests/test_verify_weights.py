# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_weights as verify


def test_byte_hash_matches_expected(tmp_path):
    weights = tmp_path / "w.bin"
    weights.write_bytes(b"abc")
    exp = hashlib.sha256(b"abc").hexdigest()
    report = verify.verify(verify.parser().parse_args(["--onnx-weights", str(weights), "--expected", exp]))
    assert report["onnx_weights_match_expected"] is True
    assert report["tensor_compare"] is None


def test_byte_hash_mismatch_strict_exits(tmp_path, capsys):
    weights = tmp_path / "w.bin"
    weights.write_bytes(b"abc")
    rc = verify.main(["--onnx-weights", str(weights), "--expected", "0" * 64, "--strict"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["onnx_weights_match_expected"] is False


def _tiny_onnx(path, array):
    onnx = pytest.importorskip("onnx")
    from onnx import helper, numpy_helper
    tensor = numpy_helper.from_array(array, name="w")
    graph = helper.make_graph([], "g", [], [], initializer=[tensor])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    onnx.save(model, str(path))


def _tiny_pth(path, array):
    torch = pytest.importorskip("torch")
    torch.save({"w": torch.from_numpy(array).clone()}, str(path))


def test_tensor_compare_identical(tmp_path):
    array = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    onnx_path, pth_path = tmp_path / "m.onnx", tmp_path / "m.pth"
    _tiny_onnx(onnx_path, array)
    _tiny_pth(pth_path, array)
    (tmp_path / "w.bin").write_bytes(b"abc")
    report = verify.verify(verify.parser().parse_args(
        ["--onnx-weights", str(tmp_path / "w.bin"), "--onnx", str(onnx_path),
         "--pth", str(pth_path), "--expected", "x"]))
    cmp = report["tensor_compare"]
    assert cmp["weights_identical"] is True
    assert cmp["tensors_only_in_onnx"] == 0
    assert cmp["tensors_only_in_pth"] == 0


def test_tensor_compare_mismatch(tmp_path):
    onnx_path, pth_path = tmp_path / "m.onnx", tmp_path / "m.pth"
    _tiny_onnx(onnx_path, np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32))
    _tiny_pth(pth_path, np.array([[9.0, 9.0], [9.0, 9.0]], dtype=np.float32))
    (tmp_path / "w.bin").write_bytes(b"abc")
    rc = verify.main(["--onnx-weights", str(tmp_path / "w.bin"), "--onnx", str(onnx_path),
                      "--pth", str(pth_path), "--expected", "x", "--strict", "--report", str(tmp_path / "r.json")])
    cmp = json.loads((tmp_path / "r.json").read_text())["tensor_compare"]
    assert cmp["weights_identical"] is False
    assert rc == 1

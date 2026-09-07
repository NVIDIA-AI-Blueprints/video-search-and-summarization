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
"""Unit tests for the config generators shipped in the config-init image.

These run the *same* functions the container runs, imported straight from
``src/``, with no docker involved. The pub/sub test is the important one: it
compares generated output against the committed golden file, which is what
caught the OpenCV-to-numpy migration being correct.

Counterpart to the rt-cv-bev-fusion unit tests, resolving the generators from
this service's ``src/`` directory.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

MQTT_BROKERS = "localhost:1883"


def _load_orchestrator(src_dir: Path):
    """Import ``mv3dt-config-init.py``, whose hyphens block a normal import."""
    path = src_dir / "mv3dt-config-init.py"
    spec = importlib.util.spec_from_file_location("mv3dt_config_init", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sensor_ids(calibration_json: Path) -> list[str]:
    import json

    with calibration_json.open(encoding="utf-8") as fh:
        return [s["id"] for s in json.load(fh)["sensors"]]


@pytest.mark.parametrize(
    "bad_id", ["../escape", "nested/cam", "..", ".", "cam/", "/abs/path", "a/../b"]
)
def test_generate_cam_info_rejects_ids_that_are_not_filenames(
    tmp_path, src_dir, bad_id
):
    """A sensor id becomes a filename, so it must stay inside output_dir."""
    import json

    from generate_cam_info_configs import _parse_model_args, generate_cam_info_files

    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps(
            {
                "sensors": [
                    {
                        "id": bad_id,
                        "type": "camera",
                        "cameraMatrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    model_entries = _parse_model_args([["0", "1.60", "0.3"]])
    out = tmp_path / "camInfo"

    with pytest.raises(ValueError, match="not a valid filename component"):
        generate_cam_info_files(calibration, out, model_entries)

    assert not list(tmp_path.rglob("*.yml")), "a file was written despite the bad id"


def test_generate_cam_info_writes_one_file_per_sensor(
    tmp_path, src_dir, calibration_json
):
    """Every sensor in calibration.json yields a camInfo file."""
    from generate_cam_info_configs import _parse_model_args, generate_cam_info_files

    model_entries = _parse_model_args([["0", "1.60", "0.3"]])
    out = tmp_path / "camInfo"
    count = generate_cam_info_files(calibration_json, out, model_entries)

    expected_ids = _sensor_ids(calibration_json)
    assert count == len(expected_ids)

    written = sorted(p.stem for p in out.glob("*.yml"))
    assert written == sorted(expected_ids)


def test_generated_cam_info_is_valid_and_complete(tmp_path, src_dir, calibration_json):
    """Each camInfo file parses as YAML and carries the keys DeepStream needs."""
    from generate_cam_info_configs import _parse_model_args, generate_cam_info_files

    model_entries = _parse_model_args([["0", "1.60", "0.3"]])
    out = tmp_path / "camInfo"
    generate_cam_info_files(calibration_json, out, model_entries)

    for path in sorted(out.glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        assert "projectionMatrix_3x4_w2p" in doc, f"{path.name} missing projection matrix"
        assert "modelInfo" in doc, f"{path.name} missing modelInfo"

        matrix = doc["projectionMatrix_3x4_w2p"]
        assert len(matrix) == 12, f"{path.name} projection matrix is not 3x4"
        assert all(isinstance(v, (int, float)) for v in matrix)


def _write_one_camera(tmp_path, **fields):
    """A calibration.json holding a single camera sensor built from ``fields``."""
    import json

    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps({"sensors": [dict(fields, id="Cam", type="camera")]}),
        encoding="utf-8",
    )
    return path


def _first_camera(calibration_json: Path) -> dict:
    import json

    with calibration_json.open(encoding="utf-8") as fh:
        return json.load(fh)["sensors"][0]


def _written_projection(out_dir: Path):
    paths = sorted(out_dir.glob("*.yml"))
    assert len(paths) == 1, f"expected one camInfo file, got {paths}"
    return yaml.safe_load(paths[0].read_text())["projectionMatrix_3x4_w2p"]


def test_projection_prefers_intrinsics_times_extrinsics(tmp_path, src_dir, calibration_json):
    """K @ Rt wins over a precomputed cameraMatrix, which is a fit and drifts from it."""
    import numpy as np

    from generate_cam_info_configs import _parse_model_args, generate_cam_info_files

    sensor = _first_camera(calibration_json)
    out = tmp_path / "camInfo"
    generate_cam_info_files(
        _write_one_camera(tmp_path, **sensor), out, _parse_model_args([["0", "1.60", "0.3"]])
    )

    written = np.array(_written_projection(out)).reshape(3, 4)
    expected = np.array(sensor["intrinsicMatrix"]) @ np.array(sensor["extrinsicMatrix"])
    assert np.allclose(written, expected, rtol=0, atol=0)
    assert not np.allclose(written, np.array(sensor["cameraMatrix"]), rtol=1e-3), (
        "fixture no longer distinguishes the two sources, so this test proves nothing"
    )


@pytest.mark.parametrize(
    "fields, match",
    [
        ({"intrinsicMatrix": [[1, 0, 0], [0, 1, 0]], "extrinsicMatrix": None},
         "invalid intrinsicMatrix shape"),
        ({"intrinsicMatrix": None, "extrinsicMatrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
         "invalid extrinsicMatrix shape"),
        ({"intrinsicMatrix": [[1662.8, 0, "960"], [0, 1662.8, 540], [0, 0, 1]],
          "extrinsicMatrix": None},
         "non-numeric intrinsicMatrix entry"),
        ({"intrinsicMatrix": [[1662.8, 0, float("nan")], [0, 1662.8, 540], [0, 0, 1]],
          "extrinsicMatrix": None},
         "non-finite intrinsicMatrix entry"),
        ({"cameraMatrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
         "invalid cameraMatrix shape"),
    ],
)
def test_malformed_projection_inputs_are_rejected(
    tmp_path, src_dir, calibration_json, fields, match
):
    """Both projection sources are validated, so neither can smuggle a bad matrix in.

    Nothing may be written: a camInfo carrying a malformed matrix corrupts 3D
    localisation without any complaint at the point it is generated.
    """
    from generate_cam_info_configs import _parse_model_args, generate_cam_info_files

    reference = _first_camera(calibration_json)
    fields = {k: (reference[k] if v is None else v) for k, v in fields.items()}
    out = tmp_path / "camInfo"

    with pytest.raises(ValueError, match=match):
        generate_cam_info_files(
            _write_one_camera(tmp_path, **fields), out, _parse_model_args([["0", "1.6", "0.3"]])
        )
    assert not list(tmp_path.rglob("*.yml"))


@pytest.mark.parametrize(
    "fields, match",
    [
        # Rank-deficient left block: no camera centre and no intrinsics to recover.
        ({"cameraMatrix": [[1, 2, 3, 4], [2, 4, 6, 8], [1, 1, 1, 1]]},
         "singular or ill-conditioned"),
        # Intrinsics in metres rather than pixels.
        ({"intrinsicMatrix": [[0.0037, 0, 0.0021], [0, 0.0037, 0.0012], [0, 0, 1]],
          "extrinsicMatrix": None},
         "implausible focal length"),
        # Principal point far outside any sensor.
        ({"intrinsicMatrix": [[1662.8, 0, 1e6], [0, 1662.8, 540], [0, 0, 1]],
          "extrinsicMatrix": None},
         "implausible principal point"),
    ],
)
def test_projection_that_does_not_decompose_to_a_camera_is_rejected(
    tmp_path, src_dir, calibration_json, fields, match
):
    """A well-formed 3x4 of numbers still has to be a camera.

    P = K [R | -R C], so decomposing it recovers intrinsics, a rotation and a
    centre; a matrix that is not a camera yields nonsense for at least one of them.
    """
    from generate_cam_info_configs import _parse_model_args, generate_cam_info_files

    reference = _first_camera(calibration_json)
    fields = {k: (reference[k] if v is None else v) for k, v in fields.items()}
    out = tmp_path / "camInfo"

    with pytest.raises(ValueError, match=match):
        generate_cam_info_files(
            _write_one_camera(tmp_path, **fields), out, _parse_model_args([["0", "1.6", "0.3"]])
        )
    assert not list(tmp_path.rglob("*.yml"))


def test_either_overall_sign_of_the_projection_matrix_is_accepted(
    tmp_path, src_dir, calibration_json
):
    """P and -P project identically, and fitted cameraMatrix blocks carry both signs.

    The validation normalises the sign to decompose; it must not reject on it, nor
    rewrite the matrix it was given.
    """
    import numpy as np

    from generate_cam_info_configs import _parse_model_args, generate_cam_info_files

    negated = (-np.array(_first_camera(calibration_json)["cameraMatrix"])).tolist()
    out = tmp_path / "camInfo"
    generate_cam_info_files(
        _write_one_camera(tmp_path, cameraMatrix=negated),
        out,
        _parse_model_args([["0", "1.60", "0.3"]]),
    )

    written = np.array(_written_projection(out)).reshape(3, 4)
    assert np.allclose(written, np.array(negated), rtol=0, atol=0)


def test_generate_pub_sub_matches_expected(
    tmp_path, src_dir, calibration_json, expected_pub_sub
):
    """End-to-end generator run reproduces the committed golden config.

    This is the regression guard for the projection math. It exercises
    ``_linalg`` (RQ decomposition + perspective transform) through the real
    FOV-overlap and neighbour-topology code path.
    """
    orchestrator = _load_orchestrator(src_dir)

    cam_info_dir = tmp_path / "camInfo"
    generated_dir = tmp_path / "generated"

    orchestrator.generate_cam_info(
        calibration_json, cam_info_dir, orchestrator.DEFAULT_CLASS_SPECS
    )
    orchestrator.generate_pub_sub(
        cam_info_dir,
        generated_dir,
        MQTT_BROKERS,
        "",  # range_of_interest: derive from camera positions
        "",  # neighbor_criteria: default overlap_threshold
        "",  # min_object_size: default
    )

    produced = generated_dir / "pub_sub_info_config.yml"
    assert produced.exists(), "generator did not write pub_sub_info_config.yml"

    assert yaml.safe_load(produced.read_text()) == yaml.safe_load(
        expected_pub_sub.read_text()
    ), "generated pub_sub_info_config.yml differs from the expected golden file"

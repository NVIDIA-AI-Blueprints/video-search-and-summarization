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
"""Integration tests: run the config-init image in volume mode and API mode.

Files move with ``docker cp``, not bind mounts: in CI the daemon is a separate
dind container, so ``-v`` would resolve against its filesystem, not ours.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import time
import uuid
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

CONTAINER_TIMEOUT = 180

# Must not pre-exist in the stub image: docker cp renames a source dir onto a
# missing destination, but nests it inside an existing one (/srv/srv/...).
STUB_DIR = "/opt/stub"

BASE_ENV = {
    "CALIBRATION_POLL_INTERVAL": "1",
    "CALIBRATION_WAIT_TIMEOUT": "120",
    "MQTT_HOST": "localhost",
    "MQTT_PORT": "1883",
}


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _create(image: str, env: dict, *, network: str = "", name: str = "", cmd: list = ()) -> str:
    argv = ["docker", "create"]
    if network:
        argv += ["--network", network]
    if name:
        argv += ["--name", name]
    for k, v in env.items():
        argv += ["-e", f"{k}={v}"]
    cid = _run([*argv, image, *cmd]).stdout.strip()
    assert cid, f"failed to create container from {image}"
    return cid


def _cp_in(cid: str, src: Path, dest: str) -> None:
    """Copy a *staged* path in. Never pass a repo file: modes are rewritten.

    docker cp keeps host modes but forces ownership to root, so a restrictive
    umask would leave the payload unreadable to the image's nonroot user.
    """
    for path in [src, *(src.rglob("*") if src.is_dir() else ())]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    proc = _run(["docker", "cp", str(src), f"{cid}:{dest}"])
    assert proc.returncode == 0, f"docker cp into {cid}:{dest} failed: {proc.stderr}"


def _cp_out(cid: str, src: str, dest: Path) -> bool:
    """False when the path does not exist in the container."""
    return _run(["docker", "cp", f"{cid}:{src}", str(dest)]).returncode == 0


def _logs(cid: str) -> str:
    proc = _run(["docker", "logs", cid])
    return proc.stdout + proc.stderr


def _start(cid: str) -> None:
    assert _run(["docker", "start", cid]).returncode == 0, f"failed to start {cid}"


def _wait(cid: str) -> int:
    proc = subprocess.run(
        ["docker", "wait", cid], capture_output=True, text=True, timeout=CONTAINER_TIMEOUT
    )
    return int(proc.stdout.strip() or 1)


def _seed_calibration_dir(cid: str, stage: dict) -> None:
    """Create an empty /calibration in the container."""
    empty = stage["in"] / "calibration"
    empty.mkdir(parents=True, exist_ok=True)
    _cp_in(cid, empty, "/calibration")


def _check_pub_sub(cid: str, stage: dict, expected_pub_sub: Path, logs: str) -> None:
    assert _cp_out(cid, "/tmp/generated/pub_sub_info_config.yml", stage["out"]), (
        f"no pub_sub_info_config.yml produced\n{logs}"
    )
    produced = stage["out"] / "pub_sub_info_config.yml"
    assert yaml.safe_load(produced.read_text()) == yaml.safe_load(
        expected_pub_sub.read_text()
    ), "generated config differs from expected"


@pytest.fixture
def stage(tmp_path, request):
    """Host scratch dirs for staging files into, and out of, containers."""
    dirs = {name: tmp_path / name for name in ("in", "out", "stub")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    yield dirs
    if not request.config.getoption("--keep-output"):
        shutil.rmtree(tmp_path, ignore_errors=True)


def _start_stub(stage: dict, calibration_json: Path, stub_image: str, net: str, name: str) -> str:
    """HTTP endpoint returning two empty 200s, then the real calibration."""
    src = stage["stub"] / "stub"
    src.mkdir(parents=True, exist_ok=True)
    shutil.copy(calibration_json, src / "calibration.json")
    (src / "server.py").write_text(
        textwrap.dedent(
            """
            import json
            from http.server import BaseHTTPRequestHandler, HTTPServer

            CAL = json.load(open("@STUB_DIR@/calibration.json"))
            STATE = {"n": 0}

            class H(BaseHTTPRequestHandler):
                def do_GET(self):
                    STATE["n"] += 1
                    body = b"{}" if STATE["n"] <= 2 else json.dumps(CAL).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *a):
                    pass

            HTTPServer(("", 8081), H).serve_forever()
            """
        ).strip().replace("@STUB_DIR@", STUB_DIR)
    )

    cid = _create(
        stub_image, {}, network=net, name=name, cmd=["python3", "-u", f"{STUB_DIR}/server.py"]
    )
    _cp_in(cid, src, STUB_DIR)
    _start(cid)
    time.sleep(3)
    # Without this the failure surfaces as a config-init DNS timeout 120s later.
    alive = _run(["docker", "inspect", "-f", "{{.State.Running}}", cid]).stdout.strip()
    assert alive == "true", f"stub endpoint died on startup:\n{_logs(cid)}"
    return cid


def test_volume_mode_generates_expected_config(
    image_present, stage, calibration_json, expected_pub_sub
):
    """Container waits for calibration.json on the shared volume, then generates."""
    cid = _create(image_present, {**BASE_ENV, "CALIBRATION_API_URL": ""})
    try:
        _seed_calibration_dir(cid, stage)
        _start(cid)

        # Drop calibration in after start, mimicking runtime.
        time.sleep(2)
        staged = stage["in"] / "calibration.json"
        shutil.copy(calibration_json, staged)
        _cp_in(cid, staged, "/calibration/calibration.json")

        rc = _wait(cid)
        logs = _logs(cid)
        assert rc == 0, f"container exited {rc}\n{logs}"
        assert "Waiting for calibration.json" in logs
        assert "Validation passed" in logs

        _check_pub_sub(cid, stage, expected_pub_sub, logs)

        assert _cp_out(cid, "/tmp/camInfo", stage["out"]), "no camInfo directory produced"
        sensors = json.loads(calibration_json.read_text())["sensors"]
        assert len(sorted((stage["out"] / "camInfo").glob("*.yml"))) == len(sensors)
    finally:
        _run(["docker", "rm", "-f", cid])


def test_api_mode_fetches_and_rejects_empty_payloads(
    image_present, stage, calibration_json, expected_pub_sub, stub_image
):
    """API mode retries past empty 200s and writes to CALIBRATION_FETCH_PATH only."""
    net = f"mv3dt-config-init-test-{uuid.uuid4().hex[:8]}"
    stub_name = f"calib-stub-{uuid.uuid4().hex[:8]}"
    assert _run(["docker", "network", "create", net]).returncode == 0

    stub_cid = ""
    cid = ""
    try:
        stub_cid = _start_stub(stage, calibration_json, stub_image, net, stub_name)

        cid = _create(
            image_present,
            {**BASE_ENV, "CALIBRATION_API_URL": f"http://{stub_name}:8081/config/calibration"},
            network=net,
        )
        _seed_calibration_dir(cid, stage)
        _start(cid)

        rc = _wait(cid)
        logs = _logs(cid)
        assert rc == 0, f"container exited {rc}\nCONFIG-INIT:\n{logs}\nSTUB:\n{_logs(stub_cid)}"

        assert "Calibration empty/unconfigured" in logs, "empty-200 guard did not fire"
        assert "Fetched calibration" in logs, "calibration was never fetched"
        assert "Waiting for calibration.json" not in logs, (
            "fell back to volume wait despite CALIBRATION_API_URL being set"
        )

        assert _cp_out(cid, "/tmp/calibration/calibration.json", stage["out"]), (
            "fetched calibration not written to CALIBRATION_FETCH_PATH"
        )
        assert not _cp_out(cid, "/calibration/calibration.json", stage["out"] / "shared.json"), (
            "API mode wrote to the shared calibration volume"
        )

        _check_pub_sub(cid, stage, expected_pub_sub, logs)
    finally:
        for c in (cid, stub_cid):
            if c:
                _run(["docker", "rm", "-f", c])
        _run(["docker", "network", "rm", net])


def test_image_ships_no_opencv(image_present):
    """opencv and its vendored FFmpeg/OpenSSL were dropped in 3.3.0 for OSRB."""
    cid = _create(image_present, {})
    try:
        listing = subprocess.run(
            f"docker export {cid} | tar -t",
            shell=True, capture_output=True, text=True, timeout=300,
        ).stdout
        assert "opencv" not in listing.lower(), "opencv is back in the image"
        for lib in ("libavcodec", "libavformat", "libssl-", "libcrypto-", "libaom", "libvpx"):
            assert lib not in listing, f"{lib} found in image"
    finally:
        _run(["docker", "rm", "-f", cid])

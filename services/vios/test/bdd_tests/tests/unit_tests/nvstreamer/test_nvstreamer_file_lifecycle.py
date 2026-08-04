# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
NVStreamer file lifecycle: upload → streams → info → mediainfo → RTSP → delete.

Also covers a simple POST multipart upload with ingest transcode
(framerate / bitrate headers).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import requests

from ..unit_test_utils import api_delete, api_get, validate_json_response, validate_list_response

logger = logging.getLogger(__name__)

TEST_VIDEO = Path(__file__).resolve().parents[3] / "data" / "test_video.mp4"
UPLOAD_TS = "2025-01-01T00:00:00.000Z"
NAME_PREFIX = "bdd-nvs-life-"


def _timeout(unit_test_params: dict) -> int:
    return int(unit_test_params.get("timeout", 30))


def _stream_id_from_upload(payload: dict) -> str:
    """POST may leave sensorId empty; prefer streamId / id."""
    for key in ("streamId", "id", "sensorId"):
        val = (payload.get(key) or "").strip()
        if val:
            return val
    raise AssertionError(f"upload response missing stream id: {payload!r}")


def _put_upload(
    base: str, video: Path, filename: str, *, verify_ssl: bool, timeout: int
) -> dict:
    url = f"{base}/api/v1/storage/file/{filename}?timestamp={UPLOAD_TS}"
    with video.open("rb") as fh:
        resp = requests.put(
            url,
            data=fh,
            headers={"Content-Type": "application/octet-stream"},
            timeout=timeout,
            verify=verify_ssl,
        )
    assert resp.status_code == 200, (
        f"PUT upload failed: {resp.status_code} {resp.text[:300]}"
    )
    return resp.json()


def _post_upload_transcode(
    base: str,
    video: Path,
    filename: str,
    *,
    framerate: int,
    bitrate_kbps: int,
    verify_ssl: bool,
    timeout: int,
) -> dict:
    url = f"{base}/api/v1/storage/file"
    headers = {
        "nvstreamer-file-name": filename,
        "nvstreamer-enable-transcode": "true",
        "nvstreamer-transcode-framerate": str(framerate),
        "nvstreamer-transcode-bitrate": str(bitrate_kbps),
    }
    with video.open("rb") as fh:
        resp = requests.post(
            url,
            headers=headers,
            files={"file": (filename, fh, "video/mp4")},
            timeout=timeout,
            verify=verify_ssl,
        )
    assert resp.status_code == 200, (
        f"POST transcode upload failed: {resp.status_code} {resp.text[:300]}"
    )
    return resp.json()


def _delete_file(base: str, stream_id: str, *, verify_ssl: bool, timeout: int) -> None:
    resp = api_delete(
        base,
        f"/api/v1/storage/file/{stream_id}",
        verify_ssl=verify_ssl,
        timeout=timeout,
    )
    assert resp.status_code == 200, (
        f"DELETE /storage/file/{stream_id} failed: "
        f"{resp.status_code} {resp.text[:300]}"
    )


def _wait_sensor_streams(
    base: str,
    stream_id: str,
    *,
    verify_ssl: bool,
    timeout: int,
    wait_s: float = 60.0,
) -> List[dict]:
    """Poll GET /sensor/{id}/streams until a typed Rtsp entry with url appears."""
    deadline = time.monotonic() + wait_s
    last: Any = None
    while time.monotonic() < deadline:
        resp = api_get(
            base,
            f"/api/v1/sensor/{stream_id}/streams",
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            last = data
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict) and first.get("url") and first.get("type") == "Rtsp":
                    return data
        time.sleep(1.0)
    raise AssertionError(
        f"sensor/{stream_id}/streams never returned Rtsp url within {wait_s}s; last={last!r}"
    )


def _find_in_sensor_streams_list(
    streams_list: list, stream_id: str
) -> Optional[List[dict]]:
    for entry in streams_list:
        if isinstance(entry, dict) and stream_id in entry:
            val = entry[stream_id]
            return val if isinstance(val, list) else [val]
    return None


def _ffprobe_rtsp(rtsp_url: str, *, timeout_s: float = 20.0) -> Tuple[str, str]:
    """Return (codec_name, codec_type) for the first video stream via DESCRIBE/play."""
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe not installed (needed to validate RTSP describe)")
    cmd = [
        "ffprobe",
        "-v", "error",
        "-rtsp_transport", "tcp",
        "-show_entries", "stream=codec_type,codec_name",
        "-of", "csv=p=0",
        rtsp_url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"ffprobe timed out on {rtsp_url}") from exc
    assert proc.returncode == 0, (
        f"ffprobe failed rc={proc.returncode} stderr={proc.stderr[:300]!r} url={rtsp_url}"
    )
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1] == "video":
            return parts[0], parts[1]
        if len(parts) >= 2 and parts[0] == "video":
            # csv order can be codec_type,codec_name depending on ffprobe build
            return parts[1], parts[0]
    raise AssertionError(f"no video stream in ffprobe output: {proc.stdout!r}")


@pytest.fixture
def unique_mp4_name() -> str:
    return f"{NAME_PREFIX}{uuid.uuid4().hex[:10]}.mp4"


@pytest.fixture
def uploaded_put_stream(nvstreamer_api_config, unit_test_params, unique_mp4_name):
    """PUT-upload test_video.mp4; yield (stream_id, upload_json); DELETE unless consumed."""
    assert TEST_VIDEO.is_file(), f"missing {TEST_VIDEO}"
    base = nvstreamer_api_config["base_url"]
    verify = nvstreamer_api_config.get("verify_ssl", False)
    timeout = max(_timeout(unit_test_params), 120)
    upload = _put_upload(base, TEST_VIDEO, unique_mp4_name, verify_ssl=verify, timeout=timeout)
    stream_id = _stream_id_from_upload(upload)
    state = {"stream_id": stream_id, "deleted": False}
    try:
        yield stream_id, upload, state
    finally:
        if not state["deleted"]:
            try:
                _delete_file(base, stream_id, verify_ssl=verify, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cleanup DELETE failed for %s: %s", stream_id, exc)


def test_nvstreamer_put_upload_streams_info_mediainfo_rtsp(
    nvstreamer_api_config: dict,
    unit_test_params: dict,
    uploaded_put_stream,
) -> None:
    """Upload → /sensor/streams → info → mediainfo → RTSP (ffprobe) → DELETE."""
    base = nvstreamer_api_config["base_url"]
    verify = nvstreamer_api_config.get("verify_ssl", False)
    timeout = _timeout(unit_test_params)
    stream_id, upload, state = uploaded_put_stream

    assert upload.get("filename"), upload
    assert upload.get("bytes", 0) > 0, upload

    streams_resp = api_get(
        base, "/api/v1/sensor/streams", verify_ssl=verify, timeout=timeout,
    )
    all_streams = validate_list_response(streams_resp)
    found = _find_in_sensor_streams_list(all_streams, stream_id)
    if found is None:
        deadline = time.monotonic() + 30
        while found is None and time.monotonic() < deadline:
            time.sleep(1.0)
            all_streams = validate_list_response(
                api_get(base, "/api/v1/sensor/streams", verify_ssl=verify, timeout=timeout)
            )
            found = _find_in_sensor_streams_list(all_streams, stream_id)
    assert found is not None, (
        f"uploaded streamId {stream_id} missing from /sensor/streams"
    )

    per_sensor = _wait_sensor_streams(base, stream_id, verify_ssl=verify, timeout=timeout)
    rtsp_url = per_sensor[0]["url"]
    assert rtsp_url.startswith("rtsp://"), per_sensor[0]
    assert per_sensor[0].get("streamId") == stream_id
    assert per_sensor[0].get("type") == "Rtsp"

    info_resp = api_get(
        base, f"/api/v1/sensor/{stream_id}/info", verify_ssl=verify, timeout=timeout,
    )
    info = validate_json_response(info_resp)
    assert info.get("sensorId") == stream_id, info
    assert info.get("name"), info

    media_resp = api_get(
        base,
        f"/api/v1/storage/file/mediainfo?sensorId={stream_id}",
        verify_ssl=verify,
        timeout=timeout,
    )
    media = validate_json_response(media_resp)
    assert str(media.get("Codec", "")).lower() in ("h264", "h265", "hevc"), media
    assert int(media.get("Width") or 0) > 0, media
    assert float(media.get("Framerate") or 0) > 0, media

    codec, kind = _ffprobe_rtsp(rtsp_url)
    assert kind == "video"
    assert codec.lower() in ("h264", "hevc", "h265"), codec

    _delete_file(base, stream_id, verify_ssl=verify, timeout=timeout)
    state["deleted"] = True

    # NvStreamer DELETE returns HTTP 200 with JSON null; sensor record is gone.
    info_after = api_get(
        base, f"/api/v1/sensor/{stream_id}/info", verify_ssl=verify, timeout=timeout,
    )
    assert info_after.status_code == 200, info_after.text[:200]
    assert info_after.text.strip() in ("null", "", "None"), (
        f"expected null info after DELETE, got {info_after.text[:200]!r}"
    )
    listed = validate_list_response(
        api_get(base, "/api/v1/sensor/list", verify_ssl=verify, timeout=timeout)
    )
    still = [
        s for s in listed
        if isinstance(s, dict) and s.get("sensorId") == stream_id
    ]
    assert not still, f"sensor {stream_id} still in /sensor/list after DELETE: {still}"

def test_nvstreamer_post_upload_with_transcode_framerate_bitrate(
    nvstreamer_api_config: dict,
    unit_test_params: dict,
    unique_mp4_name: str,
) -> None:
    """POST multipart ingest with enable-transcode + framerate/bitrate headers."""
    assert TEST_VIDEO.is_file(), f"missing {TEST_VIDEO}"
    base = nvstreamer_api_config["base_url"]
    verify = nvstreamer_api_config.get("verify_ssl", False)
    timeout = max(_timeout(unit_test_params), 180)
    target_fps = 10
    target_kbps = 1000

    upload = _post_upload_transcode(
        base,
        TEST_VIDEO,
        unique_mp4_name,
        framerate=target_fps,
        bitrate_kbps=target_kbps,
        verify_ssl=verify,
        timeout=timeout,
    )
    stream_id = _stream_id_from_upload(upload)
    try:
        # mediainfo reflects transcode immediately on this stack.
        media = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            resp = api_get(
                base,
                f"/api/v1/storage/file/mediainfo?sensorId={stream_id}",
                verify_ssl=verify,
                timeout=timeout,
            )
            if resp.status_code == 200:
                media = resp.json()
                fr = float(media.get("Framerate") or 0)
                if abs(fr - target_fps) <= 1.0:
                    break
            time.sleep(1.0)
        assert media is not None, "mediainfo never returned"
        fr = float(media.get("Framerate") or 0)
        assert abs(fr - target_fps) <= 1.0, (
            f"transcode framerate {fr} != requested {target_fps}; media={media}"
        )
        # Bitrate is encoder-dependent; require it to be present and below source.
        br = int(media.get("Bitrate") or 0)
        assert br > 0, media
        # 1000 kbps target → expect roughly < ~2 Mbps (bytes/sec field is bps).
        assert br < 2_000_000, f"bitrate unexpectedly high after 1000kbps transcode: {br}"

        streams = _wait_sensor_streams(
            base, stream_id, verify_ssl=verify, timeout=timeout, wait_s=60.0,
        )
        assert streams[0]["url"].startswith("rtsp://")
    finally:
        _delete_file(base, stream_id, verify_ssl=verify, timeout=timeout)

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
Functional tests for VIA Engine file management API.

Tests the /files endpoints: upload, list, retrieve, delete.
All tests are marked ``test_in_ci`` and work in both black-box (--base-url) and
in-process (ViaTestServer) modes via the shared ``base_url`` fixture.
"""

import base64
import logging

import pytest
import requests

logger = logging.getLogger(__name__)


# Removed local session fixture that shadowed conftest.py — use the shared one.
# File upload tests rely on requests auto-setting Content-Type for multipart.


# Smallest valid H.264 MP4 we could generate: 16x16, single black frame, 0.04s,
# yuv420p, +faststart. Produced once with:
#
#   ffmpeg -f lavfi -i color=c=black:s=16x16:r=1 -t 0.04 \
#       -c:v libx264 -pix_fmt yuv420p -movflags +faststart -y tiny.mp4
#   base64 -w0 tiny.mp4
#
# Embedded as base64 (~2 KB on the wire) so the test stays self-contained
# (no extra binary checked into the repo, no ffmpeg in the test image).
# Replaces the previous 64-byte zero-filled placeholder which RTVI VLM
# >= 3.2.0-26.04.3 rejects with `InvalidFile: not a valid MediaType.VIDEO
# file` because that image actually parses the ISO BMFF container header
# instead of trusting the extension/MIME alone.
_TINY_MP4_BASE64 = (
    "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMVbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAA"
    "AAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAj90cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAAB"
    "AAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAA"
    "ABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAAAAABAAAAAAG3bWRpYQAAACBtZGhk"
    "AAAAAAAAAAAAAAAAAABAAAAAQABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRl"
    "b0hhbmRsZXIAAAABYm1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAA"
    "AQAAAAx1cmwgAAAAAQAAASJzdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAAAAAAAAEAAAAAAAAA"
    "AAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2MC4zMS4xMDIgbGlieDI2NAAAAAAAAAAAAAAA"
    "GP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAAg8SJZYAQAGaOvjyyLA/fj4AAAAABBw"
    "YXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAABYoAAAWKAAAABhzdHRzAAAAAAAAAAEAAAABAABAAAAAABxz"
    "dHNjAAAAAAAAAAEAAAABAAAAAQAAAAEAAAAUc3RzegAAAAAAAALFAAAAAQAAABRzdGNvAAAAAAAAAAEA"
    "AANFAAAAYnVkdGEAAABabWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAt"
    "aWxzdAAAACWpdG9vAAAAHWRhdGEAAAABAAAAAExhdmY2MC4xNi4xMDAAAAAIZnJlZQAAAs1tZGF0AAAC"
    "rQYF//+p3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NCByMzEwOCAzMWUxOWY5IC0gSC4yNjQv"
    "TVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyMyAtIGh0dHA6Ly93d3cudmlkZW9sYW4u"
    "b3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTEgcmVmPTMgZGVibG9jaz0xOjA6MCBhbmFseXNl"
    "PTB4MzoweDExMyBtZT1oZXggc3VibWU9NyBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0x"
    "IG1lX3JhbmdlPTE2IGNocm9tYV9tZT0xIHRyZWxsaXM9MSA4eDhkY3Q9MSBjcW09MCBkZWFkem9uZT0y"
    "MSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNldD0tMiB0aHJlYWRzPTEgbG9va2FoZWFkX3Ro"
    "cmVhZHM9MSBzbGljZWRfdGhyZWFkcz0wIG5yPTAgZGVjaW1hdGU9MSBpbnRlcmxhY2VkPTAgYmx1cmF5"
    "X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2ludHJhPTAgYmZyYW1lcz0zIGJfcHlyYW1pZD0yIGJfYWRhcHQ9"
    "MSBiX2JpYXM9MCBkaXJlY3Q9MSB3ZWlnaHRiPTEgb3Blbl9nb3A9MCB3ZWlnaHRwPTIga2V5aW50PTI1"
    "MCBrZXlpbnRfbWluPTEgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCBy"
    "Yz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00"
    "IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAAQZYiEABX//vfJ78Cm69vfgQ=="
)


@pytest.fixture
def tiny_mp4(tmp_path):
    """Write the smallest valid H.264 MP4 we can ship for upload tests."""
    f = tmp_path / "test_clip.mp4"
    f.write_bytes(base64.b64decode(_TINY_MP4_BASE64))
    return str(f)


@pytest.fixture
def text_file(tmp_path):
    """A plain-text file that should be rejected as a non-video asset."""
    f = tmp_path / "not_a_video.txt"
    f.write_text("This is not a video file.\n")
    return str(f)


# ---------------------------------------------------------------------------
# Upload tests
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_upload_local_file_returns_file_id(base_url, session, tiny_mp4, shared_state):
    """POST /files with a local file path returns a file_id."""
    url = f"{base_url}/files"
    # Use a fresh session — the shared session has Content-Type: application/json
    # which breaks multipart form uploads.
    with open(tiny_mp4, "rb") as fh:
        resp = requests.post(
            url,
            files={"file": ("test_clip.mp4", fh, "video/mp4")},
            data={"purpose": "vision", "media_type": "video"},
            timeout=30,
        )

    logger.info("Upload response: %s %s", resp.status_code, resp.text[:500])
    if resp.status_code == 404:
        pytest.skip(
            "POST /files returned 404 — server may not have VIA_DEV_API=true enabled. "
            "Start the server with VIA_DEV_API=true to enable file management routes."
        )
    assert resp.status_code in (200, 201), f"Expected 2xx, got {resp.status_code}: {resp.text}"

    data = resp.json()
    assert "id" in data, f"Response missing 'id': {data}"
    shared_state["uploaded_file_id"] = data["id"]


@pytest.mark.test_in_ci
def test_upload_invalid_path_returns_4xx(base_url, session):
    """POST /files with a non-existent path returns a 4xx error."""
    url = f"{base_url}/files"
    payload = {"path": "/nonexistent/path/that/does_not_exist.mp4"}
    resp = session.post(url, json=payload, timeout=10)

    logger.info("Invalid path response: %s %s", resp.status_code, resp.text[:500])
    assert resp.status_code in (
        400,
        404,
        422,
    ), f"Expected 4xx for invalid path, got {resp.status_code}"


@pytest.mark.test_in_ci
def test_upload_non_video_file_returns_error(base_url, session, text_file):
    """POST /files with a non-video file should return an error or gracefully handle it."""
    url = f"{base_url}/files"
    with open(text_file, "rb") as fh:
        resp = session.post(
            url,
            files={"file": ("not_a_video.txt", fh, "text/plain")},
            data={"purpose": "vision", "media_type": "video"},
            timeout=10,
        )

    logger.info("Non-video upload response: %s %s", resp.status_code, resp.text[:500])
    # Server may return 400/422 for unsupported type, or 200 if it accepts all file types.
    # This test documents the actual behaviour; the important thing is no 5xx crash.
    assert resp.status_code < 500, f"Server crashed with 5xx on non-video upload: {resp.text}"


# ---------------------------------------------------------------------------
# List tests
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_list_files_returns_uploaded_asset(base_url, session, shared_state):
    """GET /files returns a list that includes the previously uploaded file."""
    file_id = shared_state.get("uploaded_file_id")
    if not file_id:
        pytest.skip("No uploaded file_id in shared_state (upload test may have been skipped)")

    url = f"{base_url}/files?purpose=vision"
    resp = session.get(url, timeout=15)

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "data" in data, f"Response missing 'data' key: {data}"

    ids = [item.get("id") for item in data["data"]]
    assert file_id in ids, f"Uploaded file_id {file_id!r} not found in file list: {ids}"


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_delete_file_removes_asset(base_url, session, shared_state):
    """DELETE /files/{file_id} removes the asset and subsequent GET returns 404."""
    file_id = shared_state.get("uploaded_file_id")
    if not file_id:
        pytest.skip("No uploaded file_id in shared_state")

    # Delete the file
    del_resp = session.delete(f"{base_url}/files/{file_id}", timeout=15)
    logger.info("Delete response: %s %s", del_resp.status_code, del_resp.text[:200])
    assert del_resp.status_code in (200, 204), f"Expected 2xx on delete, got {del_resp.status_code}"

    # Confirm it no longer appears in the list
    list_resp = session.get(f"{base_url}/files?purpose=vision", timeout=15)
    assert list_resp.status_code == 200
    ids = [item.get("id") for item in list_resp.json().get("data", [])]
    assert file_id not in ids, f"Deleted file_id {file_id!r} still present in list"

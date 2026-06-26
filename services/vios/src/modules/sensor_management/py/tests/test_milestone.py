# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Milestone (milestone_soap) control adaptor: pure SOAP/XML helpers, HTTP-mocked
connect/discover, and discovery registration via SensorManagement. No Milestone server required."""
from __future__ import annotations

import sensor_ms.adaptors.milestone as M
from sensor_ms.adaptors.base import AdaptorInfo
from sensor_ms.adaptors.milestone import (
    MilestoneControl,
    _embed_creds,
    build_login_envelope,
    parse_login_token,
    parse_systeminfo,
)
from sensor_ms.config import Config
from sensor_ms.core.sensor_management import SensorManagement
from sensor_ms.db.engine import make_engine
from sensor_ms.db.models import Base


# --- pure helpers ---
def test_build_login_envelope():
    env = build_login_envelope("inst-123", "tok-9")
    assert "<instanceId>inst-123</instanceId>" in env
    assert "<currentToken>tok-9</currentToken>" in env
    assert "videoos.net/2/XProtectCSServerCommand" in env


def test_parse_login_token():
    xml = (b'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
           b'<LoginResponse xmlns="http://videoos.net/2/XProtectCSServerCommand"><LoginResult>'
           b'<Token>ABC-TOKEN-123</Token></LoginResult></LoginResponse></s:Body></s:Envelope>')
    assert parse_login_token(xml) == "ABC-TOKEN-123"
    assert parse_login_token(b"not xml") == ""


def test_embed_creds():
    assert _embed_creds("rtsp://h:554/live/x", "u", "p") == "rtsp://u:p@h:554/live/x"
    assert _embed_creds("rtsp://h:554/live/x", "", "") == "rtsp://h:554/live/x"


def test_parse_systeminfo_builds_rtsp_urls():
    xml = (b'<systeminfo><cameras>'
           b'<camera name="Lobby"><guid>11111111-2222-3333-4444-555555555555</guid></camera>'
           b'<camera name="Dock"><guid>aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee</guid></camera>'
           b'<camera name="NoGuid"></camera>'
           b'</cameras></systeminfo>')
    cams = parse_systeminfo(xml, "10.0.0.5", "admin", "pw")
    assert len(cams) == 2                              # camera without a guid is skipped
    c0 = cams[0]
    assert c0["sensor_id"] == "11111111-2222-3333-4444-555555555555"
    assert c0["name"] == "Lobby_555555555555"
    assert c0["live_url"] == "rtsp://admin:pw@10.0.0.5:554/live/11111111-2222-3333-4444-555555555555"
    assert c0["replay_url"] == "rtsp://admin:pw@10.0.0.5:554/vod/11111111-2222-3333-4444-555555555555"
    assert c0["codec"] == "h264"


# --- HTTP-mocked connect/discover ---
class _FakeResp:
    def __init__(self, content=b"", status=200):
        self.content = content
        self.status_code = status


class _FakeClient:
    def __init__(self, post_resp=None, get_resp=None, **kw):
        self._post, self._get = post_resp, get_resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return self._post

    async def get(self, *a, **k):
        return self._get


async def test_refresh_token_and_discover(monkeypatch):
    login_xml = b"<Env><Token>T1</Token></Env>"
    sysinfo = b'<systeminfo><camera name="C"><guid>g-1-2-3-zzz</guid></camera></systeminfo>'
    ctl = MilestoneControl()
    ctl.info = AdaptorInfo(m_name="milestone_soap", m_ipaddress="10.0.0.9", m_user="u", m_password="p")
    monkeypatch.setattr(
        M.httpx, "AsyncClient",
        lambda **kw: _FakeClient(post_resp=_FakeResp(login_xml), get_resp=_FakeResp(sysinfo)))
    assert await ctl.refresh_token() == 0
    assert ctl._token == "T1"
    cams = await ctl.discover()
    assert len(cams) == 1 and cams[0]["sensor_id"] == "g-1-2-3-zzz"
    assert cams[0]["live_url"].endswith("/live/g-1-2-3-zzz")


async def test_discover_handles_login_failure(monkeypatch):
    ctl = MilestoneControl()
    ctl.info = AdaptorInfo(m_name="milestone_soap", m_ipaddress="10.0.0.9", m_user="u", m_password="p")
    monkeypatch.setattr(
        M.httpx, "AsyncClient",
        lambda **kw: _FakeClient(post_resp=_FakeResp(b"no token"), get_resp=_FakeResp(b"", status=401)))
    assert await ctl.refresh_token() == -1
    assert await ctl.discover() == []


# --- discovery registration via SensorManagement ---
class _FakeMilestoneControl:
    def __init__(self, cams):
        self._cams = cams

    async def discover(self):
        return self._cams


def _mgmt(tmp_path):
    cfg = Config(use_centralize_db=False, sqlite_db_path=str(tmp_path / "v.db"),
                 vst_data_path=str(tmp_path), use_message_broker="", adaptor="milestone_soap")
    Base.metadata.create_all(make_engine(cfg))
    return SensorManagement(cfg)


async def test_scan_milestone_registers_cameras(tmp_path):
    mgmt = _mgmt(tmp_path)
    assert mgmt._adaptor_name == "milestone_soap"
    mgmt._control = _FakeMilestoneControl([
        {"sensor_id": "cam-1", "name": "Lobby", "live_url": "rtsp://h:554/live/cam-1",
         "replay_url": "rtsp://h:554/vod/cam-1", "codec": "h264"},
    ])
    try:
        n = await mgmt.scan(force=True)
        assert n == 1
        row = mgmt.repo.get_sensor("cam-1")
        assert row is not None and row.name == "Lobby"
        streams = mgmt.repo.list_streams("cam-1")
        assert streams[0].stream_live_url == "rtsp://h:554/live/cam-1"
        assert streams[0].stream_replay_url == "rtsp://h:554/vod/cam-1"
        # idempotent re-scan adds nothing
        assert await mgmt.scan(force=True) == 0
    finally:
        await mgmt.stop()

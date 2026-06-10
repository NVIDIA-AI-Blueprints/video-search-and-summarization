# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ONVIF WS-Discovery + control-mapping tests.

Verifiable without a camera: WS-Discovery message build + ProbeMatch parse (deterministic), and the
pure profile/device-info -> model mapping (mock ONVIF objects). Live camera-matrix validation
(GetProfiles/GetStreamUri/PTZ/digest against real hardware) remains the outstanding P3 gate.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sensor_ms.adaptors.onvif.control import device_info_to_fields, profile_to_stream
from sensor_ms.adaptors.onvif.discovery import (
    WS_DISCOVERY_ADDR,
    WS_DISCOVERY_PORT,
    build_probe,
    dedup_matches,
    parse_probe_match,
)

# A realistic ProbeMatches response from an ONVIF NVT.
_PROBE_MATCH = b"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
 <SOAP-ENV:Body>
  <d:ProbeMatches>
   <d:ProbeMatch>
    <wsa:EndpointReference><wsa:Address>urn:uuid:abcd-1234</wsa:Address></wsa:EndpointReference>
    <d:Types>dn:NetworkVideoTransmitter</d:Types>
    <d:Scopes>onvif://www.onvif.org/name/AcmeCam onvif://www.onvif.org/hardware/X1</d:Scopes>
    <d:XAddrs>http://10.0.0.50/onvif/device_service http://[fe80::1]/onvif/device_service</d:XAddrs>
   </d:ProbeMatch>
  </d:ProbeMatches>
 </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""


def test_build_probe_has_action_and_message_id():
    msg = build_probe("11111111-2222-3333-4444-555555555555").decode()
    assert "/ws/2005/04/discovery/Probe" in msg
    assert "uuid:11111111-2222-3333-4444-555555555555" in msg
    assert "NetworkVideoTransmitter" in msg


def test_parse_probe_match():
    matches = parse_probe_match(_PROBE_MATCH)
    assert len(matches) == 1
    m = matches[0]
    assert m.address == "urn:uuid:abcd-1234"
    assert m.device_service_url == "http://10.0.0.50/onvif/device_service"
    assert len(m.xaddrs) == 2
    assert "NetworkVideoTransmitter" in m.types


def test_parse_probe_match_garbage_is_safe():
    assert parse_probe_match(b"not xml") == []
    assert parse_probe_match(b"<x/>") == []


def test_dedup_matches_by_service_url():
    # Two ProbeMatch entries from the same device (same XAddrs) collapse to one.
    matches = parse_probe_match(_PROBE_MATCH) + parse_probe_match(_PROBE_MATCH)
    assert len(matches) == 2
    deduped = dedup_matches(matches)
    assert len(deduped) == 1
    assert deduped[0].device_service_url == "http://10.0.0.50/onvif/device_service"


def test_discovery_constants():
    assert (WS_DISCOVERY_ADDR, WS_DISCOVERY_PORT) == ("239.255.255.250", 3702)


def test_profile_to_stream_mapping():
    prof = SimpleNamespace(
        token="profile_1", Name="MainStream",
        VideoEncoderConfiguration=SimpleNamespace(
            Encoding="H265",
            Resolution=SimpleNamespace(Width=1920, Height=1080),
            RateControl=SimpleNamespace(FrameRateLimit=30, BitrateLimit=4096),
            GovLength=50,
        ),
    )
    s = profile_to_stream(prof, "rtsp://10.0.0.50:554/Streaming/Channels/101", is_main=True)
    assert s["streamId"] == "profile_1" and s["isMain"] is True and s["type"] == "Rtsp"
    assert s["url"] == "rtsp://10.0.0.50:554/Streaming/Channels/101"
    assert s["name"] == "MainStream"
    assert s["metadata"] == {"bitrate": "4096", "codec": "H265", "framerate": "30",
                             "govlength": "50", "resolution": "1920x1080"}


def test_device_info_mapping():
    info = SimpleNamespace(Manufacturer="Acme", Model="X1", SerialNumber="SN9",
                           FirmwareVersion="1.2.3", HardwareId="HW7")
    f = device_info_to_fields(info)
    assert f == {"hardware": "HW7", "manufacturer": "Acme", "serialNumber": "SN9",
                 "firmwareVersion": "1.2.3"}


def test_loader_selects_onvif_control():
    from sensor_ms.adaptors.loader import load_control_adaptor
    from sensor_ms.adaptors.onvif.control import OnvifControl

    assert isinstance(load_control_adaptor("onvif"), OnvifControl)
    assert load_control_adaptor("vst_rtsp") is None     # url-only adaptor, no control class
    assert load_control_adaptor("nonexistent") is None


def test_scan_runs_onvif_discovery(tmp_path, monkeypatch):
    # adaptor=onvif -> scan() invokes WS-Discovery and returns the device count; other adaptors no-op.
    import sensor_ms.adaptors.onvif.discovery as disc
    from sensor_ms.config import Config
    from sensor_ms.core.sensor_management import SensorManagement
    from sensor_ms.db.engine import make_engine
    from sensor_ms.db.models import Base

    async def fake_discover(message_id, timeout=3.0, bind_ip="0.0.0.0"):
        return parse_probe_match(_PROBE_MATCH)  # one device

    monkeypatch.setattr(disc, "discover", fake_discover)

    async def run(adaptor):
        cfg = Config(use_centralize_db=False, sqlite_db_path=str(tmp_path / f"{adaptor}.db"),
                     vst_data_path=str(tmp_path), use_message_broker="", adaptor=adaptor)
        Base.metadata.create_all(make_engine(cfg))
        mgmt = SensorManagement(cfg)
        try:
            return await mgmt.scan(force=True)
        finally:
            await mgmt.stop()

    assert asyncio.run(run("onvif")) == 1
    assert asyncio.run(run("vst_rtsp")) == 0


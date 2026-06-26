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

from datetime import datetime, timedelta, timezone

from sensor_ms.adaptors.onvif.control import (
    ONVIF_TIME_SYNC_DRIFT_THRESHOLD_SECS,
    OnvifControl,
    _extract_utc_datetime,
    _nearest_resolution,
    _ntp_entry,
    default_encode_from_options,
    device_info_to_fields,
    profile_to_stream, 
)
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


def test_scope_fields_parsing():
    # Real Hikvision-style scopes: URL-decoded name, hardware, type, MAC, profiles.
    pm = parse_probe_match(
        b'<d:ProbeMatches xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
        b' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"><d:ProbeMatch>'
        b'<wsa:EndpointReference><wsa:Address>urn:uuid:x</wsa:Address></wsa:EndpointReference>'
        b'<d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/Profile/Streaming '
        b'onvif://www.onvif.org/Profile/G onvif://www.onvif.org/MAC/98:8b:0a:38:e0:d7 '
        b'onvif://www.onvif.org/hardware/DS-2CD2T43G0-I5 '
        b'onvif://www.onvif.org/name/HIKVISION%20DS-2CD2T43G0-I5</d:Scopes>'
        b'<d:XAddrs>http://10.0.0.50/onvif/device_service</d:XAddrs></d:ProbeMatch></d:ProbeMatches>')[0]
    sf = pm.scope_fields()
    assert sf["name"] == "HIKVISION%20DS-2CD2T43G0-I5"   # kept RAW (C++ parity)
    assert sf["hardware"] == "DS-2CD2T43G0-I5"
    assert sf["type"] == "video_encoder"
    assert sf["mac"] == "98:8b:0a:38:e0:d7"
    assert set(sf["profiles"]) == {"Streaming", "G"}


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
    assert f == {"hardware": "X1", "manufacturer": "Acme", "serialNumber": "SN9",
                 "firmwareVersion": "1.2.3"}   # hardware = Model, not HardwareId


def test_loader_selects_onvif_control():
    from sensor_ms.adaptors.loader import load_control_adaptor
    from sensor_ms.adaptors.onvif.control import OnvifControl

    assert isinstance(load_control_adaptor("onvif"), OnvifControl)
    assert load_control_adaptor("vst_rtsp") is None     # url-only adaptor, no control class
    assert load_control_adaptor("nonexistent") is None


def _sdt(dt):
    """Build a fake ONVIF GetSystemDateAndTime response carrying UTCDateTime=dt."""
    return SimpleNamespace(UTCDateTime=SimpleNamespace(
        Date=SimpleNamespace(Year=dt.year, Month=dt.month, Day=dt.day),
        Time=SimpleNamespace(Hour=dt.hour, Minute=dt.minute, Second=dt.second)))


class _FakeDev:
    def __init__(self, cam_time):
        self._cam_time = cam_time
        self.set_called = False
        self.set_req = None
        self.ntp_arg = None

    async def GetSystemDateAndTime(self):
        if self._cam_time is None:
            raise RuntimeError("no time")
        return _sdt(self._cam_time)

    def create_type(self, _name):
        return SimpleNamespace()

    async def SetSystemDateAndTime(self, req):
        self.set_called = True
        self.set_req = req

    async def SetNTP(self, arg):
        self.ntp_arg = arg


class _FakeCam:
    def __init__(self, dev):
        self._dev = dev

    async def create_devicemgmt_service(self):
        return self._dev


def _control_with_cam_time(monkeypatch, cam_time):
    ctrl = OnvifControl()
    dev = _FakeDev(cam_time)

    async def fake_connect(*_a, **_k):
        return _FakeCam(dev)

    async def fake_close(_cam):
        return None

    monkeypatch.setattr(ctrl, "_connect", fake_connect)
    monkeypatch.setattr(ctrl, "_close", fake_close)
    return ctrl, dev


_SENSOR = {"ip": "10.0.0.7", "port": 80, "user": "admin", "password": "pw"}


def test_extract_utc_datetime():
    dt = datetime(2026, 6, 24, 12, 30, 15, tzinfo=timezone.utc)
    assert _extract_utc_datetime(_sdt(dt)) == dt
    assert _extract_utc_datetime(SimpleNamespace()) is None
    assert _extract_utc_datetime(SimpleNamespace(UTCDateTime=SimpleNamespace(Date=None, Time=None))) is None


def test_time_sync_skips_when_within_threshold(monkeypatch):
    # Camera clock matches host UTC -> no correction, returns 1 (in-sync).
    ctrl, dev = _control_with_cam_time(monkeypatch, datetime.now(timezone.utc))
    assert asyncio.run(ctrl.synchronize_sensor_time(_SENSOR)) == 1
    assert dev.set_called is False


def test_time_sync_corrects_when_drifted(monkeypatch):
    # Camera clock far in the past -> correction applied, returns 0.
    drifted = datetime.now(timezone.utc) - timedelta(seconds=ONVIF_TIME_SYNC_DRIFT_THRESHOLD_SECS + 60)
    ctrl, dev = _control_with_cam_time(monkeypatch, drifted)
    assert asyncio.run(ctrl.synchronize_sensor_time(_SENSOR)) == 0
    assert dev.set_called is True


def test_time_sync_corrects_when_time_unreadable(monkeypatch):
    # Device doesn't report a readable time -> fall through and set it (returns 0).
    ctrl, dev = _control_with_cam_time(monkeypatch, None)
    assert asyncio.run(ctrl.synchronize_sensor_time(_SENSOR)) == 0
    assert dev.set_called is True


def test_ntp_entry_ipv4_vs_dns():
    assert _ntp_entry("192.168.1.1") == {"Type": "IPv4", "IPv4Address": "192.168.1.1"}
    assert _ntp_entry("time.google.com") == {"Type": "DNS", "DNSname": "time.google.com"}
    assert _ntp_entry("999.1.1.1") == {"Type": "DNS", "DNSname": "999.1.1.1"}  # out-of-range octet


def test_configure_sensor_ntp_sets_servers_and_ntp_mode(monkeypatch):
    ctrl, dev = _control_with_cam_time(monkeypatch, datetime.now(timezone.utc))
    rc = asyncio.run(ctrl.configure_sensor_ntp(_SENSOR, ["time.google.com", "10.0.0.1"]))
    assert rc == 0
    assert dev.ntp_arg["FromDHCP"] is False
    assert dev.ntp_arg["NTPManual"] == [
        {"Type": "DNS", "DNSname": "time.google.com"},
        {"Type": "IPv4", "IPv4Address": "10.0.0.1"},
    ]
    assert dev.set_req.DateTimeType == "NTP"   # device disciplines its own clock -> ms-level


def test_configure_sensor_ntp_no_servers_is_noop(monkeypatch):
    ctrl, dev = _control_with_cam_time(monkeypatch, datetime.now(timezone.utc))
    assert asyncio.run(ctrl.configure_sensor_ntp(_SENSOR, [])) == 1
    assert dev.ntp_arg is None


def test_batch_sync_empty_is_noop():
    assert asyncio.run(OnvifControl().synchronize_sensors_time_batch([])) == 0


def test_batch_sync_sets_all_cameras_to_same_whole_second(monkeypatch):
    # All cameras must be set to the SAME whole-second boundary (this is what locks them to each
    # other). Each gets its own connection; the fire is via asyncio.gather (simultaneous).
    ctrl = OnvifControl()
    devs = {}

    async def fake_connect(host, port, user, pw):
        dev = _FakeDev(datetime.now(timezone.utc))
        devs[host] = dev
        return _FakeCam(dev)

    async def fake_close(_cam):
        return None

    monkeypatch.setattr(ctrl, "_connect", fake_connect)
    monkeypatch.setattr(ctrl, "_close", fake_close)
    sensors = [dict(_SENSOR, ip="10.0.0.1"), dict(_SENSOR, ip="10.0.0.2"),
               dict(_SENSOR, ip="10.0.0.3")]
    n = asyncio.run(ctrl.synchronize_sensors_time_batch(sensors, compensation_ms=50))
    assert n == 3
    assert len(devs) == 3 and all(d.set_called for d in devs.values())
    # every camera received the identical Time block, and Second is a whole second (0..59)
    times = {tuple(d.set_req.UTCDateTime["Time"].values()) for d in devs.values()}
    assert len(times) == 1
    assert all(0 <= v <= 59 for v in next(iter(times)))


def test_default_encode_from_options_clamps_to_camera():
    # framerate/gov clamped to ranges; resolution snapped to nearest supported; bitrate passthrough.
    opt = SimpleNamespace(
        FrameRateRange=SimpleNamespace(Min=1, Max=25),
        GovLengthRange=SimpleNamespace(Min=1, Max=50),
        ResolutionsAvailable=[SimpleNamespace(Width=1280, Height=720),
                              SimpleNamespace(Width=1920, Height=1080),
                              SimpleNamespace(Width=3840, Height=2160)],
    )
    enc = default_encode_from_options(
        {"bitrate": 8000, "framerate": 30.0, "gov_length": 60, "resolution": "1920x1080"}, opt)
    assert enc["Bitrate"] == "8000"
    assert enc["FrameRate"] == "25"        # 30 clamped to max 25
    assert enc["GovLength"] == "50"        # 60 clamped to max 50
    assert enc["Resolution"] == {"Width": 1920, "Height": 1080}   # exact match


def test_default_encode_from_options_no_options_passthrough():
    # No options block -> framerate/gov applied as-is, resolution left unchanged (no list to snap to).
    enc = default_encode_from_options(
        {"bitrate": 6000, "framerate": 30.0, "gov_length": 60, "resolution": "1280x720"}, None)
    assert enc == {"Bitrate": "6000", "FrameRate": "30", "GovLength": "60"}


def test_default_encode_skips_zero_values():
    assert default_encode_from_options({"bitrate": 0, "framerate": 0, "gov_length": 0,
                                        "resolution": ""}, None) == {}


def test_nearest_resolution_picks_closest():
    avail = [SimpleNamespace(Width=640, Height=480), SimpleNamespace(Width=1280, Height=720),
             SimpleNamespace(Width=1920, Height=1080)]
    assert _nearest_resolution("1900x1070", avail) == (1920, 1080)   # closest by pixel count
    assert _nearest_resolution("700x500", avail) == (640, 480)
    assert _nearest_resolution("1920x1080", None) is None            # no options -> unchanged


class _FakeMedia:
    def __init__(self, vec, opts):
        self._vec = vec
        self._opts = opts
        self.set_config = None

    async def GetProfiles(self):
        return [SimpleNamespace(VideoEncoderConfiguration=self._vec)]

    async def GetVideoEncoderConfigurationOptions(self, _arg):
        return self._opts

    def create_type(self, _name):
        return SimpleNamespace()

    async def SetVideoEncoderConfiguration(self, req):
        self.set_config = req


class _FakeCamMedia:
    def __init__(self, media):
        self._media = media

    async def create_media_service(self):
        return self._media


def test_apply_default_encode_settings_pushes_clamped(monkeypatch):
    ctrl = OnvifControl()
    vec = SimpleNamespace(
        Encoding="H264", token="vec0",
        RateControl=SimpleNamespace(BitrateLimit=4000, FrameRateLimit=15, EncodingInterval=1),
        Resolution=SimpleNamespace(Width=1280, Height=720),
        H264=SimpleNamespace(GovLength=30))
    opts = SimpleNamespace(H264=SimpleNamespace(
        FrameRateRange=SimpleNamespace(Min=1, Max=25),
        GovLengthRange=SimpleNamespace(Min=1, Max=50),
        ResolutionsAvailable=[SimpleNamespace(Width=1920, Height=1080)]))
    media = _FakeMedia(vec, opts)

    async def fake_connect(*_a, **_k):
        return _FakeCamMedia(media)

    async def fake_close(_c):
        return None

    monkeypatch.setattr(ctrl, "_connect", fake_connect)
    monkeypatch.setattr(ctrl, "_close", fake_close)
    rc = asyncio.run(ctrl.apply_default_encode_settings(
        _SENSOR, {"bitrate": 8000, "framerate": 30.0, "gov_length": 60, "resolution": "1920x1080"}))
    assert rc == 0
    assert vec.RateControl.BitrateLimit == 8000
    assert vec.RateControl.FrameRateLimit == 25            # clamped to camera max
    assert vec.H264.GovLength == 50                        # clamped to camera max
    assert (vec.Resolution.Width, vec.Resolution.Height) == (1920, 1080)
    assert media.set_config.Configuration is vec and media.set_config.ForcePersistence is True


def test_batch_sync_skips_unreachable_cameras(monkeypatch):
    # A camera that fails to connect is dropped; the reachable ones are still synced.
    ctrl = OnvifControl()
    good = _FakeDev(datetime.now(timezone.utc))

    async def fake_connect(host, port, user, pw):
        if host == "10.0.0.bad":
            raise RuntimeError("unreachable")
        return _FakeCam(good)

    async def fake_close(_cam):
        return None

    monkeypatch.setattr(ctrl, "_connect", fake_connect)
    monkeypatch.setattr(ctrl, "_close", fake_close)
    sensors = [dict(_SENSOR, ip="10.0.0.bad"), dict(_SENSOR, ip="10.0.0.9")]
    assert asyncio.run(ctrl.synchronize_sensors_time_batch(sensors, compensation_ms=50)) == 1
    assert good.set_called is True


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


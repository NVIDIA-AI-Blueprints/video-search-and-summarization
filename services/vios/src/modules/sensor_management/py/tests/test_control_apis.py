# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the control-plane write/device APIs added on top of the read/add/delete core:
info update, replace, configuration apply, debug plug/unplug, and the ONVIF-backed reboot/network/
settings paths (exercised through a fake control adaptor + the pure ONVIF mapping helpers).

DB-backed cases use a throwaway SQLite file with the notification broker disabled, so no redis is
required. The camera-facing ONVIF calls are validated via the unit-tested pure helpers and a fake
adaptor; live-hardware validation remains the outstanding P3 gate.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sensor_ms.adaptors.base import SensorControlAdaptor
from sensor_ms.adaptors.onvif.control import (
    _apply_encode_settings,
    encoder_options_to_encode,
    netmask_to_prefix,
    network_interface_to_info,
    prefix_to_netmask,
)
from sensor_ms.api.errors import VmsError, VmsErrorCode
from sensor_ms.config import Config
from sensor_ms.core import mapping
from sensor_ms.core.sensor_management import (
    ONVIF_DISCOVERY_MISS_THRESHOLD,
    SENSOR_TYPE_ONVIF,
    SensorManagement,
)
from sensor_ms.db.engine import make_engine
from sensor_ms.db.models import Base, SensorDetails


def _sqlite_cfg(tmp_path, adaptor="vst_rtsp", max_sensors=8) -> Config:
    return Config(
        use_centralize_db=False,
        sqlite_db_path=str(tmp_path / "vst.db"),
        vst_data_path=str(tmp_path),
        use_message_broker="",   # events disabled -> no redis needed
        adaptor=adaptor,
        max_sensors_supported=max_sensors,
    )


def _mgmt(tmp_path, adaptor="vst_rtsp") -> SensorManagement:
    cfg = _sqlite_cfg(tmp_path, adaptor)
    Base.metadata.create_all(make_engine(cfg))
    return SensorManagement(cfg)


# --- POST /add duplicate-conflict error messages (AMC bug-fix parity) ---------------
async def test_add_duplicate_errors_carry_conflicting_id_and_name(tmp_path):
    """All add-conflict errors must surface the conflicting sensor's id+name so AMC can distinguish
    the cases (Slack thread w/ Prakhar + C++ sensor_management_utils.cpp parity)."""
    mgmt = _mgmt(tmp_path)
    try:
        sid = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.1:554/cam", "name": "first"})
        # 1) same URL + same name -> URL conflict
        with pytest.raises(VmsError) as e1:
            await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.1:554/cam", "name": "first"})
        assert e1.value.code == VmsErrorCode.InvalidParameterError
        assert e1.value.message == f"Sensor exists already, sensorId: {sid}, sensorName: first"
        # 2) same URL + different name -> still URL conflict (same message)
        with pytest.raises(VmsError) as e2:
            await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.1:554/cam", "name": "different"})
        assert e2.value.message == f"Sensor exists already, sensorId: {sid}, sensorName: first"
        # 3) different URL + same name -> name conflict
        with pytest.raises(VmsError) as e3:
            await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.2:554/other", "name": "first"})
        assert e3.value.code == VmsErrorCode.InvalidParameterError
        assert e3.value.message == (
            f"User given name is invalid or already exists, sensorId: {sid}, sensorName: first")
    finally:
        await mgmt.stop()


# --- POST /{id}/info ----------------------------------------------------------------
async def test_set_sensor_info_updates_name_tags_position(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        sid = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.9:554/s1", "name": "orig"})
        ok = await mgmt.set_sensor_info(sid, {
            "name": "renamed", "tags": "a,b", "location": "lobby",
            "position": {"depth": "5", "fieldOfView": "90",
                         "coordinates": {"x": "1", "y": "2"},
                         "geoLocation": {"latitude": "10", "longitude": "20"}},
        })
        assert ok is True
        info = await mgmt.get_sensor(sid)
        assert info["name"] == "renamed" and info["tags"] == "a,b" and info["location"] == "lobby"
        assert info["position"]["fieldOfView"] == "90"
        assert info["position"]["coordinates"] == {"x": "1", "y": "2"}
        assert info["position"]["geoLocation"] == {"latitude": "10", "longitude": "20"}
    finally:
        await mgmt.stop()


async def test_set_sensor_info_rejects_duplicate_name(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.1:554/a", "name": "cam-a"})
        sid_b = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.2:554/b", "name": "cam-b"})
        with pytest.raises(VmsError) as ei:
            await mgmt.set_sensor_info(sid_b, {"name": "cam-a"})
        assert ei.value.code == VmsErrorCode.InvalidParameterError
        # unchanged in DB
        assert (await mgmt.get_sensor(sid_b))["name"] == "cam-b"
    finally:
        await mgmt.stop()


async def test_set_sensor_info_truncates_name(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        sid = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.9:554/s1", "name": "x"})
        await mgmt.set_sensor_info(sid, {"name": "y" * 500})
        assert len((await mgmt.get_sensor(sid))["name"]) == 175
    finally:
        await mgmt.stop()


async def test_set_sensor_info_missing_sensor(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        with pytest.raises(VmsError) as ei:
            await mgmt.set_sensor_info("nope", {"name": "z"})
        assert ei.value.code == VmsErrorCode.VMSInternalError
    finally:
        await mgmt.stop()


# --- POST /{id}/replace -------------------------------------------------------------
def _set_offline(mgmt, sid):
    from sqlalchemy import update
    with mgmt.repo._sf() as s, s.begin():
        s.execute(update(SensorDetails).where(SensorDetails.sensor_id == sid).values(sensor_status=0))


async def test_replace_sensor_swaps_onto_old_id(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        old = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.1:554/old", "name": "old-cam"})
        new = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.2:554/new", "name": "new-cam"})
        _set_offline(mgmt, old)
        await mgmt.replace_sensor(old, {"sensorId": new})
        ids = {x["sensorId"] for x in await mgmt.list_sensors()}
        assert old in ids and new not in ids            # collapsed onto the old id
        info = await mgmt.get_sensor(old)
        assert info["name"] == "new-cam"                 # new sensor's details, old id
        streams = await mgmt.list_streams(old)
        assert len(streams) == 1 and streams[0]["streamId"] == old
    finally:
        await mgmt.stop()


async def test_replace_sensor_rejects_active_old(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        old = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.1:554/old", "name": "o"})
        new = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.2:554/new", "name": "n"})
        with pytest.raises(VmsError) as ei:                 # old still online (sensor_status=1)
            await mgmt.replace_sensor(old, {"sensorId": new})
        assert ei.value.code == VmsErrorCode.InvalidParameterError
    finally:
        await mgmt.stop()


async def test_replace_sensor_validation(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        with pytest.raises(VmsError):
            await mgmt.replace_sensor("x", {})                       # empty sensorId
        with pytest.raises(VmsError):
            await mgmt.replace_sensor("missing", {"sensorId": "also-missing"})
    finally:
        await mgmt.stop()


# --- POST /configuration ------------------------------------------------------------
async def test_apply_configuration_updates_ntp_and_interfaces(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        await mgmt.apply_configuration({"ntpServers": ["1.1.1.1", ""], "deviceDiscoveryInterfaces": ["eth0"]})
        assert mgmt._cfg.ntp_servers == ["1.1.1.1"]      # empties filtered
        assert mgmt._cfg.sensor_discovery_interfaces == ["eth0"]
        cfg_out = mgmt.get_configuration()
        assert cfg_out["ntpServers"] == ["1.1.1.1"]
    finally:
        await mgmt.stop()


# --- debug plug/unplug --------------------------------------------------------------
async def test_debug_block_unblock(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        assert mgmt.sensor_block_status("10.0.0.5") == "plug"
        mgmt.block_sensor("10.0.0.5", "unplug")
        assert mgmt.sensor_block_status("10.0.0.5") == "unplug"
        assert "10.0.0.5" in mgmt._blocked_ips
        mgmt.block_sensor("10.0.0.5", "plug")
        assert mgmt.sensor_block_status("10.0.0.5") == "plug"
    finally:
        await mgmt.stop()


# --- ONVIF-backed reboot / network / settings (fake control adaptor) ----------------
class _FakeControl(SensorControlAdaptor):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def connect(self):
        return 0

    async def get_sensor_stream_info(self, sensor):
        return 0

    async def reboot_sensor(self, sensor):
        self.calls.append(("reboot", sensor["ip"]))
        return 0

    async def synchronize_sensor_time(self, sensor):
        self.calls.append(("time_sync", sensor["ip"]))
        return 0

    async def synchronize_sensors_time_batch(self, sensors, compensation_ms=20):
        self.calls.append(("time_sync_batch", tuple(s["ip"] for s in sensors), compensation_ms))
        return len(sensors)

    async def configure_sensor_ntp(self, sensor, ntp_servers):
        self.calls.append(("ntp", sensor["ip"], tuple(ntp_servers)))
        return 0

    async def get_network_info(self, sensor):
        return 0, {"isIpv4Enabled": True, "dhcpV4": "false", "ipAddressV4": "10.0.0.7",
                   "subnetMaskV4": "255.255.255.0", "isIpv6Enabled": False, "dhcpV6": "false",
                   "ipAddressV6": "", "subnetMaskV6": ""}

    async def set_network_info(self, sensor, net):
        self.calls.append(("set_net", net.get("ipAddressV4")))
        return 0, True

    async def get_settings(self, sensor, type_=""):
        return 0, {"profile_1": {"Encode": {"Encoding": {"AllowedValues": ["H264"], "Value": "H264"}}}}

    async def set_settings(self, sensor, settings):
        self.calls.append(("set_settings", settings))
        return 0


def _add_onvif(mgmt, sid="onvif-1", user="admin"):
    now = "2026-06-09T00:00:00Z"
    mgmt.repo.insert_sensor(SensorDetails(
        device_id="VST", sensor_id=sid, sensor_hw_id=sid, username=user, name="cam",
        ipaddress="10.0.0.7", url="http://10.0.0.7/onvif/device_service", type=SENSOR_TYPE_ONVIF,
        position=mapping.position_api_to_db(None), is_remote="false", http_status=200,
        sensor_status=1, created_date_time=now, modified_date_time=now), "pw", now)
    return sid


async def test_reboot_via_adaptor(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _FakeControl()
    try:
        sid = _add_onvif(mgmt)
        await mgmt.reboot_sensor(sid)
        assert mgmt._control.calls == [("reboot", "10.0.0.7")]
    finally:
        await mgmt.stop()


async def test_time_sync_pushes_to_online_onvif(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _FakeControl()
    try:
        assert mgmt._time_sync_enabled() is True
        _add_onvif(mgmt)                       # online ONVIF sensor (sensor_status=1)
        synced = await mgmt._time_sync_once()
        # Manual mode -> simultaneous boundary-aligned batch across all credentialed cameras.
        assert synced == 1
        assert ("time_sync_batch", ("10.0.0.7",), mgmt._cfg.onvif_sensor_time_sync_compensation_ms) \
            in mgmt._control.calls
    finally:
        await mgmt.stop()


async def test_device_type_matches_adaptor(tmp_path):
    # /sensor/version "type": mms for the Milestone adaptor, vst otherwise (C++ getDeviceType parity).
    vst = _mgmt(tmp_path, adaptor="onvif")
    mms = _mgmt(tmp_path, adaptor="milestone_soap")
    try:
        assert vst.device_type == "vst"
        assert mms.device_type == "mms"
    finally:
        await vst.stop()
        await mms.stop()


async def test_time_sync_uses_ntp_when_configured(tmp_path):
    # With use_sensor_ntp_time + ntp_servers, the pass switches cameras to NTP (the ms-level path)
    # via configure_sensor_ntp -- once per camera, not a manual SetSystemDateAndTime each cycle.
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _FakeControl()
    mgmt._cfg.use_sensor_ntp_time = True
    mgmt._cfg.ntp_servers = ["time.google.com"]
    try:
        _add_onvif(mgmt)
        assert await mgmt._time_sync_once() == 1
        assert ("ntp", "10.0.0.7", ("time.google.com",)) in mgmt._control.calls
        # second pass is a no-op: NTP is persistent on the camera (configured once)
        mgmt._control.calls.clear()
        assert await mgmt._time_sync_once() == 0
        assert mgmt._control.calls == []
    finally:
        await mgmt.stop()


async def test_time_sync_skips_offline_and_blocked(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _FakeControl()
    try:
        _add_onvif(mgmt)                       # online -> should sync
        mgmt._blocked_ips.add("10.0.0.7")      # but blocked (debug unplug) -> skipped
        assert await mgmt._time_sync_once() == 0
        assert mgmt._control.calls == []
    finally:
        await mgmt.stop()


async def test_time_sync_disabled_for_non_onvif(tmp_path):
    mgmt = _mgmt(tmp_path)                      # vst_rtsp, control=None
    try:
        assert mgmt._time_sync_enabled() is False
    finally:
        await mgmt.stop()


class _RecordingEvents:
    """Captures published notification payloads (broker disabled in tests)."""
    def __init__(self):
        self.published = []

    async def publish(self, payload):
        self.published.append(payload)

    def close(self):
        pass


async def test_monitoring_marks_onvif_offline_after_threshold(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._events = _RecordingEvents()
    try:
        sid = _add_onvif(mgmt)                  # online (sensor_status=1)
        # Consecutive discovery rounds where the sensor is NOT seen -> debounced offline transition.
        for _ in range(ONVIF_DISCOVERY_MISS_THRESHOLD):
            await mgmt._handle_onvif_offline(seen=set())
        assert (mgmt.repo.get_sensor(sid).sensor_status or 0) == 0   # offline
        changes = [p["event"]["change"] for p in mgmt._events.published]
        assert changes.count("camera_remove") == 1                   # emitted exactly once
    finally:
        await mgmt.stop()


async def test_monitoring_offline_is_debounced(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._events = _RecordingEvents()
    try:
        sid = _add_onvif(mgmt)
        # One missed round (below threshold) must NOT flip the sensor offline.
        await mgmt._handle_onvif_offline(seen=set())
        assert (mgmt.repo.get_sensor(sid).sensor_status or 0) != 0   # still online
        assert mgmt._events.published == []
    finally:
        await mgmt.stop()


async def test_monitoring_restores_onvif_online_and_reannounces(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._events = _RecordingEvents()
    try:
        sid = _add_onvif(mgmt)
        mgmt.repo.set_sensor_status(sid, 0, http_status=408, now_iso="2026-01-01T00:00:00Z")
        assert (mgmt.repo.get_sensor(sid).sensor_status or 0) == 0   # offline
        await mgmt._mark_onvif_online(mgmt.repo.get_sensor(sid))
        assert (mgmt.repo.get_sensor(sid).sensor_status or 0) == 1   # back online
        changes = [p["event"]["change"] for p in mgmt._events.published]
        assert "camera_add" in changes                              # re-announced
    finally:
        await mgmt.stop()


def _add_uncredentialed_onvif(mgmt, sid, ip):
    """A discovered-but-uncredentialed ONVIF camera: online (status=1) but http_status=401."""
    now = "2026-06-09T00:00:00Z"
    mgmt.repo.insert_sensor(SensorDetails(
        device_id="VST", sensor_id=sid, sensor_hw_id=sid, name="cam", ipaddress=ip,
        url=f"http://{ip}/onvif/device_service", type=SENSOR_TYPE_ONVIF,
        position=mapping.position_api_to_db(None), is_remote="false",
        http_status=mapping.CAMERA_UNAUTHORIZED_CODE, sensor_status=1,
        created_date_time=now, modified_date_time=now), "", now)


async def test_time_sync_skips_uncredentialed_discovered(tmp_path):
    """Regression: time-sync must NOT touch discovered-but-uncredentialed cameras (would flood
    'Sender not Authorized' on a large network)."""
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _FakeControl()
    try:
        _add_uncredentialed_onvif(mgmt, "disc-1", "10.0.0.50")
        assert await mgmt._time_sync_once() == 0
        assert mgmt._control.calls == []          # never attempted on an uncredentialed camera
    finally:
        await mgmt.stop()


async def test_monitoring_skips_uncredentialed_discovered(tmp_path):
    """Regression: uncredentialed discovered cameras must not flap offline/online (no camera_remove
    churn) when WS-Discovery multicast drops them."""
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._events = _RecordingEvents()
    try:
        _add_uncredentialed_onvif(mgmt, "disc-2", "10.0.0.51")
        for _ in range(ONVIF_DISCOVERY_MISS_THRESHOLD + 2):
            await mgmt._handle_onvif_offline(seen=set())
        assert (mgmt.repo.get_sensor("disc-2").sensor_status or 0) != 0   # stays online
        assert mgmt._events.published == []                              # no churn
    finally:
        await mgmt.stop()


async def test_reboot_non_onvif_internal_error(tmp_path):
    mgmt = _mgmt(tmp_path)               # vst_rtsp, control=None
    try:
        sid = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.9:554/s1", "name": "r"})
        with pytest.raises(VmsError) as ei:
            await mgmt.reboot_sensor(sid)
        assert ei.value.code == VmsErrorCode.VMSInternalError
    finally:
        await mgmt.stop()


async def test_network_get_and_set(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _FakeControl()
    try:
        sid = _add_onvif(mgmt)
        info = await mgmt.sensor_network(sid)
        assert info["ipAddressV4"] == "10.0.0.7" and info["subnetMaskV4"] == "255.255.255.0"
        resp = await mgmt.set_sensor_network(sid, {"ipAddressV4": "10.0.0.8",
                                                   "subnetMaskV4": "255.255.255.0", "isIpv4Enabled": True})
        assert resp == {"rebootNeeded": True}
        assert ("set_net", "10.0.0.8") in mgmt._control.calls
    finally:
        await mgmt.stop()


async def test_network_set_invalid_ip(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _FakeControl()
    try:
        sid = _add_onvif(mgmt)
        with pytest.raises(VmsError) as ei:
            await mgmt.set_sensor_network(sid, {"ipAddressV4": "999.1.1.1"})
        assert ei.value.code == VmsErrorCode.InvalidParameterError
    finally:
        await mgmt.stop()


async def test_settings_get_and_set(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _FakeControl()
    try:
        sid = _add_onvif(mgmt)
        settings = await mgmt.sensor_settings(sid)
        assert "profile_1" in settings and settings["profile_1"]["Encode"]["Encoding"]["Value"] == "H264"
        assert await mgmt.set_sensor_settings(sid, {"Encode": {"Encoding": "H265"}}) == {}
        assert ("set_settings", {"Encode": {"Encoding": "H265"}}) in mgmt._control.calls
    finally:
        await mgmt.stop()


async def test_settings_set_non_onvif_not_supported(tmp_path):
    mgmt = _mgmt(tmp_path)
    try:
        sid = await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.9:554/s1", "name": "s"})
        with pytest.raises(VmsError) as ei:
            await mgmt.set_sensor_settings(sid, {"Encode": {}})
        assert ei.value.code == VmsErrorCode.VMSNotSupportedError
    finally:
        await mgmt.stop()


# --- credentials: reject when media access is unauthorized -------------------------
class _AuthOkStreamsFailControl(SensorControlAdaptor):
    """Camera that serves GetDeviceInformation anonymously (validate passes) but rejects media
    access with 'Sender not Authorized' (get_sensor_stream_info fails) -- i.e. wrong credentials."""

    async def connect(self):
        return 0

    async def validate_credentials(self, sensor, username, password):
        return True

    async def get_sensor_stream_info(self, sensor):
        return -1


async def test_set_credentials_rejects_when_media_unauthorized(tmp_path):
    mgmt = _mgmt(tmp_path, adaptor="onvif")
    mgmt._control = _AuthOkStreamsFailControl()
    try:
        sid = _add_onvif(mgmt, user="")           # discovered, uncredentialed ONVIF sensor
        with pytest.raises(VmsError) as ei:
            await mgmt.set_credentials(sid, "admin", "wrongpass")
        assert ei.value.code == VmsErrorCode.InvalidParameterError
        # credentials NOT persisted and no streams resolved
        assert (mgmt.repo.get_sensor(sid).username or "") == ""
        assert await mgmt.list_streams(sid) == []
    finally:
        await mgmt.stop()


# --- max_sensors_supported cap (parity with C++ isSpaceForNewSensor) ----------------
async def test_add_sensor_respects_max_sensors(tmp_path):
    cfg = _sqlite_cfg(tmp_path, max_sensors=2)
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    try:
        await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.1:554/a", "name": "a"})
        await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.2:554/b", "name": "b"})
        with pytest.raises(VmsError) as ei:
            await mgmt.add_sensor({"sensorUrl": "rtsp://10.0.0.3:554/c", "name": "c"})
        assert ei.value.code == VmsErrorCode.VMSNotSupportedError
        assert len(await mgmt.list_sensors()) == 2
    finally:
        await mgmt.stop()


async def test_onvif_discovery_caps_at_max_sensors(tmp_path, monkeypatch):
    import sensor_ms.adaptors.onvif.discovery as disc
    from sensor_ms.adaptors.onvif.discovery import parse_probe_match

    def _probe(uuid_s, ip, name):
        xml = (
            '<d:ProbeMatches xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
            ' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"><d:ProbeMatch>'
            f'<wsa:EndpointReference><wsa:Address>urn:uuid:{uuid_s}</wsa:Address></wsa:EndpointReference>'
            f'<d:Scopes>onvif://www.onvif.org/name/{name}</d:Scopes>'
            f'<d:XAddrs>http://{ip}/onvif/device_service</d:XAddrs></d:ProbeMatch></d:ProbeMatches>'
        ).encode()
        return parse_probe_match(xml)[0]

    # 5 distinct discovered devices, but the cap is 3.
    matches = [_probe(f"dev-{i}", f"10.0.0.{i}", f"cam{i}") for i in range(5)]

    async def fake_discover(message_id, timeout=3.0, bind_ip="0.0.0.0"):
        return matches

    monkeypatch.setattr(disc, "discover", fake_discover)

    cfg = _sqlite_cfg(tmp_path, adaptor="onvif", max_sensors=3)
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    try:
        added = await mgmt.scan(force=True)
        assert added == 3                                   # capped
        assert len(await mgmt.list_sensors()) == 3
    finally:
        await mgmt.stop()


# --- ONVIF session cleanup robustness ----------------------------------------------
async def test_onvif_close_closes_all_resources_even_on_error():
    from sensor_ms.adaptors.onvif.control import OnvifControl

    class _Closeable:
        def __init__(self, fail=False):
            self.closed = False
            self._fail = fail

        async def close(self):
            if self._fail:
                raise RuntimeError("boom")
            self.closed = True

    svc_bad, svc_ok = _Closeable(fail=True), _Closeable()
    snap, conn = _Closeable(), _Closeable()

    class _Cam:
        services = {("a", None): svc_bad, ("b", None): svc_ok}   # failing one first
        _snapshot_client = snap
        _snapshot_connector = conn

    await OnvifControl._close(_Cam())
    # one failing close must not prevent the rest from being closed -> no leaked sessions
    assert svc_ok.closed and snap.closed and conn.closed


async def test_onvif_close_finishes_all_on_cancellation_then_reraises():
    import asyncio as _asyncio

    from sensor_ms.adaptors.onvif.control import OnvifControl

    class _Closeable:
        def __init__(self, cancel=False):
            self.closed = False
            self._cancel = cancel

        async def close(self):
            if self._cancel:
                raise _asyncio.CancelledError()
            self.closed = True

    svc_cancel, svc_ok, snap = _Closeable(cancel=True), _Closeable(), _Closeable()

    class _Cam:
        services = {("a", None): svc_cancel, ("b", None): svc_ok}   # cancel on first
        _snapshot_client = snap
        _snapshot_connector = None

    with pytest.raises(_asyncio.CancelledError):
        await OnvifControl._close(_Cam())
    # cancellation mid-cleanup must NOT skip the remaining closes (no leaked sessions)
    assert svc_ok.closed and snap.closed


# --- discovery cadence: periodic only for ONVIF -------------------------------------
async def test_periodic_discovery_only_for_onvif(tmp_path, monkeypatch):
    monkeypatch.delenv("ADAPTOR", raising=False)
    # vst_rtsp: no periodic loop -> discovery is startup + on /scan only (no continuous polling).
    rtsp = _mgmt(tmp_path, adaptor="vst_rtsp")
    try:
        assert rtsp._periodic_discovery_enabled() is False
        await rtsp.start()
        assert rtsp._discovery_task is None
    finally:
        await rtsp.stop()
    # onvif: periodic WS-Discovery loop is enabled (C++ parity).
    onvif = _mgmt(tmp_path, adaptor="onvif")
    try:
        assert onvif._periodic_discovery_enabled() is True
    finally:
        await onvif.stop()


# --- bounded retry on discovery failure ---------------------------------------------
async def test_discovery_bounded_retry_on_failure(tmp_path, monkeypatch):
    import json as _json

    import sensor_ms.core.sensor_management as mod

    monkeypatch.delenv("ADAPTOR", raising=False)
    (tmp_path / "rtsp_streams.json").write_text(_json.dumps({
        "Nvstreamer": [{"enabled": True, "endpoint": "10.0.0.9:31000",
                        "api": "/api/v1/sensor/streams", "max_stream_count": 10}]}))

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise OSError("connection refused")

    monkeypatch.setattr(mod, "fetch_streams", boom)
    cfg = Config(use_centralize_db=False, sqlite_db_path=str(tmp_path / "v.db"),
                 vst_data_path=str(tmp_path), use_message_broker="", adaptor="vst_rtsp",
                 adaptor_config_path=str(tmp_path / "adaptor_config.json"),
                 discovery_retry_count=2, discovery_retry_interval_secs=0)  # min interval clamps to 1s
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    try:
        await mgmt.start()
        assert mgmt._last_scan_had_failures is True
        assert mgmt._discovery_task is not None  # bounded retry task scheduled
        await mgmt._discovery_task               # let the 2 retries run to completion
        assert calls["n"] == 3                    # initial scan + 2 bounded retries, then stops
    finally:
        await mgmt.stop()


# --- rtsp_streams.json (C++ vst_rtsp parity) ----------------------------------------
async def test_rtsp_streams_json_registers_streams_and_polls_nvstreamer(tmp_path, monkeypatch):
    import json as _json

    import sensor_ms.core.sensor_management as mod

    monkeypatch.delenv("ADAPTOR", raising=False)
    (tmp_path / "rtsp_streams.json").write_text(_json.dumps({
        "streams": [
            {"enabled": True, "stream_in": "rtsp://10.0.0.5:554/s1", "name": "cam1",
             "video": {"codec": "h264", "framerate": 25}},
            {"enabled": False, "stream_in": "rtsp://10.0.0.6:554/s2", "name": "cam2"},
            {"enabled": True, "stream_in": "udp", "name": "udpcam"},
        ],
        "Nvstreamer": [
            {"enabled": True, "endpoint": "10.0.0.9:31000", "api": "/api/v1/sensor/streams", "max_stream_count": 10},
            {"enabled": False, "endpoint": "10.0.0.10:31000", "api": "/api/v1/sensor/streams", "max_stream_count": 10},
        ],
    }))

    polled = []

    def fake_fetch(endpoint, timeout=5.0, api="/api/v1/sensor/streams", max_count=None):
        polled.append(endpoint)
        return [{"sensorId": "nv-1", "name": "nvcam", "url": "rtsp://10.0.0.9:8554/nv-1",
                 "metadata": {"codec": "h264"}}]

    monkeypatch.setattr(mod, "fetch_streams", fake_fetch)

    cfg = Config(use_centralize_db=False, sqlite_db_path=str(tmp_path / "vst.db"),
                 vst_data_path=str(tmp_path), use_message_broker="", adaptor="vst_rtsp",
                 adaptor_config_path=str(tmp_path / "adaptor_config.json"))
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    try:
        added = await mgmt.scan(force=True)
        names = {s["name"] for s in await mgmt.list_sensors()}
        ids = {s["sensorId"] for s in await mgmt.list_sensors()}
        assert added == 2                              # cam1 (direct) + nv-1 (nvstreamer)
        assert "cam1" in names and "nvcam" in names    # enabled direct + nvstreamer registered
        assert "cam2" not in names and "udpcam" not in names  # disabled + udp skipped
        assert "nv-1" in ids
        assert polled == ["10.0.0.9:31000"]            # only the enabled Nvstreamer endpoint polled
    finally:
        await mgmt.stop()


async def test_rtsp_streams_json_removes_disabled_stream(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.delenv("ADAPTOR", raising=False)
    cfgfile = tmp_path / "rtsp_streams.json"
    cfg = Config(use_centralize_db=False, sqlite_db_path=str(tmp_path / "vst.db"),
                 vst_data_path=str(tmp_path), use_message_broker="", adaptor="vst_rtsp",
                 adaptor_config_path=str(tmp_path / "adaptor_config.json"))
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    try:
        cfgfile.write_text(_json.dumps({"streams": [
            {"enabled": True, "stream_in": "rtsp://10.0.0.5:554/s1", "name": "cam1"}]}))
        await mgmt.scan(force=True)
        assert "cam1" in {s["name"] for s in await mgmt.list_sensors()}
        # flip to disabled -> next scan removes it
        cfgfile.write_text(_json.dumps({"streams": [
            {"enabled": False, "stream_in": "rtsp://10.0.0.5:554/s1", "name": "cam1"}]}))
        await mgmt.scan(force=True)
        assert "cam1" not in {s["name"] for s in await mgmt.list_sensors()}
    finally:
        await mgmt.stop()


# --- ONVIF pure mapping helpers -----------------------------------------------------
def test_prefix_netmask_roundtrip():
    assert prefix_to_netmask(24) == "255.255.255.0"
    assert prefix_to_netmask(16) == "255.255.0.0"
    assert prefix_to_netmask(0) == "0.0.0.0"
    assert netmask_to_prefix("255.255.255.0") == 24
    assert netmask_to_prefix("255.255.0.0") == 16
    assert netmask_to_prefix("bogus") == 0


def test_network_interface_to_info_mapping():
    iface = SimpleNamespace(
        IPv4=SimpleNamespace(Enabled=True, Config=SimpleNamespace(
            DHCP=False, Manual=[SimpleNamespace(Address="10.0.0.7", PrefixLength=24)])),
        IPv6=SimpleNamespace(Enabled=False, Config=None),
    )
    info = network_interface_to_info(iface)
    assert info["isIpv4Enabled"] is True and info["dhcpV4"] == "false"
    assert info["ipAddressV4"] == "10.0.0.7" and info["subnetMaskV4"] == "255.255.255.0"
    assert info["isIpv6Enabled"] is False


def test_apply_encode_settings_mutates_config():
    vec = SimpleNamespace(
        Encoding="H264", Quality=3.0,
        Resolution=SimpleNamespace(Width=640, Height=480),
        RateControl=SimpleNamespace(BitrateLimit=1024, FrameRateLimit=15, EncodingInterval=1),
        H264=SimpleNamespace(GovLength=25, H264Profile="Main"),
    )
    _apply_encode_settings(vec, {
        "Encoding": "H265", "Bitrate": "4096", "FrameRate": "30", "Quality": "5",
        "GovLength": "50", "Resolution": {"Width": "1920", "Height": "1080"}, "Profiles": "High",
    })
    assert vec.Encoding == "H265"
    assert vec.RateControl.BitrateLimit == 4096 and isinstance(vec.RateControl.BitrateLimit, int)
    assert vec.RateControl.FrameRateLimit == 30
    assert vec.Quality == 5.0
    assert (vec.Resolution.Width, vec.Resolution.Height) == (1920, 1080)
    assert vec.H264.GovLength == 50 and vec.H264.H264Profile == "High"


def test_apply_encode_settings_only_changes_provided_fields():
    vec = SimpleNamespace(
        Encoding="H264", Quality=3.0, Resolution=SimpleNamespace(Width=640, Height=480),
        RateControl=SimpleNamespace(BitrateLimit=1024, FrameRateLimit=15, EncodingInterval=1),
        H264=SimpleNamespace(GovLength=25, H264Profile="Main"),
    )
    _apply_encode_settings(vec, {"Bitrate": "2048"})   # only bitrate changes
    assert vec.RateControl.BitrateLimit == 2048
    assert vec.Encoding == "H264" and vec.RateControl.FrameRateLimit == 15
    assert (vec.Resolution.Width, vec.Resolution.Height) == (640, 480)


def test_encoder_options_to_encode_mapping():
    config = SimpleNamespace(
        Encoding="H265", Quality=4, GovLength=50,
        Resolution=SimpleNamespace(Width=1920, Height=1080),
        RateControl=SimpleNamespace(FrameRateLimit=30, BitrateLimit=4096),
    )
    options = SimpleNamespace(
        H265=SimpleNamespace(
            QualityRange=SimpleNamespace(Min=1, Max=5),
            FrameRateRange=SimpleNamespace(Min=1, Max=30),
            GovLengthRange=SimpleNamespace(Min=1, Max=100),
            ResolutionsAvailable=[SimpleNamespace(Width=1920, Height=1080)],
        ),
        H264=None,
    )
    enc = encoder_options_to_encode(config, options)
    assert enc["Encoding"] == {"AllowedValues": ["H265"], "Value": "H265"}
    opt = enc["Options"][0]["H265"]
    assert opt["Quality"] == {"Min": "1", "Max": "5", "Value": "4"}
    assert opt["GovLength"] == {"Min": "1", "Max": "100", "Value": "50"}
    assert opt["Resolution"]["Value"] == {"Width": 1920, "Height": 1080}

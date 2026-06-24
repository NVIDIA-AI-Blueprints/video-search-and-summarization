# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Response-parity tests: lock the sensor-API fields to match the C++ sensor-ms (validated against
captured C++ golden responses, onvif adaptor)."""
from __future__ import annotations

import pytest

import sensor_ms.adaptors.onvif.discovery as disc
from sensor_ms.api.errors import VmsError, VmsErrorCode
from sensor_ms.config import Config
from sensor_ms.core.sensor_management import SensorManagement
from sensor_ms.db.engine import make_engine
from sensor_ms.db.models import Base

# C++ /configuration key set (configuration.json golden).
CPP_CONFIG_KEYS = {
    "defaultBitrateKbps", "defaultEncodingInterval", "defaultFramerate", "defaultGovLength",
    "defaultProfile", "defaultQuality", "defaultResolution", "deviceDiscoveryFrequencySeconds",
    "deviceDiscoveryInterfaces", "deviceDiscoveryTimeoutSeconds", "deviceLocation", "deviceName",
    "enableDebugApis", "enableNotification", "enablePrometheus", "enableUserCleanup", "httpPort",
    "kafkaServerAddress", "maxSensorsSupported", "messageBrokerMetadataTopic", "messageBrokerTopic",
    "message_broker_payload_key", "mqttBrokerAddress", "multiUserExtraOptions", "ntpServers",
    "nvNgcKey", "nvOrgId", "onvifRequestTimeoutSeconds", "prometheusPort",
    "redisServerEnvironmentVariable", "remoteVstAddress", "supportedAudioCodecs",
    "supportedVideoCodecs", "useHttpDigestAuthentication", "useHttps", "useMessageBroker",
    "useMultiUser", "use_sensor_ntp_time", "vstDataPath", "vstIp", "webserviceAccessControlList",
}

_PM = (
    b'<d:ProbeMatches xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
    b' xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"><d:ProbeMatch>'
    b'<wsa:EndpointReference><wsa:Address>urn:uuid:051d61fe</wsa:Address></wsa:EndpointReference>'
    b'<d:Scopes>onvif://www.onvif.org/hardware/DS-2CD2045FWD-I '
    b'onvif://www.onvif.org/name/HIKVISION%20DS-2CD2045FWD-I '
    b'onvif://www.onvif.org/location/city-hangzhou</d:Scopes>'
    b'<d:XAddrs>http://10.24.218.101/onvif/device_service</d:XAddrs></d:ProbeMatch></d:ProbeMatches>'
)


def _cfg(tmp_path, adaptor="onvif"):
    return Config(use_centralize_db=False, sqlite_db_path=str(tmp_path / "v.db"),
                  vst_data_path=str(tmp_path), use_message_broker="", adaptor=adaptor)


async def test_onvif_registration_matches_cpp(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))

    async def fake_discover(message_id, timeout=3.0, bind_ip="0.0.0.0"):
        return disc.parse_probe_match(_PM)

    monkeypatch.setattr(disc, "discover", fake_discover)
    mgmt = SensorManagement(cfg)
    try:
        assert await mgmt.scan(force=True) == 1
        row = mgmt.repo.get_sensor("051d61fe")
        assert row is not None
        assert row.name == "HIKVISION%20DS-2CD2045FWD-I"   # raw / URL-encoded
        assert row.hardware == "DS-2CD2045FWD-I"
        assert row.location == "city-hangzhou"
        assert row.manufacturer == "" and row.hardware_id == ""   # left empty (C++ parity)
        assert row.http_status == 401 and row.type == "sensor_onvif"   # unauthorized -> /list offline
        # bulk /streams: empty for onvif (no placeholder stream)
        assert await mgmt.list_streams("051d61fe") == []
        # per-sensor endpoints -> CameraUnauthorizedError
        for coro in (mgmt.sensor_streams, mgmt.sensor_status, mgmt.sensor_network, mgmt.sensor_settings):
            with pytest.raises(VmsError) as ei:
                await coro("051d61fe")
            assert ei.value.code == VmsErrorCode.CameraUnauthorizedError
    finally:
        await mgmt.stop()


class _FakeOnvifControl:
    """Stand-in for OnvifControl: validate returns `valid`; stream info fills profiles + device info."""
    def __init__(self, valid: bool):
        self.valid = valid

    async def validate_credentials(self, sensor, username, password):
        return self.valid

    async def get_sensor_stream_info(self, sensor):
        sensor.update({"hardware": "DS-2CD2T43G0-I5", "manufacturer": "HIKVISION"})
        sensor["streams"] = [{
            "streamId": "profile_1", "isMain": True, "type": "Rtsp", "url": "rtsp://cam/Streaming/Channels/101",
            "name": "main", "metadata": {"codec": "H264", "framerate": "30", "resolution": "1920x1080"},
        }]
        return 0


def _onvif_sensor(mgmt, sid="cam1", ip="10.0.0.9"):
    from sensor_ms.db.models import SensorDetails
    mgmt.repo.insert_sensor(SensorDetails(
        device_id="VST", sensor_id=sid, sensor_hw_id=sid, name="HIKVISION%20DS-2CD2T43G0-I5",
        ipaddress=ip, url=f"http://{ip}/onvif/device_service", type="sensor_onvif", username="",
        http_status=401, sensor_status=1, created_date_time="t", modified_date_time="t"), "", "t")


async def test_set_credentials_wrong_is_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    mgmt._control = _FakeOnvifControl(valid=False)
    try:
        _onvif_sensor(mgmt)
        with pytest.raises(VmsError) as ei:
            await mgmt.set_credentials("cam1", "admin", "wrongpass")
        assert ei.value.code == VmsErrorCode.InvalidParameterError
        # not authorized: still no creds, no streams
        assert (mgmt.repo.get_sensor("cam1").username or "") == ""
        assert await mgmt.list_streams("cam1") == []
    finally:
        await mgmt.stop()


async def test_set_credentials_valid_resolves_streams(tmp_path):
    cfg = _cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    mgmt._control = _FakeOnvifControl(valid=True)
    try:
        _onvif_sensor(mgmt)
        assert await mgmt.set_credentials("cam1", "admin", "nvidia123") is True
        row = mgmt.repo.get_sensor("cam1")
        assert row.username == "admin" and row.http_status == 200       # authorized
        assert mgmt.repo.get_password("cam1") == "nvidia123"            # stored encrypted, decrypts back
        streams = await mgmt.list_streams("cam1")                       # now resolved
        assert len(streams) == 1 and streams[0]["metadata"]["codec"] == "H264"
        # The camera RTSP URI is stored credentialed (stream_live_url): the RTSP-server's
        # restoreRtspStreamsFromDB reads it verbatim, so the DESCRIBE only authenticates with
        # creds embedded. The served "url" (proxy_url) stays empty until the proxy is built.
        raw = mgmt.repo.list_streams("cam1")
        assert raw[0].stream_live_url == "rtsp://admin:nvidia123@cam/Streaming/Channels/101"
        # Main stream id == sensor_id (C++ onvif_client convention) so streamStart/getSensorIdFromStreamId
        # resolve; sub-streams would be sensor_id-<profileToken>.
        assert raw[0].stream_id == "cam1" and raw[0].stream_ismainstream == "true"
    finally:
        await mgmt.stop()


async def test_reannounce_republishes_credentialed_proxy(tmp_path):
    # After a restart, online sensors must re-emit camera_proxy with creds so a restarted
    # stream-processor can rebuild its RTSP proxy (C++ start()->getAndAddProxyUrl parity).
    cfg = _cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    mgmt._control = _FakeOnvifControl(valid=True)
    published: list = []

    async def capture(payload):
        published.append(payload)

    mgmt._events.publish = capture
    try:
        _onvif_sensor(mgmt)
        await mgmt.set_credentials("cam1", "admin", "nvidia123")
        published.clear()
        await mgmt._reannounce_online_sensors()
        proxies = [p for p in published if p["event"]["change"] == "camera_proxy"]
        assert len(proxies) == 1
        # main stream re-announced under sensor_id with credentials embedded in the proxy url.
        assert proxies[0]["event"]["camera_id"] == "cam1"
        assert proxies[0]["event"]["camera_url"] == "rtsp://admin:nvidia123@cam/Streaming/Channels/101"
    finally:
        await mgmt.stop()


async def test_reannounce_skips_uncredentialed_onvif(tmp_path, monkeypatch):
    # An uncredentialed (offline) ONVIF sensor has no main stream -> nothing to re-announce.
    cfg = _cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))

    async def fake_discover(message_id, timeout=3.0, bind_ip="0.0.0.0"):
        return disc.parse_probe_match(_PM)

    monkeypatch.setattr(disc, "discover", fake_discover)
    mgmt = SensorManagement(cfg)
    published: list = []

    async def capture(payload):
        published.append(payload)

    mgmt._events.publish = capture
    try:
        await mgmt.scan(force=True)
        published.clear()
        await mgmt._reannounce_online_sensors()
        assert published == []
    finally:
        await mgmt.stop()


async def test_onvif_discovery_logs_only_state_changes(tmp_path, monkeypatch, caplog):
    # First detection logs once; repeat scans of the same device are silent; a device that stops
    # replying is logged as removed only after the consecutive-miss threshold (debounced).
    import logging as _logging

    from sensor_ms.core import sensor_management as sm

    cfg = _cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))
    present = {"on": True}

    async def fake_discover(message_id, timeout=3.0, bind_ip="0.0.0.0"):
        return disc.parse_probe_match(_PM) if present["on"] else []

    monkeypatch.setattr(disc, "discover", fake_discover)
    mgmt = SensorManagement(cfg)
    try:
        with caplog.at_level(_logging.INFO, logger="sensor_ms.core.sensor_management"):
            # first scan -> one "detected" line
            await mgmt.scan(force=True)
            detected = [r for r in caplog.records if "ONVIF device detected" in r.message]
            assert len(detected) == 1

            # repeat scans of the same device -> no new info logs (no spam)
            caplog.clear()
            await mgmt.scan(force=True)
            await mgmt.scan(force=True)
            assert [r for r in caplog.records if r.levelno >= _logging.INFO] == []

            # device disappears -> "removed" logged exactly once, only at the miss threshold
            present["on"] = False
            caplog.clear()
            for _ in range(sm.ONVIF_DISCOVERY_MISS_THRESHOLD + 2):
                await mgmt.scan(force=True)
            removed = [r for r in caplog.records if "removed from network" in r.message]
            assert len(removed) == 1
    finally:
        await mgmt.stop()


async def test_configuration_key_set_matches_cpp(tmp_path):
    mgmt = SensorManagement(_cfg(tmp_path, adaptor="vst_rtsp"))
    try:
        cfg_resp = mgmt.get_configuration()
        assert set(cfg_resp.keys()) == CPP_CONFIG_KEYS
        assert "adaptor" not in cfg_resp   # C++ emits no adaptor key
    finally:
        await mgmt.stop()
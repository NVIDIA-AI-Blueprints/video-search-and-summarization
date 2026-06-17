# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Config loading tests: the nested vst_config.json sections (C++ schema) must map to the flat
py Config fields, and VST_CONFIG_PATH may be the configs directory (not a file)."""
from __future__ import annotations

import json

from sensor_ms.config import Config


def _write_vst_config(d) -> None:
    (d / "vst_config.json").write_text(json.dumps({
        "onvif": {
            "max_devices_supported": 500,
            "device_discovery_freq_secs": 5,
            "device_discovery_timeout_secs": 12,
            "device_discovery_interfaces": ["eth0"],
            "default_bitrate_kbps": 6000,
            "default_resolution": "1280x720",
        },
    }))


def test_onvif_section_maps_to_fields_via_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("VST_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ADAPTOR_CONFIG_PATH", raising=False)
    _write_vst_config(tmp_path)
    cfg = Config.load(str(tmp_path))                 # directory, not a file
    assert cfg.max_sensors_supported == 500          # was the hard-coded default of 8
    assert cfg.sensor_discovery_freq_secs == 5
    assert cfg.sensor_discovery_timeout == 12
    assert cfg.sensor_discovery_interfaces == ["eth0"]
    assert cfg.default_bitrate == 6000
    assert cfg.default_resolution == "1280x720"


def test_config_resolved_from_adaptor_config_path(tmp_path, monkeypatch):
    _write_vst_config(tmp_path)
    monkeypatch.delenv("VST_CONFIG_PATH", raising=False)
    monkeypatch.setenv("ADAPTOR_CONFIG_PATH", str(tmp_path / "adaptor_config.json"))
    cfg = Config.load()
    assert cfg.max_sensors_supported == 500


def test_notifications_section_maps_to_fields(tmp_path, monkeypatch):
    for v in ("VST_CONFIG_PATH", "ADAPTOR_CONFIG_PATH", "USE_MESSAGE_BROKER",
              "MESSAGE_BROKER_TOPIC", "KAFKA_BOOTSTRAP_URL", "MQTT_BROKER_ADDRESS"):
        monkeypatch.delenv(v, raising=False)
    (tmp_path / "vst_config.json").write_text(json.dumps({
        "notifications": {
            "enable_notification": True,
            "use_message_broker": "kafka",
            "message_broker_topic": "vst_events",
            "message_broker_payload_key": "sensor.id",
            "kafka_server_address": "kafka-host:9092",
            "mqtt_broker_address": "tcp://mqtt-host:1883",
        },
    }))
    cfg = Config.load(str(tmp_path))
    assert cfg.use_message_broker == "kafka"
    assert cfg.message_broker_topic == "vst_events"
    assert cfg.message_broker_payload_key == "sensor.id"
    assert cfg.kafka_server_address == "kafka-host:9092"
    assert cfg.mqtt_broker_address == "tcp://mqtt-host:1883"


def test_missing_config_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("VST_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ADAPTOR_CONFIG_PATH", raising=False)
    cfg = Config.load(str(tmp_path))                 # no vst_config.json present
    assert cfg.max_sensors_supported == 8            # dataclass default

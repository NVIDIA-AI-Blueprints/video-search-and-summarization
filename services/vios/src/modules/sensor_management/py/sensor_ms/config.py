# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configuration keys read by the sensor microservice (DESIGN.md §6.4).

Loaded from vst_config.json with optional environment-variable overrides. This is a subset
relevant to the control-plane service; extend as phases land. Field names use the canonical
camelCase from the swagger GetConfiguration schema where they are surfaced via
/sensor/configuration.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from functools import lru_cache


# Maps nested vst_config.json sections to the flat Config fields. Extend as more config is surfaced.
# Only the control-plane-relevant keys are mapped today.
_JSON_SECTION_MAP: dict[str, dict[str, str]] = {
    "notifications": {
        "enable_notification": "enable_notification",
        "use_message_broker": "use_message_broker",
        "use_message_broker_consumer": "use_message_broker_consumer",
        "message_broker_topic": "message_broker_topic",
        "message_broker_payload_key": "message_broker_payload_key",
        "message_broker_metadata_topic": "message_broker_metadata_topic",
        "redis_server_env_var": "redis_server_env_var",
        "kafka_server_address": "kafka_server_address",
        "mqtt_broker_address": "mqtt_broker_address",
    },
    "onvif": {
        "max_devices_supported": "max_sensors_supported",
        "device_discovery_timeout_secs": "sensor_discovery_timeout",
        "device_discovery_freq_secs": "sensor_discovery_freq_secs",
        "onvif_request_timeout_secs": "onvif_request_timeout_secs",
        "device_discovery_interfaces": "sensor_discovery_interfaces",
        "default_bitrate_kbps": "default_bitrate",
        "default_framerate": "default_framerate",
        "default_resolution": "default_resolution",
        "default_gov_length": "default_gov_length",
        "onvif_sensor_time_sync_interval_secs": "onvif_sensor_time_sync_interval_secs",
        "onvif_sensor_time_sync_compensation_ms": "onvif_sensor_time_sync_compensation_ms",
    },
}


@dataclass
class Config:
    # --- notification / broker ---
    enable_notification: bool = True
    use_message_broker: str = ""              # "redis" | "kafka" | "mqtt" | ""
    use_message_broker_consumer: str = ""
    message_broker_topic: str = "vst.event"
    message_broker_payload_key: str = "sensor.id"
    message_broker_metadata_topic: str = "test"
    redis_server_env_var: str = "ROSIE_REDIS_SVC_SERVICE_HOST:6379"
    kafka_server_address: str = ""
    mqtt_broker_address: str = ""

    # --- adaptor selection ---
    # adaptor_config_path: the configs/adaptor_config.json the active adaptor is chosen from
    # (selection = $ADAPTOR by name, else first enabled entry). `adaptor` is the fallback name used
    # only when the config file is absent/unreadable.
    adaptor_config_path: str = "/home/vst/vst_release/configs/adaptor_config.json"
    adaptor: str = "vst_rtsp"

    # --- discovery ---
    sensor_discovery_timeout: int = 10
    sensor_discovery_freq_secs: int = 15
    onvif_request_timeout_secs: int = 10
    # Bounded retry for one-shot (rtsp/nvstreamer) discovery: if the startup scan hits endpoint
    # failures (e.g. nvstreamer not up yet), retry this many times, waiting this long between tries,
    # then stop (no continuous polling). ONVIF uses the periodic WS-Discovery loop instead.
    discovery_retry_count: int = 3
    discovery_retry_interval_secs: int = 15
    onvif_sensor_time_sync_interval_secs: int = 60
    # Lead time subtracted from the whole-second boundary when batch-syncing camera clocks: the
    # simultaneous SetSystemDateAndTime fire is launched this many ms BEFORE the target second so the
    # requests land on the cameras right at the boundary (covers processing + LAN latency). Mirrors
    # C++ onvif_sensor_time_sync_compensation_ms.
    onvif_sensor_time_sync_compensation_ms: int = 20
    sensor_discovery_interfaces: list[str] = field(default_factory=list)
    max_sensors_supported: int = 8
    # nvstreamer endpoints polled by the rtsp/streamer adaptor's scan. Primary source is the
    # rtsp_streams.json "Nvstreamer" array; this field is only an optional NVSTREAMER_ENDPOINTS env
    # fallback (comma-separated "host:port"), each queried at /api/v1/sensor/streams.
    nvstreamer_endpoints: list[str] = field(default_factory=list)

    # --- rtsp / network / proxy ---
    rtsp_server_port: int = -1
    server_domain_name: str = ""
    use_reverse_proxy: bool = False

    # --- remote VST (edge sync; optional) ---
    remote_vst_address: str = ""

    # --- database ---
    use_centralize_db: bool = False
    centralize_db_name: str = ""
    centralize_db_username: str = ""
    centralize_remote_db_password: str = ""
    centralize_remote_db_hostaddr: str = ""
    centralize_remote_db_port: str = ""
    # Fixed in-container data dir (the compose volume mount target), used for AES key cert-file lookup
    # (db/crypto.py). The VST_DATA_PATH compose var is only the HOST path for the volume mount, not an
    # in-container override.
    vst_data_path: str = "/home/vst/vst_release/vst_data"
    sqlite_db_path: str = "/tmp/vst.db"

    # --- ntp ---
    ntp_servers: list[str] = field(default_factory=list)
    use_sensor_ntp_time: bool = False

    # --- codec defaults ---
    default_bitrate: int = 8000
    default_framerate: float = 30.0
    default_resolution: str = "1920x1080"
    default_gov_length: int = 60
    default_profile: str = "Main"

    # --- identity / auth mode ---
    device_name: str = "VST"
    device_location: str = ""
    use_multi_user: bool = False
    http_port: int = 30010
    # Debug/test hooks (/sensor/debug/plug|unplug|status). Enabled in non-release builds.
    enable_debug_apis: bool = True

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        cfg = cls()
        file = cls._resolve_config_file(path)
        if file:
            with open(file) as fh:
                data = json.load(fh)
            known = {f.name for f in fields(cls)}
            # Flat top-level keys that directly match a field name.
            for k, v in data.items():
                if k in known and not isinstance(v, dict):
                    setattr(cfg, k, v)
            # Nested vst_config.json sections mapped to the flat fields, e.g.
            # onvif.max_devices_supported -> max_sensors_supported. Without this the service ignored
            # the config and used the dataclass defaults (the "device limit of 8" surprise).
            for section, mapping in _JSON_SECTION_MAP.items():
                sec = data.get(section)
                if isinstance(sec, dict):
                    for json_key, field_name in mapping.items():
                        if json_key in sec:
                            setattr(cfg, field_name, sec[json_key])
        cfg._apply_env_overrides()
        return cfg

    @staticmethod
    def _resolve_config_file(path: str | None) -> str | None:
        """Locate vst_config.json. VST_CONFIG_PATH is the configs DIRECTORY (not a file), so accept
        either a direct file or a directory containing vst_config.json; fall back to the directory of
        ADAPTOR_CONFIG_PATH (both are mounted at the same configs dir in the container)."""
        candidates: list[str] = []
        p = path or os.environ.get("VST_CONFIG_PATH")
        if p:
            candidates += [p, os.path.join(p, "vst_config.json")]
        acp = os.environ.get("ADAPTOR_CONFIG_PATH")
        if acp:
            candidates.append(os.path.join(os.path.dirname(acp), "vst_config.json"))
        # Default in-container configs dir (the compose mount target), so no env is required.
        candidates.append("/home/vst/vst_release/configs/vst_config.json")
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return None

    def _apply_env_overrides(self) -> None:
        # Centralized DB. Accept the names the docker-compose actually provides (CENTRALIZE_DB_*),
        # with the legacy CENTRALIZE_REMOTE_DB_* spellings as aliases. Presence of all -> centralized DB.
        def first_env(*names: str) -> str | None:
            for n in names:
                if os.environ.get(n):
                    return os.environ[n]
            return None

        db = {
            "centralize_db_name": first_env("CENTRALIZE_DB_NAME"),
            "centralize_db_username": first_env("CENTRALIZE_DB_USERNAME"),
            "centralize_remote_db_password": first_env("CENTRALIZE_DB_PASSWORD", "CENTRALIZE_REMOTE_DB_PASSWORD"),
            "centralize_remote_db_hostaddr": first_env("CENTRALIZE_DB_HOSTADDR", "CENTRALIZE_REMOTE_DB_HOSTADDR"),
            "centralize_remote_db_port": first_env("CENTRALIZE_DB_PORT", "CENTRALIZE_REMOTE_DB_PORT"),
        }
        for attr, val in db.items():
            if val is not None:
                setattr(self, attr, val)
        if all(db.values()):
            self.use_centralize_db = True

        # Service config from env. NOTE: the broker settings (use_message_broker, topic,
        # redis/kafka/mqtt addresses) are intentionally NOT here -- they are read ONLY from
        # vst_config.json's "notifications" section.
        str_env = {
            "HTTP_PORT": "http_port", "VST_DATA_PATH": "vst_data_path",
            "SQLITE_DB_PATH": "sqlite_db_path", "ADAPTOR": "adaptor",
            "ADAPTOR_CONFIG_PATH": "adaptor_config_path",
            "DEVICE_NAME": "device_name", "REMOTE_ADDRESS_ENV": "remote_vst_address",
        }
        for env, attr in str_env.items():
            if os.environ.get(env):
                cur = getattr(self, attr)
                setattr(self, attr, int(os.environ[env]) if isinstance(cur, int) else os.environ[env])
        if os.environ.get("NVSTREAMER_ENDPOINTS"):
            self.nvstreamer_endpoints = [e.strip() for e in os.environ["NVSTREAMER_ENDPOINTS"].split(",") if e.strip()]


@lru_cache
def get_config() -> Config:
    return Config.load()

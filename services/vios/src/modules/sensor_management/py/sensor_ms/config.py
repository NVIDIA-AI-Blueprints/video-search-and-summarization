# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configuration keys read by the sensor microservice (DESIGN.md §6.4).

Loaded from vst_config.json with environment-variable overrides, mirroring config.cpp.
Defaults match the C++ defaults. This is a subset relevant to the control-plane service;
extend as phases land. Field names use the canonical camelCase from the swagger
GetConfiguration schema where they are surfaced via /sensor/configuration.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from functools import lru_cache


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
    onvif_sensor_time_sync_interval_secs: int = 60
    sensor_discovery_interfaces: list[str] = field(default_factory=list)
    max_sensors_supported: int = 8

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
    vst_data_path: str = "/tmp"               # used for AES key cert-file lookup (db/crypto.py)
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

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        cfg = cls()
        path = path or os.environ.get("VST_CONFIG_PATH")
        if path and os.path.isfile(path):
            with open(path) as fh:
                data = json.load(fh)
            known = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in known:
                    setattr(cfg, k, v)
        cfg._apply_env_overrides()
        return cfg

    def _apply_env_overrides(self) -> None:
        # Centralized DB. Accept the names the docker-compose actually provides (CENTRALIZE_DB_*),
        # with the C++ CENTRALIZE_REMOTE_DB_* spellings as aliases. Presence of all -> centralized DB.
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

        # Service + broker config (driven by the sensor-py compose service environment).
        str_env = {
            "HTTP_PORT": "http_port", "VST_DATA_PATH": "vst_data_path",
            "SQLITE_DB_PATH": "sqlite_db_path",
            "USE_MESSAGE_BROKER": "use_message_broker",
            "MESSAGE_BROKER_TOPIC": "message_broker_topic",
            "MESSAGE_BROKER_PAYLOAD_KEY": "message_broker_payload_key",
            "REDIS_SERVER_ENV_VAR": "redis_server_env_var", "ADAPTOR": "adaptor",
            "ADAPTOR_CONFIG_PATH": "adaptor_config_path",
            "DEVICE_NAME": "device_name", "REMOTE_ADDRESS_ENV": "remote_vst_address",
        }
        for env, attr in str_env.items():
            if os.environ.get(env):
                cur = getattr(self, attr)
                setattr(self, attr, int(os.environ[env]) if isinstance(cur, int) else os.environ[env])


@lru_cache
def get_config() -> Config:
    return Config.load()

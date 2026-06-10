# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SQLAlchemy models for the shared VIOS sensor schema.

CROSS-BACKEND NAMING (verified against a live Postgres deployment, 2026-06-09):
The C++ DDL (sqlite_helper.cpp) uses UPPERCASE identifiers, but the same unquoted DDL produces
DIFFERENT physical names per backend:
  - PostgreSQL folds unquoted identifiers to lowercase  -> table `sensor_details`, col `sensor_id`,
    and the misspelled `STREAM_ENCODING_INTERVAl` becomes a clean `stream_encoding_interval`.
  - SQLite preserves case (`SENSOR_DETAILS`, `STREAM_ENCODING_INTERVAl`) but matches identifiers
    case-insensitively.
Therefore LOWERCASE names are the portable choice: native on Postgres, case-insensitively matched on
SQLite. Do NOT quote/uppercase these or Postgres lookups fail. (The literal SQLite spelling of the
interval column is `STREAM_ENCODING_INTERVAl`; lowercase `stream_encoding_interval` matches it.)
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SensorDetails(Base):
    __tablename__ = "sensor_details"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    sensor_id: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    sensor_hw_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    username: Mapped[str | None] = mapped_column(String(1024))
    password: Mapped[str | None] = mapped_column(String(1024))   # base64(AES-256-CBC); see crypto.py
    name: Mapped[str | None] = mapped_column(String(1024))
    ipaddress: Mapped[str | None] = mapped_column(String(1024))
    hardware: Mapped[str | None] = mapped_column(String(1024))
    manufacturer: Mapped[str | None] = mapped_column(String(1024))
    serial_number: Mapped[str | None] = mapped_column(String(1024))
    firmware_version: Mapped[str | None] = mapped_column(String(1024))
    hardware_id: Mapped[str | None] = mapped_column(String(1024))
    location: Mapped[str | None] = mapped_column(String(1024))
    tags: Mapped[str | None] = mapped_column(String(1024))
    url: Mapped[str | None] = mapped_column(String(1024))
    type: Mapped[str | None] = mapped_column(String(1024))
    position: Mapped[str | None] = mapped_column(String(1024))    # JSON string, snake_case keys
    users: Mapped[str | None] = mapped_column(String(1024))
    is_remote: Mapped[str | None] = mapped_column(String(1024))   # "true"/"false" string
    remote_device_id: Mapped[str | None] = mapped_column(String(1024))
    remote_device_name: Mapped[str | None] = mapped_column(String(1024))
    remote_device_location: Mapped[str | None] = mapped_column(String(1024))
    http_status: Mapped[int | None] = mapped_column(Integer)
    sensor_status: Mapped[int | None] = mapped_column(Integer)
    created_date_time: Mapped[str] = mapped_column(String(1024), nullable=False)
    modified_date_time: Mapped[str] = mapped_column(String(1024), nullable=False)


class SensorStreams(Base):
    __tablename__ = "sensor_streams"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(String(1024), ForeignKey("sensor_details.sensor_id"), nullable=False)
    stream_id: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    stream_live_url: Mapped[str | None] = mapped_column(String(1024))
    stream_replay_url: Mapped[str | None] = mapped_column(String(1024))
    stream_proxy_url: Mapped[str | None] = mapped_column(String(1024))
    stream_resolution: Mapped[str | None] = mapped_column(String(1024))
    stream_framerate: Mapped[str | None] = mapped_column(String(1024))
    stream_encoding: Mapped[str | None] = mapped_column(String(1024))
    stream_status: Mapped[int | None] = mapped_column(Integer)
    stream_type: Mapped[int | None] = mapped_column(Integer)
    stream_encoding_profile: Mapped[str | None] = mapped_column(String(1024))
    stream_encoding_interval: Mapped[str | None] = mapped_column(String(1024))  # SQLite literal: STREAM_ENCODING_INTERVAl
    stream_duration: Mapped[str | None] = mapped_column(String(1024))
    stream_ismainstream: Mapped[str | None] = mapped_column(String(1024))
    stream_isalwaysrecording: Mapped[str | None] = mapped_column(String(1024))
    stream_storage_location: Mapped[int] = mapped_column(Integer, default=0)
    bitrate: Mapped[str | None] = mapped_column(String(1024))
    num_of_frames: Mapped[str | None] = mapped_column(String(1024))
    audio_container: Mapped[str | None] = mapped_column(String(1024))
    audio_encoding: Mapped[str | None] = mapped_column(String(1024))
    audio_sample_rate: Mapped[str | None] = mapped_column(String(1024))
    audio_bps: Mapped[str | None] = mapped_column(String(1024))
    audio_channels: Mapped[str | None] = mapped_column(String(1024))
    stream_name: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    is_bframes_present: Mapped[int] = mapped_column(Integer, default=0)
    created_date_time: Mapped[str] = mapped_column(String(1024), nullable=False)
    modified_date_time: Mapped[str] = mapped_column(String(1024), nullable=False)


class DbDetails(Base):
    __tablename__ = "db_details"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    db_version: Mapped[str | None] = mapped_column(String(1024))   # "0" (VST_DB_VERSION)
    created_date_time: Mapped[str] = mapped_column(String(1024), nullable=False)
    modified_date_time: Mapped[str] = mapped_column(String(1024), nullable=False)


# Other shared tables (video_record_details, recording_status, user_details, user_sessions,
# temp_video_files, video_record_schedule_details) are added as later phases need them.

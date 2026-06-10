# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sensor repository — read/write SENSOR_DETAILS / SENSOR_STREAMS, mirroring the C++ DB helpers.

Passwords are decrypted on read and encrypted on write (db/crypto.py), using the sensor_id as IV.
Delete cascades to sensor_streams and recording_status (deleteSensorDetails, sqlite_helper.cpp:963).
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .crypto import decrypt_password, encrypt_password, get_aes_key
from .models import SensorDetails, SensorStreams

__all__ = ["SensorRepo"]

log = logging.getLogger(__name__)


class SensorRepo:
    def __init__(self, session_factory, vst_data_path: str):
        self._sf = session_factory
        self._vst_data_path = vst_data_path

    def _key(self) -> bytes:
        return get_aes_key(self._vst_data_path)

    # --- reads ---
    def list_sensors(self) -> list[SensorDetails]:
        with self._sf() as s:
            return list(s.execute(select(SensorDetails)).scalars().all())

    def get_sensor(self, sensor_id: str) -> SensorDetails | None:
        with self._sf() as s:
            return s.execute(
                select(SensorDetails).where(SensorDetails.sensor_id == sensor_id)
            ).scalar_one_or_none()

    def list_streams(self, sensor_id: str) -> list[SensorStreams]:
        with self._sf() as s:
            return list(s.execute(
                select(SensorStreams).where(SensorStreams.sensor_id == sensor_id)
            ).scalars().all())

    def get_password(self, sensor_id: str) -> str:
        """Return the decrypted camera password for a sensor (empty if none / undecryptable)."""
        row = self.get_sensor(sensor_id)
        if row is None or not row.password:
            return ""
        try:
            return decrypt_password(row.password, sensor_id, self._key())
        except Exception as e:  # match C++: log and return empty, do not crash
            log.error("decrypt failed for sensor %s: %s", sensor_id, e)
            return ""

    def timeline_present(self, sensor_id: str) -> bool:
        # video_record_details holds recorded segments per sensor (lowercase per backend folding).
        with self._sf() as s:
            try:
                r = s.execute(
                    text("select 1 from video_record_details where sensor_id = :sid limit 1"),
                    {"sid": sensor_id},
                ).first()
                return r is not None
            except Exception:
                return False

    # --- writes ---
    def insert_sensor(self, row: SensorDetails, plaintext_password: str, now_iso: str) -> None:
        """Insert/replace a sensor row, encrypting the password with sensor_id as IV."""
        if plaintext_password:
            row.password = encrypt_password(plaintext_password, row.sensor_id, self._key())
        row.created_date_time = row.created_date_time or now_iso
        row.modified_date_time = now_iso
        with self._sf() as s, s.begin():
            s.merge(row)

    def insert_stream(self, row: SensorStreams, now_iso: str) -> None:
        row.created_date_time = row.created_date_time or now_iso
        row.modified_date_time = now_iso
        with self._sf() as s, s.begin():
            s.merge(row)

    def delete_sensor(self, sensor_id: str) -> bool:
        """Cascade delete: sensor_streams + recording_status + sensor_details."""
        with self._sf() as s, s.begin():
            existed = s.execute(
                select(SensorDetails.sensor_id).where(SensorDetails.sensor_id == sensor_id)
            ).first() is not None
            s.execute(delete(SensorStreams).where(SensorStreams.sensor_id == sensor_id))
            try:
                s.execute(text("delete from recording_status where sensor_id = :sid"), {"sid": sensor_id})
            except Exception:
                pass
            s.execute(delete(SensorDetails).where(SensorDetails.sensor_id == sensor_id))
            return existed

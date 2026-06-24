# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Credential-crypto tests.

`test_roundtrip` proves the Python implementation is internally consistent.

`test_decrypts_cpp_ciphertext` is the REAL parity gate: it decrypts a base64 ciphertext produced by
the C++ vst_common::encrypt_data() and asserts the plaintext. The fixture is a placeholder — populate
CPP_CIPHERTEXT / SENSOR_ID / EXPECTED with a value captured from a running C++ instance (using the
fallback key, or commit the cert file used) in Phase 0, then remove the skip.
"""
from __future__ import annotations

import pytest

from sensor_ms.db.crypto import (
    _AES_256_KEY_LEN,
    _FALLBACK_KEY,
    decrypt_password,
    encrypt_password,
    get_aes_key,
)


def test_roundtrip():
    key = _FALLBACK_KEY
    sensor_id = "7927cbbc-cba6-4f22-b65d-46f7382f5f65"
    secret = "P@ssw0rd-with-üñïç"
    ct = encrypt_password(secret, sensor_id, key)
    assert ct and ct != secret
    assert decrypt_password(ct, sensor_id, key) == secret


def test_empty_input():
    assert encrypt_password("", "sid", _FALLBACK_KEY) == ""
    assert decrypt_password("", "sid", _FALLBACK_KEY) == ""


def test_fallback_key_is_32_bytes():
    assert len(_FALLBACK_KEY) == _AES_256_KEY_LEN


def test_get_aes_key_falls_back_when_no_cert(tmp_path):
    assert get_aes_key(str(tmp_path)) == _FALLBACK_KEY


def test_get_aes_key_truncates_cert_to_32_bytes(tmp_path):
    # C++ effectively uses the first 32 bytes of the cert-file contents (see crypto.py).
    (tmp_path / "ca_certificate.pem").write_bytes(b"X" * 600)
    key = get_aes_key(str(tmp_path))
    assert len(key) == _AES_256_KEY_LEN
    # round-trip works with a cert-derived key too
    ct = encrypt_password("secret", "sid-123", key)
    assert decrypt_password(ct, "sid-123", key) == "secret"


# --- Phase 0 parity gate: real ciphertext captured from a live C++ deployment ---
# Captured 2026-06-09 from the stream-processing docker-compose stack (PostgreSQL `centralizedb`,
# sensor-ms vst-sensor:latest). Added an RTSP sensor via POST /api/v1/sensor/add with password
# "Sup3rSecret!23" and read sensor_details.password back. The deployment used a cert-derived key:
#   key = base64(self_signed_certificate.pem bytes)[:32]
# which for any X.509 PEM is the constant prefix below (base64("-----BEGIN CERTIFICATE--")).
CPP_CIPHERTEXT = "YLAMdbU7W3XsjkSmaKTcFA=="              # sensor_details.password
SENSOR_ID = "8b678171-57ef-40bc-8626-66f5883aa5f6"       # the IV (sensor_id) for that row
EXPECTED = "Sup3rSecret!23"                              # known plaintext
CPP_CERT_KEY = b"LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0t"       # base64(cert)[:32], cert-derived key


def test_decrypts_cpp_ciphertext():
    """The definitive parity gate: Python decrypts a password the C++ service wrote."""
    assert decrypt_password(CPP_CIPHERTEXT, SENSOR_ID, CPP_CERT_KEY) == EXPECTED


def test_encrypts_to_cpp_ciphertext():
    """And re-encrypts to the byte-identical stored value (round-trip with the real key)."""
    assert encrypt_password(EXPECTED, SENSOR_ID, CPP_CERT_KEY) == CPP_CIPHERTEXT

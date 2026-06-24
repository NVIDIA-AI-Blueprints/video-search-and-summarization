# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Credential encryption — byte-compatible with the C++ vst_common EVP scheme.

Reference: vst_common.cpp:853-1019, utils.cpp:2447 (DESIGN.md §6.2). The Python service MUST
decrypt passwords the C++ service wrote and vice-versa, so every parameter here is fixed:

  cipher   : AES-256-CBC, PKCS#7 padding
  key      : derived from the cert file <vst_data_path>/<CA_CERTIFICATE_FILE_NAME>, else
             <SELF_SIGNED_CERTIFICATE_FILE_NAME>, else the hardcoded fallback below.
             IMPORTANT: readFileIntoString() (fs_utils.cpp:744) returns base64_encode(file_bytes),
             so the cert-derived key is base64(cert_bytes), NOT the raw cert bytes. OpenSSL then
             uses the first 32 bytes of that base64 string (set_key_length(len) fails for a fixed
             AES-256 cipher and is ignored). The fallback key is used raw (already 32 bytes).
  iv       : the SENSOR_ID string, padded with \\x00 or truncated to 16 bytes.
  encoding : ciphertext is base64-encoded for storage; decrypt reverses (b64decode -> AES).
  salt/KDF : none.

  SECURITY NOTE (carry forward, do not silently replicate as "fine"): because every X.509 PEM
  begins with "-----BEGIN CERTIFICATE-----\\n", base64(cert)[:32] is effectively constant across
  deployments ("LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0t..."). The C++ comment calls this a per-deployment
  key, but it is not. We reproduce it for parity; flag for the security review of the new service.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

_AES_IV_SIZE = 16
_AES_BLOCK_BITS = 128

# Fallback key (utils.cpp:62). 32 bytes -> AES-256. Used only when no cert file is present.
_FALLBACK_KEY = b"WnZr4u7x!A%D*G-KaPdSgVkYp3s5v8y/"

# Cert file names — verified against config.h:40-41 (CA checked first, then self-signed,
# per utils.cpp:2449). These ARE the C++ constants; do not change without re-checking config.h.
_CA_CERT_FILE = "ca_certificate.pem"
_SELF_SIGNED_CERT_FILE = "self_signed_certificate.pem"


_AES_256_KEY_LEN = 32


def get_aes_key(vst_data_path: str) -> bytes:
    """Resolve the AES key exactly as utils.cpp:get_aes_key() + fs_utils.cpp:readFileIntoString().

    Cert path: key = base64(cert_file_bytes), truncated to 32 bytes (OpenSSL uses the first 32 bytes
    of the buffer; set_key_length(len) fails for fixed AES-256 and is ignored). CA cert is checked
    first, then self-signed (utils.cpp:2449). Fallback path: the 32-byte fallback used raw.

    Verified byte-for-byte against a live C++ deployment (see tests/test_crypto.py).
    """
    for name in (_CA_CERT_FILE, _SELF_SIGNED_CERT_FILE):
        path = os.path.join(vst_data_path, name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return base64.b64encode(fh.read())[:_AES_256_KEY_LEN]
    return _FALLBACK_KEY


def _iv_from_sensor_id(sensor_id: str) -> bytes:
    raw = sensor_id.encode("utf-8")
    if len(raw) < _AES_IV_SIZE:
        return raw + b"\x00" * (_AES_IV_SIZE - len(raw))
    return raw[:_AES_IV_SIZE]


def encrypt_password(plaintext: str, sensor_id: str, key: bytes) -> str:
    """Encrypt -> base64 string for storage in SENSOR_DETAILS.PASSWORD."""
    if not plaintext:
        return ""
    iv = _iv_from_sensor_id(sensor_id)
    padder = PKCS7(_AES_BLOCK_BITS).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(ct).decode("ascii")


def decrypt_password(b64_ciphertext: str, sensor_id: str, key: bytes) -> str:
    """Reverse of encrypt_password. Returns plaintext (empty string on empty input)."""
    if not b64_ciphertext:
        return ""
    iv = _iv_from_sensor_id(sensor_id)
    ct = base64.b64decode(b64_ciphertext)
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpadder = PKCS7(_AES_BLOCK_BITS).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")

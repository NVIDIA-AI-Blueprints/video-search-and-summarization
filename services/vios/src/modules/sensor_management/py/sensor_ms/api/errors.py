# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VmsErrorCode enum, HTTP mapping, and the snake_case error envelope.

Mirrors include/error_code.h and utils.cpp:1163. The error envelope on the wire is
`{"error_code": ..., "error_message": ...}` (snake_case) per swagger `Error` schema.
"""
from __future__ import annotations

from enum import Enum


class VmsErrorCode(str, Enum):
    NoError = "NoError"
    CameraUnauthorizedError = "CameraUnauthorizedError"
    ClientUnauthorizedError = "ClientUnauthorizedError"
    InvalidParameterError = "InvalidParameterError"
    CameraNotFoundError = "CameraNotFoundError"
    MethodNotAllowedError = "MethodNotAllowedError"
    DeviceRequestTimeoutError = "DeviceRequestTimeoutError"
    CommunicationError = "CommunicationError"
    VMSInternalError = "VMSInternalError"
    VMSNotSupportedError = "VMSNotSupportedError"
    VMSInsufficientStorage = "VMSInsufficientStorage"
    VMSNoDataError = "VMSNoDataError"
    ResourceConflictError = "ResourceConflictError"
    PayloadTooLargeError = "PayloadTooLargeError"
    UnsupportedMediaTypeError = "UnsupportedMediaTypeError"
    UnprocessableEntityError = "UnprocessableEntityError"
    TooManyRequestsError = "TooManyRequestsError"
    ServiceUnavailableError = "ServiceUnavailableError"


# Default human messages (utils.cpp:1163). Used when caller does not supply one.
_DEFAULT_MESSAGE: dict[VmsErrorCode, str] = {
    VmsErrorCode.NoError: "No Error",
    VmsErrorCode.CameraUnauthorizedError: "Camera is not authorized",
    VmsErrorCode.ClientUnauthorizedError: "Client is not authorized",
    VmsErrorCode.InvalidParameterError: "Invalid or out of range parameters",
    VmsErrorCode.CameraNotFoundError: "Camera not found OR camera id is not valid",
    VmsErrorCode.MethodNotAllowedError: "Method Not Allowed",
    VmsErrorCode.DeviceRequestTimeoutError: "Request Timout",
    VmsErrorCode.CommunicationError: "Camera communication error",
    VmsErrorCode.VMSInternalError: "VMS internal processing error",
    VmsErrorCode.VMSNotSupportedError: "Operation/Action not supported",
    VmsErrorCode.VMSInsufficientStorage: "Insufficient Storage",
    VmsErrorCode.VMSNoDataError: "No valid streams found for given timestamps",
    VmsErrorCode.ResourceConflictError: "Resource Conflict",
    VmsErrorCode.PayloadTooLargeError: "Payload Too Large",
    VmsErrorCode.UnsupportedMediaTypeError: "Unsupported Media Type",
    VmsErrorCode.UnprocessableEntityError: "Unprocessable Entity",
    VmsErrorCode.TooManyRequestsError: "Too Many Requests",
    VmsErrorCode.ServiceUnavailableError: "Service Unavailable",
}

# VmsErrorCode -> HTTP status (utils.cpp:1163 / DESIGN.md §6.5.7).
_HTTP_STATUS: dict[VmsErrorCode, int] = {
    VmsErrorCode.NoError: 200,
    VmsErrorCode.CameraUnauthorizedError: 401,
    VmsErrorCode.ClientUnauthorizedError: 401,
    VmsErrorCode.InvalidParameterError: 400,
    VmsErrorCode.MethodNotAllowedError: 405,
    VmsErrorCode.VMSNotSupportedError: 400,
    VmsErrorCode.CameraNotFoundError: 404,
    VmsErrorCode.DeviceRequestTimeoutError: 408,
    VmsErrorCode.CommunicationError: 500,
    VmsErrorCode.VMSInternalError: 500,
    VmsErrorCode.VMSInsufficientStorage: 507,
    VmsErrorCode.VMSNoDataError: 404,
    VmsErrorCode.ResourceConflictError: 409,
    VmsErrorCode.PayloadTooLargeError: 413,
    VmsErrorCode.UnsupportedMediaTypeError: 415,
    VmsErrorCode.UnprocessableEntityError: 422,
    VmsErrorCode.TooManyRequestsError: 429,
    VmsErrorCode.ServiceUnavailableError: 503,
}

# NOTE: 405 for MethodNotAllowed must be confirmed against a live capture in Phase 0;
# the C++ mapping table is the source of truth.


class VmsError(Exception):
    """Raise to produce a snake_case error envelope with the mapped HTTP status."""

    def __init__(self, code: VmsErrorCode, message: str | None = None):
        self.code = code
        self.message = message or _DEFAULT_MESSAGE.get(code, "VMS internal processing error")
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.code, 500)

    def envelope(self) -> dict[str, str]:
        return {"error_code": self.code.value, "error_message": self.message}

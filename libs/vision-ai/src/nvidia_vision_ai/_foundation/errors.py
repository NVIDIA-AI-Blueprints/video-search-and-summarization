# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Error types shared by nvidia-vision-ai components."""

from __future__ import annotations


class LibraryError(Exception):
    """Base class for nvidia-vision-ai errors."""


class ConfigurationError(LibraryError):
    """A caller-supplied setting is invalid or missing."""


class InvalidRequestError(LibraryError):
    """The request is valid as data but cannot be sent (e.g. the local file became unreadable)."""


class BackendUnreachableError(LibraryError):
    """A named backend could not be reached (transport failure, 429/5xx after retries)."""

    def __init__(self, backend: str, message: str, cause: Exception | None = None) -> None:
        super().__init__(f"{backend}: {message}")
        self.backend = backend
        if cause is not None:
            self.__cause__ = cause


class BackendResponseError(LibraryError):
    """A backend answered, but rejected the request (4xx) or returned an unusable response."""

    def __init__(self, backend: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(f"{backend}: {message}")
        self.backend = backend
        self.status_code = status_code

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Error types shared by reusable VSS library components."""

from __future__ import annotations


class LibraryError(Exception):
    """Base class for dependency-neutral VSS library errors."""


class ConfigurationError(LibraryError):
    """A caller-supplied runtime or backend setting is invalid."""


class BackendUnreachableError(LibraryError):
    """A named backend could not complete a required operation."""

    def __init__(self, backend: str, message: str, cause: Exception | None = None) -> None:
        super().__init__(f"{backend}: {message}")
        self.backend = backend
        if cause is not None:
            self.__cause__ = cause

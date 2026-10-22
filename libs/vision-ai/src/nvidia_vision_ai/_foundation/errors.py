# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

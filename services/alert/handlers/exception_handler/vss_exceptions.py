# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Custom exceptions for VSS operations."""


class VSSException(Exception):
    """Base exception class for VSS operations."""
    pass


class VSSConnectionError(VSSException):
    """Raised when there are connection issues with VSS API."""
    pass


class VSSModelError(VSSException):
    """Raised when there are issues with VSS model operations."""
    pass


class VSSMediaUploadError(VSSException):
    """Raised when media upload fails."""
    pass


class VSSAPIError(VSSException):
    """Raised when VSS API calls fail."""
    pass


class VSSPromptError(VSSException):
    """Raised when there are issues with prompt loading or selection."""
    pass


class VSSResponseError(VSSException):
    """Raised when there are issues with VSS response processing."""
    pass


class VSSRetryExhaustedError(VSSException):
    """Raised when all retry attempts have been exhausted."""
    pass 
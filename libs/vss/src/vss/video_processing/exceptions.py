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

"""Exceptions raised by local video processing."""


class ProcessingError(Exception):
    """Base class for safe, actionable processing failures."""


class UnsupportedSourceError(ProcessingError):
    """The source is not a supported local video locator."""


class DecoderUnavailableError(ProcessingError):
    """A requested native decoder cannot initialize for this source/device."""


class DecodeError(ProcessingError):
    """An opened decoder could not probe or decode its source."""


class TimestampUnavailableError(DecodeError):
    """Reliable source-relative presentation timing is unavailable."""


class ClipError(ProcessingError):
    """A compressed clip could not be created and verified."""

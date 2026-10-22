# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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

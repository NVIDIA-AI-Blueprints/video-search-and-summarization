# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exceptions raised by VSS ingestion."""


class IngestionError(Exception):
    """Base exception for ingestion failures."""


class IngestionBackendError(IngestionError):
    """An ingestion backend could not complete a request."""


class VLMBackendError(IngestionBackendError):
    """A frame-level VLM backend could not complete inference."""


class IngestionQueueFullError(IngestionError):
    """The bounded ingestion queue cannot accept more work."""


class RTVIBackendError(IngestionBackendError):
    """The RTVI backend could not complete ingestion."""


class RTEmbedBackendError(IngestionBackendError):
    """The RTVI-Embed backend could not complete ingestion."""


class RTVICvBackendError(IngestionBackendError):
    """The RTVI-CV backend could not complete ingestion."""

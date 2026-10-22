# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stable exceptions raised by VSS summarization."""


class SummarizationError(Exception):
    """Base exception for summarization failures."""


class SummaryConfigurationError(SummarizationError, ValueError):
    """Summarization provider configuration is invalid."""


class SummaryGenerationError(SummarizationError):
    """The configured provider could not generate a summary."""

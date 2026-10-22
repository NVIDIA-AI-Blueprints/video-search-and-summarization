# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Typed failures raised by the generic storage API."""


class StorageError(Exception):
    """Base class for storage failures."""


class StorageNotFoundError(StorageError, KeyError):
    """The requested namespace/key has no committed snapshot."""


class StorageConflictError(StorageError):
    """A compare-and-swap write did not match the current revision."""


class StorageBusyError(StorageError):
    """A nonblocking exclusive claim is already held, or close cannot drain."""


class StorageCorruptError(StorageError):
    """A persisted snapshot is malformed, unsupported, or fails verification."""


class StorageConfigurationError(StorageError, ValueError):
    """A public storage argument or configuration value is invalid."""


class StorageClosedError(StorageError):
    """An operation was attempted after adapter shutdown began."""


class StorageLimitError(StorageError):
    """A configured snapshot or record bound would be exceeded."""


class StorageIndeterminateError(StorageError):
    """Replacement became visible but a required durability barrier failed."""

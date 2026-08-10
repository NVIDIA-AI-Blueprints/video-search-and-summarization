# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Errors safe to map to the CLI contract."""


class ApplicationError(Exception):
    def __init__(self, error_code: str, message: str, *, retryable: bool, exit_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.exit_code = exit_code


class EmbeddingError(ApplicationError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__("embedding_failed", message, retryable=retryable, exit_code=3)


class RepositoryError(ApplicationError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__("repository_failed", message, retryable=retryable, exit_code=3)

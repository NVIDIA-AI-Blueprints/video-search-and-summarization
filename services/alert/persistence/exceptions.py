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

class PersistenceError(Exception):
    """Base exception for persistence layer errors."""
    pass


class PersistenceConfigError(PersistenceError):
    """Raised when persistence is enabled but cannot be initialised —
    unsupported backend, missing hosts, or client construction failure.

    Kept distinct from the not-found / already-exists exceptions (which
    are normal control flow) so callers can treat misconfiguration as a
    fail-fast startup condition rather than swallow it silently.
    """
    pass


class DocumentNotFoundError(PersistenceError):
    """Raised when a requested document does not exist."""

    def __init__(self, collection: str, doc_id: str):
        self.collection = collection
        self.doc_id = doc_id
        super().__init__(f"Document '{doc_id}' not found in '{collection}'")


class DocumentAlreadyExistsError(PersistenceError):
    """Raised when creating a document that already exists."""

    def __init__(self, collection: str, doc_id: str):
        self.collection = collection
        self.doc_id = doc_id
        super().__init__(f"Document '{doc_id}' already exists in '{collection}'")


class ConcurrentModificationError(PersistenceError):
    """Raised when an update fails because the document was modified concurrently
    by another writer and retries did not resolve the conflict."""

    def __init__(self, collection: str, doc_id: str, retries: int):
        self.collection = collection
        self.doc_id = doc_id
        self.retries = retries
        super().__init__(
            f"Document '{doc_id}' in '{collection}' was modified concurrently "
            f"after {retries} retries"
        )

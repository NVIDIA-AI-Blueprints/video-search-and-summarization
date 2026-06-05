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

"""Pluggable VLM response parser architecture.

Provides :class:`BaseResponseParser` as an optional ABC for internal use,
and :func:`load_response_parser` to resolve a Python dotted path
(e.g. ``"my_package.parsers.FireClassifier"``) into a ready-to-use
parser instance.

External parsers do NOT need to subclass ``BaseResponseParser`` — any class
with a callable ``parse(self, raw_response: str) -> dict`` method works.
This allows parser modules to live entirely outside the AB repo with zero
dependency on Alert Bridge code.

See ``pluggable_parser_design.md`` for the full design rationale.
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class BaseResponseParser(ABC):
    """Optional ABC for parser classes that live inside the AB repo.

    External parsers do NOT need to extend this class.  Any class with a
    ``parse(self, raw_response: str) -> dict`` method is accepted.
    """

    @abstractmethod
    def parse(self, raw_response: str) -> dict:
        """Parse raw VLM response text into a flat key-value dictionary.

        Args:
            raw_response: The complete text returned by the VLM.

        Returns:
            A ``dict`` whose keys and values will be merged into the alert
            ``info`` field.  Values may be strings, numbers, or stringified
            JSON for nested structures.

        Thread-safety contract (IMPORTANT):
            Alert Bridge loads the parser **once** at startup and shares
            the resulting instance across all worker threads (up to
            ``alert_agent.num_workers``) and the async dispatcher.
            Implementations **MUST** be thread-safe:

            * Do not mutate instance state inside ``parse()``.  If you
              need scratch state, keep it on the local stack or in
              thread-local storage.
            * Treat any attribute set in ``__init__`` as read-only.
            * Caches (e.g. compiled regexes, LRU caches on pure functions)
              are fine because they are read-only after warm-up.

            A stateful parser will produce race conditions under load
            that are very hard to reproduce in CI.  See
            ``test/test_pluggable_parser_concurrency.py`` for the
            regression lock.
        """
        ...


@runtime_checkable
class ResponseParserProtocol(Protocol):
    """Duck-type protocol: any object with a callable ``parse`` method."""

    def parse(self, raw_response: str) -> dict: ...


def load_response_parser(dotted_path: str) -> ResponseParserProtocol:
    """Resolve a dotted Python path to a parser instance.

    The *dotted_path* must point to a class (not a function or module).
    For example ``"my_package.parsers.FireClassifier"`` will:

    1. Import ``my_package.parsers``
    2. Retrieve the ``FireClassifier`` attribute
    3. Instantiate it with no arguments
    4. Verify it has a callable ``parse`` method (duck typing)

    External parser classes do NOT need to subclass ``BaseResponseParser``.
    Any class with ``parse(self, raw_response: str) -> dict`` works.

    Args:
        dotted_path: Fully-qualified ``module.ClassName`` string.

    Returns:
        An instantiated parser ready for use.

    Raises:
        ValueError: If the path is malformed or the class lacks a
            ``parse`` method.
        ImportError: If the module cannot be imported.
    """
    if not dotted_path or "." not in dotted_path:
        raise ValueError(
            f"response_parser path must be 'module.ClassName', got: '{dotted_path}'"
        )

    module_path, _, class_name = dotted_path.rpartition(".")

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(
            f"Failed to import parser module '{module_path}' "
            f"from response_parser='{dotted_path}': {exc}"
        ) from exc

    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(
            f"Module '{module_path}' has no attribute '{class_name}' "
            f"(response_parser='{dotted_path}')"
        )

    if not isinstance(cls, type):
        raise ValueError(
            f"'{dotted_path}' is not a class "
            f"(response_parser='{dotted_path}')"
        )

    parse_attr = getattr(cls, "parse", None)
    if parse_attr is None or not callable(parse_attr):
        raise ValueError(
            f"'{dotted_path}' does not declare a callable parse() method. "
            f"Parser classes must define: def parse(self, raw_response: str) -> dict "
            f"(response_parser='{dotted_path}')"
        )

    try:
        instance = cls()
    except TypeError as exc:
        raise ValueError(
            f"Failed to instantiate parser class '{dotted_path}': {exc}. "
            f"Parser classes must be instantiable with no arguments "
            f"(def __init__(self) -> None); abstract base classes and "
            f"classes with required __init__ parameters are not supported."
        ) from exc

    if not callable(getattr(instance, "parse", None)):
        raise ValueError(
            f"'{dotted_path}' instance does not implement a callable parse() method. "
            f"Parser classes must have: def parse(self, raw_response: str) -> dict "
            f"(response_parser='{dotted_path}')"
        )

    logger.info("Loaded pluggable response parser: '%s'", dotted_path)
    return instance

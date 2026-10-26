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

"""Small stage decorators for regular calls; iterators use detached lifetimes."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from types import CoroutineType
from typing import Any, ParamSpec, TypeVar

from .context import current_scope, measure, metric_scope
from .contracts import Library

P = ParamSpec("P")
R = TypeVar("R")


def _lineage(
    args: tuple[Any, ...], kwargs: dict[str, Any], library: Library
) -> dict[str, Any]:
    source = kwargs.get("source_id")
    scope = current_scope()
    if source is None and (
        library != "ingestion" or scope is None or scope.source_id == "unknown"
    ):
        for item in args:
            source = getattr(item, "source_id", None)
            if source is None:
                source = getattr(getattr(item, "source", None), "source_id", None)
            if isinstance(source, str):
                break
    changes: dict[str, Any] = {"source_id": source} if isinstance(source, str) else {}
    for item in args:
        backend = getattr(item, "backend_name", None)
        if backend in {"opencv", "pynv"}:
            changes.update(
                backend=backend, device="cpu" if backend == "opencv" else "gpu"
            )
            break
    if scope is None or scope.pipeline == "not_applicable":
        changes["pipeline"] = library
    return changes


def _resolve(observed: Any, result: Any, library: Library) -> None:
    first = result[0] if isinstance(result, list) and result else result
    identities = {}
    for name, attribute in (
        ("source_id", "source_id"),
        ("backend", "decoder"),
        ("backend", "backend_name"),
    ):
        value = getattr(first, attribute, None)
        if isinstance(value, str):
            scope = current_scope()
            if (
                name == "source_id"
                and library == "ingestion"
                and scope is not None
                and scope.source_id != "unknown"
            ):
                continue
            identities[name] = value
    observed.resolve(**identities)
    device = getattr(first, "device", None)
    if device in {"cpu", "gpu"}:
        observed.metadata(device=device)


def stage(name: str, *, library: Library) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            if current_scope() is None:
                return fn(*args, **kwargs)
            with metric_scope(**_lineage(args, kwargs, library)):
                with measure(name, library=library) as observed:
                    result = fn(*args, **kwargs)
                    _resolve(observed, result, library)
                    return result

        return wrapped

    return decorate


def async_stage(
    name: str, *, library: Library
) -> Callable[
    [Callable[P, CoroutineType[Any, Any, R]]], Callable[P, CoroutineType[Any, Any, R]]
]:
    def decorate(
        fn: Callable[P, CoroutineType[Any, Any, R]],
    ) -> Callable[P, CoroutineType[Any, Any, R]]:
        @wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            if current_scope() is None:
                return await fn(*args, **kwargs)
            with metric_scope(**_lineage(args, kwargs, library)):
                with measure(name, library=library) as observed:
                    result = await fn(*args, **kwargs)
                    _resolve(observed, result, library)
                    return result

        return wrapped

    return decorate

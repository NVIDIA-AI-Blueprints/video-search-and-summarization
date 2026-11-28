# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in serial raw SDK execution; not a CLI latency measurement.

Use one async context on one event loop. The CLI remains the default query
backend. This helper intentionally does not register with the threaded flow
runner: it supports neither verification nor persistence nor object lookup.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from typing import Self

    from vss_cli.config import Deployment
    from vss_core.search_core.host import VSSSearch


class RawSdkRunner:
    """Reuse SDK clients while retaining the checkout's CLI request contract."""

    def __init__(
        self,
        *,
        deployment: Deployment | None = None,
        tuning: dict[str, Any] | None = None,
        embedding_cache_policy: Literal["native", "query"] = "native",
    ):
        if embedding_cache_policy not in {"native", "query"}:
            raise ValueError("embedding_cache_policy must be native or query")
        self.embedding_cache_policy = embedding_cache_policy
        self._deployment = deployment
        self._tuning = dict(tuning or {})
        self._host: VSSSearch | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._busy = False
        self._active_task: asyncio.Task | None = None
        self._closing = False

    async def __aenter__(self) -> Self:
        from vss_cli import config
        from vss_cli.search.group import SearchTuning, _runtime_from
        from vss_core.search_core.host import VSSSearch
        from vss_core.search_core.runtime import resolve_fusion_method

        if self._host is not None or self._closing:
            raise RuntimeError("Runner is already open")
        unknown = set(self._tuning) - SearchTuning.model_fields.keys()
        if unknown:
            raise ValueError(f"Unknown CLI tuning fields: {sorted(unknown)}")
        if (
            self._tuning.get("critic_eval_count") is not None
            or self._tuning.get("no_critic") is False
        ):
            raise ValueError("Raw SDK experiments require verification disabled")
        tuning = SearchTuning.model_validate(self._tuning).model_dump(
            exclude_none=True, exclude_defaults=True
        )
        tuning.pop("no_critic", None)
        tuning.pop("critic_eval_count", None)
        if tuning.pop("no_merge_adjacent", False):
            tuning["merge_adjacent"] = False
        tuning["fusion_method"] = resolve_fusion_method(
            tuning.get("fusion_method"), tuning.get("w_tag") or 0.0
        )
        deployment = self._deployment if self._deployment is not None else config.load()
        self._host = VSSSearch.from_runtime(_runtime_from(deployment, tuning))
        self._deployment = deployment
        self._loop = asyncio.get_running_loop()
        return self

    def _check_owner(self) -> None:
        if (
            self._host is None
            or self._closing
            or self._loop is not asyncio.get_running_loop()
        ):
            raise RuntimeError(
                "Runner requires an open async context on its original event loop"
            )

    async def search(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        from vss_cli.search.group import SEARCH
        from vss_core.search_core.models.search import SearchInput

        self._check_owner()
        if path not in {"embed", "attribute", "fusion"}:
            raise ValueError(
                "Raw SDK experiments support embed, attribute, and fusion only"
            )
        if self._busy:
            raise RuntimeError("Raw SDK experiments require serial query execution")
        action = next(action for action in SEARCH.actions if action.name == path)
        inputs = action.Input.model_validate(payload)
        request = inputs.model_dump(exclude_none=True, exclude_defaults=True)
        request["search_mode"] = path
        SearchInput(**request).validate_semantics()
        for service in sorted(action.requires):
            self._deployment.endpoint(service)
        if self.embedding_cache_policy == "query" and self._host._search is not None:
            # Owned clients are from this checkout. Clear only between queries;
            # the native caches still deduplicate repeated text within a query.
            for leg in (self._host._search._embed, self._host._search._attribute):
                client = getattr(leg, "_embed", None)
                cache = getattr(client, "_text_cache", None)
                if cache is not None:
                    cache.clear()
        self._busy = True
        self._active_task = asyncio.current_task()
        try:
            output = await self._execute(path, request)
            return output.model_dump(mode="json")
        finally:
            self._busy = False
            self._active_task = None

    async def _execute(self, path: str, request: dict[str, Any]):
        """Dispatch a validated request; specialized benchmark runners may override."""
        return await self._host.search(**request)

    async def __aexit__(self, *_exc: object) -> None:
        self._check_owner()
        if self._active_task is asyncio.current_task():
            raise RuntimeError("Cannot close the runner inside its active query")
        self._closing = True
        host = self._host
        try:
            active = self._active_task
            if active is not None:
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
            await host.aclose()
        finally:
            self._host = None
            self._loop = None
            self._closing = False

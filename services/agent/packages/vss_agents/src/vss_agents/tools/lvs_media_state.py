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

"""Short-term LVS media state shared by LVS tools."""

from collections import OrderedDict
from dataclasses import dataclass
import logging
from typing import Literal

from nat.builder.context import ContextState

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONFIGURED_MEDIA = 1000


@dataclass(frozen=True)
class LVSConfiguredMedia:
    """Short-term memory entry for an LVS-configured media source."""

    media_type: Literal["stream"]
    media_name: str
    media_id: str
    media_url: str
    scenario: str
    events: tuple[str, ...]
    objects_of_interest: tuple[str, ...]


def _normalize_media_name(media_name: str) -> str:
    return media_name.strip().casefold()


def _get_conversation_id() -> str | None:
    try:
        conversation_id = ContextState.get().conversation_id.get()
    except Exception:
        logger.debug("Could not read conversation id from ContextState", exc_info=True)
        return None
    return conversation_id if isinstance(conversation_id, str) else None


class LVSConfiguredMediaState:
    """Bounded per-conversation state for configured LVS media."""

    def __init__(self, max_entries: int = DEFAULT_MAX_CONFIGURED_MEDIA):
        self._max_entries = max_entries
        self._media_by_key: OrderedDict[tuple[str | None, str, str], LVSConfiguredMedia] = OrderedDict()

    def _key(self, media_type: str, media_name: str) -> tuple[str | None, str, str]:
        return _get_conversation_id(), media_type, _normalize_media_name(media_name)

    def get(self, media_type: str, media_name: str) -> LVSConfiguredMedia | None:
        key = self._key(media_type, media_name)
        media = self._media_by_key.get(key)
        if media is not None:
            self._media_by_key.move_to_end(key)
        return media

    def remember(self, media: LVSConfiguredMedia) -> None:
        key = self._key(media.media_type, media.media_name)
        if key in self._media_by_key:
            self._media_by_key.move_to_end(key)
        self._media_by_key[key] = media

        while len(self._media_by_key) > self._max_entries:
            evicted_key, _ = self._media_by_key.popitem(last=False)
            logger.debug("Evicted LVS configured media state for %s", evicted_key)

    def clear(self) -> None:
        self._media_by_key.clear()


_configured_media_state = LVSConfiguredMediaState()


def configured_media(media_type: str, media_name: str) -> LVSConfiguredMedia | None:
    return _configured_media_state.get(media_type, media_name)


def remember_configured_media(media: LVSConfiguredMedia) -> None:
    _configured_media_state.remember(media)


def clear_configured_media_state() -> None:
    _configured_media_state.clear()

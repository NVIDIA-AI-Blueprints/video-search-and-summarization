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

import logging

from mdx.analytics.core.schema.models import Behavior

logger = logging.getLogger(__name__)


class BehaviorHoldback:
    """
    Holds back the latest behavior of each live track and releases it once, when the track ends.

    A pure container: it decides nothing about *when* a track ends. State management owns that
    judgement -- inactivity, discontinuity or state expiry -- and calls :meth:`end_track`. Keeping
    the policy out of here is what makes both halves testable on their own.

    Used only when ``behaviorEmitOnce`` is enabled.

    :ivar dict[str, Behavior] pending: Latest behavior per live track, keyed by behavior ID.
    :ivar list[Behavior] ended: Behaviors of tracks that ended and are waiting to be collected.

    Examples::
        >>> holdback = BehaviorHoldback()
        >>> holdback.retain(behaviors)
        >>> holdback.end_track("sensor1 #-# obj1", reason="track inactive")
        >>> to_write = holdback.take_ended()
    """

    def __init__(self) -> None:
        self.pending: dict[str, Behavior] = dict()
        self.ended: list[Behavior] = []

    def retain(self, behaviors: list[Behavior]) -> None:
        """
        Keep each behavior as the latest snapshot of its track, replacing the previous one.

        Replacing is correct because state management accumulates the trajectory in place, so the
        newest behavior of a track always supersedes the one before it.

        :param list[Behavior] behaviors: Behaviors produced for the current batch.
        :return: None
        """
        for behavior in behaviors:
            self.pending[behavior.id] = behavior

    def end_track(self, message_key: str, reason: str) -> None:
        """
        Release a finished track's retained behavior for writing.

        A no-op when nothing is held for that key, which makes it safe to call from every path that
        can end a track without each one having to check first.

        :param str message_key: Key of the track that ended (sensor ID + object ID).
        :param str reason: Why the track ended; logged for traceability.
        :return: None
        """
        behavior = self.pending.pop(message_key, None)
        if behavior is not None:
            logger.info(f"Behavior ready to write ({reason}): {message_key}")
            self.ended.append(behavior)

    def take_ended(self) -> list[Behavior]:
        """
        Return the behaviors of tracks that ended since the last call, and forget them.

        :return list[Behavior]: Behaviors ready to be written to the behavior stream.
        """
        ended, self.ended = self.ended, []
        return ended

    def flush(self) -> list[Behavior]:
        """
        Return everything held -- ended and still live -- and drop it all.

        Intended for shutdown, where tracks that were still live would otherwise never be written.

        :return list[Behavior]: Ended behaviors followed by the still-live retained behaviors.
        """
        held = self.ended + list(self.pending.values())
        self.ended = []
        self.pending.clear()
        return held

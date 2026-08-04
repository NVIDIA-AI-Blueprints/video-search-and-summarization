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

import json
import logging
import signal
import time
from typing import Callable, Optional, Sequence

import redis


class GracefulExit:
    def __init__(self, enable_signals: bool = True):
        self.should_exit = False
        if enable_signals:
            try:
                signal.signal(signal.SIGINT, self._handle)
                signal.signal(signal.SIGTERM, self._handle)
            except ValueError:
                # Signals can only be set in the main thread; ignore in workers
                pass

    def _handle(self, *_):
        self.should_exit = True


class RedisSubscriber:
    def __init__(
        self,
        redis_client: redis.StrictRedis,
        logger: Optional[logging.Logger] = None,
        ignore_subscribe_messages: bool = True,
        enable_signals: bool = True,
    ) -> None:
        self._redis = redis_client
        self._pubsub = self._redis.pubsub(
            ignore_subscribe_messages=ignore_subscribe_messages
        )
        self._logger = logger or logging.getLogger(__name__)
        self._stopper = GracefulExit(enable_signals=enable_signals)

    def subscribe(self, channels: Sequence[str]) -> None:
        if channels:
            self._logger.info("Subscribing to channels: %s", ", ".join(channels))
            self._pubsub.subscribe(*channels)

    def psubscribe(self, patterns: Sequence[str]) -> None:
        if patterns:
            self._logger.info("Pattern-subscribing: %s", ", ".join(patterns))
            self._pubsub.psubscribe(*patterns)

    def listen(
        self,
        handler: Callable[[str, str], None],
        poll_sleep_seconds: float = 0.05,
    ) -> None:
        """Start consuming pub/sub messages.

        handler: callable taking (channel, data_str)
        """
        self._logger.info("Starting Redis pub/sub listener")
        while not self._stopper.should_exit:
            try:
                message = self._pubsub.get_message(timeout=1.0)
                if not message:
                    time.sleep(poll_sleep_seconds)
                    continue

                mtype = message.get("type")
                if mtype not in ("message", "pmessage"):
                    continue

                channel = message.get("channel")
                data = message.get("data")
                if not isinstance(data, str):
                    try:
                        data = json.dumps(data)
                    except Exception:
                        data = str(data)

                handler(channel, data)
            except KeyboardInterrupt:
                break
            except Exception:
                self._logger.exception("Error while consuming pub/sub message")
                time.sleep(0.25)

    def close(self) -> None:
        try:
            self._pubsub.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._stopper.should_exit = True
        self.close()


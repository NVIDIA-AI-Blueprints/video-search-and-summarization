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

"""A floor under the startup budget, for a step that bounds nothing itself.

Passing the remaining budget into each blocking step was tried first and kept
leaving holes: three review rounds each found the next step that had been
missed. This was added as the backstop, on the reasoning that the prompt store
and the pipeline are built by constructors that take no timeout parameter.

**That reasoning did not survive contact with hardware.** Attempts to make this
fire -- an Elasticsearch that accepts and never answers, a VLM that does the
same -- all ended before the budget did: the Elasticsearch client times out on
its own inside ten seconds, and warmup is skipped rather than blocking. Every
step known today bounds itself, so this has never been observed to fire.

It is kept as defence in depth, not because a hole is known: it costs one
daemon thread, measures no change in cold-start time, and is the only thing
that would bound a future step added without a deadline of its own. Steps that
*can* take a deadline still take one, and should -- they fail with a message
about themselves, which is more useful than this one.
"""

import os
import signal
import threading
import time
from typing import Callable, Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

# Set before the expiry signal is raised, and read by the entry point's signal
# handler to choose its exit code. Without it the handler cannot tell a startup
# that ran out of budget from an operator asking for a shutdown, and reports a
# hung startup as a clean exit 0.
_expired_step: Optional[str] = None


def expired_step() -> Optional[str]:
    """The step startup was on when its budget ran out, if it did."""
    return _expired_step


class StartupDeadline:
    """Ends the process if startup has not finished within its budget.

    Used as a context manager around the whole of startup. ``step()`` records
    what is running so the expiry message names it, which is the difference
    between "startup timed out" and "startup timed out seeding prompts".
    """

    def __init__(self, budget: float, on_expiry: Optional[Callable[[str], None]] = None) -> None:
        self._budget = budget
        self._on_expiry = on_expiry or self._terminate
        self._step = "starting"
        # One condition guards the deadline and the fired flag together. A
        # flag plus a separate event lost the race at the edge: an extension
        # arriving as the wait expired was recorded, then ignored.
        self._state = threading.Condition()
        self._deadline: Optional[float] = None
        self._done = False
        self._fired = False
        self._timer: Optional[threading.Thread] = None

    def step(self, name: str) -> None:
        with self._state:
            self._step = name

    def extend(self, seconds: float) -> None:
        """Push the deadline out so a granted window is actually honoured.

        The fleet join is granted a reserved floor when the steps before it
        overran. Without this the watchdog still fired at the original budget,
        so the grant was logged and then revoked a few seconds later -- the
        same shape as a cap written where nothing reads it.
        """
        with self._state:
            if self._fired or self._done or self._deadline is None:
                return
            self._deadline += max(0.0, seconds)
            self._state.notify_all()

    @property
    def current_step(self) -> str:
        with self._state:
            return self._step

    def __enter__(self) -> "StartupDeadline":
        with self._state:
            self._deadline = time.monotonic() + self._budget
        self._timer = threading.Thread(target=self._watch, name="ab-startup-deadline",
                                       daemon=True)
        self._timer.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        with self._state:
            self._done = True
            self._state.notify_all()
        return False

    def _watch(self) -> None:
        with self._state:
            while True:
                if self._done:
                    return
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    # Decided here, holding the lock an extension must also
                    # take: nothing can push the deadline out between this
                    # check and the flag.
                    self._fired = True
                    break
                self._state.wait(remaining)
        step = self.current_step
        logger.error(
            "Startup did not finish within alert_agent.startup_timeout_seconds="
            "%.0fs; it was still at %s. Raise the budget, or fix what that step "
            "is waiting on.",
            self._budget, step,
        )
        self._on_expiry(step)

    @staticmethod
    def _terminate(step: str) -> None:
        """Signal the main thread so the usual teardown runs.

        SIGTERM rather than ``os._exit``: the entry point already turns it into
        an orderly shutdown that stops the children and closes the endpoint.
        Killing outright would leave a child holding its consumer-group slot,
        which is what the supervisor exists to prevent.

        The reason is recorded first. That handler exits 0, so without it a
        startup that ran out of budget was indistinguishable from an operator
        stopping the container -- a failure reported as a success.
        """
        global _expired_step
        _expired_step = step
        os.kill(os.getpid(), signal.SIGTERM)

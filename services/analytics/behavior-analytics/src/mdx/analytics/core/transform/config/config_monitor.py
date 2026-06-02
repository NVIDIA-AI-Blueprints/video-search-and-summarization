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

"""
Worker-side watchdog handler that picks up dynamic-config files written by
:class:`ConfigListener` and applies them to the worker's local
:class:`AppConfig` via :class:`ConfigApplier`.

A single ``on_moved`` handler does the entire pipeline:

1. Filter dotfiles and non-``.json`` paths (so the listener's staging
   ``.<name>.tmp`` is invisible).
2. Read the file body via :func:`load_json_from_file`.
3. Hand ``body["app"]`` and ``body["sensors"]`` to
   :meth:`ConfigApplier.apply`. No return value, no ack -- workers are
   silent consumers. The file body is the pre-filtered subset
   :class:`ConfigListener` wrote after validation; re-running the
   validator here would find nothing to reject (every item is already
   vetted) and would just duplicate work, so the worker trusts the
   file.

Because both flows (Flow A ``upsert`` and Flow B ``upsert-all``) funnel into
the same :meth:`ConfigApplier.apply` (additive merge), the worker no longer
dispatches on the filename's action prefix. The prefix stays in the filename
for audit/debugging only.

Atomic-write contract
---------------------

Writes into :data:`CONFIG_DIR` MUST be atomic-rename
(:class:`ConfigListener._atomic_write` stages a hidden ``.<name>.tmp`` and
``os.rename``\\ s it into place). The watchdog only listens for
``on_moved``; ``on_created`` is intentionally NOT handled because a
non-atomic direct write fires ``on_created`` while the file is still
partial and would race the read. Any debug / operator workflow that
needs to drop a file in must ``mv`` from outside the watched dir, not
``cp``.

Lifecycle (per worker process)
------------------------------

1. :class:`BaseApp` instantiates :class:`ConfigFileMonitor` with the
   worker's :class:`ConfigApplier` and calls :meth:`start_listen` -- registers
   a watchdog ``Observer`` on :data:`CONFIG_DIR`.
2. When the listener atomically renames a new file into place, the
   worker's ``Observer`` fires ``on_moved``; this handler validates the body
   and applies via the worker's :meth:`ConfigApplier.apply`.
3. ``BaseApp.close`` calls :meth:`close` to stop the ``Observer``.
"""

import logging
import os
import json
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from mdx.analytics.core.constants import CONFIG_DIR
from mdx.analytics.core.transform.config.config_applier import ConfigApplier
from mdx.analytics.core.utils.io_utils import load_json_from_file

logger = logging.getLogger(__name__)


class ConfigFileMonitor(FileSystemEventHandler):
    """
    Per-worker watchdog observer that applies config files written by
    :class:`ConfigListener`.

    Owns the watchdog ``Observer`` and is itself the ``FileSystemEventHandler``
    that the observer dispatches to. Only ``on_moved`` is implemented; see
    the module-level "Atomic-write contract" docstring for why ``on_created``
    is intentionally not handled.

    :ivar ConfigApplier _applier: Mutates the worker's local AppConfig once
        a file has been validated.
    :ivar Observer | None _observer: Watchdog observer thread; ``None`` until
        :meth:`start_listen` is called and after :meth:`close`.
    """

    def __init__(self, applier: ConfigApplier) -> None:
        """
        Initialize the monitor (does not start the observer; call
        :meth:`start_listen`).

        :param ConfigApplier applier: Worker's applier, bound to its local
            :class:`AppConfig`.
        :return: None
        """
        self._applier = applier
        self._observer: Observer | None = None


    def start_listen(self) -> None:
        """
        Register the watchdog observer on :data:`CONFIG_DIR`.

        Watches non-recursively -- the listener never creates subdirectories
        in :data:`CONFIG_DIR`, so ``recursive=False`` avoids picking up
        events from any debug subdir an operator might drop in.

        :return: None
        """
        config_dir = Path(CONFIG_DIR)
        config_dir.mkdir(parents=True, exist_ok=True)
        self._observer = Observer()
        self._observer.schedule(self, str(config_dir), recursive=False)
        self._observer.start()
        logger.info(f"started monitoring {config_dir}")


    def close(self) -> None:
        """
        Stop the watchdog observer.

        ``Observer.join`` is called with a finite timeout so a watchdog
        dispatcher stuck mid-callback can't block process shutdown
        indefinitely.

        :return: None
        """
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=5.0)
        if self._observer.is_alive():
            logger.warning(
                "config observer did not terminate within 5s; "
                "leaving as daemon -- process exit will reap it"
            )
        else:
            logger.info("Stopped monitoring config dir...")


    def on_moved(self, event: FileSystemEvent) -> None:
        """
        Atomic-rename writes from :class:`ConfigListener` end up here.

        Filters dotfiles and non-``.json`` paths (so the staging
        ``.<name>.tmp`` files are silently ignored), reads the body, and
        hands ``body["app"]`` / ``body["sensors"]`` to
        :meth:`ConfigApplier.apply`. No re-validation -- the listener has
        already filtered and only writes the applied subset to disk.

        Both ``upsert`` and ``upsert-all`` filenames funnel through the same
        path; the action prefix is informational and is no longer parsed for
        dispatch.

        ``FileNotFoundError`` (rare pruner-vs-read race) is logged and
        skipped; other exceptions are logged via ``.exception()`` so the
        watchdog thread keeps running.

        :param FileSystemEvent event: Watchdog event carrying ``dest_path``.
        :return: None
        """
        if event.is_directory:
            return
        path = event.dest_path
        name = os.path.basename(path)
        if name.startswith(".") or not name.endswith(".json"):
            return

        try:
            body = load_json_from_file(path)
        except FileNotFoundError:
            logger.warning(f"config file disappeared during apply: {path}")
            return
        except Exception as e:  # pragma: no cover (defensive: malformed JSON / IO errors)
            logger.exception(f"failed to read config file {name}: {e}")
            return

        if not isinstance(body, dict):
            # Cheap shape check so a corrupt / hand-edited non-object file
            # produces a clear log line instead of an AttributeError from
            # the subsequent ``.get()`` calls.
            logger.error(f"config file {name} is not a JSON object; skipping")
            return

        try:
            self._applier.apply(body.get("app", []), body.get("sensors", []))
            logger.info(f"applied config from {name}")
            logger.info(f"applied config: {json.dumps(self._applier._config.to_mutable_snapshot(), indent=2)}")
        except Exception as e:
            logger.exception(f"failed to apply config from {name}: {e}")

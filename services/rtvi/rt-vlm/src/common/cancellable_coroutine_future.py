# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import concurrent.futures


class _CancellableCoroutineFuture(concurrent.futures.Future):
    """Keep completion callbacks pending until the cancelled coroutine drains."""

    def __init__(self, coroutine, loop, on_done=None):
        super().__init__()
        self._loop = loop
        self._on_done = on_done
        self._task = None
        self._cancel_requested = False

        def start():
            self._task = loop.create_task(coroutine)
            self._task.add_done_callback(self._complete)

        try:
            loop.call_soon_threadsafe(start)
        except BaseException:
            coroutine.close()
            raise

    def cancel(self):
        if self.cancelled():
            return True
        if not self.done():
            self._loop.call_soon_threadsafe(self._request_cancel)
        # A running coroutine owns resources until its cancellation cleanup ends.
        return False

    def _request_cancel(self):
        if not self.done() and not self._cancel_requested:
            self._cancel_requested = True
            self._task.cancel()

    def _complete(self, task):
        if self._on_done is not None:
            self._on_done()
        if task.cancelled():
            super().cancel()
        elif task.exception() is not None:
            self.set_exception(task.exception())
        else:
            self.set_result(task.result())

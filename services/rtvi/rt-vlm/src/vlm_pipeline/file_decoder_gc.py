# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Coordinate full collection across the file decoders in one process."""

import gc
import os
from threading import Lock


class FileDecoderGarbageCollector:
    def __init__(self):
        try:
            self._interval = int(os.environ.get("RTVI_FILE_DECODER_GC_INTERVAL", "10"))
            if self._interval < 1:
                raise ValueError
        except ValueError:
            raise ValueError("RTVI_FILE_DECODER_GC_INTERVAL must be a positive integer") from None
        self._pending = 0
        self._collecting = False
        self._lock = Lock()

    def collect(self, *, force: bool = False):
        with self._lock:
            self._pending += 1
            if self._collecting or (not force and self._pending < self._interval):
                return
            self._pending = 0
            self._collecting = True
        # Finalizers can reenter decoder cleanup; never hold the lock during GC.
        try:
            gc.collect()
        finally:
            with self._lock:
                self._collecting = False

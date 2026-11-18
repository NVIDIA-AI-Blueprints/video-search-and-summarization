# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded cache of the exact video bytes sent for event review."""
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

LOCK = threading.RLock()


def clip_path(event_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", event_id):
        raise ValueError("Invalid event ID")
    root = Path(os.getenv("D1_CLIP_CACHE", "results/agent/review-clips"))
    root.mkdir(parents=True, exist_ok=True)
    return root / (event_id + ".mp4")


def save_clip(event_id, media):
    """Publish atomically before inference; retain at most 200 clips / 2 GiB."""
    path = clip_path(event_id)
    with LOCK:
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as dest:
                tmp = Path(dest.name)
                media.seek(0)
                shutil.copyfileobj(media, dest)
            tmp.replace(path)
            media.seek(0)
            files = sorted(path.parent.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            while len(files) > 200 or total > 2 * 1024 ** 3:
                oldest = files.pop(0)
                if oldest == path:
                    break
                total -= oldest.stat().st_size
                oldest.unlink(missing_ok=True)
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
    return path

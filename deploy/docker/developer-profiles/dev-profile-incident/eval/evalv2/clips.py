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

"""Video URLs for a live run: presigned R2 GETs for the clips of ``dataset-manifest-v1``, read-only.

A presigned URL is a local signature over the manifest's R2 key (``r2_key``): signing sends nothing, and the model
server (Track A) or the experiment agent (Track B) fetches the clip itself. Nothing here writes to R2. The URL is
never stored: request artifacts keep it without its query string (``adapters.base.stored_request``).

- **Signing is lazy and cached in memory.** A URL is re-signed when less than ``refresh_margin_s`` of its lifetime
  is left, so a long run never sends an expired URL, and a resumed run always signs afresh.
- **The test half is refused** for a pilot (``purpose: "pilot"``): a pilot never touches a held-out clip, whatever
  the plan says.
- **Credentials** (``R2_ACCOUNT_ID``, ``R2_ACCESS_KEY``, ``R2_SECRET_KEY``, ``R2_BUCKET``) come from the environment
  (``.env.local``); a missing one is named, its value never printed.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

R2_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET")
SIGNED_URL_TTL_SECONDS = 3600  # V1's scripts/eval_video_resolution.py
REFRESH_MARGIN_SECONDS = 900  # re-sign when a URL has less than this left: a call (and its retries) outlives it

Signer = Callable[[str, int], str]


class ClipError(RuntimeError):
    """A clip that cannot be resolved: unknown, held out, or R2 not configured. Nothing was sent."""


def missing_r2_env(environ: Mapping[str, str]) -> list[str]:
    return [name for name in R2_ENV if not environ.get(name)]


def r2_signer(environ: Mapping[str, str] | None = None) -> Signer:
    """``sign(key, ttl)``: a presigned GET for an R2 object, as V1 signs it (inline disposition). Local only."""
    environ = os.environ if environ is None else environ
    missing = missing_r2_env(environ)
    if missing:
        raise ClipError(f"R2 is not configured: set {missing} (from .env.local)")
    import boto3
    from botocore.config import Config

    client = boto3.client("s3", endpoint_url=f"https://{environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                          aws_access_key_id=environ["R2_ACCESS_KEY"], aws_secret_access_key=environ["R2_SECRET_KEY"],
                          region_name="auto", config=Config(signature_version="s3v4"))
    bucket = environ["R2_BUCKET"]

    def sign(key: str, ttl: int) -> str:
        url: str = client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key, "ResponseContentDisposition": "inline"},
            ExpiresIn=ttl)
        return url

    return sign


class ClipResolver:
    """``url(clip)`` and ``duration(clip)`` for the clips of the dataset manifest, guarded by the split."""

    def __init__(self, manifest_clips: Iterable[Mapping[str, Any]], split: Mapping[str, Any], *, purpose: str | None,
                 signer: Signer, ttl_s: int = SIGNED_URL_TTL_SECONDS, refresh_margin_s: int = REFRESH_MARGIN_SECONDS,
                 clock: Callable[[], float] = time.time) -> None:
        if refresh_margin_s >= ttl_s:
            raise ValueError("the refresh margin must be shorter than the URL's lifetime")
        self._clips = {clip["incident_id"]: dict(clip) for clip in manifest_clips}
        self._test = frozenset(split.get("test") or ())
        self.purpose, self._sign, self.ttl_s, self.margin_s, self._clock = purpose, signer, ttl_s, refresh_margin_s, clock
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self.signed = 0  # how many signatures were made (a test counts re-signing with it)

    def _entry(self, clip: str) -> Mapping[str, Any]:
        if self.purpose == "pilot" and clip in self._test:
            raise ClipError(f"{clip} is a test-half clip: a pilot never touches the held-out split")
        entry = self._clips.get(clip)
        if entry is None:
            raise ClipError(f"{clip} is not in dataset-manifest-v1")
        if not entry.get("r2_key"):
            raise ClipError(f"{clip} has no R2 key in the manifest")
        return entry

    def url(self, clip: str) -> str:
        entry = self._entry(clip)
        with self._lock:
            now = self._clock()
            cached = self._cache.get(clip)
            if cached is not None and cached[1] - now > self.margin_s:
                return cached[0]
            url = self._sign(str(entry["r2_key"]), self.ttl_s)
            self._cache[clip] = (url, now + self.ttl_s)
            self.signed += 1
            return url

    def duration(self, clip: str) -> float | None:
        value = self._entry(clip).get("duration_seconds")
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None

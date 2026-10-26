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

"""Bounded two-worker admission barrier, outside each operation clock."""

import asyncio
import json
import os
import time
from uuid import uuid4


def publish(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".writing")
    try:
        fd = os.open(
            temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False)
        os.link(temporary, path)  # atomic visibility and exclusive destination
    finally:
        temporary.unlink(missing_ok=True)


async def wait(profile):
    if profile.barrier_directory is None:
        return None
    directory = profile.barrier_directory
    ready = {"barrier_id": profile.barrier_id, "ready_monotonic_s": time.monotonic()}
    path = directory / (profile.barrier_member + ".ready.json")
    publish(path, ready)
    async with asyncio.timeout(120):
        while True:
            if (directory / "abort.json").exists():
                raise ValueError("batch_admission_aborted")
            go = directory / "go.json"
            if go.exists():
                if go.is_symlink() or not 0 < go.stat().st_size <= 1024:
                    raise ValueError("barrier_release_file")
                release = json.loads(go.read_bytes())
                if release.get("barrier_id") != profile.barrier_id:
                    raise ValueError("barrier_release_identity")
                return ready | {
                    "release_monotonic_s": release["release_monotonic_s"],
                    "observed_release_monotonic_s": time.monotonic(),
                }
            await asyncio.sleep(0.02)

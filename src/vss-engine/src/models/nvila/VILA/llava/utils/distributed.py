# Copyright 2024 NVIDIA CORPORATION & AFFILIATES
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

import os
import warnings
from typing import Any, List, Optional

from torch import distributed as dist

__all__ = [
    "init",
    "is_initialized",
    "size",
    "rank",
    "local_size",
    "local_rank",
    "is_main",
    "barrier",
    "gather",
    "all_gather",
]


def init() -> None:
    if "RANK" not in os.environ:
        warnings.warn("Environment variable `RANK` is not set. Skipping distributed initialization.")
        return
    dist.init_process_group(backend="nccl", init_method="env://")


def is_initialized() -> bool:
    return dist.is_initialized()


def size() -> int:
    return int(os.environ.get("WORLD_SIZE", 1))


def rank() -> int:
    return int(os.environ.get("RANK", 0))


def local_size() -> int:
    return int(os.environ.get("LOCAL_WORLD_SIZE", 1))


def local_rank() -> int:
    return int(os.environ.get("LOCAL_RANK", 0))


def is_main() -> bool:
    return rank() == 0


def barrier() -> None:
    dist.barrier()


def gather(obj: Any, dst: int = 0) -> Optional[List[Any]]:
    if not is_initialized():
        return [obj]
    if is_main():
        objs = [None for _ in range(size())]
        dist.gather_object(obj, objs, dst=dst)
        return objs
    else:
        dist.gather_object(obj, dst=dst)
        return None


def all_gather(obj: Any) -> List[Any]:
    if not is_initialized():
        return [obj]
    objs = [None for _ in range(size())]
    dist.all_gather_object(objs, obj)
    return objs

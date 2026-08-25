# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Patch installed vLLM 0.17.1 to add the multimodal tensor IPC path from
vLLM PR #32104.

The upstream change is merged in newer vLLM releases, but the pinned NVIDIA
0.17.1 wheel used by this image does not contain it. A direct git patch does
not apply cleanly because both the NVIDIA wheel and the EVS overlay have local
drift, so this script applies the targeted edits after the EVS overlay.

When enabled with ``mm_tensor_ipc="torch_shm"``, large CPU/CUDA multimodal
tensors are sent out-of-band through a torch multiprocessing queue instead of
being serialized through the msgpack/ZMQ buffer path. This is most useful for
cache-disabled video benchmarks where frame tensors are already resident on GPU.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

VLLM_ROOT = Path(os.environ.get("VLLM_ROOT", "/usr/local/lib/python3.12/dist-packages/vllm"))


TENSOR_IPC_FILE = """# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

\"\"\"Tensor IPC transport via torch.multiprocessing.Queue.\"\"\"

import dataclasses
import uuid
from collections import defaultdict
from dataclasses import field
from multiprocessing.queues import Queue as MPQueue
from typing import Any

import torch

from vllm.logger import init_logger
from vllm.v1.serial_utils import OOBTensorConsumer

logger = init_logger(__name__)

TensorIpcQueue = MPQueue


@dataclasses.dataclass
class TensorIpcData:
    sender_id: str
    message_id: int
    tensor_id: int
    tensor: torch.Tensor


class TensorIpcSender(OOBTensorConsumer):
    \"\"\"Send-side logic for tensor IPC via torch.multiprocessing.Queue.\"\"\"

    def __init__(self, queue: TensorIpcQueue):
        self.queue = queue
        self._tensor_id_counter = 0
        self._message_counter = 0
        self._sender_id = uuid.uuid4().hex[:8]

    def set_target_engine(self, target_engine: int) -> None:
        if target_engine != 0:
            raise IndexError(
                \"TensorIpcSender only supports a single queue; \"
                f\"got target engine {target_engine}\"
            )

    def new_message(self) -> None:
        self._message_counter += 1
        self._tensor_id_counter = 0

    def __call__(self, tensor: torch.Tensor) -> dict[str, Any] | None:
        try:
            if not tensor.is_shared():
                tensor = tensor.share_memory_()

            metadata = {
                \"sender_id\": self._sender_id,
                \"message_id\": self._message_counter,
                \"tensor_id\": self._tensor_id_counter,
            }
            self._tensor_id_counter += 1

            ipc_data = TensorIpcData(**metadata, tensor=tensor)
            self.queue.put(ipc_data, timeout=10.0)

            logger.debug(
                \"Sent tensor %s for (shape=%s, device=%s) via IPC queue\",
                metadata,
                tensor.shape,
                tensor.device,
            )
            return metadata
        except Exception as exc:
            logger.warning(
                \"Failed to send tensor via IPC queue: %s. Falling back to standard \"
                \"serialization.\",
                exc,
            )
            return None


@dataclasses.dataclass
class _Sender:
    current_message_id: int = -1
    tensors: dict[int, dict[int, torch.Tensor]] = field(default_factory=dict)


class TensorIpcReceiver:
    \"\"\"Receive-side logic for tensor IPC via torch.multiprocessing.Queue.\"\"\"

    def __init__(self, queue: TensorIpcQueue):
        self.queue = queue
        self._tensor_buffers = defaultdict[str, _Sender](_Sender)

    def __call__(
        self, dtype: str, shape: tuple[int, ...], meta: dict[str, Any]
    ) -> torch.Tensor:
        sender_id: str = meta[\"sender_id\"]
        message_id: int = meta[\"message_id\"]
        tensor_id: int = meta[\"tensor_id\"]

        while True:
            sender = self._tensor_buffers.get(sender_id)
            if sender is not None:
                tensors = sender.tensors
                tensor = tensors.get(message_id, {}).pop(tensor_id, None)
                if tensor is not None:
                    if sender.current_message_id != message_id:
                        while tensors and (mid := next(iter(tensors))) < message_id:
                            if sender.tensors.pop(mid):
                                logger.warning(
                                    \"Discarding stale tensors from sender %s\", sender_id
                                )
                        sender.current_message_id = message_id
                    logger.debug(
                        \"Received tensor (%s, %s) from sender %s for \"
                        \"(shape=%s, device=%s) via IPC queue\",
                        message_id,
                        tensor_id,
                        sender_id,
                        tensor.shape,
                        tensor.device,
                    )
                    return tensor

            ipc_data: TensorIpcData = self.queue.get(timeout=10.0)
            sender = self._tensor_buffers[ipc_data.sender_id]
            if sender.current_message_id > ipc_data.message_id:
                logger.warning(\"Ignoring stale tensor from sender %s\", ipc_data.sender_id)
                continue

            sender.tensors.setdefault(ipc_data.message_id, {})[ipc_data.tensor_id] = (
                ipc_data.tensor
            )
"""


def _path(rel_path: str) -> Path:
    return VLLM_ROOT / rel_path


def _read(rel_path: str) -> str:
    return _path(rel_path).read_text()


def _write(rel_path: str, content: str) -> None:
    _path(rel_path).write_text(content)


def _replace(rel_path: str, old: str, new: str, tag: str) -> None:
    content = _read(rel_path)
    if new in content:
        print(f"  ✓ {tag} already patched, skipping.")
        return
    if old not in content:
        raise AssertionError(f"PATCH ANCHOR NOT FOUND in {rel_path}: {tag}")
    _write(rel_path, content.replace(old, new, 1))
    print(f"  ✓ {tag}")


def _replace_any(rel_path: str, replacements: list[tuple[str, str]], tag: str) -> None:
    content = _read(rel_path)
    for _, new in replacements:
        if new in content:
            print(f"  ✓ {tag} already patched, skipping.")
            return
    for old, new in replacements:
        if old in content:
            _write(rel_path, content.replace(old, new, 1))
            print(f"  ✓ {tag}")
            return
    raise AssertionError(f"PATCH ANCHOR NOT FOUND in {rel_path}: {tag}")


def _insert_before(rel_path: str, anchor: str, insertion: str, tag: str) -> None:
    content = _read(rel_path)
    if insertion in content:
        print(f"  ✓ {tag} already patched, skipping.")
        return
    if anchor not in content:
        raise AssertionError(f"PATCH ANCHOR NOT FOUND in {rel_path}: {tag}")
    _write(rel_path, content.replace(anchor, insertion + anchor, 1))
    print(f"  ✓ {tag}")


def _insert_before_any(rel_path: str, anchors: list[str], insertion: str, tag: str) -> None:
    content = _read(rel_path)
    if insertion in content:
        print(f"  ✓ {tag} already patched, skipping.")
        return
    for anchor in anchors:
        if anchor in content:
            _write(rel_path, content.replace(anchor, insertion + anchor, 1))
            print(f"  ✓ {tag}")
            return
    raise AssertionError(f"PATCH ANCHOR NOT FOUND in {rel_path}: {tag}")


def _write_tensor_ipc_module() -> None:
    rel_path = "v1/engine/tensor_ipc.py"
    path = _path(rel_path)
    if path.exists() and "class TensorIpcSender" in path.read_text():
        print("  ✓ tensor_ipc.py already present, skipping.")
        return
    path.write_text(TENSOR_IPC_FILE)
    print("  ✓ wrote v1/engine/tensor_ipc.py")


def patch_multimodal_config() -> None:
    rel = "config/multimodal.py"
    _replace_any(
        rel,
        [
            (
                'MMCacheType = Literal["shm", "lru"]\n' "DummyOptions: TypeAlias = (\n",
                'MMCacheType = Literal["shm", "lru"]\n'
                'MMTensorIPC = Literal["direct_rpc", "torch_shm"]\n'
                "DummyOptions: TypeAlias = (\n",
            ),
            (
                'MMCacheType = Literal["shm", "lru"]\n'
                "MMDummyOptions: TypeAlias = dict[str, BaseDummyOptions]\n",
                'MMCacheType = Literal["shm", "lru"]\n'
                'MMTensorIPC = Literal["direct_rpc", "torch_shm"]\n'
                "MMDummyOptions: TypeAlias = dict[str, BaseDummyOptions]\n",
            ),
        ],
        "add MMTensorIPC type alias",
    )
    _replace(
        rel,
        "    video_pruning_rate: float | None = Field(default=None, ge=0.0, lt=1.0)\n"
        '    """Sets pruning rate for video pruning via Efficient Video Sampling.\n'
        "    Value sits in range [0;1) and determines fraction of media tokens\n"
        "    from each video to be pruned.\n"
        '    """\n',
        "    video_pruning_rate: float | None = Field(default=None, ge=0.0, lt=1.0)\n"
        '    """Sets pruning rate for video pruning via Efficient Video Sampling.\n'
        "    Value sits in range [0;1) and determines fraction of media tokens\n"
        "    from each video to be pruned.\n"
        '    """\n'
        '    mm_tensor_ipc: MMTensorIPC = "direct_rpc"\n'
        '    """IPC method for multimodal tensors.\n\n'
        '    - "direct_rpc": use msgpack/ZMQ serialization.\n'
        '    - "torch_shm": use torch multiprocessing queues for out-of-band\n'
        "      tensor transfer.\n"
        '    """\n',
        "add mm_tensor_ipc config field",
    )


def patch_model_config() -> None:
    rel = "config/model.py"
    _replace(
        rel,
        "from vllm.config.multimodal import MMCacheType, MMEncoderTPMode, MultiModalConfig\n",
        "from vllm.config.multimodal import (\n"
        "    MMCacheType,\n"
        "    MMEncoderTPMode,\n"
        "    MMTensorIPC,\n"
        "    MultiModalConfig,\n"
        ")\n",
        "import MMTensorIPC",
    )
    _replace_any(
        rel,
        [
            (
                "    video_pruning_rate: InitVar[float | None] = None\n\n",
                "    video_pruning_rate: InitVar[float | None] = None\n"
                "    mm_tensor_ipc: InitVar[MMTensorIPC | None] = None\n\n",
            ),
            (
                "    video_pruning_rate: InitVar[float | None] = None\n"
                "    evs_skip_threshold: InitVar[float | None] = None\n",
                "    video_pruning_rate: InitVar[float | None] = None\n"
                "    mm_tensor_ipc: InitVar[MMTensorIPC | None] = None\n"
                "    evs_skip_threshold: InitVar[float | None] = None\n",
            ),
        ],
        "add mm_tensor_ipc InitVar",
    )
    _replace_any(
        rel,
        [
            (
                "        video_pruning_rate: float | None,\n" "    ) -> None:\n",
                "        video_pruning_rate: float | None,\n"
                "        mm_tensor_ipc: MMTensorIPC | None,\n"
                "    ) -> None:\n",
            ),
            (
                "        video_pruning_rate: float | None,\n"
                "        evs_skip_threshold: float | None,\n",
                "        video_pruning_rate: float | None,\n"
                "        mm_tensor_ipc: MMTensorIPC | None,\n"
                "        evs_skip_threshold: float | None,\n",
            ),
        ],
        "thread mm_tensor_ipc through __post_init__",
    )
    _replace_any(
        rel,
        [
            (
                "                video_pruning_rate=video_pruning_rate,\n" "            )\n",
                "                video_pruning_rate=video_pruning_rate,\n"
                "                mm_tensor_ipc=mm_tensor_ipc,\n"
                "            )\n",
            ),
            (
                "                video_pruning_rate=video_pruning_rate,\n"
                "                evs_skip_threshold=evs_skip_threshold,\n",
                "                video_pruning_rate=video_pruning_rate,\n"
                "                mm_tensor_ipc=mm_tensor_ipc,\n"
                "                evs_skip_threshold=evs_skip_threshold,\n",
            ),
        ],
        "pass mm_tensor_ipc to MultiModalConfig",
    )
    _insert_before(
        rel,
        "    def get_sliding_window(self) -> int | None:\n",
        "        # torch_shm uses one IPC queue to rank 0. It is not compatible with\n"
        "        # multiple engine worlds because the front-end cannot route a tensor\n"
        "        # handle to every TP/PP/DP rank.\n"
        "        if (\n"
        "            self.multimodal_config is not None\n"
        '            and self.multimodal_config.mm_tensor_ipc == "torch_shm"\n'
        "            and parallel_config.world_size_across_dp > 1\n"
        "        ):\n"
        "            raise ValueError(\n"
        "                \"mm_tensor_ipc='torch_shm' is not supported with \"\n"
        '                "data_parallel_size > 1, tensor_parallel_size > 1, "\n'
        '                "or pipeline_parallel_size > 1."\n'
        "            )\n\n",
        "validate tensor IPC parallel mode",
    )


def patch_vllm_config() -> None:
    rel = "config/vllm.py"
    _insert_before_any(
        rel,
        [
            "        # Final off-switch for CP/APC:\n",
            "        if (\n"
            "            self.model_config\n"
            '            and self.model_config.architecture == "WhisperForConditionalGeneration"\n',
        ],
        "        if (\n"
        "            self.model_config is not None\n"
        "            and self.model_config.multimodal_config is not None\n"
        '            and self.model_config.multimodal_config.mm_tensor_ipc == "torch_shm"\n'
        '            and os.environ.get("VLLM_WORKER_MULTIPROC_METHOD") != "spawn"\n'
        "        ):\n"
        "            raise ValueError(\n"
        '                "torch_shm is known to fail without "\n'
        '                "VLLM_WORKER_MULTIPROC_METHOD=spawn"\n'
        "            )\n\n",
        "require spawn multiprocessing for torch_shm tensor IPC",
    )


def patch_arg_utils() -> None:
    rel = "engine/arg_utils.py"
    _replace(
        rel,
        "from vllm.config.multimodal import MMCacheType, MMEncoderTPMode\n",
        "from vllm.config.multimodal import MMCacheType, MMEncoderTPMode, MMTensorIPC\n",
        "import MMTensorIPC in arg_utils",
    )
    _replace_any(
        rel,
        [
            (
                "    video_pruning_rate: float = MultiModalConfig.video_pruning_rate\n"
                "    # LoRA fields\n",
                "    video_pruning_rate: float = MultiModalConfig.video_pruning_rate\n"
                "    mm_tensor_ipc: MMTensorIPC = MultiModalConfig.mm_tensor_ipc\n"
                "    # LoRA fields\n",
            ),
            (
                "    video_pruning_rate: float | None = MultiModalConfig.video_pruning_rate\n"
                "    # LoRA fields\n",
                "    video_pruning_rate: float | None = MultiModalConfig.video_pruning_rate\n"
                "    mm_tensor_ipc: MMTensorIPC = MultiModalConfig.mm_tensor_ipc\n"
                "    # LoRA fields\n",
            ),
            (
                "    video_pruning_rate: float | None = MultiModalConfig.video_pruning_rate\n"
                "    evs_skip_threshold: float | None = MultiModalConfig.evs_skip_threshold\n",
                "    video_pruning_rate: float | None = MultiModalConfig.video_pruning_rate\n"
                "    mm_tensor_ipc: MMTensorIPC = MultiModalConfig.mm_tensor_ipc\n"
                "    evs_skip_threshold: float | None = MultiModalConfig.evs_skip_threshold\n",
            ),
        ],
        "add EngineArgs.mm_tensor_ipc",
    )
    _replace_any(
        rel,
        [
            (
                "        multimodal_group.add_argument(\n"
                '            "--video-pruning-rate", **multimodal_kwargs["video_pruning_rate"]\n'
                "        )\n\n",
                "        multimodal_group.add_argument(\n"
                '            "--video-pruning-rate", **multimodal_kwargs["video_pruning_rate"]\n'
                "        )\n"
                "        multimodal_group.add_argument(\n"
                '            "--mm-tensor-ipc", **multimodal_kwargs["mm_tensor_ipc"]\n'
                "        )\n\n",
            ),
            (
                "        multimodal_group.add_argument(\n"
                '            "--video-pruning-rate", **multimodal_kwargs["video_pruning_rate"]\n'
                "        )\n"
                "        multimodal_group.add_argument(\n"
                '            "--evs-skip-threshold",\n',
                "        multimodal_group.add_argument(\n"
                '            "--video-pruning-rate", **multimodal_kwargs["video_pruning_rate"]\n'
                "        )\n"
                "        multimodal_group.add_argument(\n"
                '            "--mm-tensor-ipc", **multimodal_kwargs["mm_tensor_ipc"]\n'
                "        )\n"
                "        multimodal_group.add_argument(\n"
                '            "--evs-skip-threshold",\n',
            ),
        ],
        "add --mm-tensor-ipc CLI arg",
    )
    _replace_any(
        rel,
        [
            (
                "            video_pruning_rate=self.video_pruning_rate,\n"
                "            io_processor_plugin=self.io_processor_plugin,\n",
                "            video_pruning_rate=self.video_pruning_rate,\n"
                "            mm_tensor_ipc=self.mm_tensor_ipc,\n"
                "            io_processor_plugin=self.io_processor_plugin,\n",
            ),
            (
                "            video_pruning_rate=self.video_pruning_rate,\n"
                "            evs_skip_threshold=self.evs_skip_threshold,\n",
                "            video_pruning_rate=self.video_pruning_rate,\n"
                "            mm_tensor_ipc=self.mm_tensor_ipc,\n"
                "            evs_skip_threshold=self.evs_skip_threshold,\n",
            ),
        ],
        "pass mm_tensor_ipc to ModelConfig",
    )


def patch_serve_cli() -> None:
    rel = "entrypoints/cli/serve.py"
    _replace_any(
        rel,
        [
            (
                "    with launch_core_engines(\n"
                "        vllm_config, executor_class, log_stats, num_api_servers\n"
                "    ) as (local_engine_manager, coordinator, addresses):\n",
                "    with launch_core_engines(\n"
                "        vllm_config, executor_class, log_stats, num_api_servers\n"
                "    ) as (local_engine_manager, coordinator, addresses, tensor_queue):\n",
            ),
            (
                "    with launch_core_engines(\n"
                "        vllm_config, executor_class, log_stats, addresses, num_api_servers\n"
                "    ) as (local_engine_manager, coordinator, addresses):\n",
                "    with launch_core_engines(\n"
                "        vllm_config, executor_class, log_stats, addresses, num_api_servers\n"
                "    ) as (local_engine_manager, coordinator, addresses, tensor_queue):\n",
            ),
        ],
        "unpack tensor_queue from launch_core_engines",
    )
    _replace_any(
        rel,
        [
            (
                "            stats_update_address=coordinator.get_stats_publish_address()\n"
                "            if coordinator\n"
                "            else None,\n"
                "        )\n",
                "            stats_update_address=coordinator.get_stats_publish_address()\n"
                "            if coordinator\n"
                "            else None,\n"
                "            tensor_queue=tensor_queue,\n"
                "        )\n",
            ),
        ],
        "pass tensor_queue to API server workers",
    )


def patch_engine_core() -> None:
    rel = "v1/engine/core.py"
    _replace(
        rel,
        "from logging import DEBUG\n",
        "from logging import DEBUG\nfrom multiprocessing.queues import Queue\n",
        "import multiprocessing Queue",
    )
    _replace(
        rel,
        "from vllm.v1.engine.utils import (\n",
        "from vllm.v1.engine.tensor_ipc import TensorIpcReceiver\n"
        "from vllm.v1.engine.utils import (\n",
        "import TensorIpcReceiver",
    )
    _replace_any(
        rel,
        [
            (
                "        log_stats: bool,\n"
                "        client_handshake_address: str | None = None,\n"
                "        engine_index: int = 0,\n"
                "    ):\n",
                "        log_stats: bool,\n"
                "        client_handshake_address: str | None = None,\n"
                "        engine_index: int = 0,\n"
                "        tensor_queue: Queue | None = None,\n"
                "    ):\n",
            ),
            (
                "        log_stats: bool,\n"
                "        client_handshake_address: str | None = None,\n"
                "        *,\n"
                "        engine_index: int = 0,\n"
                "    ):\n",
                "        log_stats: bool,\n"
                "        client_handshake_address: str | None = None,\n"
                "        *,\n"
                "        engine_index: int = 0,\n"
                "        tensor_queue: Queue | None = None,\n"
                "    ):\n",
            ),
        ],
        "add tensor_queue to EngineCoreProc",
    )
    _replace(
        rel,
        "        self.engines_running = False\n\n" "        with self._perform_handshakes(\n",
        "        self.engines_running = False\n\n"
        "        self.tensor_ipc_receiver: TensorIpcReceiver | None = None\n"
        "        if tensor_queue is not None:\n"
        "            self.tensor_ipc_receiver = TensorIpcReceiver(tensor_queue)\n"
        '            logger.info("Using tensor IPC queue for multimodal tensor sharing")\n\n'
        "        with self._perform_handshakes(\n",
        "create TensorIpcReceiver in EngineCoreProc",
    )
    _replace(
        rel,
        "        # Msgpack serialization decoding.\n"
        "        add_request_decoder = MsgpackDecoder(EngineCoreRequest)\n"
        "        generic_decoder = MsgpackDecoder()\n",
        "        # Msgpack serialization decoding with optional tensor IPC receiver.\n"
        "        add_request_decoder = MsgpackDecoder(\n"
        "            EngineCoreRequest, oob_tensor_provider=self.tensor_ipc_receiver\n"
        "        )\n"
        "        generic_decoder = MsgpackDecoder(oob_tensor_provider=self.tensor_ipc_receiver)\n",
        "use tensor IPC receiver during decode",
    )
    _replace_any(
        rel,
        [
            (
                "        log_stats: bool,\n"
                "        client_handshake_address: str | None = None,\n"
                "    ):\n"
                "        # Counts forward-passes of the model so that we can synchronize\n",
                "        log_stats: bool,\n"
                "        client_handshake_address: str | None = None,\n"
                "        tensor_queue: Queue | None = None,\n"
                "    ):\n"
                "        # Counts forward-passes of the model so that we can synchronize\n",
            ),
            (
                "        log_stats: bool,\n"
                "        client_handshake_address: str | None = None,\n"
                "    ):\n"
                "        assert vllm_config.model_config.is_moe",
                "        log_stats: bool,\n"
                "        client_handshake_address: str | None = None,\n"
                "        tensor_queue: Queue | None = None,\n"
                "    ):\n"
                "        assert vllm_config.model_config.is_moe",
            ),
        ],
        "add tensor_queue to DPEngineCoreProc",
    )
    _replace_any(
        rel,
        [
            (
                "            client_handshake_address,\n" "            dp_rank,\n" "        )\n",
                "            client_handshake_address,\n"
                "            dp_rank,\n"
                "            tensor_queue=tensor_queue,\n"
                "        )\n",
            ),
            (
                "            client_handshake_address,\n"
                "            engine_index=dp_rank,\n"
                "        )\n",
                "            client_handshake_address,\n"
                "            engine_index=dp_rank,\n"
                "            tensor_queue=tensor_queue,\n"
                "        )\n",
            ),
        ],
        "pass tensor_queue to parent engine core",
    )


def patch_core_client() -> None:
    rel = "v1/engine/core_client.py"
    _replace(
        rel,
        "from dataclasses import dataclass\n",
        "from dataclasses import dataclass\nfrom multiprocessing.queues import Queue\n",
        "import multiprocessing Queue in core_client",
    )
    _replace(
        rel,
        "from vllm.v1.engine.exceptions import EngineDeadError\n",
        "from vllm.v1.engine.exceptions import EngineDeadError\n"
        "from vllm.v1.engine.tensor_ipc import TensorIpcSender\n",
        "import TensorIpcSender",
    )
    _replace(
        rel,
        "        self.vllm_config = vllm_config\n"
        "        # Serialization setup.\n"
        "        self.encoder = MsgpackEncoder()\n"
        "        self.decoder = MsgpackDecoder(EngineCoreOutputs)\n\n",
        "        self.vllm_config = vllm_config\n\n",
        "delay serialization setup until tensor queue is known",
    )
    _replace(
        rel,
        "            self.stats_update_address: str | None = None\n"
        "            if client_addresses:\n",
        "            self.stats_update_address: str | None = None\n"
        "            tensor_queue: Queue | None = None\n"
        "            if client_addresses:\n",
        "initialize tensor_queue in core_client",
    )
    _replace_any(
        rel,
        [
            (
                '                self.stats_update_address = client_addresses.get("stats_update_address")\n'
                "            else:\n",
                '                self.stats_update_address = client_addresses.get("stats_update_address")\n'
                "                tensor_queue = client_addresses.get("
                '"tensor_queue")  # type: ignore[assignment]\n'
                "            else:\n",
            ),
            (
                '                self.stats_update_address = client_addresses.get("stats_update_address")\n'
                "                self.input_socket = self.resources.input_socket = make_zmq_socket(\n",
                '                self.stats_update_address = client_addresses.get("stats_update_address")\n'
                "                tensor_queue = client_addresses.get("
                '"tensor_queue")  # type: ignore[assignment]\n'
                "                self.input_socket = self.resources.input_socket = make_zmq_socket(\n",
            ),
        ],
        "read tensor_queue from client addresses",
    )
    _replace_any(
        rel,
        [
            (
                "                with launch_core_engines(vllm_config, executor_class, log_stats) as (\n"
                "                    engine_manager,\n"
                "                    coordinator,\n"
                "                    addresses,\n"
                "                ):\n",
                "                with launch_core_engines(vllm_config, executor_class, log_stats) as (\n"
                "                    engine_manager,\n"
                "                    coordinator,\n"
                "                    addresses,\n"
                "                    tensor_queue,\n"
                "                ):\n",
            ),
            (
                "                with launch_core_engines(\n"
                "                    vllm_config,\n"
                "                    executor_class,\n"
                "                    log_stats,\n"
                "                    addresses,\n"
                "                ) as (engine_manager, coordinator, addresses):\n",
                "                with launch_core_engines(\n"
                "                    vllm_config,\n"
                "                    executor_class,\n"
                "                    log_stats,\n"
                "                    addresses,\n"
                "                ) as (engine_manager, coordinator, addresses, tensor_queue):\n",
            ),
        ],
        "unpack tensor_queue in core_client",
    )
    _insert_before_any(
        rel,
        [
            "            # Create input and output sockets.\n",
            "            # Wait for ready messages from each engine on the input socket.\n",
        ],
        "            # Serialization setup with tensor queues for multimodal tensor IPC.\n"
        "            tensor_ipc_sender: TensorIpcSender | None = None\n"
        '            model_config = getattr(vllm_config, "model_config", None)\n'
        "            if (\n"
        "                model_config is not None\n"
        "                and model_config.multimodal_config is not None\n"
        "            ):\n"
        "                mm_tensor_ipc = model_config.multimodal_config.mm_tensor_ipc\n"
        '                if mm_tensor_ipc == "torch_shm" and tensor_queue is not None:\n'
        "                    tensor_ipc_sender = TensorIpcSender(tensor_queue)\n\n"
        "            self.encoder = MsgpackEncoder(oob_tensor_consumer=tensor_ipc_sender)\n"
        "            self.decoder = MsgpackDecoder(EngineCoreOutputs)\n\n",
        "set up tensor IPC-aware encoder",
    )


def patch_engine_utils() -> None:
    rel = "v1/engine/utils.py"
    _replace(
        rel,
        "from multiprocessing import Process, connection\n",
        "from multiprocessing import Process, connection\nfrom multiprocessing.queues import Queue\n",
        "import multiprocessing Queue in engine utils",
    )
    _replace(
        rel,
        "        log_stats: bool,\n"
        "        client_handshake_address: str | None = None,\n"
        "    ):\n",
        "        log_stats: bool,\n"
        "        client_handshake_address: str | None = None,\n"
        "        tensor_queue: Queue | None = None,\n"
        "    ):\n",
        "add tensor_queue to CoreEngineProcManager",
    )
    _replace(
        rel,
        '            "log_stats": log_stats,\n' "        }\n",
        '            "log_stats": log_stats,\n'
        '            "tensor_queue": tensor_queue,\n'
        "        }\n",
        "pass tensor_queue to engine core process",
    )
    _replace(
        rel,
        "        EngineZmqAddresses,\n" "    ]\n",
        "        EngineZmqAddresses,\n" "        Queue | None,\n" "    ]\n",
        "update launch_core_engines return type",
    )
    _replace_any(
        rel,
        [
            (
                '    """Launch engine and DP coordinator processes as needed."""\n'
                "\n"
                "    parallel_config = vllm_config.parallel_config\n",
                '    """Launch engine and DP coordinator processes as needed."""\n'
                "\n"
                "    parallel_config = vllm_config.parallel_config\n"
                "    tensor_queue: Queue | None = None\n"
                "    multimodal_config = vllm_config.model_config.multimodal_config\n"
                "    if (\n"
                "        multimodal_config is not None\n"
                '        and multimodal_config.mm_tensor_ipc == "torch_shm"\n'
                '        and parallel_config.data_parallel_backend != "ray"\n'
                "    ):\n"
                "        tensor_queue = get_mp_context().Queue()\n\n",
            )
        ],
        "create tensor IPC queue",
    )
    _replace(
        rel,
        "        yield engine_actor_manager, coordinator, addresses\n",
        "        yield engine_actor_manager, coordinator, addresses, None\n",
        "yield four values for ray backend",
    )
    _replace(
        rel,
        "                local_start_index=local_start_index or 0,\n" "            )\n",
        "                local_start_index=local_start_index or 0,\n"
        "                tensor_queue=tensor_queue,\n"
        "            )\n",
        "pass tensor_queue to CoreEngineProcManager",
    )
    _replace(
        rel,
        "        yield local_engine_manager, coordinator, addresses\n",
        "        yield local_engine_manager, coordinator, addresses, tensor_queue\n",
        "yield tensor_queue from launch_core_engines",
    )


def patch_serial_utils() -> None:
    rel = "v1/serial_utils.py"
    _replace(
        rel,
        "import pickle\n",
        "import pickle\nfrom abc import ABC, abstractmethod\n",
        "import ABC helpers in serial_utils",
    )
    _insert_before(
        rel,
        "\n\ndef _log_insecure_serialization_warning():\n",
        textwrap.dedent(
            """\


            class OOBTensorConsumer(ABC):
                @abstractmethod
                def __call__(self, tensor: torch.Tensor) -> dict | None:
                    return None

                @abstractmethod
                def new_message(self) -> None:
                    pass


            OOBTensorProvider = Callable[[str, tuple[int, ...], dict], torch.Tensor]
            """
        ),
        "add OOB tensor interfaces",
    )
    _replace(
        rel,
        "    def __init__(self, size_threshold: int | None = None):\n"
        "        if size_threshold is None:\n",
        "    def __init__(\n"
        "        self,\n"
        "        size_threshold: int | None = None,\n"
        "        oob_tensor_consumer: OOBTensorConsumer | None = None,\n"
        "    ):\n"
        "        if size_threshold is None:\n",
        "add OOB consumer to MsgpackEncoder init",
    )
    _replace(
        rel,
        "        self.aux_buffers: list[bytestr] | None = None\n"
        "        self.size_threshold = size_threshold\n",
        "        self.aux_buffers: list[bytestr] | None = None\n"
        "        self.size_threshold = size_threshold\n"
        "        self.oob_tensor_consumer = oob_tensor_consumer\n",
        "store OOB consumer",
    )
    _replace(
        rel,
        "        try:\n" '            self.aux_buffers = bufs = [b""]\n',
        "        try:\n"
        "            if self.oob_tensor_consumer is not None:\n"
        "                self.oob_tensor_consumer.new_message()\n"
        '            self.aux_buffers = bufs = [b""]\n',
        "start OOB message in encode",
    )
    _replace(
        rel,
        "        try:\n" "            self.aux_buffers = [buf]\n",
        "        try:\n"
        "            if self.oob_tensor_consumer is not None:\n"
        "                self.oob_tensor_consumer.new_message()\n"
        "            self.aux_buffers = [buf]\n",
        "start OOB message in encode_into",
    )
    _replace(
        rel,
        "    def _encode_tensor(\n"
        "        self, obj: torch.Tensor\n"
        "    ) -> tuple[str, tuple[int, ...], int | memoryview]:\n"
        "        assert self.aux_buffers is not None\n"
        "        # view the tensor as a contiguous 1D array of bytes\n"
        "        arr_data = tensor_data(obj)\n"
        "        if obj.nbytes < self.size_threshold:\n"
        "            # Smaller tensors are encoded inline, just like ndarrays.\n"
        "            data = msgpack.Ext(CUSTOM_TYPE_RAW_VIEW, arr_data)\n"
        "        else:\n"
        "            # Otherwise encode index of backing buffer to avoid copy.\n"
        "            data = len(self.aux_buffers)\n"
        "            self.aux_buffers.append(arr_data)\n"
        '        dtype = str(obj.dtype).removeprefix("torch.")\n'
        "        return dtype, obj.shape, data\n",
        "    def _encode_tensor(\n"
        "        self, obj: torch.Tensor\n"
        "    ) -> tuple[str, tuple[int, ...], int | dict | memoryview]:\n"
        "        oob_consumer = self.oob_tensor_consumer\n"
        '        if obj.nbytes < self.size_threshold and obj.device.type == "cpu":\n'
        "            # Smaller CPU tensors are encoded inline, just like ndarrays.\n"
        "            data = msgpack.Ext(CUSTOM_TYPE_RAW_VIEW, tensor_data(obj))\n"
        "        elif oob_consumer is not None and (data := oob_consumer(obj)) is not None:\n"
        "            assert isinstance(data, dict)\n"
        "        else:\n"
        "            # Otherwise encode index of backing buffer to avoid copy.\n"
        "            assert self.aux_buffers is not None\n"
        "            data = len(self.aux_buffers)\n"
        "            self.aux_buffers.append(tensor_data(obj))\n"
        '        dtype = str(obj.dtype).removeprefix("torch.")\n'
        "        return dtype, obj.shape, data\n",
        "route tensors through OOB consumer",
    )
    _replace(
        rel,
        "    def __init__(self, t: Any | None = None, share_mem: bool = True):\n"
        "        self.share_mem = share_mem\n",
        "    def __init__(\n"
        "        self,\n"
        "        t: Any | None = None,\n"
        "        share_mem: bool = True,\n"
        "        oob_tensor_provider: OOBTensorProvider | None = None,\n"
        "    ):\n"
        "        self.share_mem = share_mem\n",
        "add OOB provider to MsgpackDecoder init",
    )
    _replace(
        rel,
        "        self.aux_buffers: Sequence[bytestr] = ()\n"
        "        if envs.VLLM_ALLOW_INSECURE_SERIALIZATION:\n",
        "        self.aux_buffers: Sequence[bytestr] = ()\n"
        "        self.oob_tensor_provider = oob_tensor_provider\n"
        "        if envs.VLLM_ALLOW_INSECURE_SERIALIZATION:\n",
        "store OOB provider",
    )
    _replace(
        rel,
        "    def _decode_tensor(self, arr: Any) -> torch.Tensor:\n"
        "        dtype, shape, data = arr\n"
        "        is_aux = isinstance(data, int)\n",
        "    def _decode_tensor(self, arr: Any) -> torch.Tensor:\n"
        "        dtype, shape, data = arr\n"
        "        if isinstance(data, dict):\n"
        "            assert self.oob_tensor_provider, (\n"
        '                "Received OOB tensor but tensor provider is not set"\n'
        "            )\n"
        "            return self.oob_tensor_provider(dtype, shape, data)\n\n"
        "        is_aux = isinstance(data, int)\n",
        "decode OOB tensor handles",
    )


def patch_v1_utils() -> None:
    rel = "v1/utils.py"
    _replace(
        rel,
        "from multiprocessing import connection\n",
        "from multiprocessing import connection\nfrom multiprocessing.queues import Queue\n",
        "import multiprocessing Queue in v1 utils",
    )
    _replace(
        rel,
        "        stats_update_address: str | None = None,\n" "    ):\n",
        "        stats_update_address: str | None = None,\n"
        "        tensor_queue: Queue | None = None,\n"
        "    ):\n",
        "add tensor_queue to APIServerProcessManager",
    )
    _replace(
        rel,
        "            stats_update_address: Optional stats update address\n" '        """\n',
        "            stats_update_address: Optional stats update address\n"
        "            tensor_queue: Optional tensor IPC queue for sharing MM tensors\n"
        '        """\n',
        "document tensor_queue argument",
    )
    _replace(
        rel,
        "            if stats_update_address is not None:\n"
        '                client_config["stats_update_address"] = stats_update_address\n\n'
        "            proc = spawn_context.Process(\n",
        "            if stats_update_address is not None:\n"
        '                client_config["stats_update_address"] = stats_update_address\n'
        "            if tensor_queue is not None:\n"
        '                client_config["tensor_queue"] = tensor_queue\n\n'
        "            proc = spawn_context.Process(\n",
        "pass tensor_queue to API worker client config",
    )
    _replace(
        rel,
        "    return tensor.flatten().contiguous().view(torch.uint8).numpy().data\n",
        "    return tensor.flatten().cpu().contiguous().view(torch.uint8).numpy().data\n",
        "make tensor_data safe for CUDA fallback",
    )


def main() -> None:
    print(f"Applying vLLM multimodal tensor IPC patch under {VLLM_ROOT}...")
    _write_tensor_ipc_module()
    patch_multimodal_config()
    patch_model_config()
    patch_vllm_config()
    patch_arg_utils()
    patch_serve_cli()
    patch_engine_core()
    patch_core_client()
    patch_engine_utils()
    patch_serial_utils()
    patch_v1_utils()
    print("Done.")


if __name__ == "__main__":
    main()

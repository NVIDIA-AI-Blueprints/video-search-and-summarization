# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""
ECNixlConnector — GPU-to-GPU RDMA transfer of encoder embeddings via NIXL.

Replaces the disk-based ECExampleConnector with direct GPU RDMA transfers
using the NIXL library (same transport used by the KV NixlConnector for
disaggregated prefill).

Architecture:
  Producer (Encoder instance):
    - Worker: pre-allocates a chunked GPU staging buffer, registers with NIXL,
      copies encoder outputs into it on save_caches(), and serves metadata
      via a ZMQ ROUTER socket.
    - Scheduler: no-op (has_cache_item returns False).

  Consumer (PD instance):
    - Scheduler: background poller queries the producer's ZMQ server for
      available embeddings; has_cache_item() checks a local set.
    - Worker: on first load, does a NIXL handshake with the producer.
      start_load_caches() issues RDMA READs into a local staging buffer,
      then copies the tensor into encoder_cache.
"""

import contextlib
import os
import sys
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import msgspec
import numpy as np
import torch
import zmq

from vllm.config import VllmConfig
from vllm.distributed.ec_transfer.ec_connector.base import (
    ECConnectorBase,
    ECConnectorMetadata,
    ECConnectorRole,
)
from vllm.logger import init_logger
from vllm.platforms import current_platform
from vllm.utils.network_utils import make_zmq_path, make_zmq_socket
from vllm.v1.core.sched.output import SchedulerOutput

if TYPE_CHECKING:
    from vllm.v1.request import Request

logger = init_logger(__name__)

# ---------------------------------------------------------------------------
# Lazy NIXL import (mirrors pattern from kv_transfer nixl_connector)
# ---------------------------------------------------------------------------
try:
    if "UCX_RCACHE_MAX_UNRELEASED" not in os.environ:
        if "nixl" not in sys.modules and "rixl" not in sys.modules:
            os.environ["UCX_RCACHE_MAX_UNRELEASED"] = "1024"

    if not current_platform.is_rocm():
        from nixl._api import nixl_agent as NixlWrapper
    else:
        from rixl._api import nixl_agent as NixlWrapper

    logger.info("NIXL available for EC transfer")
except ImportError:
    NixlWrapper = None
    logger.warning("NIXL not available for EC transfer")

try:
    if not current_platform.is_rocm():
        from nixl._api import nixl_agent_config
    else:
        from rixl._api import nixl_agent_config
except ImportError:
    nixl_agent_config = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_GET_AGENT_META = b"ec_get_meta"
_LIST_AVAILABLE = b"ec_list_avail"
_ACK_CONSUMED = b"ec_ack"
_NOTIFY_AVAILABLE = b"ec_notify_avail"
_DEFAULT_CHUNK_SIZE = 256 * 1024  # 256 KB
_INTEGRITY_TOKEN_BYTES = 16


# ---------------------------------------------------------------------------
# Data classes (msgspec-serializable for ZMQ transport)
# ---------------------------------------------------------------------------
@dataclass
class ECSlotMeta:
    """Metadata for a single embedding slot in the producer's staging buffer."""

    mm_hash: str
    chunk_ids: list[int] = field(default_factory=list)
    num_chunks: int = 0
    size_bytes: int = 0
    shape: list[int] = field(default_factory=list)
    dtype_str: str = ""
    integrity_token: bytes = b""

    @property
    def start_chunk(self) -> int:
        """Backward compat: first chunk ID (contiguous allocations only)."""
        return self.chunk_ids[0] if self.chunk_ids else 0


@dataclass
class ECNixlAgentMetadata:
    """NIXL agent metadata exchanged during handshake."""

    engine_id: str
    agent_metadata: bytes
    buffer_base_addr: int
    buffer_size_bytes: int
    num_chunks: int
    chunk_size: int
    device_id: int


@dataclass
class ECNixlConnectorMetadata(ECConnectorMetadata):
    """Scheduler → Worker metadata for a single step."""

    items_to_load: list[ECSlotMeta]

    def __init__(self):
        self.items_to_load = []


# ---------------------------------------------------------------------------
# Chunked staging buffer
# ---------------------------------------------------------------------------
class ChunkedStagingBuffer:
    """
    GPU buffer divided into fixed-size chunks for NIXL block-level transfers.

    Each chunk maps 1:1 to a NIXL transfer descriptor, so chunk IDs can be
    used directly as descriptor IDs in make_prepped_xfer().

    Supports two allocation modes:
      - Free-list (default): O(N) pop from deque, allows non-contiguous
        allocations. Producer uses this for scatter-write.
      - Ring buffer (ring_mode=True): O(1) bump-pointer allocation,
        always contiguous. Consumer uses this for RDMA staging.
    """

    def __init__(
        self,
        total_bytes: int,
        device: str,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        ring_mode: bool = False,
    ):
        from collections import deque

        self.chunk_size = chunk_size
        self.num_chunks = total_bytes // chunk_size
        actual = self.num_chunks * chunk_size
        self.buffer = torch.empty(actual, dtype=torch.uint8, device=device)
        self.base_addr: int = self.buffer.data_ptr()
        self.device_id: int = max(self.buffer.get_device(), 0)

        self._ring_mode = ring_mode
        self._free_chunks: deque[int] = deque(range(self.num_chunks))
        self._allocs: OrderedDict[str, list[int]] = OrderedDict()
        self._lock = threading.Lock()

        # Ring buffer state
        self._write_cursor = 0
        self._read_cursor = 0
        self._freed_ranges: dict[int, int] = {}

        if self.buffer.is_cuda:
            self._verify_stream = torch.cuda.Stream(
                device=self.buffer.device)
            self._copy_stream = torch.cuda.Stream(
                device=self.buffer.device)
        else:
            self._verify_stream = None
            self._copy_stream = None

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _is_contiguous(chunk_ids: list[int]) -> bool:
        if len(chunk_ids) <= 1:
            return True
        return chunk_ids[-1] - chunk_ids[0] == len(chunk_ids) - 1

    # -- allocation -----------------------------------------------------------

    def allocate(
        self, key: str, size_bytes: int, contiguous: bool = False,
    ) -> tuple[list[int], int]:
        """Allocate chunks for *key*. Returns (chunk_ids, n_chunks).

        In ring mode, always contiguous O(1). In free-list mode, pops
        arbitrary chunks unless *contiguous* is True (O(N) scan).
        """
        n = (size_bytes + self.chunk_size - 1) // self.chunk_size
        with self._lock:
            if key in self._allocs:
                old_ids = self._allocs[key]
                if len(old_ids) >= n:
                    return old_ids, len(old_ids)
                # Free old allocation
                if self._ring_mode:
                    for cid in old_ids:
                        self._freed_ranges[cid] = 1
                    self._flush_freed()
                else:
                    self._free_chunks.extend(old_ids)
                del self._allocs[key]

            if self._ring_mode:
                return self._ring_allocate(key, n)

            if contiguous:
                result = self._find_contiguous(key, n)
                if result is not None:
                    return result
                raise RuntimeError(
                    f"EC staging buffer full (contiguous): need {n} "
                    f"contiguous chunks, "
                    f"{len(self._free_chunks)}/{self.num_chunks} free. "
                    f"Increase ec_buffer_size."
                )

            return self._freelist_allocate(key, n)

    def _freelist_allocate(
        self, key: str, n: int,
    ) -> tuple[list[int], int]:
        """Pop *n* chunks from free-list (caller must hold self._lock)."""
        if len(self._free_chunks) < n:
            raise RuntimeError(
                f"EC staging buffer full: need {n} chunks, "
                f"{len(self._free_chunks)}/{self.num_chunks} free. "
                f"Increase ec_buffer_size."
            )
        chunk_ids = [self._free_chunks.popleft() for _ in range(n)]
        self._allocs[key] = chunk_ids
        return chunk_ids, n

    def _find_contiguous(
        self, key: str, n: int,
    ) -> tuple[list[int], int] | None:
        """Find *n* contiguous free chunks (caller must hold self._lock)."""
        free_set = set(self._free_chunks)
        for start in range(self.num_chunks - n + 1):
            if all((start + j) in free_set for j in range(n)):
                chunk_ids = list(range(start, start + n))
                for cid in chunk_ids:
                    self._free_chunks.remove(cid)
                self._allocs[key] = chunk_ids
                return chunk_ids, n
        return None

    def _ring_allocate(
        self, key: str, n: int,
    ) -> tuple[list[int], int]:
        """Occupancy-aware ring-buffer allocation (caller holds self._lock).

        Raises RuntimeError when there is not enough *contiguous* free
        space.  Three regions are checked in order:

        1. **Tail**: contiguous free chunks from write_cursor forward.
        2. **Head**: contiguous free chunks in [0, write_cursor).
        3. **Full scan**: first contiguous gap of size *n* anywhere
           in the buffer (fallback when out-of-order frees leave
           gaps that tail/head miss).

        All regions are verified against actually-occupied chunks
        rather than assumed free, so non-FIFO eviction order and
        wrap-around re-allocation are safe.
        """
        allocated = sum(len(v) for v in self._allocs.values())
        available = self.num_chunks - allocated
        if n > available:
            raise RuntimeError(
                f"EC ring buffer full: need {n} chunks, "
                f"{available}/{self.num_chunks} free "
                f"(write={self._write_cursor} read={self._read_cursor}, "
                f"allocs={len(self._allocs)}). "
                f"Increase ec_buffer_size or wait for consumer."
            )

        occupied: set[int] = set()
        for v in self._allocs.values():
            occupied.update(v)

        # 1) Tail: contiguous free from write_cursor to end.
        tail_free = 0
        pos = self._write_cursor
        while pos < self.num_chunks and pos not in occupied:
            tail_free += 1
            pos += 1

        if n <= tail_free:
            start = self._write_cursor
            chunk_ids = list(range(start, start + n))
            self._write_cursor = start + n
            self._allocs[key] = chunk_ids
            return chunk_ids, n

        # 2) Head: contiguous free from 0 up to first occupied chunk
        #    (bounded by write_cursor to avoid overlap with tail).
        head_free = 0
        pos = 0
        while pos < self._write_cursor and pos not in occupied:
            head_free += 1
            pos += 1

        if n <= head_free:
            chunk_ids = list(range(0, n))
            self._write_cursor = n
            self._allocs[key] = chunk_ids
            return chunk_ids, n

        # 3) Full scan: find the first contiguous gap of size n
        #    anywhere in the buffer. This handles the case where
        #    out-of-order frees created interior gaps that neither
        #    tail nor head cover (e.g. read_cursor stuck at 0
        #    because the first alloc is still live).
        run_start = -1
        run_len = 0
        for i in range(self.num_chunks):
            if i not in occupied:
                if run_len == 0:
                    run_start = i
                run_len += 1
                if run_len >= n:
                    chunk_ids = list(range(run_start, run_start + n))
                    self._write_cursor = run_start + n
                    self._allocs[key] = chunk_ids
                    return chunk_ids, n
            else:
                run_len = 0

        raise RuntimeError(
            f"EC ring buffer full: need {n} contiguous chunks, "
            f"tail={tail_free} head={head_free} "
            f"(write={self._write_cursor} read={self._read_cursor}, "
            f"allocs={len(self._allocs)}). "
            f"Increase ec_buffer_size or wait for consumer."
        )

    def _flush_freed(self) -> None:
        """Advance read_cursor through contiguous freed regions
        (caller must hold self._lock)."""
        while self._read_cursor in self._freed_ranges:
            self._freed_ranges.pop(self._read_cursor)
            self._read_cursor += 1
            if self._read_cursor >= self.num_chunks:
                self._read_cursor = 0

    def free(self, key: str) -> None:
        with self._lock:
            chunk_ids = self._allocs.pop(key, None)
            if chunk_ids is None:
                return
            if self._ring_mode:
                for cid in chunk_ids:
                    self._freed_ranges[cid] = 1
                self._flush_freed()
                if not self._allocs:
                    self._write_cursor = 0
                    self._read_cursor = 0
                    self._freed_ranges.clear()
            else:
                self._free_chunks.extend(chunk_ids)

    def evict_oldest(
        self, consumed_keys: set[str] | None = None,
    ) -> tuple[str | None, bool]:
        """Evict the oldest *consumed* allocation.

        Iterates ``_allocs`` in insertion order (oldest first) and evicts
        the first entry whose key appears in *consumed_keys*.  Un-consumed
        entries are skipped — this avoids head-of-line blocking when the
        absolute oldest hash belongs to a request the consumer hasn't
        scheduled yet.

        Safety against ring-buffer overwrites is guaranteed by the
        occupancy-aware ``_ring_allocate``, which verifies that target
        chunks are not in use before writing.

        Returns ``(key, True)`` on success, ``(None, False)`` when no
        consumed entry exists.
        """
        with self._lock:
            if not self._allocs or not consumed_keys:
                return None, False

            for key in list(self._allocs.keys()):
                if key in consumed_keys:
                    chunk_ids = self._allocs.pop(key)
                    if self._ring_mode:
                        for cid in chunk_ids:
                            self._freed_ranges[cid] = 1
                        self._flush_freed()
                        if not self._allocs:
                            self._write_cursor = 0
                            self._read_cursor = 0
                            self._freed_ranges.clear()
                    else:
                        self._free_chunks.extend(chunk_ids)
                    return key, True
            return None, False

    # -- data movement -------------------------------------------------------

    def _scatter_write(
        self, chunk_ids: list[int], payload: torch.Tensor,
    ) -> None:
        """Write payload across possibly non-contiguous chunks."""
        remaining = payload.numel()
        src_pos = 0
        for cid in chunk_ids:
            dst = cid * self.chunk_size
            n = min(self.chunk_size, remaining)
            self.buffer[dst:dst + n].copy_(payload[src_pos:src_pos + n])
            src_pos += n
            remaining -= n
            if remaining <= 0:
                break

    def _gather_read(
        self, chunk_ids: list[int], total_bytes: int,
    ) -> torch.Tensor:
        """Read data from possibly non-contiguous chunks into a
        contiguous tensor."""
        if self._is_contiguous(chunk_ids):
            offset = chunk_ids[0] * self.chunk_size
            return self.buffer[offset:offset + total_bytes].clone()

        result = torch.empty(
            total_bytes, dtype=torch.uint8, device=self.buffer.device,
        )
        remaining = total_bytes
        dst_pos = 0
        for cid in chunk_ids:
            src = cid * self.chunk_size
            n = min(self.chunk_size, remaining)
            result[dst_pos:dst_pos + n].copy_(self.buffer[src:src + n])
            dst_pos += n
            remaining -= n
            if remaining <= 0:
                break
        return result

    def copy_in(
        self,
        key: str,
        tensor: torch.Tensor,
        async_copy: bool = False,
    ) -> ECSlotMeta:
        """Copy *tensor* into the buffer and return its slot metadata.

        Layout per allocation: [integrity_token | tensor_data]
        The integrity token lets the consumer detect stale/evicted data
        after an RDMA read.
        """
        data_bytes = tensor.numel() * tensor.element_size()
        total_bytes = _INTEGRITY_TOKEN_BYTES + data_bytes
        n_needed = (total_bytes + self.chunk_size - 1) // self.chunk_size

        with self._lock:
            reuse = (key in self._allocs
                     and len(self._allocs[key]) >= n_needed)

        chunk_ids, n = self.allocate(key, total_bytes)
        first_offset = chunk_ids[0] * self.chunk_size

        if reuse:
            with (torch.cuda.stream(self._copy_stream)
                  if self._copy_stream is not None
                  else contextlib.nullcontext()):
                token = bytes(
                    self.buffer[
                        first_offset:first_offset + _INTEGRITY_TOKEN_BYTES
                    ].cpu().numpy()
                )
        else:
            token = uuid.uuid4().bytes

        flat = tensor.contiguous().view(torch.uint8).reshape(-1)

        stream_ctx = (
            torch.cuda.stream(self._copy_stream)
            if async_copy and self._copy_stream is not None
            else contextlib.nullcontext()
        )

        with stream_ctx:
            if not reuse:
                token_t = torch.frombuffer(
                    bytearray(token), dtype=torch.uint8
                ).to(self.buffer.device, non_blocking=True)
                self.buffer[
                    first_offset:first_offset + _INTEGRITY_TOKEN_BYTES
                ].copy_(token_t)

            if self._is_contiguous(chunk_ids):
                dst = first_offset + _INTEGRITY_TOKEN_BYTES
                self.buffer[dst:dst + flat.numel()].copy_(flat)
            else:
                payload = torch.empty(
                    total_bytes, dtype=torch.uint8,
                    device=self.buffer.device,
                )
                payload[:_INTEGRITY_TOKEN_BYTES].copy_(
                    self.buffer[
                        first_offset:first_offset + _INTEGRITY_TOKEN_BYTES
                    ]
                )
                payload[_INTEGRITY_TOKEN_BYTES:].copy_(flat)
                self._scatter_write(chunk_ids, payload)

        return ECSlotMeta(
            mm_hash=key,
            chunk_ids=chunk_ids,
            num_chunks=n,
            size_bytes=total_bytes,
            shape=list(tensor.shape),
            dtype_str=str(tensor.dtype),
            integrity_token=token,
        )

    def sync_copy(self) -> None:
        """Block until all async copy_in ops on _copy_stream are done."""
        if self._copy_stream is not None:
            self._copy_stream.synchronize()

    def read_out_with_verify(
        self,
        chunk_ids: list[int],
        size_bytes: int,
        shape: list[int],
        dtype: torch.dtype,
        expected_token: bytes,
    ) -> torch.Tensor | None:
        """Gather data from chunks, verify integrity, reshape.

        Returns None if the integrity token doesn't match (stale data).
        """
        stream_ctx = (torch.cuda.stream(self._verify_stream)
                       if self._verify_stream is not None
                       else contextlib.nullcontext())
        with stream_ctx:
            raw = self._gather_read(chunk_ids, size_bytes)
            actual_token = bytes(
                raw[:_INTEGRITY_TOKEN_BYTES].cpu().numpy())
        if actual_token != expected_token:
            return None
        data = raw[_INTEGRITY_TOKEN_BYTES:]
        return data.view(dtype).reshape(shape)

    def read_out(
        self,
        chunk_ids: list[int],
        size_bytes: int,
        shape: list[int],
        dtype: torch.dtype,
    ) -> torch.Tensor:
        """Gather data from chunks, reshape (no integrity check)."""
        raw = self._gather_read(chunk_ids, size_bytes)
        return raw.view(dtype).reshape(shape)

    # -- zero-copy views (consumer) ------------------------------------------

    def batch_verify_tokens(
        self,
        items: list[tuple[int, bytes]],
    ) -> list[bool]:
        """Verify integrity tokens for multiple items in a single GPU→CPU copy.

        Uses a dedicated CUDA stream so that the GPU→GPU gather and the
        subsequent D2H copy do not serialize behind model-inference kernels
        running on the default stream.

        Args:
            items: list of (start_chunk, expected_token) tuples.

        Returns:
            list of booleans, True if the token matches.
        """
        if not items:
            return []

        n = len(items)
        stream_ctx = (torch.cuda.stream(self._verify_stream)
                       if self._verify_stream is not None
                       else contextlib.nullcontext())
        with stream_ctx:
            token_buf = torch.empty(
                n * _INTEGRITY_TOKEN_BYTES, dtype=torch.uint8,
                device=self.buffer.device,
            )
            for i, (start_chunk, _) in enumerate(items):
                offset = start_chunk * self.chunk_size
                token_buf[
                    i * _INTEGRITY_TOKEN_BYTES
                    : (i + 1) * _INTEGRITY_TOKEN_BYTES
                ] = self.buffer[offset : offset + _INTEGRITY_TOKEN_BYTES]

            all_tokens = token_buf.cpu().numpy()

        results = []
        for i, (start_chunk, expected) in enumerate(items):
            actual = bytes(
                all_tokens[
                    i * _INTEGRITY_TOKEN_BYTES
                    : (i + 1) * _INTEGRITY_TOKEN_BYTES
                ]
            )
            ok = actual == expected
            if not ok:
                logger.error(
                    "ECTrace token_mismatch: chunk=%d "
                    "expected=%s actual=%s all_zero=%s",
                    start_chunk,
                    expected.hex(), actual.hex(),
                    all(b == 0 for b in actual),
                )
            results.append(ok)
        return results

    def view_after_verify(
        self,
        start_chunk: int,
        size_bytes: int,
        shape: list[int],
        dtype: torch.dtype,
    ) -> torch.Tensor:
        """Return a zero-copy view into the buffer (caller already verified).

        The caller must NOT free the staging allocation while the view is live.
        """
        offset = start_chunk * self.chunk_size
        data_bytes = size_bytes - _INTEGRITY_TOKEN_BYTES
        raw = self.buffer[
            offset + _INTEGRITY_TOKEN_BYTES :
            offset + _INTEGRITY_TOKEN_BYTES + data_bytes
        ]
        return raw.view(dtype).reshape(shape)

    def view_with_verify(
        self,
        start_chunk: int,
        size_bytes: int,
        shape: list[int],
        dtype: torch.dtype,
        expected_token: bytes,
    ) -> torch.Tensor | None:
        """Return a zero-copy view into the buffer after integrity check.

        The view points directly at the buffer memory — no clone.
        Returns None if the integrity token doesn't match (stale data).
        The caller must NOT free the staging allocation while the view is live.
        """
        ok = self.batch_verify_tokens([(start_chunk, expected_token)])
        if not ok[0]:
            return None

        return self.view_after_verify(start_chunk, size_bytes, shape, dtype)

    def view_as(
        self,
        start_chunk: int,
        size_bytes: int,
        shape: list[int],
        dtype: torch.dtype,
    ) -> torch.Tensor:
        """Return a zero-copy view into the buffer (no integrity check).

        The caller must NOT free the staging allocation while the view is live.
        """
        offset = start_chunk * self.chunk_size
        raw = self.buffer[offset : offset + size_bytes]
        return raw.view(dtype).reshape(shape)

    # -- NIXL helpers --------------------------------------------------------

    def get_nixl_blocks_data(self) -> list[tuple[int, int, int]]:
        """(addr, length, device_id) per chunk — fed to get_xfer_descs."""
        return [
            (self.base_addr + i * self.chunk_size, self.chunk_size, self.device_id)
            for i in range(self.num_chunks)
        ]


# ---------------------------------------------------------------------------
# ZMQ context helper
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _zmq_ctx(socket_type: int, addr: str) -> Iterator[zmq.Socket]:
    ctx: zmq.Context | None = None
    try:
        ctx = zmq.Context()
        yield make_zmq_socket(
            ctx=ctx,
            path=addr,
            socket_type=socket_type,
            bind=socket_type == zmq.ROUTER,
        )
    finally:
        if ctx is not None:
            ctx.destroy(linger=0)


# ---------------------------------------------------------------------------
# ECNixlConnector
# ---------------------------------------------------------------------------
class ECNixlConnector(ECConnectorBase):
    """NIXL-backed EC connector for GPU-to-GPU encoder embedding transfer."""

    def __init__(self, vllm_config: VllmConfig, role: ECConnectorRole):
        super().__init__(vllm_config=vllm_config, role=role)

        ec_cfg = vllm_config.ec_transfer_config
        assert ec_cfg is not None
        extra = ec_cfg.ec_connector_extra_config

        self._side_channel_host: str = extra.get("side_channel_host", ec_cfg.ec_ip)
        self._side_channel_port: int = int(
            extra.get("side_channel_port", ec_cfg.ec_port)
        )
        self._engine_id: str = ec_cfg.engine_id or str(uuid.uuid4())
        self._buffer_size: int = int(ec_cfg.ec_buffer_size)
        self._chunk_size: int = int(extra.get("chunk_size", _DEFAULT_CHUNK_SIZE))
        self._backends: list[str] = extra.get("backends", ["UCX"])
        self._expiry_s: float = float(extra.get("expiry_seconds", 600))
        self._poll_interval_s: float = (
            float(extra.get("poll_interval_ms", 50)) / 1000.0
        )
        self._max_concurrent_rdma: int = int(
            extra.get("max_concurrent_rdma", 4)
        )
        self._rdma_max_retries: int = int(
            extra.get("rdma_max_retries", 3)
        )
        self._rdma_timeout_s: float = float(
            extra.get("rdma_timeout_seconds", 30)
        )
        self._backpressure_timeout_s: float = float(
            extra.get("backpressure_timeout_seconds", 120)
        )

        if role == ECConnectorRole.SCHEDULER:
            self._init_scheduler()
        elif role == ECConnectorRole.WORKER:
            self._init_worker()

    # ================================================================
    #  Initialisation helpers
    # ================================================================

    def _init_scheduler(self) -> None:
        self._available_slots: dict[str, ECSlotMeta] = {}
        self._available_snapshot: dict[str, ECSlotMeta] = {}
        self._snapshot_valid = False
        self._mm_datas_need_loads: dict[str, int] = {}

        if self.is_consumer:
            self._avail_lock = threading.Lock()
            self._stop_event = threading.Event()
            ready = threading.Event()
            self._poller_t = threading.Thread(
                target=self._availability_poller,
                args=(ready,),
                daemon=True,
                name="ec-nixl-avail-poller",
            )
            self._poller_t.start()
            ready.wait(timeout=10)

    def _init_worker(self) -> None:
        if NixlWrapper is None:
            raise RuntimeError(
                "NIXL is not installed. "
                "ECNixlConnector requires the nixl package."
            )

        ec_cfg = self._vllm_config.ec_transfer_config
        assert ec_cfg is not None
        device = ec_cfg.ec_buffer_device or "cuda"

        # --- staging buffer (allocate first to ensure CUDA context exists) ---
        self._mem_type = "VRAM" if device in ("cuda", "xpu") else "DRAM"
        # Both producer and consumer use ring_mode for O(1) contiguous allocs
        try:
            self._staging = ChunkedStagingBuffer(
                self._buffer_size, device, self._chunk_size,
                ring_mode=True,
            )
        except (RuntimeError, Exception) as e:
            raise RuntimeError(
                f"ECNixlConnector: failed to allocate staging buffer "
                f"({self._buffer_size / 1e6:.0f} MB on {device}). "
                f"Reduce ec_buffer_size or free device memory. "
                f"Original error: {e}"
            ) from e
        logger.info(
            "ECNixlConnector: %d chunks × %d B on %s (%.1f MB total)",
            self._staging.num_chunks,
            self._chunk_size,
            device,
            self._staging.num_chunks * self._chunk_size / 1e6,
        )

        # --- NIXL agent (created after staging buffer so CUDA context exists) ---
        num_threads = ec_cfg.ec_connector_extra_config.get("num_threads", 4)
        cfg = (
            nixl_agent_config(num_threads=num_threads, capture_telemetry=True)
            if nixl_agent_config is not None
            else None
        )
        self._nixl = NixlWrapper(str(uuid.uuid4()), cfg)

        # --- register with NIXL ---
        reg_data = [(
            self._staging.base_addr,
            self._staging.num_chunks * self._chunk_size,
            self._staging.device_id,
            "",
        )]
        self._reg_descs = self._nixl.get_reg_descs(reg_data, self._mem_type)
        self._nixl.register_memory(self._reg_descs, backends=self._backends)

        xfer_descs = self._nixl.get_xfer_descs(
            self._staging.get_nixl_blocks_data(), self._mem_type
        )
        self._local_xfer_handle: int = self._nixl.prep_xfer_dlist(
            "NIXL_INIT_AGENT", xfer_descs
        )

        # --- producer state ---
        if self.is_producer:
            self._slot_meta: dict[str, ECSlotMeta] = {}
            self._slot_expiry: dict[str, float] = {}
            self._ack_counts: dict[str, int] = {}
            self._meta_lock = threading.Lock()
            self._stop_event = threading.Event()
            self._pub_sock: zmq.Socket | None = None

            self._agent_meta = ECNixlAgentMetadata(
                engine_id=self._engine_id,
                agent_metadata=self._nixl.get_agent_metadata(),
                buffer_base_addr=self._staging.base_addr,
                buffer_size_bytes=self._staging.num_chunks * self._chunk_size,
                num_chunks=self._staging.num_chunks,
                chunk_size=self._chunk_size,
                device_id=self._staging.device_id,
            )
            ready = threading.Event()
            self._zmq_t = threading.Thread(
                target=self._zmq_server_loop,
                args=(ready,),
                daemon=True,
                name="ec-nixl-zmq-server",
            )
            self._zmq_t.start()
            ready.wait(timeout=10)

        # --- consumer state ---
        if self.is_consumer:
            self._remote_agent_name: str | None = None
            self._remote_xfer_handle: int | None = None
            self._remote_meta: ECNixlAgentMetadata | None = None
            self._handshake_done = False
            self._handshake_lock = threading.Lock()
            self._zero_copy_keys: set[str] = set()
            self._ack_zmq_ctx: zmq.Context | None = None
            self._ack_sock: zmq.Socket | None = None

    # ================================================================
    #  Cleanup
    # ================================================================

    def close(self) -> None:
        """Deterministic cleanup of NIXL agents, ZMQ sockets, and buffers.

        Safe to call multiple times. Should be called when the connector
        is no longer needed to avoid leaking GPU memory and NIXL state.
        """
        # Signal background threads to stop
        stop = getattr(self, "_stop_event", None)
        if stop is not None:
            stop.set()

        # Wait for background threads
        for attr in ("_zmq_t", "_poller_t"):
            t = getattr(self, attr, None)
            if t is not None and t.is_alive():
                t.join(timeout=5)

        # Close consumer ACK socket
        if getattr(self, "_ack_sock", None) is not None:
            try:
                self._ack_sock.close(linger=0)
            except Exception:
                pass
            self._ack_sock = None
        if getattr(self, "_ack_zmq_ctx", None) is not None:
            try:
                self._ack_zmq_ctx.term()
            except Exception:
                pass
            self._ack_zmq_ctx = None

        # Release NIXL resources
        nixl = getattr(self, "_nixl", None)
        if nixl is not None:
            try:
                reg = getattr(self, "_reg_descs", None)
                if reg is not None:
                    nixl.deregister_memory(reg)
                    self._reg_descs = None
            except Exception:
                logger.debug("ECNixlConnector: error deregistering memory",
                             exc_info=True)
            self._nixl = None

        # Release staging buffer GPU memory
        staging = getattr(self, "_staging", None)
        if staging is not None:
            staging.buffer = None
            self._staging = None

        logger.info("ECNixlConnector: closed")

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ================================================================
    #  Producer: ZMQ metadata server
    # ================================================================

    def _zmq_server_loop(self, ready: threading.Event) -> None:
        path = make_zmq_path(
            "tcp", self._side_channel_host, self._side_channel_port
        )
        pub_path = make_zmq_path(
            "tcp", self._side_channel_host, self._side_channel_port + 1
        )
        logger.info("ECNixlConnector: ZMQ server binding on %s", path)
        logger.info("ECNixlConnector: ZMQ PUB binding on %s", pub_path)

        ctx = zmq.Context()
        pub_sock = None
        try:
            sock = ctx.socket(zmq.ROUTER)
            sock.bind(path)
            sock.setsockopt(zmq.RCVTIMEO, 1000)

            pub_sock = ctx.socket(zmq.PUB)
            pub_sock.bind(pub_path)
            self._pub_sock = pub_sock

            ready.set()
            enc = msgspec.msgpack.Encoder()

            while not self._stop_event.is_set():
                self._expire_slots()
                try:
                    identity, _, msg = sock.recv_multipart()
                except zmq.Again:
                    continue
                try:
                    req = msgspec.msgpack.decode(msg)
                    tag = req[0] if isinstance(req, (list, tuple)) else req

                    if tag == _GET_AGENT_META:
                        resp = enc.encode(self._agent_meta)
                    elif tag == _LIST_AVAILABLE:
                        with self._meta_lock:
                            resp = enc.encode(list(self._slot_meta.values()))
                    elif tag == _ACK_CONSUMED:
                        acked_hashes = req[1] if len(req) > 1 else []
                        recorded = 0
                        stale = 0
                        with self._meta_lock:
                            for h in acked_hashes:
                                if h in self._slot_meta:
                                    self._ack_counts[h] = (
                                        self._ack_counts.get(h, 0) + 1
                                    )
                                    recorded += 1
                                else:
                                    stale += 1
                        with self._meta_lock:
                            n_slots = len(self._slot_meta)
                            n_acked = sum(
                                1 for v in self._ack_counts.values()
                                if v >= 1
                            )
                        if stale:
                            logger.warning(
                                "ECNixlConnector: ACK received for "
                                "%d items (%d recorded, %d stale/"
                                "expired) buffer=%d/%d acked",
                                len(acked_hashes), recorded, stale,
                                n_acked, n_slots,
                            )
                        else:
                            logger.info(
                                "ECNixlConnector: ACK received for "
                                "%d items (all recorded) "
                                "buffer=%d/%d acked",
                                len(acked_hashes), n_acked, n_slots,
                            )
                        resp = enc.encode(True)
                    else:
                        logger.warning(
                            "ECNixlConnector: unexpected msg tag %s", tag
                        )
                        resp = enc.encode(None)

                    sock.send_multipart((identity, b"", resp))
                except Exception:
                    logger.exception("ECNixlConnector ZMQ server error")
        finally:
            if pub_sock is not None:
                pub_sock.close(linger=0)
            try:
                sock.close(linger=0)
            except Exception:
                pass
            ctx.destroy(linger=0)
            self._pub_sock = None

    def _expire_slots(self) -> None:
        now = time.monotonic()
        with self._meta_lock:
            expired = [h for h, t in self._slot_expiry.items() if now > t]
        for mm_hash in expired:
            self._staging.free(mm_hash)
            with self._meta_lock:
                self._slot_meta.pop(mm_hash, None)
                self._slot_expiry.pop(mm_hash, None)
                ack_count = self._ack_counts.pop(mm_hash, 0)
            logger.info(
                "ECNixlConnector: TTL-expired slot %s (ack_count=%d)",
                mm_hash, ack_count,
            )

    # ================================================================
    #  Consumer scheduler: availability listener (push + poll fallback)
    # ================================================================

    def _full_poll_available(self, req_path: str, enc) -> None:
        """Fetch the full availability list from the encoder (fallback)."""
        try:
            with _zmq_ctx(zmq.REQ, req_path) as sock:
                sock.setsockopt(zmq.RCVTIMEO, 2000)
                sock.setsockopt(zmq.SNDTIMEO, 2000)
                sock.setsockopt(zmq.LINGER, 0)
                sock.send(enc.encode((_LIST_AVAILABLE,)))
                resp = sock.recv()
                slots: list[ECSlotMeta] = msgspec.msgpack.decode(
                    resp, type=list[ECSlotMeta]
                )
                fresh = {s.mm_hash: s for s in slots}
                with self._avail_lock:
                    self._available_slots = fresh
        except Exception as e:
            logger.debug("EC full poll failed: %s", e)

    def _availability_poller(self, ready: threading.Event) -> None:
        """Hybrid listener: ZMQ SUB for instant push notifications from
        the encoder, with a periodic full-poll fallback every 5 seconds
        to catch any missed messages."""
        req_path = make_zmq_path(
            "tcp", self._side_channel_host, self._side_channel_port
        )
        pub_path = make_zmq_path(
            "tcp", self._side_channel_host, self._side_channel_port + 1
        )
        enc = msgspec.msgpack.Encoder()
        _FULL_POLL_INTERVAL = 5.0

        ready.set()
        last_full_poll = 0.0

        while not self._stop_event.is_set():
            ctx: zmq.Context | None = None
            try:
                ctx = zmq.Context()
                sub_sock = ctx.socket(zmq.SUB)
                sub_sock.connect(pub_path)
                sub_sock.subscribe(b"")
                sub_sock.setsockopt(zmq.RCVTIMEO, 200)

                logger.info(
                    "ECNixlConnector: availability listener connected "
                    "to PUB %s (poll fallback every %.0fs)",
                    pub_path, _FULL_POLL_INTERVAL,
                )

                while not self._stop_event.is_set():
                    # 1) Drain all pending push notifications
                    got_any = False
                    while True:
                        try:
                            msg = sub_sock.recv(zmq.NOBLOCK)
                            req = msgspec.msgpack.decode(msg)
                            tag = req[0] if isinstance(
                                req, (list, tuple)) else req
                            if tag == _NOTIFY_AVAILABLE:
                                slot = msgspec.msgpack.decode(
                                    msgspec.msgpack.encode(req[1]),
                                    type=ECSlotMeta,
                                )
                                with self._avail_lock:
                                    self._available_slots[
                                        slot.mm_hash] = slot
                                got_any = True
                        except zmq.Again:
                            break

                    # 2) Periodic full-poll fallback
                    now = time.monotonic()
                    if now - last_full_poll > _FULL_POLL_INTERVAL:
                        self._full_poll_available(req_path, enc)
                        last_full_poll = now

                    if not got_any:
                        time.sleep(self._poll_interval_s)

            except Exception as e:
                logger.debug(
                    "EC availability listener: %s — reconnecting in 1s", e
                )
            finally:
                if ctx is not None:
                    ctx.destroy(linger=0)
            time.sleep(1.0)

    # ================================================================
    #  Consumer worker: NIXL handshake
    # ================================================================

    def _ensure_handshake(self) -> None:
        if self._handshake_done:
            return
        with self._handshake_lock:
            if self._handshake_done:
                return
            self._do_handshake()

    def _do_handshake(self) -> None:
        path = make_zmq_path(
            "tcp", self._side_channel_host, self._side_channel_port
        )
        logger.info("ECNixlConnector: handshake with producer at %s …", path)

        enc = msgspec.msgpack.Encoder()
        with _zmq_ctx(zmq.REQ, path) as sock:
            sock.setsockopt(zmq.RCVTIMEO, 30_000)
            sock.send(enc.encode((_GET_AGENT_META,)))
            resp = sock.recv()

        remote = msgspec.msgpack.decode(resp, type=ECNixlAgentMetadata)
        self._remote_meta = remote

        if self._mem_type == "VRAM":
            current_platform.set_device(self._staging.device_id)

        self._remote_agent_name = self._nixl.add_remote_agent(
            remote.agent_metadata
        )

        remote_blocks = [
            (
                remote.buffer_base_addr + i * remote.chunk_size,
                remote.chunk_size,
                remote.device_id,
            )
            for i in range(remote.num_chunks)
        ]
        remote_descs = self._nixl.get_xfer_descs(remote_blocks, self._mem_type)
        self._remote_xfer_handle = self._nixl.prep_xfer_dlist(
            self._remote_agent_name, remote_descs
        )

        self._handshake_done = True
        logger.info(
            "ECNixlConnector: handshake complete — engine %s, %d remote chunks",
            remote.engine_id,
            remote.num_chunks,
        )

    # ================================================================
    #  Worker-side interface
    # ================================================================

    def save_caches(
        self, encoder_cache: dict[str, torch.Tensor], mm_hash: str, **kwargs
    ) -> None:
        if not self.is_producer:
            return

        with self._meta_lock:
            if mm_hash in self._slot_meta:
                self._slot_expiry[mm_hash] = (
                    time.monotonic() + self._expiry_s
                )
                return

        t0 = time.monotonic()
        tensor = encoder_cache[mm_hash]
        evict_count = 0
        t_wait_start = time.monotonic()
        while True:
            try:
                slot = self._staging.copy_in(mm_hash, tensor)
                break
            except RuntimeError:
                with self._meta_lock:
                    consumed = {
                        k for k, v in self._ack_counts.items()
                        if v >= 1
                    }
                evicted, was_consumed = self._staging.evict_oldest(consumed)
                if evicted is not None:
                    evict_count += 1
                    with self._meta_lock:
                        self._slot_meta.pop(evicted, None)
                        self._slot_expiry.pop(evicted, None)
                        self._ack_counts.pop(evicted, None)
                    logger.info(
                        "ECNixlConnector: evicted acked slot %s",
                        evicted,
                    )
                    continue
                elapsed = time.monotonic() - t_wait_start
                if (self._backpressure_timeout_s > 0
                        and elapsed > self._backpressure_timeout_s):
                    raise RuntimeError(
                        f"ECNixlConnector: save_caches backpressure "
                        f"timeout after {elapsed:.0f}s for {mm_hash}. "
                        f"Buffer has {len(self._staging._allocs)} "
                        f"slots, none consumed. Consumer may be "
                        f"dead or too slow. Increase "
                        f"backpressure_timeout_seconds or "
                        f"ec_buffer_size."
                    )
                if elapsed > 0 and int(elapsed) % 10 == 0 and elapsed % 10 < 0.05:
                    oldest_key = next(
                        iter(self._staging._allocs), None,
                    )
                    with self._meta_lock:
                        n_acked = len(consumed)
                        oldest_ack = (
                            self._ack_counts.get(oldest_key, 0)
                            if oldest_key else -1
                        )
                    logger.warning(
                        "ECNixlConnector: buffer full, no consumed "
                        "slots to evict — oldest=%.12s (ack=%d) "
                        "(%.0fs/%.0fs elapsed, %d/%d slots acked)",
                        oldest_key, oldest_ack,
                        elapsed, self._backpressure_timeout_s,
                        n_acked,
                        len(self._staging._allocs),
                    )
                time.sleep(0.01)
        self._staging.sync_copy()
        torch.cuda.current_stream().synchronize()
        copy_ms = (time.monotonic() - t0) * 1e3

        with self._meta_lock:
            self._slot_meta[mm_hash] = slot
            self._slot_expiry[mm_hash] = time.monotonic() + self._expiry_s

        # Push notification to consumers (non-blocking, best-effort)
        if self._pub_sock is not None:
            try:
                pub_enc = msgspec.msgpack.Encoder()
                self._pub_sock.send(
                    pub_enc.encode((_NOTIFY_AVAILABLE, slot)),
                    zmq.NOBLOCK,
                )
            except Exception:
                pass

        total_ms = (time.monotonic() - t0) * 1e3
        used_chunks = sum(
            len(c) for c in self._staging._allocs.values()
        )
        with self._meta_lock:
            n_acked = sum(1 for v in self._ack_counts.values() if v >= 1)
        logger.info(
            "ECNixlConnector: save_caches %s — %d B in %.1f ms "
            "(copy=%.1f evict=%d) chunks=%s "
            "fill=%d/%d chunks (%d%%) slots=%d acked=%d",
            mm_hash, slot.size_bytes, total_ms,
            copy_ms, evict_count, slot.chunk_ids[:3],
            used_chunks, self._staging.num_chunks,
            int(used_chunks * 100 / self._staging.num_chunks),
            len(self._staging._allocs), n_acked,
        )

    def start_load_caches(
        self, encoder_cache: dict[str, torch.Tensor], **kwargs
    ) -> None:
        if not self.is_consumer:
            return

        metadata = self._get_connector_metadata()
        assert isinstance(metadata, ECNixlConnectorMetadata)

        if not metadata.items_to_load:
            return

        self._ensure_handshake()

        already_cached = [i for i in metadata.items_to_load
                          if i.mm_hash in encoder_cache]
        items = [i for i in metadata.items_to_load
                 if i.mm_hash not in encoder_cache]
        logger.info(
            "ECTrace load_caches: %d items requested, %d already cached, "
            "%d to RDMA, cache_keys=%d, "
            "staging(cursor=%d/%d allocs=%d zero_copy=%d) "
            "hashes=%s chunks=%s",
            len(metadata.items_to_load), len(already_cached),
            len(items), len(encoder_cache),
            self._staging._write_cursor, self._staging.num_chunks,
            len(self._staging._allocs),
            len(self._zero_copy_keys),
            [i.mm_hash[:12] for i in items[:5]],
            [i.chunk_ids[0] for i in items[:5]],
        )
        if not items:
            return

        t_batch = time.monotonic()
        max_conc = self._max_concurrent_rdma or len(items)
        max_retries = self._rdma_max_retries

        loaded = 0
        total_bytes = 0
        corrupted = 0
        rdma_polls = 0
        retries_used = 0
        acked_hashes: list[str] = []
        poll_intervals: list[float] = []
        total_issue_ms = 0.0
        total_poll_ms = 0.0
        total_sync_ms = 0.0
        # Defer staging buffer frees until after _verify_stream.synchronize()
        # at the end of the function. The clone() that produces encoder_cache
        # tensors is queued on _verify_stream; freeing the slot before the
        # clone completes would let the next allocate()/RDMA-write race with
        # the in-flight clone read on _verify_stream.
        deferred_frees: list[str] = []

        # Process-as-done: issue RDMA for up to max_conc items, then
        # poll in a tight loop.  As each transfer completes, immediately
        # verify + clone it and issue the next pending item, keeping the
        # NVLink pipe full at all times.
        item_queue = list(range(len(items)))  # indices into `items`
        inflight: dict[int, tuple[ECSlotMeta, int, list[int], int]] = {}
        # inflight: item_idx -> (slot_meta, local_start, local_ids, handle)

        def _issue_one(item_idx: int) -> bool:
            """Allocate staging + issue RDMA for one item. Returns False
            if staging is full."""
            item = items[item_idx]
            try:
                local_ids, _ = self._staging.allocate(
                    item.mm_hash, item.size_bytes
                )
            except RuntimeError:
                logger.warning(
                    "ECNixlConnector: consumer staging full, "
                    "skipping %s", item.mm_hash,
                )
                return False
            local_id_arr = np.array(local_ids, dtype=np.int64)
            remote_id_arr = np.array(item.chunk_ids, dtype=np.int64)
            assert self._remote_xfer_handle is not None
            handle = self._nixl.make_prepped_xfer(
                "READ",
                self._local_xfer_handle,
                local_id_arr,
                self._remote_xfer_handle,
                remote_id_arr,
            )
            self._nixl.transfer(handle)
            inflight[item_idx] = (item, local_ids[0], local_ids, handle)
            return True

        def _ingest_completed(item_idx: int) -> None:
            """Verify + clone a completed transfer into encoder_cache."""
            nonlocal loaded, total_bytes
            item, local_start, local_ids, _ = inflight.pop(item_idx)
            dtype = getattr(torch, item.dtype_str.replace("torch.", ""))
            clone_ctx = (
                torch.cuda.stream(self._staging._verify_stream)
                if self._staging._verify_stream is not None
                else contextlib.nullcontext()
            )
            with clone_ctx:
                if item.integrity_token:
                    ok = self._staging.batch_verify_tokens(
                        [(local_start, item.integrity_token)]
                    )
                    if not ok[0]:
                        return  # will be retried or marked corrupted
                    tensor = self._staging.view_after_verify(
                        local_start, item.size_bytes, item.shape, dtype,
                    )
                else:
                    data_bytes = item.size_bytes - _INTEGRITY_TOKEN_BYTES
                    tok_off = (
                        local_start * self._staging.chunk_size
                        + _INTEGRITY_TOKEN_BYTES
                    )
                    raw = self._staging.buffer[tok_off:tok_off + data_bytes]
                    tensor = raw.view(dtype).reshape(item.shape)
                encoder_cache[item.mm_hash] = tensor.clone()
            deferred_frees.append(item.mm_hash)
            acked_hashes.append(item.mm_hash)
            loaded += 1
            total_bytes += item.size_bytes

        # Seed the pipeline with up to max_conc items
        t_issue = time.monotonic()
        while item_queue and len(inflight) < max_conc:
            idx = item_queue.pop(0)
            _issue_one(idx)
        total_issue_ms += (time.monotonic() - t_issue) * 1e3

        # Poll loop: process completions immediately + refill pipeline
        t_poll_start = time.monotonic()
        poll_deadline = t_poll_start + self._rdma_timeout_s
        spin_count = 0
        _SPIN_BEFORE_SLEEP = 64

        while inflight:
            now = time.monotonic()
            if now > poll_deadline:
                for idx, (item, _, _, handle) in list(inflight.items()):
                    self._nixl.release_xfer_handle(handle)
                    logger.error(
                        "ECNixlConnector: RDMA timed out after "
                        "%.1fs for %s",
                        self._rdma_timeout_s, item.mm_hash,
                    )
                    corrupted += 1
                    self._staging.free(item.mm_hash)
                    acked_hashes.append(item.mm_hash)
                inflight.clear()
                break

            completed = []
            failed = []
            for idx, (item, local_start, local_ids, handle) in \
                    inflight.items():
                state = self._nixl.check_xfer_state(handle)
                rdma_polls += 1
                if state == "DONE":
                    self._nixl.release_xfer_handle(handle)
                    completed.append(idx)
                elif state != "PROC":
                    self._nixl.release_xfer_handle(handle)
                    failed.append(idx)
                    logger.error(
                        "ECNixlConnector: RDMA failed %s: %s",
                        item.mm_hash, state,
                    )

            # Sync verify stream before ingesting completed transfers
            if completed and self._staging._verify_stream is not None:
                rdma_event = torch.cuda.Event()
                rdma_event.record()
                self._staging._verify_stream.wait_event(rdma_event)

            for idx in completed:
                poll_intervals.append(
                    (time.monotonic() - t_poll_start) * 1e3)
                _ingest_completed(idx)

                # Refill pipeline: issue next item immediately
                if item_queue:
                    t_iss = time.monotonic()
                    next_idx = item_queue.pop(0)
                    _issue_one(next_idx)
                    total_issue_ms += (time.monotonic() - t_iss) * 1e3
                    poll_deadline = time.monotonic() + self._rdma_timeout_s

            for idx in failed:
                item, _, _, _ = inflight.pop(idx)
                corrupted += 1
                self._staging.free(item.mm_hash)
                acked_hashes.append(item.mm_hash)

            if not completed and not failed and inflight:
                spin_count += 1
                if spin_count > _SPIN_BEFORE_SLEEP:
                    time.sleep(0.00002)  # 20μs — much lighter than 100μs
                    spin_count = 0

        total_poll_ms += (time.monotonic() - t_poll_start) * 1e3

        if self._staging._verify_stream is not None:
            self._staging._verify_stream.synchronize()

        # Now safe to free staging slots — all clones on _verify_stream
        # have completed, so no in-flight reads can race with reallocation.
        for mm_hash in deferred_frees:
            self._staging.free(mm_hash)

        rdma_ms = (time.monotonic() - t_batch) * 1e3

        t_ack = time.monotonic()
        if acked_hashes:
            self._send_ack(acked_hashes)
        ack_ms = (time.monotonic() - t_ack) * 1e3

        batch_ms = (time.monotonic() - t_batch) * 1e3

        # Poll interval statistics
        poll_p50 = poll_p99 = poll_max = poll_mean = 0.0
        if poll_intervals:
            pi_sorted = sorted(poll_intervals)
            poll_mean = sum(pi_sorted) / len(pi_sorted)
            poll_p50 = pi_sorted[len(pi_sorted) // 2]
            poll_p99 = pi_sorted[min(
                int(len(pi_sorted) * 0.99), len(pi_sorted) - 1
            )]
            poll_max = pi_sorted[-1]

        if loaded or corrupted:
            logger.info(
                "ECNixlConnector: start_load_caches — "
                "%d loaded, %d corrupted, %d retries, %.2f MB, %.1f ms "
                "(issue=%.1f poll=%.1f sync=%.1f ack=%.1f) "
                "[%d polls, interval_ms: mean=%.2f p50=%.2f p99=%.2f "
                "max=%.2f] conc=%d",
                loaded, corrupted, retries_used,
                total_bytes / (1024 * 1024), batch_ms,
                total_issue_ms, total_poll_ms, total_sync_ms, ack_ms,
                rdma_polls, poll_mean, poll_p50, poll_p99,
                poll_max, max_conc,
            )

    def _refetch_slot_meta(
        self, mm_hashes: list[str],
    ) -> dict[str, ECSlotMeta]:
        """Re-fetch current slot metadata from the producer for specific hashes.

        Used as a fallback when integrity checks fail after all retries —
        the slot may have been evicted and re-allocated with new chunk_ids.
        """
        try:
            sock = self._ensure_ack_socket()
            enc = msgspec.msgpack.Encoder()
            sock.send(enc.encode((_LIST_AVAILABLE,)))
            resp = sock.recv()
            all_slots: list[ECSlotMeta] = msgspec.msgpack.decode(
                resp, type=list[ECSlotMeta]
            )
            wanted = set(mm_hashes)
            return {s.mm_hash: s for s in all_slots if s.mm_hash in wanted}
        except Exception:
            logger.warning(
                "ECNixlConnector: failed to refetch metadata for %d hashes",
                len(mm_hashes),
            )
            return {}

    def _ensure_ack_socket(self) -> zmq.Socket:
        """Lazily create a persistent ZMQ REQ socket for ACKs."""
        if self._ack_sock is None:
            path = make_zmq_path(
                "tcp", self._side_channel_host, self._side_channel_port
            )
            self._ack_zmq_ctx = zmq.Context()
            self._ack_sock = make_zmq_socket(
                ctx=self._ack_zmq_ctx,
                path=path,
                socket_type=zmq.REQ,
                bind=False,
            )
            self._ack_sock.setsockopt(zmq.RCVTIMEO, 5000)
            self._ack_sock.setsockopt(zmq.SNDTIMEO, 5000)
            self._ack_sock.setsockopt(zmq.LINGER, 1000)
            logger.info("ECNixlConnector: persistent ACK socket connected to %s", path)
        return self._ack_sock

    def _send_ack(self, mm_hashes: list[str]) -> None:
        """Notify the producer that these embeddings have been consumed."""
        try:
            sock = self._ensure_ack_socket()
            enc = msgspec.msgpack.Encoder()
            sock.send(enc.encode((_ACK_CONSUMED, mm_hashes)))
            sock.recv()
            logger.info(
                "ECNixlConnector: ACK sent for %d items: %s",
                len(mm_hashes),
                [h[:12] for h in mm_hashes[:5]],
            )
        except Exception:
            logger.warning(
                "ECNixlConnector: failed to send ACK for %d items, "
                "resetting socket (producer may evict unconsumed data)",
                len(mm_hashes),
            )
            if self._ack_sock is not None:
                self._ack_sock.close(linger=0)
                self._ack_sock = None
            if self._ack_zmq_ctx is not None:
                self._ack_zmq_ctx.term()
                self._ack_zmq_ctx = None

    def free_consumer_slots(self, mm_hashes: list[str]) -> None:
        """Release staging buffer allocations backing zero-copy views."""
        if not self.is_consumer:
            return
        for h in mm_hashes:
            if h in self._zero_copy_keys:
                self._staging.free(h)
                self._zero_copy_keys.discard(h)

    # ================================================================
    #  Scheduler-side interface
    # ================================================================

    def _ensure_snapshot(self) -> None:
        """Take a point-in-time snapshot of available slots for this step."""
        if not self._snapshot_valid:
            with self._avail_lock:
                self._available_snapshot = dict(self._available_slots)
            self._snapshot_valid = True

    def has_cache_item(self, identifier: str) -> bool:
        if not self.is_consumer:
            return False
        self._ensure_snapshot()
        found = identifier in self._available_snapshot
        if found:
            logger.debug(
                "ECTrace has_cache_item: %s FOUND (snapshot_size=%d)",
                identifier[:16], len(self._available_snapshot),
            )
        return found

    def update_state_after_alloc(self, request: "Request", index: int) -> None:
        if not self.is_consumer:
            return
        mm_hash = request.mm_features[index].identifier
        if not self.has_cache_item(mm_hash):
            return
        num_tokens = request.get_num_encoder_embeds(index)
        self._mm_datas_need_loads[mm_hash] = num_tokens

    def build_connector_meta(
        self, scheduler_output: SchedulerOutput
    ) -> ECConnectorMetadata:
        meta = ECNixlConnectorMetadata()
        if self.is_consumer:
            for mm_hash in self._mm_datas_need_loads:
                slot = self._available_snapshot.get(mm_hash)
                if slot is not None:
                    meta.items_to_load.append(slot)
                else:
                    logger.warning(
                        "ECNixlConnector: hash %s was available at "
                        "scheduling time but missing at metadata build "
                        "(should not happen with snapshot)",
                        mm_hash,
                    )
        if meta.items_to_load:
            logger.info(
                "ECNixlConnector: build_connector_meta — %d items, "
                "chunks=%s",
                len(meta.items_to_load),
                [s.chunk_ids[0] for s in meta.items_to_load[:5]],
            )
        self._mm_datas_need_loads.clear()
        self._snapshot_valid = False
        return meta

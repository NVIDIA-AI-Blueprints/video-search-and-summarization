#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""
ECCudaIPCConnector — same-node GPU-to-GPU encoder cache transfer via
torch's CUDA IPC primitives.

This connector bypasses NIXL/UCX entirely. It uses torch's battle-tested
CUDA tensor sharing (`storage._share_cuda_()` + `rebuild_cuda_tensor`)
to expose the producer's staging buffer to the consumer as a mapped
tensor. Transfers happen via NVLink-accelerated peer copies.

Why torch's IPC (not raw cudaIpcGetMemHandle):
  - The NVIDIA Open Kernel Module rejects legacy `cudaIpcOpenMemHandle`
    with "invalid argument" (error 1). Torch's IPC path uses additional
    machinery (ref counters via POSIX shm, IPC events) that work correctly
    with the Open driver. Empirically: `python -c "torch.mp test"` works,
    raw `cudaIpc*` ctypes calls don't.
  - Torch handles ref counting, event sync, and cleanup automatically.

Performance: same-node GPU→GPU transfers achieve ~370 GB/s (confirmed
via `torch.Tensor.copy_` between peer GPUs on NV18 NVLink). Compare to
NIXL+UCX which tops out at ~0.5 GB/s on the same hardware.

Architecture (mirrors ECNixlConnector):

  Producer (Encoder instance):
    - Worker: pre-allocates a torch.Tensor staging buffer, shares it
      via `_share_cuda_()`, and publishes slot metadata via ZMQ.
    - Scheduler: no-op (has_cache_item returns False).

  Consumer (PD instance):
    - Scheduler: background poller queries the producer's ZMQ server.
    - Worker: on first load, pickles the producer's shared-storage tuple
      and rebuilds a mapped tensor that views the producer's buffer.
      Subsequent loads are slices + clones — each clone() triggers a
      peer-to-peer NVLink copy.

Differences from ECNixlConnector:

  - No UCX / no NIXL / no InfiniBand / no nvidia_peermem.
  - Transfers are simple `remote_tensor[off:off+size].clone()` calls
    that torch issues as `cudaMemcpyPeerAsync` under the hood.
  - Same-node only. Cross-node must use ECNixlConnector.

Required `ec_connector_extra_config`:
  - `side_channel_host` / `side_channel_port`: ZMQ endpoint.
  - `producer_device_id`: producer's GPU ID (default 0).
  - `chunk_size`: staging chunk size (default 256 KB).
  - `expiry_seconds`: slot TTL (default 600).
  - `backpressure_timeout_seconds`: producer back-pressure deadline.
"""

import contextlib
import pickle
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import msgspec
import torch
import zmq

from vllm.config import VllmConfig
from vllm.distributed.ec_transfer.ec_connector.base import (
    ECConnectorBase,
    ECConnectorMetadata,
    ECConnectorRole,
)
from vllm.distributed.ec_transfer.ec_connector.nixl_connector import (
    _INTEGRITY_TOKEN_BYTES,
    ChunkedStagingBuffer,
)
from vllm.logger import init_logger
from vllm.utils.network_utils import make_zmq_path, make_zmq_socket
from vllm.v1.core.sched.output import SchedulerOutput

if TYPE_CHECKING:
    from vllm.v1.request import Request

logger = init_logger(__name__)


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------
_GET_AGENT_META = b"ipc_get_meta"
_LIST_AVAILABLE = b"ipc_list_avail"
_ACK_CONSUMED = b"ipc_ack"
_NOTIFY_AVAILABLE = b"ipc_notify_avail"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ECCudaIPCSubItem:
    """One sub-tensor packed inside a slot.

    EVS pruned encoder cache is a dict like
    ``{"embeddings": tensor, "positions": tensor}`` — both sub-tensors
    share one staging slot, separated by ``offset_within_payload_bytes``.
    For plain tensor caches there is a single sub-item with ``key="ec_cache"``.
    """

    key: str = ""
    offset_within_payload_bytes: int = 0
    size_bytes: int = 0
    shape: list[int] = field(default_factory=list)
    dtype_str: str = ""


@dataclass
class ECCudaIPCSlotMeta:
    """Metadata for a single embedding slot.

    Unlike NIXL's chunked slots, CUDA IPC gives us a mapped view over
    the producer's entire buffer. We just need byte offset + size.

    The slot layout is ``[integrity_token | payload]`` where payload
    holds one or more sub-tensors back-to-back, described by ``items``.
    """

    mm_hash: str
    offset_bytes: int = 0
    size_bytes: int = 0
    items: list[ECCudaIPCSubItem] = field(default_factory=list)
    integrity_token: bytes = b""
    producer_id: str = ""


@dataclass
class RemoteProducer:
    """Per-producer connection state on the consumer side."""

    remote_tensor: torch.Tensor
    device_id: int
    port: int
    ack_sock: "zmq.Socket | None" = None
    ack_zmq_ctx: "zmq.Context | None" = None


@dataclass
class ECCudaIPCConnectorMetadata(ECConnectorMetadata):
    items_to_load: list[ECCudaIPCSlotMeta]

    def __init__(self):
        self.items_to_load = []


# ---------------------------------------------------------------------------
# ECCudaIPCConnector
# ---------------------------------------------------------------------------
class ECCudaIPCConnector(ECConnectorBase):
    """CUDA-IPC-backed EC connector for same-node NVLink transfers."""

    def __init__(self, vllm_config: VllmConfig, role: ECConnectorRole):
        super().__init__(vllm_config=vllm_config, role=role)

        ec_cfg = vllm_config.ec_transfer_config
        assert ec_cfg is not None
        extra = ec_cfg.ec_connector_extra_config

        self._side_channel_host: str = extra.get(
            "side_channel_host",
            ec_cfg.ec_ip,
        )
        self._side_channel_port: int = int(
            extra.get("side_channel_port", ec_cfg.ec_port)
        )
        self._engine_id: str = ec_cfg.engine_id or str(uuid.uuid4())
        self._buffer_size: int = int(ec_cfg.ec_buffer_size)
        self._chunk_size: int = int(extra.get("chunk_size", 256 * 1024))
        self._expiry_s: float = float(extra.get("expiry_seconds", 600))
        self._poll_interval_s: float = float(extra.get("poll_interval_ms", 50)) / 1000.0
        self._backpressure_timeout_s: float = float(
            extra.get("backpressure_timeout_seconds", 120)
        )

        # Multi-producer: resolve the list of producer ports the consumer
        # should connect to. Falls back to single ec_port for 1P1D compat.
        if getattr(ec_cfg, "ec_producer_ports", None):
            self._producer_ports: list[int] = list(ec_cfg.ec_producer_ports)
        else:
            self._producer_ports = [self._side_channel_port]

        if role == ECConnectorRole.SCHEDULER:
            self._init_scheduler()
        elif role == ECConnectorRole.WORKER:
            self._init_worker()

    # ================================================================
    #  Initialisation
    # ================================================================

    def _init_scheduler(self) -> None:
        self._available_slots: dict[str, ECCudaIPCSlotMeta] = {}
        self._available_snapshot: dict[str, ECCudaIPCSlotMeta] = {}
        self._snapshot_valid = False
        self._mm_datas_need_loads: dict[str, int] = {}

        if self.is_consumer:
            self._avail_lock = threading.Lock()
            self._stop_event = threading.Event()
            self._poller_threads: list[threading.Thread] = []
            for idx, port in enumerate(self._producer_ports):
                ready = threading.Event()
                t = threading.Thread(
                    target=self._availability_poller,
                    args=(ready, port),
                    daemon=True,
                    name=f"ec-cudaipc-avail-poller-{idx}",
                )
                t.start()
                ready.wait(timeout=10)
                self._poller_threads.append(t)
            logger.info(
                "ECCudaIPCConnector: started %d availability pollers "
                "for ports %s",
                len(self._poller_threads),
                self._producer_ports,
            )

    def _init_worker(self) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError("ECCudaIPCConnector requires CUDA.")

        ec_cfg = self._vllm_config.ec_transfer_config
        assert ec_cfg is not None
        device_str = ec_cfg.ec_buffer_device or "cuda"
        if not device_str.startswith("cuda"):
            raise RuntimeError(
                f"ECCudaIPCConnector needs CUDA device, got '{device_str}'"
            )
        if ":" in device_str:
            self._device_id = int(device_str.split(":")[-1])
        else:
            self._device_id = torch.cuda.current_device()

        # --- staging buffer ---
        try:
            self._staging = ChunkedStagingBuffer(
                self._buffer_size,
                f"cuda:{self._device_id}",
                self._chunk_size,
                ring_mode=True,
            )
        except Exception as e:
            raise RuntimeError(
                f"ECCudaIPCConnector: failed to allocate staging buffer "
                f"({self._buffer_size / 1e6:.0f} MB on cuda:"
                f"{self._device_id}): {e}"
            ) from e
        logger.info(
            "ECCudaIPCConnector: %d chunks × %d B on cuda:%d (%.1f MB)",
            self._staging.num_chunks,
            self._chunk_size,
            self._device_id,
            self._staging.num_chunks * self._chunk_size / 1e6,
        )

        # --- producer state ---
        if self.is_producer:
            self._slot_meta: OrderedDict[str, ECCudaIPCSlotMeta] = OrderedDict()
            self._slot_expiry: dict[str, float] = {}
            self._ack_counts: dict[str, int] = {}
            self._meta_lock = threading.Lock()
            self._stop_event = threading.Event()
            self._pub_sock: zmq.Socket | None = None
            # mm_hashes the API server has discarded (idle / pre-spike).
            # Populated by free_caches() (may run from any thread),
            # drained by save_caches() backpressure loop to avoid
            # deadlock: the EngineCore main loop is blocked on
            # future.result() while save_caches spins, so scheduler-
            # routed frees can't arrive until the step finishes.
            self._pending_free: set[str] = set()
            self._pending_free_lock = threading.Lock()

            # Export the staging buffer via torch CUDA IPC
            storage = self._staging.buffer.untyped_storage()
            self._share_cuda_tuple = storage._share_cuda_()
            logger.info(
                "ECCudaIPCConnector: exported staging buffer via "
                "torch CUDA IPC (handle len=%d)",
                len(self._share_cuda_tuple[1]) if self._share_cuda_tuple[1] else 0,
            )

            ready = threading.Event()
            self._zmq_t = threading.Thread(
                target=self._zmq_server_loop,
                args=(ready,),
                daemon=True,
                name="ec-cudaipc-zmq-server",
            )
            self._zmq_t.start()
            ready.wait(timeout=10)

        # --- consumer state ---
        if self.is_consumer:
            self._producers: dict[str, RemoteProducer] = {}
            self._producers_lock = threading.Lock()
            self._copy_stream: torch.cuda.Stream | None = None

    # ================================================================
    #  Cleanup
    # ================================================================

    def close(self) -> None:
        stop = getattr(self, "_stop_event", None)
        if stop is not None:
            stop.set()

        zmq_t = getattr(self, "_zmq_t", None)
        if zmq_t is not None and zmq_t.is_alive():
            zmq_t.join(timeout=5)

        for t in getattr(self, "_poller_threads", []):
            if t.is_alive():
                t.join(timeout=5)

        for prod in getattr(self, "_producers", {}).values():
            prod.remote_tensor = None  # type: ignore[assignment]
            if prod.ack_sock is not None:
                with contextlib.suppress(Exception):
                    prod.ack_sock.close(linger=0)
            if prod.ack_zmq_ctx is not None:
                with contextlib.suppress(Exception):
                    prod.ack_zmq_ctx.term()

        staging = getattr(self, "_staging", None)
        if staging is not None:
            staging.buffer = None
            self._staging = None

        logger.info("ECCudaIPCConnector: closed")

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()

    # ================================================================
    #  Producer: ZMQ server
    # ================================================================

    def _zmq_server_loop(self, ready: threading.Event) -> None:
        # Producer binds 0.0.0.0 (not ec_ip) so docker port-forwarding can
        # deliver host traffic into the container. The consumer still dials
        # the configured ec_ip on its side. Same-host enforcement happens
        # via the POSIX-shm rebuild below, not the bind address.
        bind_host = "0.0.0.0"
        path = make_zmq_path("tcp", bind_host, self._side_channel_port)
        pub_path = make_zmq_path(
            "tcp", bind_host, self._side_channel_port + 1
        )
        logger.info(
            "ECCudaIPCConnector: ZMQ ROUTER on %s, PUB on %s",
            path,
            pub_path,
        )

        ctx = zmq.Context()
        pub_sock = None
        sock = None
        try:
            sock = ctx.socket(zmq.ROUTER)
            sock.bind(path)
            sock.setsockopt(zmq.RCVTIMEO, 1000)

            pub_sock = ctx.socket(zmq.PUB)
            pub_sock.bind(pub_path)
            self._pub_sock = pub_sock

            ready.set()
            enc = msgspec.msgpack.Encoder()

            # Pre-pickle the share_cuda tuple + device_id for fast handshake.
            # The hostname lets the consumer detect cross-node misuse early —
            # CUDA IPC handles only resolve within the same kernel, so a
            # different-host consumer would corrupt silently without this check.
            import socket as _socket

            handshake_payload = pickle.dumps(
                {
                    "share_cuda_tuple": self._share_cuda_tuple,
                    "buffer_size_bytes": self._staging.num_chunks * self._chunk_size,
                    "device_id": self._device_id,
                    "engine_id": self._engine_id,
                    "hostname": _socket.gethostname(),
                }
            )

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
                        # Handshake payload is pickled (torch IPC tuple
                        # contains CUDA handle bytes that don't round-trip
                        # through msgpack cleanly).
                        resp = handshake_payload
                    elif tag == _LIST_AVAILABLE:
                        with self._meta_lock:
                            resp = enc.encode(list(self._slot_meta.values()))
                    elif tag == _ACK_CONSUMED:
                        acked_hashes = req[1] if len(req) > 1 else []
                        with self._meta_lock:
                            for h in acked_hashes:
                                if h in self._slot_meta:
                                    self._slot_meta.pop(h, None)
                                    self._slot_expiry.pop(h, None)
                                    self._ack_counts.pop(h, 0)
                        for h in acked_hashes:
                            self._staging.free(h)
                        resp = enc.encode(True)
                    else:
                        logger.warning(
                            "ECCudaIPCConnector: unexpected tag %s",
                            tag,
                        )
                        resp = enc.encode(None)

                    sock.send_multipart((identity, b"", resp))
                except Exception:
                    logger.exception("ECCudaIPCConnector ZMQ error")
        finally:
            if pub_sock is not None:
                pub_sock.close(linger=0)
            if sock is not None:
                with contextlib.suppress(Exception):
                    sock.close(linger=0)
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
                self._ack_counts.pop(mm_hash, 0)

    # ================================================================
    #  Consumer scheduler: availability listener
    # ================================================================

    def _full_poll_available(
        self, req_path: str, enc, producer_port: int
    ) -> None:
        try:
            ctx = zmq.Context()
            try:
                sock = make_zmq_socket(
                    ctx=ctx,
                    path=req_path,
                    socket_type=zmq.REQ,
                    bind=False,
                )
                sock.setsockopt(zmq.RCVTIMEO, 2000)
                sock.setsockopt(zmq.SNDTIMEO, 2000)
                sock.setsockopt(zmq.LINGER, 0)
                sock.send(enc.encode((_LIST_AVAILABLE,)))
                resp = sock.recv()
                slots = msgspec.msgpack.decode(
                    resp,
                    type=list[ECCudaIPCSlotMeta],
                )
                fresh_hashes = {s.mm_hash for s in slots}
                # Identify the producer_id from the response (all slots
                # from one port share the same producer_id).
                resp_producer_id = (
                    slots[0].producer_id if slots else None
                )
                with self._avail_lock:
                    if resp_producer_id:
                        # Remove stale entries from this specific producer.
                        stale = [
                            k for k, v in self._available_slots.items()
                            if v.producer_id == resp_producer_id
                            and k not in fresh_hashes
                        ]
                        for k in stale:
                            self._available_slots.pop(k, None)
                    for s in slots:
                        self._available_slots[s.mm_hash] = s
            finally:
                ctx.destroy(linger=0)
        except Exception as e:
            logger.debug(
                "ECCudaIPCConnector full poll failed (port %d): %s",
                producer_port, e,
            )

    def _availability_poller(
        self, ready: threading.Event, port: int
    ) -> None:
        req_path = make_zmq_path(
            "tcp",
            self._side_channel_host,
            port,
        )
        pub_path = make_zmq_path(
            "tcp",
            self._side_channel_host,
            port + 1,
        )
        enc = msgspec.msgpack.Encoder()
        _FULL_POLL_INTERVAL = 5.0
        ready.set()
        last_full_poll = 0.0

        while not self._stop_event.is_set():
            ctx: zmq.Context | None = None
            try:
                ctx = zmq.Context()
                sub = ctx.socket(zmq.SUB)
                sub.connect(pub_path)
                sub.subscribe(b"")
                sub.setsockopt(zmq.RCVTIMEO, 200)
                logger.info(
                    "ECCudaIPCConnector: listener connected to %s "
                    "(port %d)",
                    pub_path, port,
                )
                while not self._stop_event.is_set():
                    got_any = False
                    while True:
                        try:
                            msg = sub.recv(zmq.NOBLOCK)
                            req = msgspec.msgpack.decode(msg)
                            tag = req[0] if isinstance(req, (list, tuple)) else req
                            if tag == _NOTIFY_AVAILABLE:
                                slot = msgspec.msgpack.decode(
                                    msgspec.msgpack.encode(req[1]),
                                    type=ECCudaIPCSlotMeta,
                                )
                                with self._avail_lock:
                                    self._available_slots[slot.mm_hash] = slot
                                logger.info(
                                    "ECCudaIPCConnector: NOTIFY_AVAILABLE "
                                    "received for %s from port %d "
                                    "(total_avail=%d)",
                                    slot.mm_hash,
                                    port,
                                    len(self._available_slots),
                                )
                                got_any = True
                        except zmq.Again:
                            break
                    now = time.monotonic()
                    if now - last_full_poll > _FULL_POLL_INTERVAL:
                        self._full_poll_available(req_path, enc, port)
                        last_full_poll = now
                    if not got_any:
                        time.sleep(self._poll_interval_s)
            except Exception as e:
                logger.debug(
                    "ECCudaIPCConnector listener (port %d): %s — retrying",
                    port, e,
                )
            finally:
                if ctx is not None:
                    ctx.destroy(linger=0)
            time.sleep(1.0)

    # ================================================================
    #  Consumer worker: per-producer handshake via torch's IPC rebuild
    # ================================================================

    def _ensure_producer(self, producer_id: str, port: int) -> RemoteProducer:
        """Lazily handshake with a producer on first load."""
        prod = self._producers.get(producer_id)
        if prod is not None:
            return prod
        with self._producers_lock:
            prod = self._producers.get(producer_id)
            if prod is not None:
                return prod
            prod = self._do_handshake(producer_id, port)
            self._producers[producer_id] = prod
            return prod

    def _do_handshake(self, producer_id: str, port: int) -> RemoteProducer:
        path = make_zmq_path(
            "tcp",
            self._side_channel_host,
            port,
        )
        logger.info(
            "ECCudaIPCConnector: handshake with producer %s at %s",
            producer_id, path,
        )

        enc = msgspec.msgpack.Encoder()
        ctx = zmq.Context()
        try:
            sock = make_zmq_socket(
                ctx=ctx,
                path=path,
                socket_type=zmq.REQ,
                bind=False,
            )
            sock.setsockopt(zmq.RCVTIMEO, 30_000)
            sock.send(enc.encode((_GET_AGENT_META,)))
            resp = sock.recv()
        finally:
            ctx.destroy(linger=0)

        payload = pickle.loads(resp)

        import socket as _socket

        producer_host = payload.get("hostname")
        local_host = _socket.gethostname()
        if producer_host and producer_host != local_host:
            logger.warning(
                "ECCudaIPCConnector: hostname differs "
                "(producer=%r consumer=%r). This is OK for Docker "
                "with --ipc=host; will fail loudly at rebuild_cuda_tensor "
                "if processes are actually on different kernels.",
                producer_host,
                local_host,
            )

        share_tuple = payload["share_cuda_tuple"]
        buffer_size = payload["buffer_size_bytes"]
        remote_device_id = payload["device_id"]

        from torch.multiprocessing.reductions import rebuild_cuda_tensor

        (
            storage_device,
            storage_handle,
            storage_size_bytes,
            storage_offset_bytes,
            ref_counter_handle,
            ref_counter_offset,
            event_handle,
            event_sync_required,
        ) = share_tuple

        size_elems = buffer_size
        remote_tensor = rebuild_cuda_tensor(
            tensor_cls=torch.Tensor,
            tensor_size=(size_elems,),
            tensor_stride=(1,),
            tensor_offset=0,
            storage_cls=torch.UntypedStorage,
            dtype=torch.uint8,
            storage_device=storage_device,
            storage_handle=storage_handle,
            storage_size_bytes=storage_size_bytes,
            storage_offset_bytes=storage_offset_bytes,
            requires_grad=False,
            ref_counter_handle=ref_counter_handle,
            ref_counter_offset=ref_counter_offset,
            event_handle=event_handle,
            event_sync_required=event_sync_required,
        )

        if remote_device_id != self._device_id:
            torch.cuda.set_device(self._device_id)
            try:
                can_access = torch.cuda.can_device_access_peer(
                    self._device_id,
                    remote_device_id,
                )
                if can_access:
                    logger.info(
                        "ECCudaIPCConnector: peer access cuda:%d <- cuda:%d",
                        self._device_id,
                        remote_device_id,
                    )
            except Exception:
                pass

        if self._copy_stream is None:
            self._copy_stream = torch.cuda.Stream(
                device=f"cuda:{self._device_id}",
            )

        logger.info(
            "ECCudaIPCConnector: handshake done with producer %s "
            "(port %d) — remote tensor on %s, size=%d MB",
            producer_id, port, remote_tensor.device,
            buffer_size // (1024 * 1024),
        )

        return RemoteProducer(
            remote_tensor=remote_tensor,
            device_id=remote_device_id,
            port=port,
        )

    def _resolve_producer_port(self, producer_id: str) -> int:
        """Find the ZMQ port for a producer_id by querying each port."""
        prod = self._producers.get(producer_id)
        if prod is not None:
            return prod.port
        # Probe each configured port to find which one owns this producer_id.
        enc = msgspec.msgpack.Encoder()
        for port in self._producer_ports:
            # Skip ports already mapped to a different producer_id.
            known = {p.port: pid for pid, p in self._producers.items()}
            if port in known:
                continue
            try:
                ctx = zmq.Context()
                try:
                    path = make_zmq_path(
                        "tcp", self._side_channel_host, port
                    )
                    sock = make_zmq_socket(
                        ctx=ctx, path=path,
                        socket_type=zmq.REQ, bind=False,
                    )
                    sock.setsockopt(zmq.RCVTIMEO, 5000)
                    sock.send(enc.encode((_GET_AGENT_META,)))
                    resp = sock.recv()
                    payload = pickle.loads(resp)
                    if payload.get("engine_id") == producer_id:
                        return port
                finally:
                    ctx.destroy(linger=0)
            except Exception:
                continue
        # Fallback: use the first configured port (1P1D compat).
        return self._producer_ports[0]

    def _ensure_ack_socket(self, producer_id: str) -> zmq.Socket:
        prod = self._producers.get(producer_id)
        if prod is not None and prod.ack_sock is not None:
            return prod.ack_sock
        if prod is None:
            port = self._resolve_producer_port(producer_id)
            prod = self._ensure_producer(producer_id, port)
        path = make_zmq_path(
            "tcp",
            self._side_channel_host,
            prod.port,
        )
        prod.ack_zmq_ctx = zmq.Context()
        prod.ack_sock = make_zmq_socket(
            ctx=prod.ack_zmq_ctx,
            path=path,
            socket_type=zmq.REQ,
            bind=False,
        )
        prod.ack_sock.setsockopt(zmq.RCVTIMEO, 5000)
        prod.ack_sock.setsockopt(zmq.SNDTIMEO, 5000)
        prod.ack_sock.setsockopt(zmq.LINGER, 1000)
        return prod.ack_sock

    def _send_ack(
        self, producer_id: str, mm_hashes: list[str]
    ) -> None:
        try:
            sock = self._ensure_ack_socket(producer_id)
            enc = msgspec.msgpack.Encoder()
            sock.send(enc.encode((_ACK_CONSUMED, mm_hashes)))
            sock.recv()
        except Exception:
            logger.warning(
                "ECCudaIPCConnector: ACK failed (%d items) for "
                "producer %s",
                len(mm_hashes), producer_id,
            )
            prod = self._producers.get(producer_id)
            if prod is not None:
                if prod.ack_sock is not None:
                    prod.ack_sock.close(linger=0)
                    prod.ack_sock = None
                if prod.ack_zmq_ctx is not None:
                    prod.ack_zmq_ctx.term()
                    prod.ack_zmq_ctx = None

    # ================================================================
    #  Pack / unpack helpers (encoder_cache <-> flat staging payload)
    # ================================================================

    @staticmethod
    def _pack_cache(
        ec_cache: "torch.Tensor | dict[str, torch.Tensor]",
    ) -> tuple[torch.Tensor, list[ECCudaIPCSubItem]]:
        """Flatten a (possibly multi-tensor) encoder cache entry.

        Returns a single contiguous uint8 tensor on the cache's device
        plus per-sub-tensor metadata. Plain tensors round-trip under
        the synthetic key ``"ec_cache"`` so the consumer can detect the
        single-tensor shape without ambiguity.
        """
        if isinstance(ec_cache, dict):
            sub_tensors = [
                (k, v) for k, v in ec_cache.items() if isinstance(v, torch.Tensor)
            ]
            if not sub_tensors:
                raise ValueError(
                    "ECCudaIPCConnector: dict encoder_cache entry has no "
                    "tensors to save"
                )
        else:
            sub_tensors = [("ec_cache", ec_cache)]

        # Pad each sub-tensor's start to its element size so the consumer
        # can `view(dtype)` directly without a contiguous-copy. Without this,
        # e.g. float32 (4-byte aligned) followed by int64 (8-byte aligned)
        # would land on an unaligned offset and torch.view() raises.
        items: list[ECCudaIPCSubItem] = []
        offset = 0
        for key, tensor in sub_tensors:
            contig = tensor.contiguous()
            elem_size = contig.element_size()
            if offset % elem_size:
                offset += elem_size - (offset % elem_size)
            n_bytes = contig.numel() * elem_size
            items.append(
                ECCudaIPCSubItem(
                    key=key,
                    offset_within_payload_bytes=offset,
                    size_bytes=n_bytes,
                    shape=list(contig.shape),
                    dtype_str=str(contig.dtype),
                )
            )
            offset += n_bytes

        device = sub_tensors[0][1].device
        flat = torch.empty(offset, dtype=torch.uint8, device=device)
        for (_, tensor), item in zip(sub_tensors, items):
            contig = tensor.contiguous().view(torch.uint8).reshape(-1)
            start = item.offset_within_payload_bytes
            flat[start : start + item.size_bytes].copy_(contig)
        return flat, items

    @staticmethod
    def _unpack_payload(
        payload: torch.Tensor,
        items: list[ECCudaIPCSubItem],
    ) -> "torch.Tensor | dict[str, torch.Tensor]":
        """Reconstruct an encoder_cache entry from the cloned payload.

        ``payload`` is the post-clone uint8 tensor with the integrity
        token already stripped. Returns a single tensor if the slot
        carried one ``"ec_cache"`` sub-item, otherwise a dict mirroring
        the producer's shape.
        """
        sub_views: dict[str, torch.Tensor] = {}
        for item in items:
            dtype_name = item.dtype_str.removeprefix("torch.")
            dtype = getattr(torch, dtype_name)
            start = item.offset_within_payload_bytes
            sub_views[item.key] = (
                payload[start : start + item.size_bytes].view(dtype).reshape(item.shape)
            )
        if len(sub_views) == 1 and "ec_cache" in sub_views:
            return sub_views["ec_cache"]
        return sub_views

    # ================================================================
    #  Worker-side interface
    # ================================================================

    def save_caches(
        self,
        encoder_cache: dict[str, torch.Tensor],
        mm_hash: str,
        **kwargs,
    ) -> None:
        if not self.is_producer:
            return

        with self._meta_lock:
            if mm_hash in self._slot_meta:
                self._slot_expiry[mm_hash] = time.monotonic() + self._expiry_s
                logger.info(
                    "ECCudaIPCConnector: save_caches %s — already in "
                    "slot_meta, refreshing expiry only", mm_hash,
                )
                return

        ec_cache = encoder_cache[mm_hash]
        # EVS pruned caches are dicts ({"embeddings": ..., "positions": ...});
        # plain caches are a single tensor. Mirror ECExampleConnector's
        # handling so both shapes round-trip.
        flat_payload, items = self._pack_cache(ec_cache)

        payload_bytes = flat_payload.numel() * flat_payload.element_size()
        n_chunks_needed = (
            (payload_bytes + _INTEGRITY_TOKEN_BYTES
             + self._chunk_size - 1) // self._chunk_size
        )
        with self._staging._lock:
            allocated = sum(len(v) for v in self._staging._allocs.values())
            available = self._staging.num_chunks - allocated
        logger.info(
            "ECCudaIPCConnector: save_caches %s — payload=%d B, "
            "chunks_needed=%d, allocs=%d, allocated_chunks=%d, "
            "available=%d/%d, write=%d read=%d",
            mm_hash, payload_bytes, n_chunks_needed,
            len(self._staging._allocs), allocated, available,
            self._staging.num_chunks,
            self._staging._write_cursor, self._staging._read_cursor,
        )

        t0 = time.monotonic()
        t_wait = time.monotonic()
        retry_count = 0
        while True:
            try:
                slot = self._staging.copy_in(mm_hash, flat_payload)
                break
            except RuntimeError as exc:
                retry_count += 1
                if retry_count <= 3 or retry_count % 100 == 0:
                    with self._staging._lock:
                        alloc_count = len(self._staging._allocs)
                        alloc_chunks = sum(
                            len(v) for v in self._staging._allocs.values()
                        )
                    logger.warning(
                        "ECCudaIPCConnector: copy_in failed for %s "
                        "(retry %d): %s — allocs=%d, chunks=%d/%d",
                        mm_hash, retry_count, exc,
                        alloc_count, alloc_chunks,
                        self._staging.num_chunks,
                    )
                # 1) Try evicting a consumed (ACK'd) entry.
                with self._meta_lock:
                    consumed = {k for k, v in self._ack_counts.items() if v >= 1}
                evicted, _ = self._staging.evict_oldest(consumed)
                if evicted is not None:
                    with self._meta_lock:
                        self._slot_meta.pop(evicted, None)
                        self._slot_expiry.pop(evicted, None)
                        self._ack_counts.pop(evicted, None)
                    continue

                # 2) Drain pending-free hashes (discarded clips the
                #    API server no longer needs). These can't arrive
                #    via the normal scheduler path because the
                #    EngineCore main loop is blocked on this step.
                freed_any = False
                with self._pending_free_lock:
                    to_free = list(self._pending_free)
                    self._pending_free.clear()
                if to_free:
                    logger.info(
                        "ECCudaIPCConnector: backpressure draining "
                        "%d pending-free hashes", len(to_free),
                    )
                    self.free_caches(to_free)
                    freed_any = True
                if freed_any:
                    continue

                elapsed = time.monotonic() - t_wait
                if (
                    self._backpressure_timeout_s > 0
                    and elapsed > self._backpressure_timeout_s
                ):
                    logger.error(
                        "ECCudaIPCConnector: backpressure timeout "
                        "after %.0fs for %s — skipping IPC save "
                        "(D node will re-encode this clip)",
                        elapsed, mm_hash,
                    )
                    return
                time.sleep(0.01)

        t_sync = time.monotonic()
        self._staging.sync_copy()
        torch.cuda.current_stream().synchronize()
        sync_ms = (time.monotonic() - t_sync) * 1e3
        if sync_ms > 50:
            logger.info(
                "ECCudaIPCConnector: sync_copy+cuda_sync took %.1f ms "
                "for %s", sync_ms, mm_hash,
            )

        first_chunk = slot.chunk_ids[0]
        offset_bytes = first_chunk * self._staging.chunk_size

        ipc_slot = ECCudaIPCSlotMeta(
            mm_hash=mm_hash,
            offset_bytes=offset_bytes,
            size_bytes=slot.size_bytes,
            items=items,
            integrity_token=slot.integrity_token,
            producer_id=self._engine_id,
        )

        with self._meta_lock:
            self._slot_meta[mm_hash] = ipc_slot
            self._slot_expiry[mm_hash] = time.monotonic() + self._expiry_s

        if self._pub_sock is not None:
            try:
                pub_enc = msgspec.msgpack.Encoder()
                self._pub_sock.send(
                    pub_enc.encode((_NOTIFY_AVAILABLE, ipc_slot)),
                    zmq.NOBLOCK,
                )
                logger.info(
                    "ECCudaIPCConnector: NOTIFY_AVAILABLE sent for %s "
                    "(offset=%d, size=%d)",
                    mm_hash, offset_bytes, slot.size_bytes,
                )
            except Exception as exc:
                logger.error(
                    "ECCudaIPCConnector: NOTIFY_AVAILABLE send FAILED "
                    "for %s: %s",
                    mm_hash, exc,
                )
        else:
            logger.error(
                "ECCudaIPCConnector: _pub_sock is None, cannot send "
                "NOTIFY_AVAILABLE for %s", mm_hash,
            )

        total_ms = (time.monotonic() - t0) * 1e3
        logger.info(
            "ECCudaIPCConnector: save %s — %d B off=%d (%.1f ms)",
            mm_hash,
            slot.size_bytes,
            offset_bytes,
            total_ms,
        )

    def free_caches(self, mm_hashes: list[str]) -> None:
        if not self.is_producer or not mm_hashes:
            return
        with self._meta_lock:
            for h in mm_hashes:
                self._slot_meta.pop(h, None)
                self._slot_expiry.pop(h, None)
                self._ack_counts.pop(h, 0)
        for h in mm_hashes:
            self._staging.free(h)

    def enqueue_pending_free(self, mm_hashes: list[str]) -> None:
        """Thread-safe: mark hashes for deferred IPC free.

        Called from EngineCore.free_ec_caches (potentially from any
        thread).  The save_caches backpressure loop drains this set so
        it can reclaim space even while the EngineCore main loop is
        blocked on future.result().
        """
        if not mm_hashes:
            return
        with self._pending_free_lock:
            self._pending_free.update(mm_hashes)

    def start_load_caches(
        self,
        encoder_cache: dict[str, torch.Tensor],
        **kwargs,
    ) -> None:
        if not self.is_consumer:
            return

        metadata = self._get_connector_metadata()
        assert isinstance(metadata, ECCudaIPCConnectorMetadata)
        if not metadata.items_to_load:
            return

        items = [i for i in metadata.items_to_load if i.mm_hash not in encoder_cache]
        if not items:
            return

        # Group items by producer_id so we read from the right buffer
        # and send ACKs to the right endpoint.
        from collections import defaultdict
        by_producer: dict[str, list[ECCudaIPCSlotMeta]] = defaultdict(list)
        for item in items:
            by_producer[item.producer_id].append(item)

        t_batch = time.monotonic()
        loaded = 0
        total_bytes = 0

        for producer_id, producer_items in by_producer.items():
            port = self._resolve_producer_port(producer_id)
            prod = self._ensure_producer(producer_id, port)
            assert self._copy_stream is not None

            pending: list[tuple[ECCudaIPCSlotMeta, torch.Tensor]] = []
            copy_ctx = torch.cuda.stream(self._copy_stream)

            buffer_bytes = prod.remote_tensor.numel()
            with copy_ctx:
                for item in producer_items:
                    end = item.offset_bytes + item.size_bytes
                    if end > buffer_bytes:
                        raise RuntimeError(
                            "ECCudaIPCConnector: slot for "
                            f"mm_hash={item.mm_hash} addresses bytes "
                            f"[{item.offset_bytes}, {end}) but consumer's "
                            f"remote_tensor view for producer {producer_id} "
                            f"is only {buffer_bytes} bytes. "
                            "The producer was likely restarted with a larger "
                            "VIA_EC_BUFFER_BYTES; restart this consumer to "
                            "re-handshake against the current producer buffer."
                        )
                    remote_slice = prod.remote_tensor[
                        item.offset_bytes : end
                    ]
                    if prod.device_id != self._device_id:
                        local_bytes = remote_slice.to(
                            device=f"cuda:{self._device_id}",
                            non_blocking=True,
                        ).clone()
                    else:
                        local_bytes = remote_slice.clone()
                    pending.append((item, local_bytes))

            self._copy_stream.synchronize()

            acked_hashes: list[str] = []
            for item, raw_bytes in pending:
                payload = raw_bytes[_INTEGRITY_TOKEN_BYTES:]
                encoder_cache[item.mm_hash] = self._unpack_payload(
                    payload,
                    item.items,
                )
                acked_hashes.append(item.mm_hash)
                loaded += 1
                total_bytes += item.size_bytes

            if acked_hashes:
                self._send_ack(producer_id, acked_hashes)

        batch_ms = (time.monotonic() - t_batch) * 1e3
        if loaded:
            logger.info(
                "ECCudaIPCConnector: start_load_caches — %d loaded, %.2f MB, %.1f ms",
                loaded,
                total_bytes / (1024 * 1024),
                batch_ms,
            )

    def free_consumer_slots(self, mm_hashes: list[str]) -> None:
        # Cloning creates independent tensors; nothing to free on
        # our staging side.
        return

    # ================================================================
    #  Scheduler-side interface
    # ================================================================

    def _ensure_snapshot(self) -> None:
        if not self._snapshot_valid:
            with self._avail_lock:
                self._available_snapshot = dict(self._available_slots)
            self._snapshot_valid = True

    def has_cache_item(self, identifier: str) -> bool:
        if not self.is_consumer:
            return False
        self._ensure_snapshot()
        found = identifier in self._available_snapshot
        if not found:
            logger.info(
                "ECCudaIPCConnector: has_cache_item(%s) = False, "
                "snapshot has %d keys: %s",
                identifier,
                len(self._available_snapshot),
                list(self._available_snapshot.keys())[:10],
            )
        return found

    def update_state_after_alloc(
        self,
        request: "Request",
        index: int,
    ) -> None:
        if not self.is_consumer:
            return
        mm_hash = request.mm_features[index].identifier
        self._snapshot_valid = False
        if not self.has_cache_item(mm_hash):
            return
        num_tokens = request.get_num_encoder_embeds(index)
        self._mm_datas_need_loads[mm_hash] = num_tokens

    def build_connector_meta(
        self,
        scheduler_output: SchedulerOutput,
    ) -> ECConnectorMetadata:
        meta = ECCudaIPCConnectorMetadata()
        if self.is_consumer:
            for mm_hash in self._mm_datas_need_loads:
                slot = self._available_snapshot.get(mm_hash)
                if slot is not None:
                    meta.items_to_load.append(slot)
        self._mm_datas_need_loads.clear()
        self._snapshot_valid = False
        return meta

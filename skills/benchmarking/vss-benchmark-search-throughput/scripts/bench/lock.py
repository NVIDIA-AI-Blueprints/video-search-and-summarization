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

"""Cluster-wide single-run lock, held in Elasticsearch.

A host-local lock file is not enough. It reaches one user on one machine, so a
different user, a different host sharing the cluster, or a direct invocation of
the script all slip past it. A second sweep once emptied Elasticsearch nine
hours into a running one, and the only trace was that the second sweep's
``doc_counts_before`` is all zeros at a timestamp inside the first sweep's run
window.

So the lock lives where every participant already looks and no extra RBAC is
needed: Elasticsearch. The benchmark cannot run without it, an in-cluster Job
reaches it, and so does any host on the network.

Mechanics. One document per namespace, written with ``op_type=create``, which is
atomic: exactly one writer gets 201 and everyone else gets 409. The holder
heartbeats with ``if_seq_no``/``if_primary_term`` so it can tell "I still hold
this" from "someone took it", and a lock whose heartbeat is older than its TTL
is stale and may be taken over -- otherwise a run killed with SIGKILL would
block the namespace forever.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_INDEX = "vss-search-benchmark-lock"
DEFAULT_TTL_SEC = 300.0
DEFAULT_HEARTBEAT_SEC = 30.0
DEFAULT_TIMEOUT_SEC = 15


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _age_sec(timestamp: str) -> float | None:
    """Seconds since an ISO timestamp, or ``None`` when it cannot be read.

    Unreadable is not the same as old: a lock whose heartbeat cannot be parsed
    is left alone rather than stolen, because stealing on a parse bug is how two
    runs end up sharing a namespace again.
    """
    text = str(timestamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - parsed).total_seconds(), 0.0)


@dataclass
class LockHolder:
    """Who holds the lock, as recorded in the document."""

    token: str = ""
    host: str = ""
    pid: int = 0
    user: str = ""
    label: str = ""
    run_id: str = ""
    output_dir: str = ""
    acquired_at: str = ""
    heartbeat_at: str = ""
    ttl_sec: float = DEFAULT_TTL_SEC

    def describe(self) -> str:
        age = _age_sec(self.heartbeat_at)
        seen = (
            f"last seen {age:.0f}s ago" if age is not None else "heartbeat unreadable"
        )
        where = f"{self.user or '?'}@{self.host or '?'} pid {self.pid or '?'}"
        return (
            f"{self.label or 'benchmark'} ({where}, "
            f"started {self.acquired_at or '?'}, {seen})"
        )


class RunLockError(RuntimeError):
    """The namespace is held by another run, or the lock store is unusable."""


class LockStoreUnavailable(RunLockError):
    """The lock store could not be asked: no URL, unreachable, or an HTTP error.

    Kept apart from "another run holds it" because a stale Elasticsearch address
    otherwise reads as a lock conflict.
    """


@dataclass
class RunLock:
    """A held lock plus the heartbeat keeping it alive."""

    elasticsearch_url: str
    namespace: str
    index: str = DEFAULT_INDEX
    ttl_sec: float = DEFAULT_TTL_SEC
    heartbeat_sec: float = DEFAULT_HEARTBEAT_SEC
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    enabled: bool = True

    label: str = ""
    run_id: str = ""
    output_dir: str = ""

    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    held: bool = False
    lost: bool = False
    lost_reason: str = ""
    stole_from: str = ""
    _acquired_at: str = ""
    _seq_no: int = -1
    _primary_term: int = -1
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    # --- transport ---------------------------------------------------------

    @property
    def _doc_url(self) -> str:
        return f"{self.elasticsearch_url.rstrip('/')}/{self.index}/_doc/{self.namespace}"

    def _body(self, acquired_at: str) -> dict[str, Any]:
        return {
            "token": self.token,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
            "label": self.label,
            "run_id": self.run_id,
            "output_dir": self.output_dir,
            "acquired_at": acquired_at,
            "heartbeat_at": _utc_now_iso(),
            "ttl_sec": self.ttl_sec,
        }

    def _ensure_index(self) -> None:
        """Create the lock index when automatic index creation is off.

        A 400 means it already exists, which is the common case and not an error.
        """
        url = f"{self.elasticsearch_url.rstrip('/')}/{self.index}"
        try:
            requests.put(
                url,
                json={"settings": {"number_of_shards": 1, "number_of_replicas": 0}},
                timeout=self.timeout_sec,
            )
        except requests.RequestException as e:
            logger.debug("lock index create failed (continuing): %s", e)

    def read_holder(self) -> tuple[LockHolder | None, int, int]:
        """Current holder with its sequence numbers, for a compare-and-set write."""
        try:
            resp = requests.get(self._doc_url, timeout=self.timeout_sec)
        except requests.RequestException as e:
            raise LockStoreUnavailable(
                f"cannot reach the lock store at {self.index}: {e}"
            ) from e
        if resp.status_code == 404:
            return None, -1, -1
        if resp.status_code != 200:
            raise LockStoreUnavailable(
                f"lock store returned HTTP {resp.status_code} reading {self.namespace}"
            )
        try:
            payload = resp.json()
            source = payload.get("_source") or {}
            holder = LockHolder(**{
                key: source[key]
                for key in LockHolder.__dataclass_fields__ if key in source
            })
            return (
                holder,
                int(payload.get("_seq_no", -1)),
                int(payload.get("_primary_term", -1)),
            )
        except (ValueError, TypeError) as e:
            raise LockStoreUnavailable(
                f"lock document for {self.namespace} is unreadable: {e}"
            ) from e

    # --- acquire / release -------------------------------------------------

    def acquire(self, preempt_stale: bool = True, _retried: bool = False) -> RunLock:
        """Claim the namespace, or raise :class:`RunLockError` naming the holder."""
        if not self.enabled:
            return self
        if not self.elasticsearch_url:
            raise LockStoreUnavailable(
                "services.elasticsearch_url is empty, so the single-run lock cannot "
                "be taken. Set it, or set execution.run_lock.enabled: false to run "
                "without the guard."
            )
        self._ensure_index()
        # Kept on the instance so the heartbeat reports when the run started
        # rather than when it last checked in, and preserved across the one
        # retry below so a release-and-retry does not look like a fresh claim.
        self._acquired_at = self._acquired_at or _utc_now_iso()
        acquired_at = self._acquired_at

        try:
            resp = requests.put(
                f"{self.elasticsearch_url.rstrip('/')}/{self.index}"
                f"/_create/{self.namespace}",
                json=self._body(acquired_at),
                params={"refresh": "true"},
                timeout=self.timeout_sec,
            )
        except requests.RequestException as e:
            raise LockStoreUnavailable(
                f"cannot reach the lock store at {self.index}: {e}"
            ) from e

        if resp.status_code in (200, 201):
            payload = resp.json() if resp.content else {}
            self._seq_no = int(payload.get("_seq_no", -1))
            self._primary_term = int(payload.get("_primary_term", -1))
            self.held = True
            self._start_heartbeat()
            return self

        if resp.status_code != 409:
            raise LockStoreUnavailable(
                f"lock store returned HTTP {resp.status_code} claiming "
                f"{self.namespace}: {resp.text[:200]}"
            )

        holder, seq_no, primary_term = self.read_holder()
        if holder is None:
            # Released between the create and the read. One retry is enough: a
            # caller looping here would be competing with a live run anyway.
            if _retried:
                raise RunLockError(
                    f"the lock on {self.namespace} changed hands twice while claiming it"
                )
            return self.acquire(preempt_stale=preempt_stale, _retried=True)

        age = _age_sec(holder.heartbeat_at)
        ttl = float(holder.ttl_sec or self.ttl_sec)
        if not (preempt_stale and age is not None and age > ttl):
            raise RunLockError(
                f"another benchmark run holds {self.namespace}: {holder.describe()}. "
                f"Wait for it to finish, or -- if you are certain it is dead -- "
                f"delete the lock with: curl -XDELETE '{self._doc_url}?refresh=true'"
            )

        # Stale. Take it over with a compare-and-set so that two runs racing on
        # the same stale lock cannot both win.
        try:
            steal = requests.put(
                self._doc_url,
                json=self._body(acquired_at),
                params={
                    "if_seq_no": seq_no,
                    "if_primary_term": primary_term,
                    "refresh": "true",
                },
                timeout=self.timeout_sec,
            )
        except requests.RequestException as e:
            raise LockStoreUnavailable(
                f"cannot reach the lock store at {self.index}: {e}"
            ) from e
        if steal.status_code == 409:
            raise RunLockError(
                f"another run claimed {self.namespace} while taking over the stale "
                f"lock of {holder.describe()}"
            )
        if steal.status_code not in (200, 201):
            raise LockStoreUnavailable(
                f"lock store returned HTTP {steal.status_code} taking over "
                f"{self.namespace}"
            )
        payload = steal.json() if steal.content else {}
        self._seq_no = int(payload.get("_seq_no", -1))
        self._primary_term = int(payload.get("_primary_term", -1))
        self.held = True
        self.stole_from = holder.describe()
        logger.warning(
            "took over a stale lock on %s from %s (heartbeat %.0fs old, ttl %.0fs)",
            self.namespace, holder.describe(), age, ttl,
        )
        self._start_heartbeat()
        return self

    def release(self) -> None:
        """Drop the claim. Idempotent, and safe to call when never acquired."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if not self.held:
            return
        self.held = False
        try:
            # Only delete a document we still own. A run that took us over for
            # being stale has already replaced it, and deleting that would hand
            # the namespace to a third run while the second is still measuring.
            holder, seq_no, primary_term = self.read_holder()
            if holder is None or holder.token != self.token:
                return
            requests.delete(
                self._doc_url,
                params={
                    "if_seq_no": seq_no,
                    "if_primary_term": primary_term,
                    "refresh": "true",
                },
                timeout=self.timeout_sec,
            )
        except (RunLockError, requests.RequestException) as e:
            logger.warning("could not release the lock on %s: %s", self.namespace, e)

    # --- heartbeat ---------------------------------------------------------

    def _start_heartbeat(self) -> None:
        if self.heartbeat_sec <= 0:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop, name="run-lock-heartbeat", daemon=True
        )
        self._thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_sec):
            try:
                resp = requests.put(
                    self._doc_url,
                    json=self._body(self._acquired_at or _utc_now_iso()),
                    params={
                        "if_seq_no": self._seq_no,
                        "if_primary_term": self._primary_term,
                    },
                    timeout=self.timeout_sec,
                )
            except requests.RequestException as e:
                # Transient. Keep the thread alive: the TTL is many heartbeats
                # long, so a blip does not cost the lock.
                logger.debug("lock heartbeat failed for %s: %s", self.namespace, e)
                continue
            if resp.status_code == 409:
                self.lost = True
                self.lost_reason = "another run overwrote the lock document"
                logger.error(
                    "LOST the single-run lock on %s: %s. Another run is active on "
                    "this namespace and may wipe it mid-sweep.",
                    self.namespace, self.lost_reason,
                )
                return
            if resp.status_code not in (200, 201):
                logger.debug(
                    "lock heartbeat HTTP %s for %s", resp.status_code, self.namespace
                )
                continue
            try:
                payload = resp.json()
                self._seq_no = int(payload.get("_seq_no", self._seq_no))
                self._primary_term = int(
                    payload.get("_primary_term", self._primary_term)
                )
            except (ValueError, TypeError):
                continue

    # --- context manager ---------------------------------------------------

    def __enter__(self) -> RunLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def lock_config(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve the ``execution.run_lock`` block against its defaults."""
    block = ((config.get("execution") or {}).get("run_lock") or {})
    return {
        "enabled": bool(block.get("enabled", True)),
        "index": str(block.get("index") or DEFAULT_INDEX),
        "ttl_sec": float(block.get("ttl_sec") or DEFAULT_TTL_SEC),
        "heartbeat_sec": float(block.get("heartbeat_sec") or DEFAULT_HEARTBEAT_SEC),
        "preempt_stale": bool(block.get("preempt_stale", True)),
    }


def _namespace_of(config: dict[str, Any]) -> str:
    return str((config.get("deployment") or {}).get("namespace") or "vss-search")


def acquire_run_lock(
    config: dict[str, Any],
    label: str,
    run_id: str = "",
    output_dir: str = "",
) -> RunLock:
    """Take the single-run lock for the namespace in ``config``.

    Raises :class:`RunLockError` when another run holds it, naming the holder.
    """
    settings = lock_config(config)
    lock = RunLock(
        elasticsearch_url=str(
            (config.get("services") or {}).get("elasticsearch_url") or ""
        ),
        namespace=_namespace_of(config),
        index=settings["index"],
        ttl_sec=settings["ttl_sec"],
        heartbeat_sec=settings["heartbeat_sec"],
        enabled=settings["enabled"],
        label=label,
        run_id=run_id,
        output_dir=output_dir,
    )
    return lock.acquire(preempt_stale=settings["preempt_stale"])

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
Minimal in-memory Elasticsearch stand-in for VIOS overlay tests.

VIOS's ElasticMetadataStore only ever issues ``GET/POST <url>/_search?size=N`` with a
bool query (a ``sensorId`` term + a time ``range``, ``sort`` ascending, optional
``search_after``) and reads back ``hits.hits[]._source.{objects,id}`` plus
``hits.hits[].sort[0]`` (epoch ms). This server implements exactly that surface over
a list of documents produced by ``metadata_generator`` -- no JVM, instant startup,
API-faithful for what VIOS exercises (including the download prefetch's parallel
per-slice ``_search`` calls, handled concurrently by the threading server).

It is intentionally permissive: it walks the query JSON to find the sensor term and
the range bounds rather than assuming an exact structure, so it keeps working if the
query body shifts slightly between VIOS versions.
"""
from __future__ import annotations

import heapq
import json
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


def _to_epoch_ms(value: Any) -> Optional[int]:
    """Coerce an ISO-8601 string or a numeric epoch (s/ms) to epoch milliseconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        # Heuristic: >1e12 is already ms, else seconds.
        return int(v if v > 1e12 else v * 1000.0)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            return _to_epoch_ms(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000 + 0.5)
        except ValueError:
            return None
    return None


def _walk(node: Any):
    """Yield every dict in a nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _extract_query(body: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], Optional[int], Optional[int]]:
    """Pull (sensorId, gte_ms, lte_ms, search_after_ms) out of an ES query body."""
    sensor: Optional[str] = None
    gte_ms: Optional[int] = None
    lte_ms: Optional[int] = None
    for d in _walk(body.get("query", {})):
        term = d.get("term")
        if isinstance(term, dict):
            for key, val in term.items():
                if key in ("sensorId", "sensorId.keyword"):
                    sensor = val.get("value") if isinstance(val, dict) else val
        rng = d.get("range")
        if isinstance(rng, dict):
            for _field, bounds in rng.items():
                if isinstance(bounds, dict):
                    if "gte" in bounds:
                        gte_ms = _to_epoch_ms(bounds["gte"])
                    if "gt" in bounds and gte_ms is None:
                        gte_ms = _to_epoch_ms(bounds["gt"])
                    if "lte" in bounds:
                        lte_ms = _to_epoch_ms(bounds["lte"])
                    if "lt" in bounds and lte_ms is None:
                        lte_ms = _to_epoch_ms(bounds["lt"])
    search_after_ms: Optional[int] = None
    sa = body.get("search_after")
    if isinstance(sa, list) and sa:
        search_after_ms = _to_epoch_ms(sa[0])
    return sensor, gte_ms, lte_ms, search_after_ms


class FakeESStore:
    """Thread-safe in-memory document set, queried like an ES index."""

    def __init__(self, index_name: str = "mdx-bev-test", retention_hours: float = 3.0):
        if retention_hours <= 0:
            raise ValueError("retention_hours must be greater than zero")
        self._lock = threading.Lock()
        self._docs_by_sensor: Dict[Any, deque] = {}
        self.index_name = index_name
        self.retention_hours = float(retention_hours)
        self._retention_ms = int(self.retention_hours * 60 * 60 * 1000)

    def _prepare(self, docs):
        prepared = []
        for i, d in enumerate(docs):
            epoch = d.get("_epoch_ms")
            if epoch is None:
                epoch = _to_epoch_ms(d.get("timestamp") or d.get("@timestamp"))
            prepared.append({"_id": str(d.get("id", i)), "epoch_ms": int(epoch), "source": d})
        return prepared

    def load(self, docs: List[Dict[str, Any]]) -> None:
        """Replace the document set. Each doc must carry ``_epoch_ms``."""
        prepared = self._prepare(docs)
        prepared.sort(key=lambda x: x["epoch_ms"])
        grouped = defaultdict(deque)
        for doc in prepared:
            grouped[doc["source"].get("sensorId")].append(doc)
        with self._lock:
            self._docs_by_sensor = dict(grouped)
        logger.info("FakeES loaded %d docs into %s", len(prepared), self.index_name)

    def append(self, docs: List[Dict[str, Any]]) -> None:
        """Append documents and retain the configured event-time window per sensor."""
        grouped = defaultdict(list)
        for doc in self._prepare(docs):
            grouped[doc["source"].get("sensorId")].append(doc)

        with self._lock:
            for sensor_id, incoming in grouped.items():
                incoming.sort(key=lambda doc: doc["epoch_ms"])
                retained = self._docs_by_sensor.get(sensor_id)
                if retained is None:
                    retained = deque(incoming)
                    self._docs_by_sensor[sensor_id] = retained
                elif incoming and incoming[0]["epoch_ms"] >= retained[-1]["epoch_ms"]:
                    retained.extend(incoming)
                else:
                    retained = deque(
                        heapq.merge(retained, incoming, key=lambda doc: doc["epoch_ms"])
                    )
                    self._docs_by_sensor[sensor_id] = retained

                cutoff_ms = retained[-1]["epoch_ms"] - self._retention_ms
                while retained and retained[0]["epoch_ms"] < cutoff_ms:
                    retained.popleft()

    def clear(self) -> None:
        with self._lock:
            self._docs_by_sensor = {}

    def search(self, sensor, gte_ms, lte_ms, size, search_after_ms) -> Dict[str, Any]:
        with self._lock:
            if sensor is None:
                buckets = [tuple(bucket) for bucket in self._docs_by_sensor.values()]
            else:
                buckets = [tuple(self._docs_by_sensor.get(sensor, ()))]
                if None in self._docs_by_sensor:
                    buckets.append(tuple(self._docs_by_sensor[None]))
        docs = heapq.merge(*buckets, key=lambda doc: doc["epoch_ms"]) if buckets else ()
        hits = []
        for d in docs:
            if sensor is not None and d["source"].get("sensorId") not in (sensor, None):
                if d["source"].get("sensorId") != sensor:
                    continue
            if gte_ms is not None and d["epoch_ms"] < gte_ms:
                continue
            if lte_ms is not None and d["epoch_ms"] > lte_ms:
                continue
            if search_after_ms is not None and d["epoch_ms"] <= search_after_ms:
                continue
            source = {k: v for k, v in d["source"].items() if k != "_epoch_ms"}
            hits.append({
                "_index": self.index_name,
                "_id": d["_id"],
                "_source": source,
                "sort": [d["epoch_ms"]],
            })
            if size is not None and len(hits) >= size:
                break
        return {
            "took": 1,
            "timed_out": False,
            "hits": {
                "total": {"value": len(hits), "relation": "eq"},
                "hits": hits,
            },
        }


class _Handler(BaseHTTPRequestHandler):
    # Injected by FakeESServer.
    store: FakeESStore = None  # type: ignore[assignment]

    def log_message(self, *_args):  # silence stdlib access logging
        return

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        # Root / cluster info so version/health probes succeed.
        if parsed.path in ("/", ""):
            self._send(200, {
                "name": "fake-es",
                "cluster_name": "vios-overlay-test",
                "version": {"number": "8.19.0"},
                "tagline": "You Know, for Overlay Tests",
            })
            return
        if not parsed.path.endswith("/_search"):
            self._send(200, {"acknowledged": True})
            return

        body: Dict[str, Any] = {}
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                body = {}
        qs = parse_qs(parsed.query)
        size = None
        if "size" in qs:
            try:
                size = int(qs["size"][0])
            except (ValueError, IndexError):
                size = None
        if size is None:
            size = int(body.get("size", 10000))

        sensor, gte_ms, lte_ms, search_after_ms = _extract_query(body)
        result = self.store.search(sensor, gte_ms, lte_ms, size, search_after_ms)
        logger.info(
            "FakeES _search sensor=%s range=[%s..%s] size=%s search_after=%s -> %d hits",
            sensor, gte_ms, lte_ms, size, search_after_ms, len(result["hits"]["hits"]),
        )
        self._send(200, result)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class FakeESServer:
    """Context-managed fake ES on ``host:port`` backed by a FakeESStore."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 0,
        index_name: str = "mdx-bev-test",
        retention_hours: float = 3.0,
    ):
        self.store = FakeESStore(index_name, retention_hours=retention_hours)
        handler = type("_BoundHandler", (_Handler,), {"store": self.store})
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self.host = host
        self.port = self._httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def index_url(self) -> str:
        """The value to put in VIOS ``video_metadata_server`` (URL incl. index)."""
        return f"{self.host}:{self.port}/{self.store.index_name}*"

    def load_docs(self, docs: List[Dict[str, Any]]) -> None:
        self.store.load(docs)

    def start(self) -> "FakeESServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("FakeES serving on %s (index %s)", self.base_url, self.store.index_name)
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("FakeES stopped")

    def __enter__(self) -> "FakeESServer":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

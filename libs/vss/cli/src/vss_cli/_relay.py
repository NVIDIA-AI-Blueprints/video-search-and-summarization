# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Optional, best-effort CLI lifecycle records. Never capture command arguments."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import json
import os
from queue import Queue
import re
from threading import Thread
import time
from typing import TYPE_CHECKING
from typing import override
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler
from urllib.request import Request
from urllib.request import build_opener
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable

_GROUPS = frozenset({"analytics", "configure", "memory", "search", "summarize", "vios", "vlm"})
_WORDS = frozenset(
    {
        "run",
        "status",
        "get",
        "list",
        "show",
        "check",
        "add",
        "delete",
        "clip",
        "snapshot",
        "timeline",
        "query",
        "introspect",
        "upsert",
        "events",
        "incidents",
        "incident",
        "sensors",
        "places",
        "fov-histogram",
        "average-speed",
        "analyze",
        "embeddings",
        "backfill",
        "memory",
        "introspection",
        "embed",
        "attribute",
        "fusion",
        "object",
    }
)
_IDENTITY = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,254}")
_NESTED = {
    ("search", "run"): {"embed", "attribute", "fusion", "object"},
    ("memory", "embeddings"): {"backfill"},
    ("configure", "memory"): {"show", "check", "introspection", "embeddings", "retrieval"},
}
_REQUEST_TIMEOUT = 0.2
_FLUSH_TIMEOUT = 0.25


class _NoRedirect(HTTPRedirectHandler):
    @override
    def redirect_request(self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        # Trial identifiers must not be forwarded to a different destination.
        return None


def _post_events(url: str, headers: dict[str, str], events: Queue[dict[str, object] | None]) -> None:
    try:
        opener = build_opener(_NoRedirect())
        for event in iter(events.get, None):
            try:
                request = Request(
                    url,
                    data=json.dumps(event, separators=(",", ":"), allow_nan=False).encode(),
                    headers={"Content-Type": "application/json", **headers},
                    method="POST",
                )
                with opener.open(request, timeout=_REQUEST_TIMEOUT):
                    pass  # Do not read or log a collector's response body.
            except Exception:
                pass  # Telemetry must not change stderr or the command's outcome.
    except Exception:
        pass  # Includes transport setup failures in the optional worker.


def run_with_events(args: list[str], command: Callable[[], int]) -> int:
    """Run once, preserving its return/exception and limiting telemetry flush time."""
    url = os.environ.get("VSS_RELAY_URL", "")
    run = os.environ.get("VSS_RELAY_RUN", "")
    trial = os.environ.get("VSS_RELAY_TRIAL", "")
    if not url or not _IDENTITY.fullmatch(run) or not _IDENTITY.fullmatch(trial):
        return command()
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and not any(ord(char) <= 32 or char == "\\" for char in url)
        )
        _ = parsed.port  # Validate a supplied port as well.
    except ValueError:
        valid = False
    if not valid:
        return command()

    try:
        # Only public command words, never option values, prompts, URLs or output.
        name = ["vss"]
        if args and args[0] in _GROUPS:
            name.append(args[0])
            if len(args) > 1 and args[1] in _WORDS:
                name.append(args[1])
                if len(args) > 2 and args[2] in _NESTED.get((args[0], args[1]), set()):
                    name.append(args[2])
        scope = str(uuid4())
        started = time.monotonic()
        events: Queue[dict[str, object] | None] = Queue(maxsize=3)  # start, end, stop; no retry backlog

        def record(phase: str, code: int | None = None) -> dict[str, object]:
            metadata: dict[str, object] = {"session_id": f"vss-eval/{run}/{trial}", "source": "vss-cli"}
            data: dict[str, object] = {}
            if code is not None:
                metadata["duration_ms"] = round(max(0, time.monotonic() - started) * 1000, 3)
                data["exit_code"] = code
                if code != 0:
                    data["error"] = f"CLI exited with code {code}"
            return {
                "kind": "scope",
                "category": "tool",
                "scope_category": phase,
                "uuid": scope,
                "name": " ".join(name),
                "timestamp": datetime.now(UTC).isoformat(),
                "data_schema": "nvidia.vss.cli.lifecycle/v1",
                "metadata": metadata,
                "data": data,
            }

        events.put_nowait(record("start"))
        # ponytail: one daemon worker per CLI process; not a durable exporter.
        # A daemon plus bounded join also limits DNS stalls, unlike socket timeouts alone.
        worker = Thread(
            target=_post_events,
            args=(url, {"X-Relay-Run": run, "X-Relay-Trial": trial}, events),
            daemon=True,
        )
        worker.start()
    except Exception:
        return command()

    exit_code = 1
    try:
        exit_code = command()
        return exit_code
    except SystemExit as error:
        exit_code = error.code if isinstance(error.code, int) else 0 if error.code is None else 1
        raise
    except KeyboardInterrupt:
        exit_code = 130
        raise
    finally:
        try:
            events.put_nowait(record("end", exit_code))
            events.put_nowait(None)
            worker.join(timeout=_FLUSH_TIMEOUT)
        except Exception:
            pass

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

"""Ingest backends: the deprecated single PUT and the three-step agent flow.

A third backend -- direct VST/VIOS -- is missing on purpose. The UI has already
moved to it (ci-vss-oss commit 0bdfc8d, the eval's previous home) but its contract is not documented
anywhere we can read, and guessing would produce an eval that indexes
differently from the product.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from typing import Any
import uuid

import requests

from .base import COMPLETE_TIMEOUT
from .base import CONTENT_TYPES
from .base import DEFAULT_UPLOAD_TIMESTAMP
from .base import UPLOAD_TIMEOUT
from .base import UPLOAD_URL_TIMEOUT
from .base import base_record
from .base import finish_record

if TYPE_CHECKING:
    from pathlib import Path

#: How ``/complete`` failures are classified. Observed on 10.86.12.161:
#: the call 502s on first attempt and succeeds on retry (Assault036_x264
#: returned 200 with chunks_processed=7 the second time). It is flaky, not
#: broken, so a single attempt under-reports ingest success badly.
COMPLETE_RETRY = "retry"
COMPLETE_ALREADY_REGISTERED = "already-registered"
COMPLETE_FATAL = "fatal"

#: RTVI-CV's response when the camera is already registered. This is what a
#: retry hits when the *previous* attempt got far enough to add the stream
#: before failing -- i.e. the work already landed.
_DUPLICATE_CAMERA_MARKER = "duplicate camera id"


def classify_complete_failure(status_code: int, body: str) -> str:
    """Decide what a failed ``/complete`` response means.

    Split out as a pure function so the policy is testable without a backend.

    "Duplicate Camera id" is deliberately NOT a failure. It means a prior
    attempt already registered the stream in RTVI-CV, so the pipeline ran; the
    observed case (warehouse_sample) had working embeddings and searchable
    results despite every ``/complete`` call returning an error.
    """
    if _DUPLICATE_CAMERA_MARKER in (body or "").lower():
        return COMPLETE_ALREADY_REGISTERED
    if status_code >= 500 or status_code == 429:
        return COMPLETE_RETRY
    return COMPLETE_FATAL


class LegacyPutIngest:
    """``PUT /api/v1/videos-for-search/{name}`` -- deprecated upstream.

    Kept so a baseline can still be captured while the route exists. Upstream
    states removal is gated on this repo migrating away from it, so this
    backend has a shelf life.
    """

    name = "legacy-put"

    #: The single PUT is synchronous and indexes before it returns.
    proves_indexing = True

    def __init__(self, endpoint: str, timeout: int = UPLOAD_TIMEOUT) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "url": f"{self.endpoint}/api/v1/videos-for-search/{{name}}",
            "deprecated": True,
        }

    def upload(self, video_path: Path) -> dict[str, Any]:
        record = base_record(video_path)
        content_type = CONTENT_TYPES.get(video_path.suffix, "video/mp4")
        try:
            with open(video_path, "rb") as f:
                start = time.time()
                resp = requests.put(
                    f"{self.endpoint}/api/v1/videos-for-search/{video_path.stem}",
                    data=f,
                    headers={"Content-Type": content_type, "Accept": "application/json"},
                    timeout=self.timeout,
                )
                latency = time.time() - start
            record["status_code"] = resp.status_code
            finish_record(record, latency)
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
            record["success"] = True
            record["video_id"] = payload.get("video_id", "unknown")
            record["sensor_id"] = payload.get("video_id")
            record["chunks_processed"] = payload.get("chunks_processed")
        except requests.Timeout:
            record["success"] = False
            record["error"] = "Request timeout"
        except Exception as e:
            record["success"] = False
            record["error"] = f"{type(e).__name__}: {e}"
        return record


class AgentThreeStepIngest:
    """The documented replacement for the deprecated single PUT.

    Three phases, timed separately -- the single PUT reported one opaque
    number, so this yields strictly more information than the old flow:

        1. POST /api/v1/videos                       -> {"url": ...}
        2. POST {url}   (nvstreamer-* headers)       -> {"sensorId": ...}
        3. POST /api/v1/videos/{sensor_id}/complete  -> {"chunks_processed": N}

    Phase 3 is where indexing happens (RTVI-CV stream add + RTVI-Embed
    embedding generation), which is why it carries the long timeout and why
    ``chunks_processed`` appears only there.
    """

    name = "agent-3step"

    #: ``/complete`` runs the embedding leg synchronously and returns
    #: ``chunks_processed``; a zero fails the upload. That IS the check.
    proves_indexing = True

    def __init__(
        self,
        endpoint: str,
        upload_timestamp: str = DEFAULT_UPLOAD_TIMESTAMP,
        complete_retries: int = 3,
        complete_backoff_s: float = 5.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.upload_timestamp = upload_timestamp
        self.complete_retries = complete_retries
        self.complete_backoff_s = complete_backoff_s

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "steps": [
                f"POST {self.endpoint}/api/v1/videos",
                "POST {upload_url}",
                f"POST {self.endpoint}/api/v1/videos/{{sensor_id}}/complete",
            ],
            "upload_timestamp": self.upload_timestamp,
            "complete_retries": self.complete_retries,
            "complete_backoff_s": self.complete_backoff_s,
        }

    def _complete(self, sensor_id: str, body: dict[str, Any]) -> tuple[dict[str, Any], int, str]:
        """Call ``/complete``, retrying the flaky 5xx.

        Returns ``(completion_json, attempts, outcome)`` where outcome is one of
        the ``COMPLETE_*`` constants, or raises on a fatal / exhausted failure.
        """
        url = f"{self.endpoint}/api/v1/videos/{sensor_id}/complete"
        last_detail = ""

        for attempt in range(1, self.complete_retries + 1):
            resp = requests.post(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=COMPLETE_TIMEOUT,
            )
            if resp.ok:
                return (resp.json() or {}), attempt, COMPLETE_RETRY if attempt > 1 else "ok"

            last_detail = (resp.text or "")[:300]
            verdict = classify_complete_failure(resp.status_code, resp.text or "")

            if verdict == COMPLETE_ALREADY_REGISTERED:
                # A previous attempt already registered the stream, so the
                # pipeline ran. chunks_processed is unknowable from here --
                # readiness and the first query are what confirm it.
                print(f"    /complete attempt {attempt}: already registered (RTVI-CV duplicate); treating as done")
                return {}, attempt, COMPLETE_ALREADY_REGISTERED

            if verdict == COMPLETE_FATAL or attempt == self.complete_retries:
                raise RuntimeError(f"/complete failed after {attempt} attempt(s): {resp.status_code} {last_detail}")

            wait = self.complete_backoff_s * attempt
            print(f"    /complete attempt {attempt} returned {resp.status_code}; retrying in {wait:.0f}s")
            time.sleep(wait)

        raise RuntimeError(f"/complete exhausted {self.complete_retries} attempts: {last_detail}")

    def upload(self, video_path: Path) -> dict[str, Any]:
        record = base_record(video_path)
        filename = video_path.name
        content_type = CONTENT_TYPES.get(video_path.suffix, "video/mp4")
        phases: dict[str, float] = {}
        overall_start = time.time()

        try:
            # -- 1. Ask the agent where to put it ---------------------------
            t0 = time.time()
            resp = requests.post(
                f"{self.endpoint}/api/v1/videos",
                json={"filename": filename},
                headers={"Content-Type": "application/json"},
                timeout=UPLOAD_URL_TIMEOUT,
            )
            resp.raise_for_status()
            upload_url = (resp.json() or {}).get("url")
            phases["request_url_s"] = round(time.time() - t0, 3)
            if not upload_url:
                raise RuntimeError(f"POST /api/v1/videos returned no upload url: {resp.text[:200]}")

            # -- 2. Send the bytes to VST -----------------------------------
            # Single chunk: the eval's fixtures are small enough that chunking
            # would only add moving parts. Real UI uploads chunk; if a fixture
            # outgrows this, the headers below are where that changes.
            identifier = str(uuid.uuid4())
            t0 = time.time()
            with open(video_path, "rb") as f:
                resp = requests.post(
                    upload_url,
                    headers={
                        "nvstreamer-chunk-number": "1",
                        "nvstreamer-total-chunks": "1",
                        "nvstreamer-is-last-chunk": "true",
                        "nvstreamer-identifier": identifier,
                        "nvstreamer-file-name": filename,
                    },
                    files={"mediaFile": (filename, f, content_type)},
                    data={
                        "filename": filename,
                        "metadata": json.dumps({"timestamp": self.upload_timestamp}),
                    },
                    timeout=UPLOAD_TIMEOUT,
                )
            resp.raise_for_status()
            upload_body = resp.json() or {}
            phases["upload_s"] = round(time.time() - t0, 3)
            sensor_id = upload_body.get("sensorId")
            if not sensor_id:
                raise RuntimeError(f"VST upload returned no sensorId: {resp.text[:200]}")

            # -- 3. Trigger post-processing / indexing ----------------------
            complete_body = {**upload_body, "filename": filename}
            t0 = time.time()
            completion, attempts, outcome = self._complete(sensor_id, complete_body)
            phases["complete_s"] = round(time.time() - t0, 3)

            record["success"] = True
            record["sensor_id"] = sensor_id
            record["video_id"] = sensor_id
            record["chunks_processed"] = completion.get("chunks_processed")
            record["complete_attempts"] = attempts
            record["complete_outcome"] = outcome
            record["phases"] = phases
            finish_record(record, time.time() - overall_start)

            # The upstream skill validates exactly this before treating an
            # upload as real; a 200 with zero chunks means nothing was indexed.
            # The duplicate-camera path is exempt: the work landed on an
            # earlier attempt, so no chunk count comes back and readiness plus
            # the first query are what actually confirm it.
            if outcome != COMPLETE_ALREADY_REGISTERED:
                chunks = completion.get("chunks_processed")
                if not isinstance(chunks, int) or chunks <= 0:
                    record["success"] = False
                    record["error"] = f"completion reported chunks_processed={chunks!r}"
        except requests.Timeout:
            record["success"] = False
            record["error"] = "Request timeout"
            record["phases"] = phases
            finish_record(record, time.time() - overall_start)
        except Exception as e:
            record["success"] = False
            record["error"] = f"{type(e).__name__}: {e}"
            record["phases"] = phases
            finish_record(record, time.time() - overall_start)
        return record


class VstDirectIngest:
    """Upload straight to VIOS and let its webhooks drive perception.

    This is what the product UI does. ``ChatUpload.tsx`` POSTs to
    ``{VST}/vst/api/v1/storage/file`` and stops; there is no
    ``POST /api/v1/videos`` and no ``/complete`` anywhere in ``services/ui``.
    VIOS raises ``camera_streaming`` and its webhook notifier fans out to
    RT-CV, RT-Embed and RT-VLM itself -- the three calls the agent's
    ``/complete`` makes by hand, plus tagging, which ``/complete`` never does.

    So this backend is :class:`AgentThreeStepIngest` minus phases 1 and 3: the
    same bytes, the same headers, the same anchor, with the upload URL built
    rather than fetched.

    **The timestamp anchor survives**, which is the thing worth checking before
    trusting any of this: ground truth is matched by absolute-timestamp overlap
    against ``2025-01-01T00:00:00``, so an ingest path that anchored chunks
    anywhere else would score every query zero and read as a retrieval
    collapse. It does not, because the anchor was never the agent's to set --
    it rides in the ``metadata={"timestamp": ...}`` field of the upload itself,
    which this backend sends byte-identically. ``/complete`` hard-codes the same
    constant (``video_ingest.py``) because it is the shared convention, not the
    source. The webhooks agree: the three ``camera_remove`` cleanup calls in
    ``dev-profile-search/vios/configs/notification_config.json`` target
    ``mdx-embed-filtered-2025-01-01`` and its two siblings by name.

    Two things are still worse here, and they are why this is not the default:

    * **No completion proof.** ``/complete`` runs the embedding leg
      synchronously and returns ``chunks_processed``; the webhook fan-out is
      fire-and-forget and VIOS never reports the outcome to the uploader. The
      readiness poll and the first query are all that confirm anything indexed,
      so ``chunks_processed`` is ``None`` -- not zero -- and the run records
      ``ingest_proof: "none"``.
    * **Helm has webhooks off.** ``webhooks.enabled`` is true only in the Docker
      search profile; the Helm chart ships the dummy item and false. On such a
      deployment this backend uploads successfully and indexes nothing, and
      without a chunk count nothing says so until the first query returns empty.

    :meth:`verify_anchor` remains as a cheap one-video sanity check, since a
    wrong anchor is silent and indistinguishable from broken retrieval.
    """

    name = "vst-direct"

    #: Whether a successful upload is itself evidence that the media is
    #: searchable. False here: the webhook fan-out is fire-and-forget, so the
    #: caller has to check. A string in ``describe()`` was the wrong home for
    #: this -- the gate compared it to "none" while describe() returned
    #: "none (webhook fan-out is fire-and-forget)", so the check silently never
    #: ran and a run against a half-built index scored as a retrieval collapse.
    proves_indexing = False

    def __init__(
        self,
        vst_url: str,
        upload_timestamp: str = DEFAULT_UPLOAD_TIMESTAMP,
    ) -> None:
        self.vst_url = vst_url.rstrip("/")
        self.upload_timestamp = upload_timestamp

    def describe(self) -> dict[str, Any]:
        return {
            "flow": self.name,
            "vst_url": self.vst_url,
            "upload_url": self.upload_url,
            "upload_timestamp": self.upload_timestamp,
            # Stated in the artifact, not left to be inferred from a null
            # chunk count: a reader comparing this run against a 3-step one
            # has to know the success criterion changed.
            "ingest_proof": "none (webhook fan-out is fire-and-forget)",
        }

    @property
    def upload_url(self) -> str:
        return f"{self.vst_url}/vst/api/v1/storage/file"

    def verify_anchor(self, sensor_id: str, timeout: int = COMPLETE_TIMEOUT) -> dict[str, Any]:
        """Read back the timeline VIOS recorded for an uploaded video.

        The one check worth making before a whole run is scored. Same endpoint
        the agent uses (``/vst/api/v1/storage/timelines``, keyed by stream id).
        Returns what it found; deciding whether the anchor is acceptable is the
        caller's, because a deliberate live-source run has a different answer
        from a file-fixture run.
        """
        url = f"{self.vst_url}/vst/api/v1/storage/timelines"
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            timelines = (resp.json() or {}).get(sensor_id) or []
        except Exception as e:
            return {"checked": False, "error": f"{type(e).__name__}: {e}"}
        if not timelines:
            return {"checked": True, "found": False, "sensor_id": sensor_id}
        start = timelines[0].get("startTime") or ""
        return {
            "checked": True,
            "found": True,
            "start_time": start,
            "end_time": timelines[0].get("endTime") or "",
            # The dataset's ground truth is offsets from this instant.
            "matches_expected_anchor": start.startswith(self.upload_timestamp[:10]),
            "expected_anchor": self.upload_timestamp,
        }

    def upload(self, video_path: Path) -> dict[str, Any]:
        record = base_record(video_path)
        filename = video_path.name
        content_type = CONTENT_TYPES.get(video_path.suffix, "video/mp4")
        overall_start = time.time()

        try:
            # Byte-identical to phase 2 of the three-step flow, which is itself
            # byte-identical to the UI's chunkedUpload -- same five
            # nvstreamer-* headers, same mediaFile/filename/metadata fields.
            identifier = str(uuid.uuid4())
            with open(video_path, "rb") as f:
                resp = requests.post(
                    self.upload_url,
                    headers={
                        "nvstreamer-chunk-number": "1",
                        "nvstreamer-total-chunks": "1",
                        "nvstreamer-is-last-chunk": "true",
                        "nvstreamer-identifier": identifier,
                        "nvstreamer-file-name": filename,
                    },
                    files={"mediaFile": (filename, f, content_type)},
                    data={
                        "filename": filename,
                        "metadata": json.dumps({"timestamp": self.upload_timestamp}),
                    },
                    timeout=UPLOAD_TIMEOUT,
                )
            resp.raise_for_status()
            upload_body = resp.json() or {}
            sensor_id = upload_body.get("sensorId")
            if not sensor_id:
                raise RuntimeError(f"VST upload returned no sensorId: {resp.text[:200]}")

            record["success"] = True
            record["sensor_id"] = sensor_id
            record["video_id"] = sensor_id
            # Deliberately None rather than 0: nothing counted, which is not
            # the same claim as "counted, and it was zero". The zero-chunk
            # guard the three-step flow applies has nothing to test here.
            record["chunks_processed"] = None
            record["ingest_proof"] = "none"
            record["phases"] = {"upload_s": round(time.time() - overall_start, 3)}
            finish_record(record, time.time() - overall_start)
        except requests.Timeout:
            record["success"] = False
            record["error"] = "Request timeout"
            finish_record(record, time.time() - overall_start)
        except Exception as e:
            record["success"] = False
            record["error"] = f"{type(e).__name__}: {e}"
            finish_record(record, time.time() - overall_start)
        return record

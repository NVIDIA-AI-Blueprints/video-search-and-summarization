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

"""
RTVI VLM Simulator for functional tests.

Mocks the RTVI VLM microservice endpoints used by RealtimeAlertService:
  POST   /v1/streams/add                     — add a stream
  GET    /v1/streams/get-stream-info         — list registered streams
  DELETE /v1/streams/delete/{stream_id}      — remove a stream
  POST   /v1/generate_captions               — start caption generation
  DELETE /v1/generate_captions/{stream_id}   — stop caption generation
  GET    /v1/ready                           — health check

Test infrastructure:
  GET    /v1/calls                     — return recorded call log (optionally filtered by ?method=&path=)
  DELETE /v1/calls                     — clear call log
  PUT    /v1/fault                     — set fault injection: {"endpoint": ..., "status_code": ..., "body": ...}
  DELETE /v1/fault                     — clear all fault injections
"""

import os
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request


def create_app():
    app = Flask(__name__)

    call_log = []
    call_log_lock = threading.Lock()

    # In-memory stream registry: stream_id -> entry dict matching the
    # shape RTVI returns. Keeps ``streams_add`` and ``get-stream-info``
    # in sync so functional tests can verify the realtime service's
    # reuse logic against a state that mirrors a real RTVI deployment.
    streams_registry = {}
    streams_registry_lock = threading.Lock()

    # {endpoint_key: {"status_code": int, "body": dict}}
    faults = {}
    faults_lock = threading.Lock()

    def record_call(method, path, body=None, params=None):
        entry = {
            "method": method,
            "path": path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if body is not None:
            entry["body"] = body
        if params:
            entry["params"] = params
        with call_log_lock:
            call_log.append(entry)

    def check_fault(endpoint_key):
        """Return fault dict if set, else None.  Applies ``delay_seconds`` before returning."""
        with faults_lock:
            fault = faults.get(endpoint_key)
        if fault and fault.get("delay_seconds"):
            time.sleep(fault["delay_seconds"])
        return fault

    # ------------------------------------------------------------------
    # RTVI VLM endpoints
    # ------------------------------------------------------------------

    @app.route("/v1/ready", methods=["GET"])
    def ready():
        return jsonify({"status": "ready"})

    @app.route("/v1/streams/add", methods=["POST"])
    def streams_add():
        body = request.get_json(silent=True) or {}
        record_call("POST", "/v1/streams/add", body=body)

        fault = check_fault("streams_add")
        if fault and fault.get("status_code", 200) != 200:
            return jsonify(fault["body"]), fault["status_code"]

        delay_entry = faults.get("_delay_streams_add")
        if delay_entry and delay_entry.get("delay_seconds"):
            time.sleep(delay_entry["delay_seconds"])

        streams = body.get("streams", [])
        results = []
        with streams_registry_lock:
            for s in streams:
                stream_id = s.get("id") or str(uuid.uuid4())
                entry = {
                    "id": stream_id,
                    "liveStreamUrl": s.get("liveStreamUrl", ""),
                    "description": s.get("description", ""),
                    "sensor_name": s.get("sensor_name"),
                }
                streams_registry[stream_id] = entry
                results.append({
                    "id": stream_id,
                    "liveStreamUrl": entry["liveStreamUrl"],
                    "status": "added",
                })
        return jsonify({"results": results, "errors": []})

    @app.route("/v1/streams/get-stream-info", methods=["GET"])
    def streams_get_info():
        record_call("GET", "/v1/streams/get-stream-info")

        fault = check_fault("get_stream_info")
        if fault:
            return jsonify(fault["body"]), fault["status_code"]

        with streams_registry_lock:
            results = list(streams_registry.values())
        return jsonify({"results": results})

    @app.route("/v1/streams/delete/<stream_id>", methods=["DELETE"])
    def streams_delete(stream_id):
        record_call("DELETE", f"/v1/streams/delete/{stream_id}")

        fault = check_fault("streams_delete")
        if fault:
            return jsonify(fault["body"]), fault["status_code"]

        with streams_registry_lock:
            streams_registry.pop(stream_id, None)
        return jsonify({"status": "deleted", "stream_id": stream_id})

    @app.route("/v1/generate_captions", methods=["POST"])
    def generate_captions():
        body = request.get_json(silent=True) or {}
        record_call("POST", "/v1/generate_captions", body=body)

        fault = check_fault("generate_captions")
        if fault:
            return jsonify(fault["body"]), fault["status_code"]

        return jsonify({"status": "started", "stream_id": body.get("id", "")})

    @app.route("/v1/generate_captions/<stream_id>", methods=["DELETE"])
    def stop_captions(stream_id):
        record_call("DELETE", f"/v1/generate_captions/{stream_id}")

        fault = check_fault("stop_captions")
        if fault:
            return jsonify(fault["body"]), fault["status_code"]

        return jsonify({"status": "stopped", "stream_id": stream_id})

    # ------------------------------------------------------------------
    # Test infrastructure endpoints
    # ------------------------------------------------------------------

    @app.route("/v1/calls", methods=["GET"])
    def get_calls():
        """Return recorded calls, optionally filtered by ?method= and/or ?path= (substring match)."""
        method_filter = request.args.get("method", "").upper()
        path_filter = request.args.get("path", "")
        with call_log_lock:
            filtered = call_log[:]
        if method_filter:
            filtered = [c for c in filtered if c["method"] == method_filter]
        if path_filter:
            filtered = [c for c in filtered if path_filter in c["path"]]
        return jsonify({"calls": filtered, "count": len(filtered)})

    @app.route("/v1/calls", methods=["DELETE"])
    def clear_calls():
        """Clear all recorded calls."""
        with call_log_lock:
            call_log.clear()
        return jsonify({"status": "cleared"})

    @app.route("/v1/fault", methods=["PUT"])
    def set_fault():
        """Set a fault injection.

        Body: {"endpoint": "generate_captions", "status_code": 500, "body": {"error": "..."},
               "delay_seconds": 5}
        Valid endpoints: streams_add, streams_delete, generate_captions, stop_captions
        Optional: delay_seconds — sleep before returning the fault response (useful for
        concurrency tests that need the endpoint to block).
        """
        body = request.get_json(silent=True) or {}
        endpoint = body.get("endpoint", "")
        status_code = body.get("status_code", 500)
        fault_body = body.get("body", {"error": "injected fault"})
        delay = body.get("delay_seconds", 0)
        with faults_lock:
            faults[endpoint] = {
                "status_code": status_code,
                "body": fault_body,
                "delay_seconds": delay,
            }
        return jsonify({"status": "fault_set", "endpoint": endpoint, "status_code": status_code, "delay_seconds": delay})

    @app.route("/v1/fault", methods=["DELETE"])
    def clear_faults():
        """Clear all fault injections."""
        with faults_lock:
            faults.clear()
        return jsonify({"status": "faults_cleared"})

    @app.route("/v1/delay", methods=["PUT"])
    def set_delay():
        """Set a global response delay for an endpoint (no error, just slow).

        Body: {"endpoint": "streams_add", "delay_seconds": 5}
        The endpoint will sleep before returning its normal 200 response.
        """
        body = request.get_json(silent=True) or {}
        endpoint = body.get("endpoint", "")
        delay = body.get("delay_seconds", 0)
        with faults_lock:
            faults[f"_delay_{endpoint}"] = {"delay_seconds": delay}
        return jsonify({"status": "delay_set", "endpoint": endpoint, "delay_seconds": delay})

    @app.route("/v1/delay", methods=["DELETE"])
    def clear_delays():
        """Clear all global delays."""
        with faults_lock:
            to_remove = [k for k in faults if k.startswith("_delay_")]
            for k in to_remove:
                del faults[k]
        return jsonify({"status": "delays_cleared"})

    return app


if __name__ == "__main__":
    port = int(os.getenv("RTVI_SIM_PORT", "18082"))
    app = create_app()
    app.run(host="0.0.0.0", port=port)

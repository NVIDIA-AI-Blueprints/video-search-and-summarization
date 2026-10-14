# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the live RT-VLM REST caption pipeline with an RTSP source."""

import argparse
import json
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


CLEANUP_RETRY_INTERVAL_SEC = 0.2
CLEANUP_RETRY_TIMEOUT_SEC = 10


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--rtsp-url", required=True)
    parser.add_argument("--captions", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.captions < 1 or args.timeout < 1 or not args.rtsp_url.startswith("rtsp://"):
        parser.error("require positive --captions/--timeout and an rtsp:// URL")
    endpoint = args.endpoint.rstrip("/")

    def request(path, payload=None, method=None):
        return urlopen(
            Request(
                endpoint + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Content-Type": "application/json"},
                method=method,
            ),
            timeout=args.timeout,
        )

    def cleanup_request(path, payload=None, method=None):
        deadline = time.monotonic() + min(args.timeout, CLEANUP_RETRY_TIMEOUT_SEC)
        while True:
            try:
                return request(path, payload, method)
            except HTTPError as error:
                if error.code != 409 or time.monotonic() >= deadline:
                    raise
                error.close()
                time.sleep(CLEANUP_RETRY_INTERVAL_SEC)

    camera = "streaming-smoke-" + uuid.uuid4().hex
    asset = None
    stopped = False
    count = 0
    try:
        with request("/models") as response:
            model = json.load(response)["data"][0]["id"]
        with request("/stream/add", {"key": "sensor", "value": {
            "camera_id": camera, "camera_url": args.rtsp_url, "change": "camera_add"
        }}) as response:
            asset = json.load(response)["asset_id"]
        deadline = time.monotonic() + args.timeout
        with request("/generate_captions", {
            "id": asset, "model": model, "prompt": "Describe the visible scene briefly.",
            "stream": True, "inference_mode": "streaming_vlm",
            "streaming_frame_policy": "ordered", "chunk_duration": 1,
            "num_frames_per_second_or_fixed_frames_chunk": 1,
            "use_fps_for_chunking": False, "max_tokens": 32, "temperature": 0,
        }) as response:
            for line in response:
                if time.monotonic() > deadline:
                    raise TimeoutError("Caption deadline exceeded")
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                event = json.loads(data)
                if "error" in event:
                    raise RuntimeError(event)
                for chunk in event.get("chunk_responses", []):
                    if chunk.get("content", "").strip():
                        print(json.dumps(chunk), flush=True)
                        count += 1
                if count >= args.captions:
                    break
            # Stop before closing SSE, which also initiates automatic teardown.
            with request("/generate_captions/" + asset, method="DELETE"):
                stopped = True
        if count < args.captions:
            raise RuntimeError(f"Only {count}/{args.captions} captions received")
    finally:
        if asset is not None:
            original_failure = sys.exc_info()[1]
            cleanup_failure = None
            try:
                if not stopped:
                    with cleanup_request("/generate_captions/" + asset, method="DELETE"):
                        pass
            except Exception as error:
                cleanup_failure = error
            finally:
                try:
                    with cleanup_request("/stream/remove", {"key": "sensor", "value": {
                        "camera_id": camera, "change": "camera_remove"
                    }}):
                        pass
                except Exception:
                    if original_failure is None and cleanup_failure is None:
                        raise
            if original_failure is None and cleanup_failure is not None:
                raise cleanup_failure
    print(json.dumps({"passed": True, "captions": count, "camera_removed": True}))


if __name__ == "__main__":
    main()

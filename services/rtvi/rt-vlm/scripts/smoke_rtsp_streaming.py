# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the live RT-VLM REST caption pipeline with an RTSP source."""

import argparse
import json
import time
import uuid
from urllib.request import Request, urlopen


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

    camera = "streaming-smoke-" + uuid.uuid4().hex
    asset = None
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
        if count < args.captions:
            raise RuntimeError(f"Only {count}/{args.captions} captions received")
    finally:
        if asset is not None:
            try:
                with request("/generate_captions/" + asset, method="DELETE"):
                    pass
            finally:
                with request("/stream/remove", {"key": "sensor", "value": {
                    "camera_id": camera, "change": "camera_remove"
                }}):
                    pass
    print(json.dumps({"passed": True, "captions": count, "camera_removed": True}))


if __name__ == "__main__":
    main()

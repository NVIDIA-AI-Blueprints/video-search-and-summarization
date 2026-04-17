#!/usr/bin/env python3
"""
End-to-end test for a deployed VSS agent using a public warehouse video.

Modeled after tests/integration_test/scripts/test_websocket_agent.py from the
deep-search repo. Downloads (or reuses) a warehouse_*.mp4, PUTs it to the
agent, sends a summarization query over the WebSocket, handles HITL prompts,
and exits 0 on a non-empty response.

Default video: Pexels #6079421 "Person Driving Forklift" (CC0, ~1.2 MB).
Override with --video-path <local.mp4> or --video-url <https://...mp4>.

Usage:
    python test_warehouse_video.py <agent_url> [--profile base|lvs]
"""

import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

import websocket

DEFAULT_VIDEO_URL = (
    "https://videos.pexels.com/video-files/6079421/6079421-sd_640_360_24fps.mp4"
)
DEFAULT_VIDEO_NAME = "warehouse_forklift_pexels_6079421"
DEFAULT_VST_URL = "http://localhost:30888"


BASE_QUERIES = [
    "What videos are available?",
    "Generate a report for video {video_name}",
]
LVS_QUERIES = [
    "What videos are available?",
    "Generate a report for video {video_name} using long video understanding",
]

HITL_LVS = {
    "scenario": "warehouse monitoring",
    "events": "box falling, accident, person entering restricted area",
    "objects": "forklifts, pallets, workers",
}
HITL_BASE_VLM_PROMPT = (
    "Describe in detail what is happening in this video, including all visible "
    "people, vehicles, equipment, objects, actions, and environmental conditions.\n"
    "\nOUTPUT REQUIREMENTS:\n[timestamp-timestamp] Description of what is happening."
)


def http_get(url, timeout=5):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


def wait_for_health(agent_url, max_wait=300):
    deadline = time.time() + max_wait
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            status, _ = http_get(f"{agent_url}/health", timeout=5)
            if status == 200:
                print(f"  ✓ /health OK (attempt {attempt})")
                return True
        except Exception as e:
            if attempt % 10 == 0:
                print(f"  ... still waiting ({e})")
        time.sleep(3)
    return False


def get_vst_upload_url(agent_url, filename):
    """POST /api/v1/videos → returns the VST storage URL for PUTting the file."""
    payload = json.dumps({"filename": filename, "embedding": False}).encode()
    req = urllib.request.Request(
        f"{agent_url}/api/v1/videos",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode())
    url = body.get("url") or body.get("value", {}).get("url")
    if not url:
        raise RuntimeError(f"no 'url' in POST /api/v1/videos response: {body}")
    return url


def upload_video(agent_url, video_path, video_id):
    """Base-profile upload: ask agent for a VST URL via POST /api/v1/videos,
    then PUT the file bytes directly to VST."""
    filename = os.path.basename(video_path)
    size = os.path.getsize(video_path)
    try:
        vst_url = get_vst_upload_url(agent_url, filename)
        print(f"  ✓ VST upload URL: {vst_url}")
    except Exception as e:
        print(f"  ✗ POST /api/v1/videos failed: {e}")
        return False

    print(f"  PUT {filename} ({size/1024/1024:.1f} MB) → {vst_url}")
    with open(video_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(
        vst_url,
        data=data,
        headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            print(f"  ✓ VST responded {r.status}")
            return True
    except urllib.error.HTTPError as e:
        print(f"  ✗ VST HTTP {e.code}: {e.read().decode()[:300]}")
        return False
    except Exception as e:
        print(f"  ✗ VST upload exception: {e}")
        return False


def video_in_vst(vst_url, video_name, timeout=10):
    """Return True if *video_name* appears in VST's sensor/streams list."""
    url = f"{vst_url.rstrip('/')}/vst/api/v1/sensor/streams"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        for item in data:
            for _sid, streams in item.items():
                for s in streams:
                    if s.get("name") == video_name:
                        return True
        return False
    except Exception as e:
        print(f"  ⚠ VST check failed: {e}")
        return False


def hitl_response(prompt_text, profile):
    low = prompt_text.lower()
    if profile == "lvs":
        if "scenario" in low:
            return HITL_LVS["scenario"]
        if "events" in low:
            return HITL_LVS["events"]
        if "objects" in low:
            return HITL_LVS["objects"]
    if profile == "base" and ("vlm prompt" in low or "report generation" in low):
        return HITL_BASE_VLM_PROMPT
    return ""


def run_query(agent_url, query, profile, recv_timeout=120, overall_timeout=900):
    ws_url = agent_url.replace("http://", "ws://").replace("https://", "wss://") + "/websocket"
    print(f"  WS: {ws_url}")
    ws = websocket.create_connection(ws_url, timeout=overall_timeout)
    try:
        ws.send(json.dumps({
            "type": "user_message",
            "schema_type": "chat_stream",
            "content": {"messages": [{"role": "user", "content": [{"type": "text", "text": query}]}]},
        }))
        print(f"  → {query}")
        full = ""
        deadline = time.time() + overall_timeout
        while time.time() < deadline:
            ws.settimeout(recv_timeout)
            try:
                chunk = ws.recv()
            except websocket.WebSocketTimeoutException:
                print("  ✗ recv timeout")
                return ""
            if not chunk:
                break
            msg = json.loads(chunk)
            t = msg.get("type", "")
            if t == "system_interaction_message":
                parent_id = msg.get("id", "default")
                prompt = msg.get("content", {}).get("text", "")
                reply = hitl_response(prompt, profile)
                print(f"  [HITL] {prompt[:120]} → {reply[:60]}")
                ws.send(json.dumps({
                    "type": "user_interaction_message",
                    "parent_id": parent_id,
                    "content": {"messages": [{"role": "user", "content": [{"type": "text", "text": reply}]}]},
                }))
            elif t == "system_response_message":
                full += msg.get("content", {}).get("text", "") or ""
                if msg.get("status") == "complete":
                    return full
            elif t == "error_message":
                err = msg.get("content", {})
                print(f"  ✗ agent error: {err.get('message')}: {err.get('details', '')[:200]}")
                return ""
        return full
    finally:
        with contextlib.suppress(Exception):
            ws.close()


def download_video(url, dest):
    print(f"  Downloading {url} → {dest}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
    print(f"  ✓ saved ({os.path.getsize(dest)/1024:.1f} KB)")


def resolve_video(args):
    if args.video_path:
        if not os.path.exists(args.video_path):
            sys.exit(f"✗ video not found: {args.video_path}")
        return args.video_path, os.path.splitext(os.path.basename(args.video_path))[0]

    url = args.video_url or DEFAULT_VIDEO_URL
    name = args.video_name or DEFAULT_VIDEO_NAME
    cache_dir = args.cache_dir or os.path.join(tempfile.gettempdir(), "vss_test_videos")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{name}.mp4")
    if not os.path.exists(path):
        download_video(url, path)
    else:
        print(f"  ✓ cached: {path}")
    return path, name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_url", help="e.g. http://localhost:8000")
    ap.add_argument("--vst-url", default=DEFAULT_VST_URL,
                    help=f"VST URL for sensor-list check (default: {DEFAULT_VST_URL})")
    ap.add_argument("--video-path", help="Local warehouse_*.mp4 (overrides --video-url)")
    ap.add_argument("--video-url", help=f"Public .mp4 URL (default: {DEFAULT_VIDEO_URL})")
    ap.add_argument("--video-name", help="Video ID to register in VST (default derived)")
    ap.add_argument("--cache-dir", help="Where to cache downloaded videos")
    ap.add_argument("--profile", choices=["base", "lvs"], default="base")
    ap.add_argument("--skip-upload", action="store_true")
    args = ap.parse_args()

    video_path, video_id = resolve_video(args)
    agent_url = args.agent_url.rstrip("/")
    vst_url = args.vst_url.rstrip("/")

    print(f"[1/4] Waiting for agent at {agent_url}...")
    if not wait_for_health(agent_url):
        sys.exit("✗ agent /health never returned 200")

    print(f"[2/4] Ensuring video is in VST (name={video_id})...")
    if video_in_vst(vst_url, video_id):
        print(f"  ✓ already registered in VST")
    elif args.skip_upload:
        sys.exit("✗ video not in VST and --skip-upload set")
    else:
        upload_ok = upload_video(agent_url, video_path, video_id)
        # Agent's ingest may 500 on the RTVI-CV side even if VST received
        # the bytes — re-check VST to determine the true state.
        time.sleep(3)
        if video_in_vst(vst_url, video_id):
            print("  ✓ present in VST "
                  + ("(via agent 2xx)" if upload_ok else "(agent errored but VST has it)"))
        else:
            sys.exit("✗ video not in VST after upload attempt")

    queries = (LVS_QUERIES if args.profile == "lvs" else BASE_QUERIES)
    print(f"[3/4] Running {len(queries)} WebSocket queries...")
    failures = 0
    for i, qt in enumerate(queries, 1):
        q = qt.format(video_name=video_id)
        print(f"  [{i}/{len(queries)}] {q}")
        resp = run_query(agent_url, q, args.profile)
        if not resp.strip():
            print(f"    ✗ empty response")
            failures += 1
        else:
            snippet = resp.strip().replace("\n", " ")[:200]
            print(f"    ✓ {len(resp)} chars — {snippet}...")

    print(f"[4/4] Results: {len(queries) - failures}/{len(queries)} queries returned content")
    if failures:
        sys.exit(f"✗ {failures} query/queries failed")
    print("✓ PASS")


if __name__ == "__main__":
    main()

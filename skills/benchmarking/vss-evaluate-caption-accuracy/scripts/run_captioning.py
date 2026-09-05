#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Stage 1: Captioning runner — gt / ref / hyp on every video in a dedup folder.

Reuses an already-running RT-VLM container ($RTVI_CONTAINER); for each run:
  1. Kill any prior python server inside the container.
  2. `docker exec -d -e ...` to launch start_rtvi_vlm.sh with run-specific env.
  3. Wait for /v1/health/ready on the host port.
  4. For each video:
        POST /v1/files            (upload)
        POST /v1/generate_captions (chunk_duration=30, stream=true) — drain SSE
        DELETE /v1/files/{id}     (free tmpfs)
  5. After all videos, kill the server, convert per-scene SSE → <run>.txt
     in [VLMCaption] format compatible with compare_vlm_captions_llm_as_judge.py.

Run configs:
  gt   — VLM_MODEL_TO_USE=openai-compat, gpt-4o, REMOTE_VIDEO_INPUT=false,
         60 frames/chunk (override of openai-compat backend's default of 10)
  ref  — VLM_MODEL_TO_USE=cosmos-reason2, no fselect, no dedup
  hyp  — cosmos-reason2 with one of two modes (selected via --hyp-mode):
           fselect-only (default):  CHOOSE_FSELECT=true + TEMPORAL_DEDUP_ENABLED=false
                                    (isolates accuracy impact of the fselect coalescer alone)
           fselect+dedup:           CHOOSE_FSELECT=true + TEMPORAL_DEDUP_ENABLED=true
                                    (set TEMPORAL_DEDUP_MODEL to the small VLM path)

Usage (from host, container must be up and healthy):
  python3 run_captioning.py --runs gt ref hyp --desc cr2-2b-t70-s80
  python3 run_captioning.py --runs hyp --scenes warehouse new_warehouse --desc test1
  python3 run_captioning.py --runs gt --dedup-dir /path/to/dedup
  python3 run_captioning.py --runs ref hyp --scenes warehouse_full \
      --desc fselect-only-warehouse-full --hyp-mode fselect-only
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Default paths (overridable via CLI)
# ---------------------------------------------------------------------------
SCRIPT_DIR     = Path(__file__).resolve().parent
SKILL_DIR      = SCRIPT_DIR.parent                 # skills/vlm-caption-accuracy
RTVI_REPO      = Path(os.environ.get("VSS_REPO_DIR", "/workspace"))
DEFAULT_DEDUP  = RTVI_REPO / "dedup"
DEFAULT_RESULTS_ROOT = SKILL_DIR / "results"
CONTAINER      = os.environ.get(
    "RTVI_CONTAINER", f"rtvi_vlm-{os.environ.get('USER', 'unknown')}"
)
IN_CONTAINER_PORT = 8000


def _resolve_host_port(default=9999):
    """Read BACKEND_PORT from <repo>/.env (matches the host port that
    docker-compose maps container:8000 to). Falls back to `default` if the
    file or the line is missing."""
    env_path = RTVI_REPO / ".env"
    if not env_path.exists():
        return default
    pat = re.compile(r"^\s*(?:export\s+)?BACKEND_PORT=(.*?)\s*$")
    for line in env_path.read_text().splitlines():
        m = pat.match(line)
        if m:
            val = m.group(1).strip().strip('"').strip("'")
            if val.isdigit():
                return int(val)
    return default


HOST_PORT      = _resolve_host_port()
# When this script runs INSIDE the rtvi_vlm container, the server it starts
# listens on the container-internal port (IN_CONTAINER_PORT=8000), NOT the
# host-mapped BACKEND_PORT (9999). Poll the right one so the readiness check
# doesn't hang against an unbound port. `/.dockerenv` is the same signal
# `_IN_CONTAINER` uses below (defined later; inline the check here).
_POLL_PORT     = IN_CONTAINER_PORT if os.path.exists("/.dockerenv") else HOST_PORT
BACKEND_URL    = f"http://localhost:{_POLL_PORT}"

# Two prompts: GT (simple dense caption) and REF/HYP (structured).
# Same prompt to GT and REF/HYP would mean the judge scores the candidate
# against a same-formatted reference, which inflates surface-form matching
# and hides real factuality differences. Keep GT free-form so the judge
# evaluates content overlap, not template compliance.
GT_PROMPT = (
    "Write a concise and detailed dense caption describing everything visible "
    "in this video, including objects, people, actions, and background."
)

REF_HYP_PROMPT = """\
You are analyzing a video chunk. You will be shown N frames sampled from the chunk,
each with a timestamp. The chunk spans T_start to T_end seconds.

Produce a description with EXACTLY the sections below, in order. Do not add
sections. Do not editorialize. Do not speculate.

## SCENE
- setting: indoor | outdoor | mixed | unclear
- location_type: 1-3 words (e.g., "parking lot", "kitchen", "warehouse aisle",
  "city street", "forest trail", "office hallway")
- camera_view: one of [first_person, fixed_overhead, fixed_ground_level,
  handheld, dashcam, drone_aerial, ptz, other]
- lighting: one of [bright_daylight, low_light, artificial_indoor, mixed,
  low_sun_long_shadows, night, unclear]
- weather: one of [clear, overcast, rain, snow, fog, n/a_indoor, unclear]
- environment_notes: 1 sentence on fixed background (furniture, terrain,
  signage, structures). Facts only.

## ENTITIES
List every distinct entity (person, object, animal, vehicle) that is relevant
to the action or persists across frames. Skip static background. One bullet each:
- id: E1, E2, E3, ...
- category: one of [person, vehicle, animal, object, group, other]
- subtype: 1-3 words (e.g., "adult male", "sedan", "dog", "shopping cart",
  "forklift", "child", "drone")
- attributes: comma-separated visible attributes (color, clothing, size,
  carried_items, markings). Skip if not visible.
- first_seen: timestamp in seconds
- last_seen: timestamp in seconds, or "still_present" if visible at chunk end

ENTITY EXISTENCE THRESHOLD: An entity belongs in this section only if it is
visibly present in at least one of the provided frames AND you can confidently
identify it as a distinct foreground object/person/vehicle — NOT a reflection,
shadow, JPEG-compression artifact, lighting shift, or piece of static
background. If you can see the entity in only one frame, briefly verify that
its appearance is consistent with a real object (right size, plausible shape,
context-appropriate) rather than a transient visual artifact. Write "none" if
no entity meets this bar. Route genuinely ambiguous interpretations (e.g.,
"can't tell if it's a person or a shadow") to ## UNCERTAIN; never invent an
entity to fill the section.

## TIMELINE
Bulleted, timestamped events. One event per line.
Format: [t1–t2 s] <entity_id> <action> [<target_or_location>]
Allowed action verbs: enter, exit, appear, disappear, approach, depart,
  stop, start_moving, pick_up, put_down, open, close, push, pull, sit, stand,
  walk, run, fall, collide_with, interact_with, hand_to, gesture, look_at,
  speak_to, remain_stationary, change_direction.
If you need a verb not in this list, use the closest match and put the original
verb in UNCERTAIN.
If nothing happens in a sub-window, write: [t1–t2 s] no_significant_action.
Use the frame timestamps provided; do not invent times between frames.

## INTERACTIONS
Bulleted. Any entity-to-entity interaction (people talking, objects exchanged,
contact between entities). Skip this section if none.
Format: [t s] <entity_id_a> <interaction_type> <entity_id_b>
Interaction types: hand_off, contact, collision, conversation, follow,
  cooperate, conflict, supervise, attend_to.

## CRITICAL_EVENTS
Zero or more notable events that a viewer should be alerted to. One bullet each:
- type: one of [collision, fall, fight, theft, fire_or_smoke, medical_event,
  unauthorized_access, crowd_surge, unattended_object, emergency_response,
  rule_violation, other]
- time: t in seconds
- participants: entity ids
- description: 1 short sentence, factual only
If nothing critical happens, write "none".

## UNCERTAIN
Anything you cannot determine from the frames. Example: "E2 subtype unclear due
to distance". One bullet per item. If everything is certain, write "none".

RULES:
- Use ONLY facts supported by the frames you were shown.
- Do NOT use hedging words: "appears to", "possibly", "as if", "likely",
  "seems", "suggesting", "implying".
- Do NOT add aesthetic, emotional, or judgmental commentary
  ("peaceful", "tense", "smooth", "chaotic", "beautifully composed").
- If a fact is uncertain, put it in ## UNCERTAIN, not in the main sections.
- Entity ids are stable within the chunk: E1 in TIMELINE refers to E1 in ENTITIES.
- An entity that leaves the frame and returns gets the same id.
- If two entities look identical and can't be distinguished, treat them as one
  id and note it in UNCERTAIN.

- FRAME-PIXEL EVIDENCE RULE: Process each provided frame pixel-by-pixel and
  generate captions strictly from what is actually visible in those pixels.
  Always use the provided frames as your only source of evidence. Do NOT
  speculate, infer, or extrapolate beyond what the pixels show.

- TIMESTAMP CLUSTERING IS NEUTRAL: If frames are clustered near a specific
  timestamp, this may or may not indicate that an entity or event is present
  — the spacing alone tells you nothing. Decide what to report ONLY from the
  pixel content of those frames, never from the clustering pattern itself.

- TIMESTAMP-GAP RULE: If there is a gap between two consecutive frame
  timestamps, you have NO visual information about that interval. DO NOT
  predict or invent entities, actions, or events inside the gap. Extend the
  state observed in the earlier frame across the gap; if the earlier frame
  shows nothing happening, the gap also has nothing happening.
"""
CHUNK_DURATION_S = 10

# Filename → scene name. Edit here to add scenes.
SCENES = {
    "176_30FPS.mp4":               "176",
    "365_30FPS.mp4":               "365",
    "original_365.mp4":            "original_365",
    "admin.mp4":                   "admin",
    "admin_60min.mp4":             "admin_60min",
    "bus.G508.mp4":                "bus",
    "GoPro5_10min_compressed.mp4": "new_warehouse",
    "hospital.mp4":                "hospital",
    "hospital_40min.mp4":          "hospital_40min",
    "its.mp4":                     "its",
    "warehouse.mp4":               "warehouse",
    "bus_40min.mp4":               "bus_40min",
    "GoPro5_40min_compressed.mp4": "gopro5_40min",
    "warehouse_82min.mp4":         "warehouse_82min",
    "warehouse_full_56min_small.mp4": "warehouse_full_56min",
    "warehouse_51min_to_56min.mp4":   "warehouse_51to56",
    "365cefe6-d7ea-442c-a34d-9761d8c1ba33.mp4": "vfr_4k",
    "zanker_ts_1.mp4":                "zanker_1",
    "zanker_ts_2.mp4":                "zanker_2",
}
ORDER = list(SCENES.keys())


def _model_env() -> dict:
    """MODEL_PATH override, or {} to inherit the container's own .env.

    The evaluation only needs both arms to run the SAME model; which model that
    is belongs to the deployment. Set MODEL_PATH in the environment to pin one
    explicitly -- the allowlist entry is then required too, because
    VLM_TRUST_REMOTE_CODE=true force-enables MODEL_PATH allowlist enforcement.
    """
    model_path = os.environ.get("MODEL_PATH", "").strip()
    if not model_path:
        return {}
    return {"MODEL_PATH": model_path, "RTVI_MODEL_PATH_ALLOWLIST": model_path}


COMMON_ENV = {
    "BACKEND_PORT":             str(IN_CONTAINER_PORT),
    "DISABLE_CA_RAG":           "true",
    "DISABLE_GUARDRAILS":       "true",
    # compose.yaml passes RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT as an empty string
    # by default, which the vllm-compatible backend reads as "not true" — so
    # the per-frame timestamp prefix that's needed for the gap-aware prompt
    # rule was silently disabled. Force it on here.
    "RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT": "true",
    # Per-second vLLM engine stats (Running/Waiting reqs, KV usage, throughput)
    # so we can see the effective batch size REF vs HYP under concurrent load.
    "VLLM_LOG_STATS_INTERVAL": "1",
    # REF/HYP set VLM_TRUST_REMOTE_CODE=true, which force-enables MODEL_PATH
    # allowlist enforcement (src/vlm_pipeline/model_path_policy.py). Without a
    # matching allowlist the server refuses to start with "must be set when
    # allowlist enforcement is on". Pinned to the exact local checkpoint rather
    # than a glob, per the guidance in CLAUDE.md.
    # Set only when MODEL_PATH is given below; see _model_env().
    # vLLM's shm multimodal-processor cache crashes EngineCore on the REF
    # (uniform 20-frame) path with "IndexError: list index out of range" in
    # serial_utils._decode_tensor -> aux_buffers[data]. compose.yaml defaults
    # this to shm; vLLM's own default is lru, which is also what the pre-rebase
    # baseline ran with (the variable did not exist then). Pin lru on BOTH arms
    # so REF/HYP stay comparable to each other and to results/mcp200-2026-08-24.
    "VLLM_MM_PROCESSOR_CACHE_TYPE": "lru",
}
HYP_MODES = ("fselect+dedup", "fselect-only")
DEFAULT_HYP_MODE = "fselect-only"


def build_hyp_env(mode):
    base = {
        **COMMON_ENV,
        "VLM_MODEL_TO_USE":            "vllm-compatible",
        **_model_env(),
        "VLM_TRUST_REMOTE_CODE":       "true",
        "CHOOSE_FSELECT":              "true",
        "TEMPORAL_DEDUP_ENABLED":      "false",
        "VLLM_GPU_MEMORY_UTILIZATION": "0.85",
        "VLM_MAX_MODEL_LEN":           "32768",
        # Wider-pre-filter design (100-frame candidate pool):
        #   1. Python sampler emits 100 equidistant PTSes per 30s chunk.
        #   2. timestampfilter pre-filters to those 100 (FSELECT_FULL_STREAM=false).
        #   3. nvdsframeselector picks selection-count frames from the
        #      candidates using the optical-flow algorithm with
        #      motion-detection=ON (exclusion-range auto-computed by the plugin).
        #   4. VLM sees the fselect-picked frames per chunk — matches REF's
        #      uniform count so the comparison isolates the selection algorithm.
        "FSELECT_FULL_STREAM":         "false",
        "VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK": "20",
        # OF-only selection + 0.7 tighten are the library's built-in defaults, so
        # we do not set NVDS_FSELECT_OF_ONLY / NVDS_FSELECT_OF_TIGHTEN here.
        # Isolate frames-only: disable the fselect timestamp-prompt prefix so HYP
        # sends the SAME structural prompt as REF (the prefix inflated output and
        # was a confound in the perf comparison). Coalescer is disabled in code.
        "FSELECT_TIMESTAMP_PROMPT":      "false",
        # --- diagnostic: plugin optical-flow timing print (OFF for clean timing) ---
        "NVDS_FRAME_SELECTOR_OF_TIMING": "0",
    }
    if mode == "fselect+dedup":
        base.update({
            # Big VLM + small dedup VLM share one GPU. Lower the big-VLM util
            # so the 2B (0.15) + fselect frame cache fit (0.6 + 0.15 < 1.0).
            "VLLM_GPU_MEMORY_UTILIZATION":            "0.6",
            "TEMPORAL_DEDUP_ENABLED":                 "true",
            "TEMPORAL_DEDUP_MODEL": os.environ.get("TEMPORAL_DEDUP_MODEL", ""),
            "TEMPORAL_DEDUP_GPU_MEMORY_UTILIZATION":  "0.15",
            "TEMPORAL_DEDUP_MAX_TOKENS":              "80",
            "TEMPORAL_DEDUP_MAX_MODEL_LEN":           "4096",
            "TEMPORAL_DEDUP_SIMILARITY_THRESHOLD":    "0.70",
            "TEMPORAL_DEDUP_SKIP_VLM_THRESHOLD":      "0.95",
        })
    elif mode == "fselect-only":
        base["TEMPORAL_DEDUP_ENABLED"] = "false"
    else:
        raise ValueError(f"unknown --hyp-mode: {mode!r}; choose from {HYP_MODES}")
    # Passthrough sweep knobs: when set on the HOST, forward them to the container
    # so the nvdsframeselector can be swept without editing this file. Any unset
    # knob is left absent so the existing default applies.
    #
    #   NVDS_FSELECT_STATIC_FRAME_COUNT  — frames emitted for a STATIC chunk
    #                                      (RTVI leaves the plugin default 3)
    #   NVDS_FSELECT_OF_TIGHTEN          — MULTIPLIER on the motion-scaled frame
    #                                      count, range (0.0, 1.0]. The plugin
    #                                      default is 0.7; 1.0 applies no reduction
    #                                      at all. Read by the plugin itself, not
    #                                      by RTVI, so leave it unset unless you are
    #                                      deliberately sweeping away from 0.7.
    #
    # equidistant-output is deliberately NOT a knob here: RTVI now defaults it to 0
    # (motion-ranked frame positions) in video_file_frame_getter.py, which measured
    # better on every motion-heavy scene. Setting it from the harness would only
    # re-enable the old uniform-position behaviour.
    #
    #   FSELECT_FULL_STREAM              — false (the RTVI default, and what the
    #                                      accuracy evaluation used) pre-filters to
    #                                      N candidate frames before the plugin;
    #                                      true sends every decoded frame, which is
    #                                      the regime where a VFR file can overflow
    #                                      cache-size.
    #
    # NVDS_FSELECT_MIN_CHANGED_PIXELS is NOT forwarded: RTVI no longer reads it or
    # sets the plugin property, so min-changed-pixels stays at the plugin default
    # (0 = off) and exporting the variable would have no effect.
    for _knob in (
        "NVDS_FSELECT_STATIC_FRAME_COUNT",
        "NVDS_FSELECT_OF_TIGHTEN",
        "FSELECT_FULL_STREAM",
    ):
        _val = os.environ.get(_knob)
        if _val is not None and _val.strip() != "":
            base[_knob] = _val.strip()
    return base


RUN_ENV = {
    "gt": {
        **COMMON_ENV,
        "VLM_MODEL_TO_USE":                    "openai-compat",
        "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "gpt-4.1",
        "REMOTE_VIDEO_INPUT":                  "false",   # OpenAI needs image_url, not video_url
        "CHOOSE_FSELECT":                      "false",
        "TEMPORAL_DEDUP_ENABLED":              "false",
        # 60 JPEG frames per 30s chunk (1 every 0.5s). Overrides the openai-compat
        # backend's hardcoded default of 10 (openai_compat_model.py:822) via the
        # server's --num-frames-per-second-or-fixed-frames-chunk arg.
        "VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK": "60",
    },
    "ref": {
        **COMMON_ENV,
        "VLM_MODEL_TO_USE":             "vllm-compatible",
        **_model_env(),
        "VLM_TRUST_REMOTE_CODE":        "true",
        "CHOOSE_FSELECT":               "false",
        "TEMPORAL_DEDUP_ENABLED":       "false",
        "VLLM_GPU_MEMORY_UTILIZATION":  "0.85",
        "VLM_MAX_MODEL_LEN":            "32768",
        # Fixed-frame mode: 20 uniform frames per 30s chunk to BigVLM.
        "VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK": "20",
        # Match HYP: use the GOP decode-opt (ENABLE_GOP_DECODE_OPT defaults true)
        # so REF and HYP share the identical GOP-probe path and
        # nvdsframeselector is the only live-path difference.
    },
    "hyp": build_hyp_env(DEFAULT_HYP_MODE),
}


# ---------------------------------------------------------------------------
# Container / server helpers
# ---------------------------------------------------------------------------
# When the script runs INSIDE the RTVI container itself, we skip the
# `docker exec` indirection entirely — we're already in the right namespace
# and the docker CLI isn't installed inside the container anyway.
_IN_CONTAINER = os.path.exists("/.dockerenv")


def _docker_prefix():
    """Return ["docker"] or ["sudo", "docker"] depending on whether the
    caller has direct docker socket access. Detected once at import time:
    `docker ps` will fail with permission-denied for non-docker-group users
    on systems that require sudo for /var/run/docker.sock."""
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return ["docker"]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return ["sudo", "docker"]


_DOCKER = ["docker"] if _IN_CONTAINER else _docker_prefix()


def docker_exec(cmd):
    """Run *cmd* either inside the container (via docker exec from host) or
    directly when this script is already running inside the container."""
    if _IN_CONTAINER:
        return subprocess.run(cmd, capture_output=True, text=True)
    return subprocess.run(_DOCKER + ["exec", CONTAINER] + cmd,
                          capture_output=True, text=True)


def container_running():
    if _IN_CONTAINER:
        return True
    r = subprocess.run(_DOCKER + ["ps", "--format", "{{.Names}}"],
                       capture_output=True, text=True)
    return CONTAINER in r.stdout.split()


def kill_server():
    """Kill the rtvi_vlm_server python process inside the container.

    Sends SIGTERM first with a 5-second grace period so atexit / signal
    handlers can run (e.g., the MP4-dump writer can finalize the moov atom),
    then SIGKILL anything still standing. Uses awk filter so the docker-exec
    bash command itself doesn't match.
    """
    if not container_running():
        return
    docker_exec([
        "bash", "-c",
        "pids=$(ps -e -o pid=,cmd= "
        "| awk '$2 ~ /^python/ && $0 ~ /rtvi_vlm_server/ {print $1}'); "
        "if [ -n \"$pids\" ]; then "
        "  kill -TERM $pids 2>/dev/null; "
        "  for i in 1 2 3 4 5; do "
        "    sleep 1; "
        "    still=$(echo $pids | xargs -n1 -I{} bash -c 'kill -0 {} 2>/dev/null && echo {}' | tr -d ' '); "
        "    [ -z \"$still\" ] && break; "
        "  done; "
        "  kill -9 $pids 2>/dev/null; sleep 1; "
        "fi"
    ])


def start_server(run_name, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")
    print(f"  Starting server (run={run_name}) → log={log_path}")
    if _IN_CONTAINER:
        # In-container path: spawn start_rtvi_vlm.sh detached with the
        # run-specific env merged on top of the current process env.
        env = os.environ.copy()
        env.update(RUN_ENV[run_name])
        logf = open(log_path, "wb")
        subprocess.Popen(
            ["bash", "-c", "./start_rtvi_vlm.sh"],
            env=env,
            cwd=str(RTVI_REPO),
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,   # detach from this process group
            close_fds=True,
        )
        return
    env_args = []
    for k, v in RUN_ENV[run_name].items():
        env_args.extend(["-e", f"{k}={v}"])
    # GStreamer debug passthrough: `docker exec` starts with a clean env, so
    # GST_DEBUG set on the HOST never reaches the server. Forward it explicitly
    # so e.g. GST_DEBUG=nvdsframeselector:4 can surface the plugin's own
    # per-chunk logs ("FSELECT chunk-input N frame(s) received, M cached").
    # Same problem for RTVI knobs that start_server_only.py callers set on the
    # host: main()'s passthrough loop never runs when this module is imported,
    # so forward them here too.
    for _gst_knob in (
        "GST_DEBUG",
        "GST_DEBUG_NO_COLOR",
        "GST_DEBUG_FILE",
        "RTVI_FSELECT_CHUNK_CLOSE_GRACE_SEC",
    ):
        _val = os.environ.get(_gst_knob)
        if _val is not None and _val.strip() != "":
            env_args.extend(["-e", f"{_gst_knob}={_val.strip()}"])
    cmd = _DOCKER + ["exec", "-d", *env_args, CONTAINER,
           "bash", "-c",
           f"cd {RTVI_REPO} && ./start_rtvi_vlm.sh > {log_path} 2>&1"]
    subprocess.run(cmd, check=True)


def wait_for_ready(timeout_s):
    deadline = time.time() + timeout_s
    print(f"  Waiting for {BACKEND_URL}/v1/health/ready (timeout={timeout_s}s) ...")
    while time.time() < deadline:
        try:
            if requests.get(f"{BACKEND_URL}/v1/health/ready", timeout=3).ok:
                print("  Server ready.")
                return
        except Exception:
            pass
        time.sleep(5)
    raise TimeoutError(f"Server not ready within {timeout_s}s")


def get_model_id():
    data = requests.get(f"{BACKEND_URL}/v1/models", timeout=10).json()
    models = data.get("data", [])
    if not models:
        raise RuntimeError("/v1/models returned empty list")
    print(f"  Loaded model id: {models[0]['id']}")
    return models[0]["id"]


# ---------------------------------------------------------------------------
# Per-video upload + caption + delete
# ---------------------------------------------------------------------------
def upload(video):
    print(f"  Uploading {video.name} ...")
    t0 = time.time()
    with video.open("rb") as fh:
        r = requests.post(
            f"{BACKEND_URL}/v1/files",
            files={"file": (video.name, fh, "video/mp4")},
            data={"purpose": "vision", "media_type": "video"},
            timeout=600,
        )
    r.raise_for_status()
    fid = r.json()["id"]
    print(f"  Uploaded → file_id={fid} ({time.time()-t0:.1f}s)")
    return fid


def generate_captions(file_id, model, sse_path, prompt):
    sse_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"id": file_id, "model": model, "prompt": prompt,
               "chunk_duration": CHUNK_DURATION_S, "stream": True,
               "temperature": 0.0, "top_p": 1.0, "seed": 1}
    print(f"  POST /v1/generate_captions (chunk={CHUNK_DURATION_S}s) ...")
    t0 = time.time()
    with requests.post(f"{BACKEND_URL}/v1/generate_captions",
                       json=payload, stream=True, timeout=None) as r:
        r.raise_for_status()
        with sse_path.open("w") as out:
            for line in r.iter_lines(decode_unicode=True):
                if line is not None:
                    out.write(line + "\n")
                    out.flush()
    elapsed = time.time() - t0
    print(f"  Captions complete in {elapsed:.1f}s → {sse_path}")
    return elapsed


def delete_file(file_id):
    try:
        requests.delete(f"{BACKEND_URL}/v1/files/{file_id}", timeout=30)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# SSE → [VLMCaption] log lines (compare-script compatible)
# ---------------------------------------------------------------------------
SSE_DATA_RE = re.compile(r"^data:\s*(\{.*\})\s*$")


def sse_to_vlmcaption(sse_path, file_id):
    if not sse_path.exists():
        return []
    chunks = []
    for line in sse_path.read_text().splitlines():
        m = SSE_DATA_RE.match(line)
        if not m:
            continue
        try:
            payload = json.loads(m.group(1))
        except Exception:
            continue
        for cr in payload.get("chunk_responses", []) or []:
            cid = cr.get("chunk_id")
            if cid is None:
                continue
            try:
                cid = int(cid)
            except (TypeError, ValueError):
                continue
            chunks.append((
                cid,
                float(cr.get("start_time", 0.0)),
                float(cr.get("end_time", 0.0)),
                (cr.get("content") or "").strip(),
            ))
    chunks.sort(key=lambda x: x[0])
    ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return [
        f"{ts_iso} INFO [VLMCaption] chunk_id={cid} stream={file_id} "
        f"chunk_start_pts={s:.1f} chunk_end_pts={e:.1f} "
        f"frame_times=[] source=BigVLM caption={cap}\n"
        for cid, s, e, cap in chunks
    ]


def next_hyp_path(scene_caps_dir, desc):
    v = 1
    while True:
        p = scene_caps_dir / f"hyp_{desc or 'hyp'}_v{v}.txt"
        if not p.exists():
            return p
        v += 1


def write_run_outputs(run_name, desc, file_id_by_scene, captions_root):
    for scene, file_id in file_id_by_scene.items():
        scene_dir = captions_root / scene
        sse_path  = scene_dir / "sse" / f"{run_name}.sse.txt"
        lines = sse_to_vlmcaption(sse_path, file_id)
        if not lines:
            print(f"  ! {scene}: no captions in {sse_path}")
            continue
        if run_name == "hyp":
            out_path = next_hyp_path(scene_dir, desc)
        else:
            out_path = scene_dir / f"{run_name}.txt"
        out_path.write_text("".join(lines))
        print(f"  {scene:15s} {len(lines):4d} captions → {out_path}")


# ---------------------------------------------------------------------------
# Per-run flow
# ---------------------------------------------------------------------------



def execute_run(run_name, scenes, desc, ready_timeout, run_dir):
    captions_root = run_dir / "captions"
    log_path      = run_dir / "server_logs" / f"{run_name}_server.log"

    print(f"\n{'='*70}")
    print(f"  RUN={run_name.upper()}   scenes={[s for s,_ in scenes]}")
    print(f"  ENV overrides: " + ", ".join(
        f"{k}={v}" for k, v in RUN_ENV[run_name].items() if k != "BACKEND_PORT"
    ))
    print(f"{'='*70}")

    if not container_running():
        raise SystemExit(f"Container '{CONTAINER}' not running. "
                         "Start it first: make start-rtvi_vlm INTERACTIVE=1")

    print("  Stopping any prior server inside container ...")
    kill_server()

    start_server(run_name, log_path)

    file_id_by_scene = {}
    try:
        wait_for_ready(ready_timeout)
        model_id = get_model_id()

        for scene_name, video_path in scenes:
            print(f"\n  --- {run_name.upper()} | {scene_name} | {video_path.name} ---")
            try:
                fid = upload(video_path)
            except Exception as e:
                print(f"  ERROR upload {video_path.name}: {e}")
                continue
            file_id_by_scene[scene_name] = fid
            sse_path = captions_root / scene_name / "sse" / f"{run_name}.sse.txt"
            # Clear any stragglers in the flat .frames root so the prior
            # scene's late-arriving JPEGs can't bleed into this scene's MP4.
            try:
                # Structural-GT regeneration: ALL runs (gt/ref/hyp) use the
                # structured prompt so GT is the structured-schema reference.
                # (Set GT back to GT_PROMPT for a free-form GT.)
                prompt = REF_HYP_PROMPT
                generate_captions(fid, model_id, sse_path, prompt)
            except Exception as e:
                print(f"  ERROR captions {scene_name}: {e}")
            finally:
                delete_file(fid)
    finally:
        print("\n  Stopping server inside container ...")
        kill_server()
        print(f"\n  Writing per-scene {run_name}.txt files ...")
        write_run_outputs(run_name, desc, file_id_by_scene, captions_root)
        # Append the server's "VLM pipeline time" line so the compare script
        # can populate its Pipeline time row.
        try:
            from augment_pipeline_time import augment_run_dir as _aug
        except ImportError:
            sys.path.insert(0, str(SCRIPT_DIR))
            from augment_pipeline_time import augment_run_dir as _aug
        _aug(captions_root.parent)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="Stage 1: capture RTVI VLM captions for gt/ref/hyp.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available scenes: {', '.join(SCENES.values())}"
    )
    p.add_argument("--runs", nargs="+", choices=["gt", "ref", "hyp"],
                   default=["gt", "ref", "hyp"])
    p.add_argument("--scenes", nargs="+",
                   help="Subset of scene names to process")
    p.add_argument("--desc", default="",
                   help="Description; used to name results/<desc>/ and hyp filenames")
    p.add_argument("--server-ready-timeout", type=int, default=900)
    p.add_argument("--dedup-dir", default=str(DEFAULT_DEDUP),
                   help=f"Directory holding the input videos (default: {DEFAULT_DEDUP})")
    p.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT),
                   help=f"Where to write results/<desc>/ (default: {DEFAULT_RESULTS_ROOT})")
    p.add_argument("--hyp-mode", choices=HYP_MODES, default=DEFAULT_HYP_MODE,
                   help=(f"HYP run configuration (default: {DEFAULT_HYP_MODE}). "
                         "'fselect+dedup' enables both CHOOSE_FSELECT and TEMPORAL_DEDUP_ENABLED; "
                         "'fselect-only' enables CHOOSE_FSELECT only (TEMPORAL_DEDUP_ENABLED=false)."))
    p.add_argument("--gt-model", default="gpt-4.1",
                   help=("OpenAI model deployment name for the GT run (sets "
                         "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME). Default 'gpt-4.1'. "
                         "Pass 'gpt-5' (reasoning model) for a stricter / more deliberate GT — "
                         "slower per chunk; the openai-compat backend already handles the "
                         "reasoning-model sampling params (max_completion_tokens, temperature=1.0)."))
    return p.parse_args()


def resolve_scenes(wanted, dedup_dir):
    by_scene_name = {scene: filename for filename, scene in SCENES.items()}
    if wanted:
        for s in wanted:
            if s not in by_scene_name:
                sys.exit(f"ERROR: unknown scene '{s}'. "
                         f"Available: {', '.join(by_scene_name)}")
        order = wanted
    else:
        order = [SCENES[f] for f in ORDER]
    out = []
    for scene in order:
        v = dedup_dir / by_scene_name[scene]
        if v.exists():
            out.append((scene, v))
        else:
            print(f"  ! skipping scene='{scene}' — video missing: {v}")
    if not out:
        sys.exit("ERROR: no videos to run.")
    return out


def main():
    args = parse_args()
    desc = args.desc.strip().replace(" ", "-") or "default"
    dedup_dir = Path(args.dedup_dir)
    run_dir = Path(args.results_root) / desc
    (run_dir / "captions").mkdir(parents=True, exist_ok=True)
    (run_dir / "server_logs").mkdir(parents=True, exist_ok=True)

    RUN_ENV["hyp"] = build_hyp_env(args.hyp_mode)
    RUN_ENV["hyp"]["GST_DEBUG"] = "nvdsframeselector:4"
    if "hyp" in args.runs:
        print(f"HYP mode: {args.hyp_mode}")

    # GT model selection (default gpt-4.1; gpt-5 / o-series handled by openai-compat backend's
    # reasoning-model branch in src/models/openai_compat/openai_compat_model.py).
    RUN_ENV["gt"]["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"] = args.gt_model
    if "gt" in args.runs:
        print(f"GT model: {args.gt_model}")

    # Passthrough sweep knobs for the vLLM input-representation A/B: when set on
    # the HOST, forward them to BOTH ref and hyp so the comparison isolates the
    # multimodal packaging (N independent images vs one video tensor) while
    # holding frame selection fixed. GT is excluded — it runs openai-compat,
    # which never reaches the vllm-compatible input branch.
    for _knob in (
        "RTVI_VLLM_LIMIT_MM_PER_PROMPT_IMAGE",
        "RTVI_VLLM_LIMIT_MM_PER_PROMPT_VIDEO",
    ):
        _val = os.environ.get(_knob)
        if _val is not None and _val.strip() != "":
            for run in ("ref", "hyp"):
                RUN_ENV[run][_knob] = _val.strip()
            print(f"Passthrough (ref+hyp): {_knob}={_val.strip()}")


    scenes = resolve_scenes(args.scenes, dedup_dir)

    overall_t0 = time.time()
    for run_name in args.runs:
        try:
            execute_run(run_name, scenes, desc,
                        args.server_ready_timeout, run_dir)
        except Exception as e:
            print(f"\n  RUN {run_name} ERROR: {e}")
            kill_server()

    print(f"\n=== ALL DONE in {time.time()-overall_t0:.1f}s ===")
    print(f"Results dir: {run_dir}")
    print(f"  captions/<scene>/{{gt,ref,hyp_<desc>_vN}}.txt")
    print(f"  captions/<scene>/sse/<run>.sse.txt   (SSE backups)")
    print(f"  server_logs/<run>_server.log")


if __name__ == "__main__":
    main()

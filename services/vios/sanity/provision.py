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
Sanity provisioning: turn ONE input mp4 into the streams the use-cases need.

The sanity user supplies a single clip. From it we:
  * make N copies (<stem>_1..<stem>_N.mp4), upload each to NVStreamer (which
    serves them as RTSP) and VST-scan so VIOS records them -> multiple live
    streams for the video-wall / multi-stream use-cases, and
  * upload <stem>_file.mp4 to VST directly -> a file-backed sensor for the
    download / replay / picture use-cases.

Reuses the bdd_tests helpers (upload_video, vst_scan) -- one-way dependency
sanity -> bdd_tests.
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import requests

from sanity_common import SanityContext, REPO_ROOT
from scripts.stream_prerequisite import upload_video  # bdd_tests helper (vst_scan intentionally NOT used: see phase 3)

logger = logging.getLogger("sanity.provision")

_DEPLOY_DIR = REPO_ROOT / "services/vios/deployment/stream-processing"
_VST_CONFIG = _DEPLOY_DIR / "docker-compose/configs/vst_config.json"           # VIOS
_NVS_CONFIG = _DEPLOY_DIR / "docker-compose/nvstreamer/configs/vst_config.json"  # NVStreamer
_VST_NOTIFICATION_CONFIG = (
    _DEPLOY_DIR / "docker-compose/configs/notification_config.json"
)
_NVS_NOTIFICATION_CONFIG = (
    _DEPLOY_DIR / "docker-compose/nvstreamer/configs/notification_config.json"
)
_ADAPTOR_CONFIG = _DEPLOY_DIR / "docker-compose/configs/adaptor_config.json"   # VMS adaptors


def _json_val(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{value}"'


def _set_json_val(text: str, key: str, value) -> str:
    """Replace a JSON scalar (string / bool / number) value for `key` in-place,
    preserving the rest of the file. Logs if the key is absent."""
    import re
    pat = rf'("{re.escape(key)}"\s*:\s*)(?:"[^"]*"|true|false|-?\d+(?:\.\d+)?)'
    new, n = re.subn(pat, lambda m: m.group(1) + _json_val(value), text, count=1)
    if n == 0:
        logger.warning("config key not found (skipped): %s", key)
    return new


def _find_key(d: dict, key: str):
    for k, v in d.items():
        if k == key:
            return v
        if isinstance(v, dict):
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def _apply_config(path, overrides: dict, recreate_target: str, recreate: bool = True) -> bool:
    """Apply {key: value} overrides to a JSON config. When `recreate` is True and
    anything changed, recreate the given service; when False, only write the file (used
    when a clean redeploy will read the config anyway). Returns True if it wrote a change."""
    import json
    import subprocess
    if not overrides:
        return False
    if not path.exists():
        logger.warning("config not found: %s; skipping", path)
        return False
    text = path.read_text()
    cur = json.loads(text)
    changed = []
    for k, v in overrides.items():
        if _find_key(cur, k) != v:
            text = _set_json_val(text, k, v)
            changed.append(f"{k}={v}")
    if not changed:
        logger.info("%s: already up to date (%s)", path.name, list(overrides))
        return False
    path.write_text(text)
    if not recreate:
        logger.info("updated %s [%s] (no recreate; clean redeploy will load it)",
                    path.name, ", ".join(changed))
        return True
    logger.info("updated %s [%s]; recreating %s...", path.name, ", ".join(changed), recreate_target)
    subprocess.run(["python3", str(_DEPLOY_DIR / "oneclick_dc_deployment.py"),
                    "recreate", recreate_target], cwd=str(_DEPLOY_DIR), check=False, timeout=400)
    return True


def apply_vst_config(overrides: dict, recreate: bool = True) -> bool:
    """Apply overrides to the VIOS vst_config.json and recreate streamprocessing."""
    return _apply_config(_VST_CONFIG, overrides, "streamprocessing", recreate)


def apply_nvstreamer_config(overrides: dict, recreate: bool = True) -> bool:
    """Apply overrides to the NVStreamer vst_config.json and recreate nvstreamer."""
    return _apply_config(_NVS_CONFIG, overrides, "nvstreamer", recreate)


def apply_vst_notification_config(overrides: dict, recreate: bool = True) -> bool:
    """Apply broker/notification overrides for VIOS and recreate streamprocessing."""
    return _apply_config(_VST_NOTIFICATION_CONFIG, overrides, "streamprocessing", recreate)


def configure_overlay_webhooks(enabled: bool, host: str = "127.0.0.1",
                               port: int = 18088) -> bool:
    """Enable exactly the overlay plugin's streaming/remove webhook receivers.

    The VIOS containers use host networking, so the loopback endpoint reaches the
    host-side sanity plugin without depending on Redis, SDRC, or Docker bridge DNS.
    Other webhook definitions stay present but disabled.
    """
    import json

    if not _VST_NOTIFICATION_CONFIG.exists():
        logger.warning("notification config not found: %s", _VST_NOTIFICATION_CONFIG)
        return False
    config = json.loads(_VST_NOTIFICATION_CONFIG.read_text())
    webhooks = config.setdefault("webhooks", {})
    webhooks["enabled"] = bool(enabled)
    found = set()
    methods = {"camera_streaming": "PUT", "camera_remove": "DELETE"}
    paths = {
        "camera_streaming": "/bdd/webhooks/camera/streaming",
        "camera_remove": "/bdd/webhooks/camera/remove",
    }
    for item in webhooks.get("items", []):
        change = item.get("camera_status_change")
        active = bool(enabled and change in methods)
        item["enabled"] = active
        if not active:
            continue
        found.add(change)
        item["id"] = f"sanity-overlay-{change.replace('_', '-')}"
        item.pop("auth", None)
        for request in item.get("request", []):
            request["url"] = f"http://{host}:{port}{paths[change]}"
            request["method"] = methods[change]
            request["camera_type"] = ["rtsp", "file"]
    missing = set(methods) - found if enabled else set()
    if missing:
        raise ValueError(f"notification_config.json missing webhook item(s): {sorted(missing)}")
    _VST_NOTIFICATION_CONFIG.write_text(json.dumps(config, indent=2) + "\n")
    logger.info("overlay webhooks %s at http://%s:%d", "enabled" if enabled else "disabled",
                host, port)
    return True


def apply_nvstreamer_notification_config(overrides: dict, recreate: bool = True) -> bool:
    """Apply broker/notification overrides for NVStreamer and recreate nvstreamer."""
    return _apply_config(_NVS_NOTIFICATION_CONFIG, overrides, "nvstreamer", recreate)


# ---- deployment env (compose.env) so images/host/paths are set implicitly, not by hand ----
_COMPOSE_ENV = _DEPLOY_DIR / "docker-compose/compose.env"                     # VIOS + sensor
_NVS_COMPOSE_ENV = _DEPLOY_DIR / "docker-compose/nvstreamer/compose.env"      # NVStreamer


def _write_env(path, updates: dict) -> bool:
    """Set KEY=value lines in a compose.env, preserving everything else (and dropping any
    trailing '### change me' on a replaced line). Appends any key that isn't present."""
    import re
    if not path.exists():
        logger.warning("env not found: %s; skipping", path)
        return False
    text = path.read_text()
    for k, v in updates.items():
        pat = rf'(?m)^(\s*{re.escape(k)}\s*=).*$'
        text, n = re.subn(pat, lambda m: f"{m.group(1)}{v}", text, count=1)
        if n == 0:
            text = text.rstrip("\n") + f"\n{k}={v}\n"
    path.write_text(text)
    logger.info("updated %s [%s]", path.name, ", ".join(updates))
    return True


def apply_deployment_mode(mode: str) -> bool:
    """Select the shipped SDRC/direct compose.env toggle block for one sanity plan."""
    mode = str(mode or "sdrc").strip().lower()
    if mode not in {"sdrc", "direct"}:
        raise ValueError(f"unsupported deployment_mode={mode!r}; expected 'sdrc' or 'direct'")
    if not _COMPOSE_ENV.exists():
        raise FileNotFoundError(_COMPOSE_ENV)

    lines = _COMPOSE_ENV.read_text().splitlines()
    start = next((i for i, line in enumerate(lines)
                  if "DIRECT MODE" in line and "---" in line), None)
    end = next((i for i in range((start or 0) + 1, len(lines))
                if lines[i].lstrip().startswith("###")), None)
    if start is None or end is None:
        raise ValueError("compose.env deployment-mode toggle block not found")
    if mode == "direct":
        block = [
            "# ---------- DIRECT MODE (default) ----------",
            "VST_USE_SDRC=false",
            "NGINX_MODE=vst",
            "STREAM_PROCESSOR_MODULE_ENDPOINT=http://${HOST_IP}:30001",
            "",
            "",
            "# ---------- SDRC MODE (uncomment below to use this mode) ----------",
            "#VST_USE_SDRC=true",
            "#COMPOSE_PROFILES=sdrc",
            "#NGINX_MODE=vst-sdrc",
            "#STREAM_PROCESSOR_MODULE_ENDPOINT=http://${HOST_IP}:10000",
        ]
    else:
        block = [
            "# ---------- DIRECT MODE (uncomment below to use this mode) ----------",
            "#VST_USE_SDRC=false",
            "#NGINX_MODE=vst",
            "#STREAM_PROCESSOR_MODULE_ENDPOINT=http://${HOST_IP}:30001",
            "",
            "",
            "# ---------- SDRC MODE (default) ----------",
            "VST_USE_SDRC=true",
            "COMPOSE_PROFILES=sdrc",
            "NGINX_MODE=vst-sdrc",
            "STREAM_PROCESSOR_MODULE_ENDPOINT=http://${HOST_IP}:10000",
        ]
    _COMPOSE_ENV.write_text("\n".join(lines[:start] + block + [""] + lines[end:]) + "\n")
    logger.info("deployment mode configured: %s", mode)
    return True


def apply_images(streamprocessing: str = None, sensor: str = None,
                 nvstreamer: str = None, pull: bool = True) -> None:
    """Point the deployment at the given container images -- write them into compose.env
    (+ nvstreamer compose.env) and docker-pull them. Any of the three may be None (unchanged).
    Lets a caller pass just the images instead of hand-editing compose.env."""
    import subprocess
    main_upd = {}
    if streamprocessing:
        main_upd["VST_STREAM_PROCESSOR_IMAGE"] = streamprocessing
    if sensor:
        main_upd["VST_SENSOR_IMAGE"] = sensor
    if main_upd:
        _write_env(_COMPOSE_ENV, main_upd)
    if nvstreamer:
        _write_env(_NVS_COMPOSE_ENV, {"NVSTREAMER_IMAGE": nvstreamer})
    if pull:
        for img in [i for i in (streamprocessing, sensor, nvstreamer) if i]:
            logger.info("docker pull %s ...", img)
            subprocess.run(["docker", "pull", img], check=False, timeout=1800)


def apply_deploy_env(host_ip: str = "") -> None:
    """Set the host-specific compose.env values implicitly so the user never edits them:
    HOST_IP (auto-detected if not given) and the repo-local VST_CONFIG_PATH / VST_VOLUME."""
    from sanity_common import _default_host_ip
    ip = host_ip or _default_host_ip()
    cc = _DEPLOY_DIR / "docker-compose"
    _write_env(_COMPOSE_ENV, {"HOST_IP": ip,
                              "VST_CONFIG_PATH": str(cc / "configs"),
                              "VST_VOLUME": str(cc / "vst_volume")})


# Adaptor names in adaptor_config.json that DISCOVER cameras -- only one should be enabled
# at a time so VIOS doesn't attach the same cameras via two adaptors.
_DISCOVERY_ADAPTORS = {"onvif", "milestone_onvif", "milestone_soap", "remote", "test_vms"}


def apply_adaptor_config(adaptor: str, setup: dict) -> bool:
    """Enable the requested VMS adaptor in adaptor_config.json (disabling the other discovery
    adaptors) and apply the plan's server config. `adaptor='milestone'` -> the `milestone_onvif`
    entry (type mms) with ip/user/password/port from `setup`; `adaptor='onvif'` -> the `onvif`
    entry (network discovery). For milestone also sets always_recording + ai_bridge_endpoint in
    vst_config.json (Milestone owns recording; no VIOS always-record, no AI bridge)."""
    import json
    target = "milestone_onvif" if adaptor == "milestone" else "onvif"
    try:
        cfg = json.loads(_ADAPTOR_CONFIG.read_text())
    except Exception as e:  # noqa: BLE001
        logger.warning("adaptor_config read failed: %s", e)
        return False
    found = False
    for e in cfg.get("vst", []):
        if not isinstance(e, dict):
            continue
        nm = e.get("name")
        if nm == target:
            e["enabled"] = True
            found = True
            for k in ("ip", "user", "password", "port"):
                if setup.get(k) is not None:
                    e[k] = str(setup[k])
        elif nm in _DISCOVERY_ADAPTORS:
            e["enabled"] = False
    _ADAPTOR_CONFIG.write_text(json.dumps(cfg, indent=2))
    logger.info("adaptor_config: enabled '%s' (adaptor=%s)%s, disabled other discovery adaptors",
                target, adaptor, "" if found else " [WARN: entry not found]")
    if adaptor == "milestone":
        apply_vst_config({"always_recording": bool(setup.get("always_recording", False)),
                          "ai_bridge_endpoint": setup.get("ai_bridge_endpoint", "")}, recreate=False)
    return found


def discover_online_cameras(base_url: str, verify_ssl: bool = False, timeout: int = 180) -> List[str]:
    """Poll VIOS until its adaptor has discovered ONLINE cameras (Milestone connect / ONVIF
    network discovery take time). Returns the online camera sensorIds."""
    deadline = time.time() + timeout
    last = []
    while time.time() < deadline:
        try:
            lst = requests.get(f"{base_url}/vst/api/v1/sensor/list", timeout=20, verify=verify_ssl).json() or []
            def _online(s):
                st = str(s.get("status", "")).lower()
                return ("online" in st) or s.get("status") in (0, "SensorStatusOnline", "online", True)
            online = [(s.get("sensorId") or s.get("name")) for s in lst
                      if (s.get("sensorId") or s.get("name")) and _online(s)]
            last = [(s.get("sensorId") or s.get("name")) for s in lst if (s.get("sensorId") or s.get("name"))]
            if online:
                logger.info("discovered %d online camera(s): %s", len(online), online)
                return online
        except Exception as e:  # noqa: BLE001
            logger.warning("camera discovery poll failed: %s", e)
        time.sleep(5)
    logger.warning("no ONLINE cameras after %ds (saw %d total: %s)", timeout, len(last), last)
    return last


def verify_nvstreamer_stream_count(nvstreamer_url: str, expected: int, timeout: int = 90) -> bool:
    """Wait until NVStreamer serves EXACTLY `expected` streams (no more, no fewer) before
    VIOS attaches. Critical for the synchronized video-wall (nv_streamer_sync_file_count):
    any leftover/extra stream desynchronizes the wall. Returns True when it matches."""
    import time as _t
    deadline = _t.time() + timeout
    last = -1
    while _t.time() < deadline:
        try:
            lst = requests.get(f"{nvstreamer_url}/api/v1/sensor/list", timeout=15).json() or []
            n = len(lst if isinstance(lst, list) else lst.get("sensors", []))
            last = n
            if n == expected:
                logger.info("NVStreamer serving exactly %d stream(s) -- OK for sync wall", n)
                return True
        except Exception:  # noqa: BLE001
            pass
        _t.sleep(3)
    logger.warning("NVStreamer stream count = %d (expected %d) after %ds -- wall may desync",
                   last, expected, timeout)
    return False


def delete_all_vios_sensors(base_url: str, verify_ssl: bool = False) -> int:
    """Delete every sensor registered in VIOS, for clean per-plan isolation WITHOUT a
    volume-wiping --clean (which would drop recordings the historical use-cases need).
    The stack stays warm, so RTSP recording resumes immediately for the next plan's
    freshly-provisioned streams. Returns the count deleted."""
    import requests
    n = 0
    try:
        lst = requests.get(f"{base_url}/vst/api/v1/sensor/list", timeout=20, verify=verify_ssl).json() or []
    except Exception as e:  # noqa: BLE001
        logger.warning("sensor list for cleanup failed: %s", e)
        return 0
    for s in lst:
        sid = s.get("sensorId") or s.get("name")
        if not sid:
            continue
        try:
            requests.delete(f"{base_url}/vst/api/v1/sensor/{sid}", timeout=30, verify=verify_ssl)
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("delete sensor %s failed: %s", sid, e)
    logger.info("deleted %d prior VIOS sensor(s) for a clean plan slate", n)
    return n


def clean_stop(timeout: int = 700) -> bool:
    """Stop the stack and wipe service-owned DB, recordings, and runtime metadata.

    The deployment tool deliberately prompts separately before deleting its shared videos
    directory because it can contain UI uploads. Answer No there; the sanity caller removes
    only files matching its own input prefix via ``wipe_nvstreamer_videos``.
    """
    import subprocess
    oneclick = str(_DEPLOY_DIR / "oneclick_dc_deployment.py")
    logger.info("clean stop: stop all --clean (preserving shared/UI-uploaded videos) ...")
    result = subprocess.run(["python3", oneclick, "stop", "all", "--clean"],
                            input="n\n", text=True, cwd=str(_DEPLOY_DIR), check=False,
                            timeout=timeout)
    if result.returncode:
        logger.warning("clean stop exited %d", result.returncode)
    return result.returncode == 0


def deploy_target(target: str, timeout: int = 700) -> bool:
    """Deploy ONE deployment target ('nvstreamer' | 'vst' | 'all'). The per-plan policy
    deploys NVStreamer FIRST, provisions + verifies its RTSP sources, and only THEN deploys
    'vst' -- so VIOS's RTSP client attaches to an NVStreamer that is already streaming.
    Deploying VIOS into an empty NVStreamer makes its RTSP connects hit onDataTimeout and
    exhaust the reconnect retries ('Max limit reached'), leaving the streams with no
    recording for the rest of the plan."""
    import subprocess
    oneclick = str(_DEPLOY_DIR / "oneclick_dc_deployment.py")
    logger.info("deploy target: %s ...", target)
    cmd = ["python3", oneclick, "deploy"]
    if target and target != "vst":            # 'vst' is the default deploy target
        cmd += ["--target", target]
    r = subprocess.run(cmd, cwd=str(_DEPLOY_DIR), check=False, timeout=timeout)
    ok = r.returncode == 0
    logger.info("deploy target %s: %s", target, "ok" if ok else "FAILED")
    return ok


def clean_redeploy(timeout: int = 700) -> bool:
    """[legacy] Stop --clean then deploy the whole stack at once. Superseded by the
    NVStreamer-first sequence (clean_stop -> deploy_target('nvstreamer') -> provision ->
    deploy_target('vst')); kept for callers that still deploy both together."""
    import subprocess
    oneclick = str(_DEPLOY_DIR / "oneclick_dc_deployment.py")
    clean_stop(timeout)
    logger.info("clean redeploy: deploy all ...")
    r = subprocess.run(["python3", oneclick, "deploy", "all"],
                       cwd=str(_DEPLOY_DIR), check=False, timeout=timeout)
    ok = r.returncode == 0
    logger.info("clean redeploy: %s", "ok" if ok else "FAILED")
    return ok


def recreate_service(target: str) -> None:
    """Recreate a deployment service (e.g. 'streamprocessing' | 'nvstreamer')."""
    import subprocess
    subprocess.run(["python3", str(_DEPLOY_DIR / "oneclick_dc_deployment.py"),
                    "recreate", target], cwd=str(_DEPLOY_DIR), check=False, timeout=400)


_CONFIG_FILES = [
    _VST_CONFIG,
    _NVS_CONFIG,
    _VST_NOTIFICATION_CONFIG,
    _NVS_NOTIFICATION_CONFIG,
    _ADAPTOR_CONFIG,
    _COMPOSE_ENV,
]


def backup_configs() -> None:
    """Snapshot the VIOS + NVStreamer config files as they are BEFORE the sanity
    mutates them, so restore_configs() can put them back (leave the box as found)."""
    for f in _CONFIG_FILES:
        if f.exists():
            shutil.copy(f, f.with_suffix(f.suffix + ".sanity-bak"))
    logger.info("backed up VIOS + NVStreamer configs (.sanity-bak)")


def restore_configs(recreate: bool = True) -> None:
    """Restore the config files the sanity snapshotted, and recreate the services so
    the box is left exactly as it was found. Always call this when the sanity is done."""
    restored = []
    for f in _CONFIG_FILES:
        bak = f.with_suffix(f.suffix + ".sanity-bak")
        if bak.exists():
            shutil.copy(bak, f)
            bak.unlink()
            restored.append(f.name)
    if not restored:
        return
    logger.info("restored configs: %s", restored)
    if recreate:
        recreate_service("streamprocessing")
        recreate_service("nvstreamer")


_NVS_VIDEOS = _DEPLOY_DIR / "docker-compose/nvstreamer/videos"


def wipe_nvstreamer_videos(video_path=None) -> int:
    """Remove only NVStreamer files owned by this sanity input.

    A source file owns its exact stem plus generated ``<stem>_*`` variants/copies. A source
    directory owns the corresponding set of video stems. Unrelated files and UI uploads are
    preserved. With no input path, nothing is removed.
    """
    if not video_path:
        logger.info("no sanity input supplied; preserving all NVStreamer served videos")
        return 0
    source = Path(video_path)
    if source.is_dir():
        prefixes = {x.stem for x in source.iterdir()
                    if x.is_file() and x.suffix.lower() in (".mp4", ".mkv")}
    else:
        prefixes = {source.stem}
    removed = 0
    try:
        for item in _NVS_VIDEOS.glob("**/*"):
            owned = any(item.stem == prefix or item.stem.startswith(prefix + "_")
                        for prefix in prefixes)
            if item.is_file() and item.suffix.lower() in (".mp4", ".mkv") and owned:
                item.unlink()
                removed += 1
    except Exception as e:  # noqa: BLE001
        logger.warning("targeted NVStreamer video cleanup failed: %s", e)
    logger.info("removed %d sanity-owned NVStreamer video(s) for prefixes=%s; "
                "unrelated/UI videos preserved", removed, sorted(prefixes))
    return removed


def clean_nvstreamer_videos(nvstreamer_url: str = "http://localhost:31000",
                               video_path=None) -> None:
    """[legacy warm-path] Wipe served videos AND recreate NVStreamer so its registry
    reloads empty. The NVStreamer-first policy instead wipes before a fresh deploy
    (wipe_nvstreamer_videos), so no in-place recreate is needed."""
    wipe_nvstreamer_videos(video_path)
    recreate_service("nvstreamer")            # reload registry from the now-empty dir
    _wait_ready(f"{nvstreamer_url}/vst/api/v1/sensor/list", 90)


def apply_consumer(consumer: str, broker_addr: str = "172.17.0.1:9092",
                   topic: str = "vst-overlay-test") -> bool:
    """Point the VST live-metadata consumer at kafka|redis (compat wrapper)."""
    over = {"use_message_broker_consumer": consumer, "enable_notification_consumer": True,
            "message_broker_topic_consumer": topic}
    if consumer == "kafka":
        over["kafka_server_address"] = broker_addr
    return apply_vst_notification_config(over)


def _wait_ready(url: str, timeout: int = 90, verify_ssl: bool = False) -> bool:
    """Poll a URL until it answers 200 (a recreated container reports 'healthy'
    before its API actually accepts connections)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=5, verify=verify_ssl).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    logger.warning("service not ready after %ss: %s", timeout, url)
    return False


def _wait_recording_current(base_url: str, sensor: str, verify_ssl: bool = False,
                            timeout: int = 90, tol_s: int = 5, min_span_s: int = 15) -> bool:
    """Wait until the sensor's latest recording segment is continuous up to ~now
    (endTime within tol_s of now AND at least min_span_s long), so a recent/live-tail
    window falls inside one gap-free segment."""
    def _ms(s):
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(d.timestamp() * 1000)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            tl = requests.get(f"{base_url}/vst/api/v1/storage/timelines",
                              timeout=15, verify=verify_ssl).json()
            rs = (tl or {}).get(sensor) or []
            if rs:
                end, start = _ms(rs[-1]["endTime"]), _ms(rs[-1]["startTime"])
                gap = time.time() * 1000 - end
                if gap < tol_s * 1000 and (end - start) >= min_span_s * 1000:
                    logger.info("recording current for %s (gap %.1fs, span %.1fs)",
                                sensor, gap / 1000, (end - start) / 1000)
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(4)
    logger.warning("recording not continuous-to-now for %s after %ss", sensor, timeout)
    return False


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"


def _sensor_ids(base_url: str, verify_ssl: bool) -> List[str]:
    r = requests.get(f"{base_url}/vst/api/v1/sensor/streams", timeout=30, verify=verify_ssl)
    r.raise_for_status()
    ids = []
    for entry in r.json() or []:
        ids.extend(entry.keys())
    return ids


def _iter_stream_entries(streams):
    """Yield each stream record from NVStreamer /sensor/streams ([{streamId:[..]}])."""
    if isinstance(streams, dict):
        streams = [streams]
    for item in (streams or []):
        if isinstance(item, dict):
            for _sid, arr in item.items():
                for e in (arr or []):
                    if isinstance(e, dict):
                        yield e


def _vios_name_to_id(ctx: SanityContext):
    """{sensor name -> VIOS sensorId} for the current VIOS sensors."""
    try:
        d = requests.get(f"{ctx.base_url}/vst/api/v1/sensor/list", timeout=30,
                         verify=ctx.verify_ssl).json()
        return {s.get("name"): (s.get("sensorId") or s.get("name"))
                for s in (d or []) if s.get("name")}
    except Exception as e:  # noqa: BLE001
        logger.warning("VIOS sensor list failed: %s", e)
        return {}


def _ensure_streams_registered(ctx: SanityContext, expected_names, nvstreamer_url):
    """Ensure each provisioned NVStreamer RTSP stream is registered in VIOS. Uses the
    auto-discovery result if present (rtsp_streams.json Nvstreamer.enabled=true), and
    EXPLICITLY POSTs /sensor/add for any missing (works when it is disabled too).
    Ignores file sensors. Returns {name -> VIOS sensorId}."""
    urls = {}
    try:
        streams = requests.get(f"{nvstreamer_url}/vst/api/v1/sensor/streams", timeout=20,
                               verify=ctx.verify_ssl).json()
        for e in _iter_stream_entries(streams):
            n, u = e.get("name"), e.get("url", "")
            if n and u.startswith("rtsp://"):
                urls[n] = u
    except Exception as e:  # noqa: BLE001
        logger.warning("NVStreamer /sensor/streams fetch failed: %s", e)
    have = _vios_name_to_id(ctx)
    out = {}
    for name in expected_names:
        if name in have:
            out[name] = have[name]
            continue
        url = urls.get(name)
        if not url:
            logger.warning("no RTSP url for %s; cannot register in VIOS", name)
            continue
        try:
            r = requests.post(f"{ctx.base_url}/vst/api/v1/sensor/add",
                              json={"sensorUrl": url, "name": name}, timeout=60, verify=ctx.verify_ssl)
            if r.status_code == 200 or "exists" in r.text:
                sid = None
                if r.status_code == 200:
                    try:
                        sid = (r.json() or {}).get("sensorId")
                    except Exception:  # noqa: BLE001
                        sid = None
                out[name] = sid or _vios_name_to_id(ctx).get(name, name)
                logger.info("registered RTSP %s -> %s (explicit add)", name, out[name])
            else:
                logger.warning("sensor/add %s -> %s %s", name, r.status_code, r.text[:100])
        except Exception as ex:  # noqa: BLE001
            logger.warning("sensor/add %s failed: %s", name, ex)
    return out


def _videos_in(path: Path):
    exts = (".mp4", ".mkv")
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in exts)
    return [path] if path.is_file() else []


# Plan-1 variant set generated from a single video: 6 RTSP + 2 file sensors, 30s,
# spanning codec (h264/h265) x resolution (480p/720p/1080p/4K) x fps (10/30). Files
# are named descriptively (e.g. <stem>_h264_1080p_30fps.mp4) so they self-document.
#   (codec, width, height, fps, is_file)
VARIANT_SET = [
    ("h264", 1920, 1080, 30, False),
    ("h265", 1920, 1080, 30, False),
    ("h264",  640,  480, 30, False),
    ("h265", 3840, 2160, 10, False),
    ("h264", 1920, 1080, 10, False),
    ("h265", 1280,  720, 30, False),
    ("h264", 1920, 1080, 30, True),
    ("h265", 1920, 1080, 30, True),
]

_RES_LABEL = {(1920, 1080): "1080p", (1280, 720): "720p", (640, 480): "480p", (3840, 2160): "4k"}


def _variant_name(stem: str, codec: str, w: int, h: int, fps: int, is_file: bool) -> str:
    label = _RES_LABEL.get((w, h), f"{w}x{h}")
    return f"{stem}_{codec}_{label}_{fps}fps" + ("_file" if is_file else "")


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _nvenc_available() -> bool:
    import subprocess
    for encoder in ("h264_nvenc", "hevc_nvenc"):
        try:
            probe = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "lavfi", "-i", "color=c=black:s=640x480:r=1",
                    "-frames:v", "1", "-an", "-c:v", encoder, "-preset", "p1",
                    "-f", "null", "-",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("NVENC probe failed for %s: %s", encoder, exc)
            return False
        if probe.returncode != 0:
            logger.info("NVENC probe failed for %s: %s", encoder, probe.stderr[:160])
            return False
    return True


def _make_variant(src: Path, dst: Path, codec: str, w: int, h: int, fps: int,
                  dur: int, use_nvenc: bool) -> bool:
    """Transcode a `dur`-second variant. bframes=0 + keyint=30 on every command.
    NVENC (GPU) when available (~1-4s each on an RTX); CPU ultrafast fallback.

    The `fps` filter runs BEFORE `drawtext=%{n}` so the burned-in number is the OUTPUT
    frame index (0,1,2,... at this variant's fps), not the source frame index. That makes
    the overlay objectId (= frameId mod FrameCount) match the burned number exactly on
    every frame for ANY fps -- no source-fps/scale assumptions. (If drawtext ran before
    the rate change, %{n} would count source frames and downsampled variants would drift.)
    Give a source WITHOUT its own burn so each variant shows just this one number."""
    import subprocess
    vf = (f"scale={w}:{h},fps={fps},"
          f"drawtext=text='%{{n}}':x=14:y=12:fontsize=48:fontcolor=white:box=1:boxcolor=black@0.5:borderw=2")
    common = ["ffmpeg", "-y", "-v", "error", "-t", str(dur), "-i", str(src),
              "-vf", vf, "-an"]
    if use_nvenc:
        enc = "h264_nvenc" if codec == "h264" else "hevc_nvenc"
        cmd = common + ["-c:v", enc, "-preset", "p1", "-bf", "0", "-g", "30", str(dst)]
    else:
        enc = "libx264" if codec == "h264" else "libx265"
        pflag = "-x264-params" if codec == "h264" else "-x265-params"
        cmd = common + ["-c:v", enc, "-preset", "ultrafast", pflag,
                        "bframes=0:keyint=30", str(dst)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            logger.warning("variant encode failed (%s): %s", dst.name, r.stderr[:160])
            return False
        return dst.exists() and dst.stat().st_size > 0
    except Exception as e:  # noqa: BLE001
        logger.warning("variant encode error (%s): %s", dst.name, e)
        return False


def _burn_counter(src: Path, dst: Path) -> bool:
    """Burn a per-frame counter (`drawtext=%{n}`) onto `src` keeping its resolution/fps,
    for the sync-wall copies -- so they carry the same frame-number overlay the variants do
    (objId verification) while staying byte-identical after the copy. Returns False if the
    burn fails (ffmpeg/drawtext missing) so the caller can fall back to a plain copy."""
    import subprocess
    vf = "drawtext=text='%{n}':x=14:y=12:fontsize=48:fontcolor=white:box=1:boxcolor=black@0.5:borderw=2"
    try:
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf,
                            "-c:v", "libx264", "-preset", "ultrafast", "-an", str(dst)],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            logger.warning("frame-counter burn failed: %s", r.stderr[:160])
            return False
        return dst.exists() and dst.stat().st_size > 0
    except Exception as e:  # noqa: BLE001
        logger.warning("frame-counter burn error: %s", e)
        return False


def _upload_file_sensor(ctx: SanityContext, video: Path, sensor_name: str, retries: int = 12):
    """PUT a video to VST as a file-backed sensor; adopt it if it already exists.

    File uploads flow ingress -> sensor-MS. Right after a fresh 'vst' deploy the ingress
    answers /health (200) while sensor-MS is still warming up, so the upload briefly gets a
    502/503 ('upstream not ready'). Retry through that window instead of dropping the file
    sensor for the whole plan."""
    file_sensor = None
    url = f"{ctx.base_url}/vst/api/v1/storage/file/{sensor_name}.mp4"
    payload = video.read_bytes()
    for attempt in range(1, retries + 1):
        try:
            resp = requests.put(url, params={"timestamp": _iso_now(), "sensorId": sensor_name},
                                data=payload, headers={"Content-Type": "application/octet-stream"},
                                timeout=180, verify=ctx.verify_ssl)
            if resp.status_code in (200, 201):
                file_sensor = (resp.json() or {}).get("sensorId") or sensor_name
                logger.info("VST file sensor uploaded: %s%s", file_sensor,
                            f" (attempt {attempt})" if attempt > 1 else "")
                break
            if resp.status_code in (502, 503) and attempt < retries:
                logger.info("VST file upload %s -> %s (sensor-MS warming up), retry %d/%d",
                            sensor_name, resp.status_code, attempt, retries)
                time.sleep(5)
                continue
            logger.warning("VST file upload %s -> %s %s", sensor_name, resp.status_code, resp.text[:120])
            break
        except Exception as e:  # noqa: BLE001
            if attempt < retries:
                logger.info("VST file upload %s errored (%s), retry %d/%d", sensor_name, e, attempt, retries)
                time.sleep(5)
                continue
            logger.warning("VST file upload %s failed: %s", sensor_name, e)
    if not file_sensor:
        try:
            if sensor_name in _sensor_ids(ctx.base_url, ctx.verify_ssl):
                file_sensor = sensor_name
                logger.info("VST file sensor already present, adopting: %s", file_sensor)
        except Exception as e:  # noqa: BLE001
            logger.warning("file-sensor lookup failed: %s", e)
    return file_sensor


def _probe_res(video: Path):
    """(width, height) of a video via ffprobe; (1920, 1080) on failure."""
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                              str(video)], capture_output=True, text=True, timeout=20).stdout.strip()
        w, h = out.split("x")
        return int(w), int(h)
    except Exception as e:  # noqa: BLE001
        logger.warning("ffprobe resolution failed for %s: %s", video, e)
        return 1920, 1080


def provision(ctx: SanityContext, video_path: Path, n_copies: int = 4,
              sync_wall: bool = False, max_streams: int = 4, variants: bool = False,
              variant_dur: int = 30,
              nvstreamer_url: str = "http://localhost:31000",
              deploy_vios=None) -> dict:
    """Provision NVStreamer RTSP sources + VST file sensor(s) from a video path.

    Two-phase, so VIOS is brought up against an already-streaming NVStreamer:
      1. Generate + upload every RTSP source to NVStreamer (VIOS need not be up yet).
      2. Call ``deploy_vios(copy_names)`` (optional) -- the caller verifies NVStreamer is
         serving those streams, then deploys VIOS. Deploying VIOS into an *empty*
         NVStreamer makes its RTSP client hit onDataTimeout and burn its reconnect retries
         -> no recording for the plan.
      3. With VIOS up + NVStreamer serving: scan/register the RTSP streams and upload the
         file sensors.

    Modes (first match wins):
      * variants=True + a single file + ffmpeg present -> generate a diverse set
        (6 RTSP + 2 file sensors, `variant_dur`s, codec/res/fps matrix). Uses CPU
        encoding when NVENC is unavailable, and falls back to identical copies only
        when ffmpeg is unavailable.
      * sync_wall=True OR a single file -> N identical copies <stem>_1..N (uniform
        durations, needed for a synchronized wall).
      * directory with >=2 videos -> the first `max_streams` used as-is (no convert).

    `max_streams` caps directory provisioning (VST streams only 8 webrtc-out live
    views; an NVStreamer instance tops out ~100). Returns
    {'rtsp_streams': [ids], 'file_sensor': id-or-None, 'stream_res': {id:(w,h)}}.
    """
    video_path = Path(video_path)
    vids = _videos_in(video_path)
    if not vids:
        raise FileNotFoundError(f"no .mp4/.mkv videos at {video_path}")
    base = vids[0]
    stem = base.stem
    ctx.stream_id = stem
    work = ctx.out_dir / "provision"
    work.mkdir(parents=True, exist_ok=True)

    # A just-deployed NVStreamer reports healthy before its API is up; wait for it.
    _wait_ready(f"{nvstreamer_url}/vst/api/v1/sensor/list", 120)

    copy_names = []
    res_map = {}                      # sensor_id -> (w, h) source resolution
    base_res = _probe_res(base)
    file_sensor = None
    file_variants = []                # (dst, name, w, h) uploaded AFTER VIOS is up

    variants_mode = variants and not sync_wall and len(vids) == 1 and _has_ffmpeg()
    if variants and not variants_mode and len(vids) == 1 and not sync_wall:
        logger.warning("variants requested but ffmpeg unavailable; falling back to identical copies")

    # ---- PHASE 1: RTSP sources -> NVStreamer (no VIOS calls yet) -------------------
    if variants_mode:
        use_nvenc = _nvenc_available()
        logger.info("provisioning mode: variants (%s, %ds) -> %d RTSP + %d file",
                    "NVENC" if use_nvenc else "CPU", variant_dur,
                    sum(1 for v in VARIANT_SET if not v[4]), sum(1 for v in VARIANT_SET if v[4]))
        for codec, w, h, fps, is_file in VARIANT_SET:
            name = _variant_name(stem, codec, w, h, fps, is_file)
            dst = work / f"{name}.mp4"
            if not _make_variant(base, dst, codec, w, h, fps, variant_dur, use_nvenc):
                continue
            if is_file:
                file_variants.append((dst, name, w, h))   # deferred to phase 3 (needs VIOS)
            else:
                ok = upload_video(nvstreamer_url, dst, _iso_now())
                logger.info("NVStreamer upload %s (%s %dx%d @%dfps) -> %s",
                            name, codec, w, h, fps, "ok" if ok else "FAILED")
                copy_names.append(name)
                res_map[name] = (w, h)
    elif sync_wall or len(vids) < 2:
        # N identical copies -> NVStreamer (uniform RTSP sources). Burn the frame counter
        # ONCE, then copy, so every copy is byte-identical (stays synchronized for the wall)
        # AND shows the burned frame number for objId verification -- same as the variants.
        burned = work / f"{stem}_burned.mp4"
        src_copy = burned if _burn_counter(base, burned) else base
        if src_copy is base:
            logger.warning("frame-counter burn unavailable; sync copies will have no burned number")
        for i in range(1, n_copies + 1):
            dst = work / f"{stem}_{i}.mp4"
            shutil.copy(src_copy, dst)
            ok = upload_video(nvstreamer_url, dst, _iso_now())
            logger.info("NVStreamer upload %s -> %s", dst.name, "ok" if ok else "FAILED")
            copy_names.append(f"{stem}_{i}")
            res_map[f"{stem}_{i}"] = base_res
        logger.info("provisioning mode: %d identical copies%s @ %dx%d",
                    n_copies, " (sync wall)" if sync_wall else "", *base_res)
    else:
        # Use the provided videos as the set (capped at max_streams).
        selected = vids[:max(1, max_streams)]
        if len(vids) > len(selected):
            logger.warning("video set: %d videos found, provisioning first %d (max_streams); "
                           "%d skipped", len(vids), len(selected), len(vids) - len(selected))
        for v in selected:
            ok = upload_video(nvstreamer_url, v, _iso_now())
            vr = _probe_res(v)
            logger.info("NVStreamer upload %s -> %s @ %dx%d", v.name, "ok" if ok else "FAILED", *vr)
            copy_names.append(v.stem)
            res_map[v.stem] = vr
        logger.info("provisioning mode: video set of %d videos", len(selected))

    # ---- PHASE 2: NVStreamer now serves the RTSP sources. Bring VIOS up AGAINST it. -
    if deploy_vios is not None:
        deploy_vios(copy_names)

    # ---- PHASE 3: VIOS up + NVStreamer serving -> file sensors + map RTSP ids ----------
    # NOTE: do NOT call POST /sensor/scan here. Because we bring VIOS up only AFTER every
    # NVStreamer stream is serving (NVStreamer-first), VIOS auto-discovers the RTSP streams
    # on startup (rtsp_streams.json Nvstreamer.enabled=true). An extra scan re-runs
    # getAndAddProxyUrl -> re-sends camera_proxy -> resets stream_status STREAMING(2) back to
    # PROXY(4), and live webrtc's checkStreamSanity then rejects it ("Streaming not ready").
    if variants_mode:
        for dst, name, w, h in file_variants:
            fs = _upload_file_sensor(ctx, dst, name)
            if fs:
                res_map[fs] = (w, h)
                if file_sensor is None:          # first (h264) file sensor is the primary
                    file_sensor = fs
        file_sensor = file_sensor or _upload_file_sensor(ctx, base, f"{stem}_file")
    else:
        file_sensor = _upload_file_sensor(ctx, base, f"{stem}_file")

    # Ensure the NVStreamer RTSP streams are in VIOS (auto-discovery may be off) and
    # map their NVStreamer names to VIOS sensorIds (the id the APIs/metadata use).
    reg = _ensure_streams_registered(ctx, copy_names, nvstreamer_url)
    for name, sid in reg.items():
        if name in res_map and sid != name:
            res_map[sid] = res_map[name]          # key resolution by sensorId too
    rtsp = [reg[n] for n in copy_names if n in reg]
    if file_sensor and file_sensor not in res_map:
        res_map[file_sensor] = base_res
    ctx.stream_res.update(res_map)
    ctx.stream_names = {sid: name for name, sid in reg.items()}   # sensorId -> descriptive name
    logger.info("provisioned rtsp streams (sensorIds): %s ; file sensor: %s", rtsp, file_sensor)
    return {"rtsp_streams": rtsp, "file_sensor": file_sensor, "stream_res": res_map}

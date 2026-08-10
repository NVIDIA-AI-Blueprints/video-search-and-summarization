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
Orchestrate a VIOS+NVStreamer sanity run: drive each use-case, capture evidence,
and emit a PDF (snapshots + http links). Assumes a running, overlay-capable
deployment (VST reachable; metadata backends per SanityContext). Deployment /
reconfiguration is a separate concern (see the vios-sanity skill).

  python3 run_sanity.py --base-url http://localhost:30888 --broker redis \
      --stream-id warehouse_sample --out /tmp/vios_sanity/report.pdf
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sanity_common import SanityContext, run_usecase
from usecases import USECASES
from report import build_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sanity")

_RTSP_COPIES = 4   # identical copies made for single-file / sync_wall provisioning

# VIOS (in-container) reaches the host-side fake-ES over the docker bridge; matches the
# metadata_service --es-port and index. Wired into overlay.video_metadata_server so the
# download/replay overlay path queries it (the live/recent path uses the broker instead).
_FAKE_ES = "172.17.0.1:19200/mdx-bev-test*"
_MANAGED_KAFKA_NAME = "vios-sanity-kafka"
_MANAGED_KAFKA_IMAGE = "docker.redpanda.com/redpandadata/redpanda:v24.2.7"


def _broker_endpoint(broker_addr: str) -> tuple[str, int]:
    """Validate and split the single Kafka bootstrap endpoint used by a plan."""
    import socket

    host, separator, port = broker_addr.rpartition(":")
    if not separator or not host or not port.isdecimal():
        raise ValueError(
            "Kafka broker_addr must be a single host:port endpoint; "
            f"got {broker_addr!r}"
        )
    try:
        socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Kafka broker host cannot be resolved: {host!r}") from exc
    return host, int(port)


def _tcp_ready(host: str, port: int, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_kafka_broker(setup: dict, target: str) -> bool:
    """Start a local Kafka-compatible broker for a Kafka sanity plan when required.

    Existing or remote brokers are only checked and are never adopted, replaced, or stopped.
    The temporary Redpanda broker uses the Kafka wire protocol and host networking so that the
    advertised gateway endpoint is reachable from the VIOS compose containers.
    """
    import atexit
    import subprocess

    if setup.get("consumer") != "kafka":
        return False
    broker_addr = setup.get("broker_addr", "172.17.0.1:9092")
    host, port = _broker_endpoint(broker_addr)
    if _tcp_ready(host, port):
        log.info("Kafka prerequisite ready at %s", broker_addr)
        return False
    if target != "local" or not setup.get("manage_kafka", False):
        raise RuntimeError(
            f"Kafka prerequisite unavailable at {broker_addr}. Start a Kafka broker reachable "
            "from VIOS, or set setup.manage_kafka: true for a local sanity-only broker."
        )
    exists = subprocess.run(
        ["docker", "container", "inspect", _MANAGED_KAFKA_NAME],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0
    if exists:
        raise RuntimeError(
            f"Kafka prerequisite unavailable at {broker_addr}; container {_MANAGED_KAFKA_NAME!r} "
            "already exists and is not adopted by the sanity harness."
        )
    cmd = [
        "docker", "run", "--detach", "--rm", "--name", _MANAGED_KAFKA_NAME,
        "--network", "host", _MANAGED_KAFKA_IMAGE,
        "redpanda", "start", "--overprovisioned", "--smp", "1", "--memory", "1G",
        "--reserve-memory", "0M", "--node-id", "0", "--check=false",
        "--kafka-addr", f"0.0.0.0:{port}",
        "--advertise-kafka-addr", broker_addr,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(
            "could not start the local Kafka-compatible sanity broker: "
            f"{(result.stderr or result.stdout).strip()}"
        )
    deadline = time.time() + 60
    while time.time() < deadline:
        if _tcp_ready(host, port):
            log.info("started local Kafka-compatible sanity broker at %s", broker_addr)
            atexit.register(_stop_managed_kafka, True)
            return True
        time.sleep(1)
    subprocess.run(["docker", "rm", "--force", _MANAGED_KAFKA_NAME], check=False)
    raise RuntimeError(f"local Kafka-compatible sanity broker did not become ready at {broker_addr}")


def _stop_managed_kafka(started: bool) -> None:
    """Remove only a broker started by this harness invocation."""
    if not started:
        return
    import subprocess

    exists = subprocess.run(
        ["docker", "container", "inspect", _MANAGED_KAFKA_NAME],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0
    if not exists:
        return
    subprocess.run(["docker", "rm", "--force", _MANAGED_KAFKA_NAME], check=False)
    log.info("stopped local Kafka-compatible sanity broker")


def _find_chrome():
    """A codec-capable browser for WebRTC capture (H.264/H.265): VIOS_SANITY_CHROME override,
    then system Google Chrome; else None (Playwright's bundled Chromium -> black WebRTC panel)."""
    import os
    import shutil
    return (os.environ.get("VIOS_SANITY_CHROME") or shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or ("/opt/google/chrome/chrome" if os.path.exists("/opt/google/chrome/chrome") else None))


def _check_prereqs():
    """Fail fast with a clear message if a runtime prerequisite is missing (a WARNING for
    Chrome, which only WebRTC capture needs). Points at --install-deps."""
    import importlib
    import shutil
    missing = [t for t in ("ffmpeg", "docker") if not shutil.which(t)]
    for mod, pip in (("av", "av"), ("requests", "requests"), ("yaml", "PyYAML"),
                     ("playwright", "playwright"), ("redis", "redis")):
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(f"python:{pip}")
    if not _find_chrome():
        log.warning("Google Chrome not found -> WebRTC capture renders black. Install Chrome or "
                    "set VIOS_SANITY_CHROME (run_sanity.py --install-deps installs it).")
    if missing:
        log.error("missing prerequisites: %s", ", ".join(missing))
        log.error("install everything with:  python3 services/vios/sanity/run_sanity.py --install-deps")
        raise SystemExit(2)


def _start_file_server(host_ip=""):
    """Start or reuse a persistent, corporate-network-accessible artifact server.

    The server binds to all interfaces by default and survives this command so links in the
    generated PDF remain usable. If the requested port belongs to a different server/share,
    the next free port is selected and advertised to every subsequently-created context.
    """
    import hashlib
    import os
    import shutil
    import socket
    import subprocess
    import time
    from urllib.request import urlopen

    if os.environ.get("VIOS_SANITY_FILE_SERVER"):
        return os.environ["VIOS_SANITY_FILE_SERVER"]
    ctx = SanityContext(host_ip=host_ip)
    share = ctx.share_dir.resolve()
    requested_port = int(os.environ.get("VIOS_SANITY_FILE_SERVER_PORT", "18080"))
    bind = os.environ.get("VIOS_SANITY_FILE_SERVER_BIND", "0.0.0.0")

    def matching_server(port):
        try:
            expected = hashlib.sha256(str(share).encode("utf-8")).hexdigest()
            with urlopen(f"http://127.0.0.1:{port}/.vios-sanity-server.json", timeout=1) as r:
                payload = json.loads(r.read())
                return (payload.get("share_id") == expected
                        and payload.get("protocol_version", 0) >= 2
                        and "byte_ranges" in payload.get("capabilities", []))
        except Exception:  # noqa: BLE001
            return False

    def port_available(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind, port))
                return True
            except OSError:
                return False

    chosen = None
    for port in range(requested_port, requested_port + 21):
        if matching_server(port):
            chosen = port
            log.info("reusing VIOS artifact server on port %d", port)
            break
        if not port_available(port):
            continue
        log_path = share / "artifact_server.log"
        server_cmd = [
            sys.executable, str(Path(__file__).with_name("artifact_server.py")),
            "--directory", str(share), "--bind", bind, "--port", str(port),
        ]
        proc = None
        unit = ""
        systemd_ready = bool(shutil.which("systemd-run") and shutil.which("systemctl"))
        if systemd_ready:
            probe = subprocess.run(
                ["systemctl", "--user", "show-environment"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            systemd_ready = probe.returncode == 0
        if systemd_ready:
            unit = f"vios-sanity-artifacts-{hashlib.sha256(str(share).encode()).hexdigest()[:10]}-{port}-{os.getpid()}"
            launch = subprocess.run(
                ["systemd-run", "--user", "--collect", f"--unit={unit}",
                 "--property=Restart=on-failure", "--property=RestartSec=2",
                 f"--property=StandardOutput=append:{log_path}",
                 f"--property=StandardError=append:{log_path}", *server_cmd],
                capture_output=True, text=True, check=False,
            )
            if launch.returncode != 0:
                log.warning("systemd artifact server launch failed; using detached fallback: %s",
                            (launch.stderr or launch.stdout).strip())
                unit = ""
        if not unit:
            log_stream = log_path.open("ab")
            proc = subprocess.Popen(
                server_cmd, stdout=log_stream, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log_stream.close()
        for _ in range(20):
            if matching_server(port):
                chosen = port
                if unit:
                    (share / "artifact_server.unit").write_text(f"{unit}\n")
                    log.info("artifact server supervised by user systemd unit %s", unit)
                else:
                    (share / "artifact_server.pid").write_text(f"{proc.pid}\n")
                    log.warning("artifact server uses a detached-process fallback; configure "
                                "user systemd or VIOS_SANITY_FILE_SERVER for durable links")
                break
            if proc is not None and proc.poll() is not None:
                break
            time.sleep(0.1)
        if chosen is not None:
            break

    if chosen is None:
        raise RuntimeError(
            f"cannot start VIOS artifact server on ports {requested_port}-"
            f"{requested_port + 20}; set VIOS_SANITY_FILE_SERVER to an existing server")

    base = f"http://{ctx.host_ip}:{chosen}"
    os.environ["VIOS_SANITY_FILE_SERVER"] = base
    os.environ["VIOS_SANITY_FILE_SERVER_PORT"] = str(chosen)
    log.info("serving evidence persistently: %s -> %s/ (bind=%s)", share, base, bind)
    return base


def _configure_artifact_namespace(out_path: str) -> str:
    """Assign one stable, URL-safe namespace for this report invocation."""
    import re

    existing = os.environ.get("VIOS_SANITY_ARTIFACT_NAMESPACE", "").strip("/")
    if existing:
        return existing
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(out_path).stem).strip("-_") or "report"
    namespace = f"{stamp}-{stem}"
    os.environ["VIOS_SANITY_ARTIFACT_NAMESPACE"] = namespace
    return namespace


def _verify_delivery(pdf_link: str, results) -> None:
    """Fail the run unless the PDF and every published evidence link are browser-ready."""
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen

    urls = [pdf_link]
    urls.extend(link for result in results for link in (result.links or []))
    failures = []
    checked = set()
    for url in urls:
        if not url.startswith(("http://", "https://")) or url in checked:
            continue
        checked.add(url)
        path = urlparse(url).path.lower()
        try:
            if path.endswith((".mp4", ".webm")):
                request = Request(url, headers={"Range": "bytes=0-1023"})
                with urlopen(request, timeout=20) as response:
                    content_type = response.headers.get_content_type()
                    if response.status != 206 or not content_type.startswith("video/"):
                        failures.append(
                            f"{url}: expected ranged video response, got "
                            f"HTTP {response.status} {content_type}"
                        )
            else:
                request = Request(url, method="HEAD")
                with urlopen(request, timeout=20) as response:
                    if response.status != 200:
                        failures.append(f"{url}: HTTP {response.status}")
        except Exception as exc:  # noqa: BLE001 - aggregate every broken report link
            failures.append(f"{url}: {exc}")

    download_url = f"{pdf_link}?download=1"
    try:
        with urlopen(Request(download_url, method="HEAD"), timeout=20) as response:
            disposition = response.headers.get("Content-Disposition", "")
            if response.status != 200 or "attachment" not in disposition.lower():
                failures.append(f"{download_url}: missing downloadable PDF response")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{download_url}: {exc}")

    if failures:
        preview = "\n  - ".join(failures[:12])
        suffix = f"\n  - ... {len(failures) - 12} more" if len(failures) > 12 else ""
        raise RuntimeError(f"artifact delivery verification failed:\n  - {preview}{suffix}")
    log.info("DELIVERY READY: PDF download + %d unique evidence link(s) verified", len(checked) - 1)


def _install_deps():
    """Install ALL prerequisites: pip deps, the Playwright browser, ffmpeg, and Google Chrome.
    ffmpeg/Chrome go through install_deps.sh (apt/.deb; Debian/Ubuntu, may prompt for sudo)."""
    import shutil
    import subprocess
    import sys
    here = Path(__file__).resolve().parent

    def run(cmd):
        log.info("$ %s", " ".join(cmd))
        return subprocess.run(cmd, check=False).returncode

    run([sys.executable, "-m", "pip", "install", "-r", str(here / "requirements.txt")])
    run([sys.executable, "-m", "playwright", "install", "chromium"])
    script = here / "install_deps.sh"
    if script.exists():
        run(["bash", str(script)])
    ok = bool(shutil.which("ffmpeg")) and bool(_find_chrome())
    log.info("dependency install complete (ffmpeg=%s, chrome=%s)",
             bool(shutil.which("ffmpeg")), bool(_find_chrome()))
    return 0 if ok else 1


def _provision(ctx, video_path, copies, sync_wall=False, max_streams=4, variants=False,
               deploy_vios=None):
    from provision import provision
    try:
        info = provision(ctx, Path(video_path), n_copies=copies, sync_wall=sync_wall,
                         max_streams=max_streams, variants=variants, deploy_vios=deploy_vios)
        ctx.provisioned_streams = info["rtsp_streams"]
        ctx.file_sensor = info["file_sensor"]
        log.info("provisioned streams=%s file_sensor=%s stream_id=%s",
                 ctx.provisioned_streams, ctx.file_sensor, ctx.stream_id)
    except Exception as e:  # noqa: BLE001
        log.warning("provisioning failed (continuing): %s", e)


def _dump_results(results, plan_meta, ctx, when, path):
    """Persist a run so the PDF can be re-rendered later WITHOUT re-running."""
    data = {"when": when, "host_ip": ctx.host_ip, "base_url": ctx.base_url,
            "stream_id": ctx.stream_id, "broker": ctx.broker, "plan_meta": plan_meta,
            "results": [{"name": r.name, "status": r.status, "detail": r.detail,
                         "duration_s": r.duration_s, "image": str(r.image) if r.image else None,
                         "links": r.links, "plan": r.plan, "group": r.group,
                         "metrics": r.metrics, "evidence": r.evidence,
                         "request": getattr(r, "request", {}) or {},
                         "started_at": getattr(r, "started_at", ""),
                         "finished_at": getattr(r, "finished_at", "")}
                        for r in results]}
    Path(path).write_text(json.dumps(data, indent=2))
    log.info("saved results -> %s (re-render with --from-json)", path)


def _plan_tag(name: str) -> str:
    """Short filesystem tag for a plan (e.g. 'Plan-2 | ... kafka' -> 'plan2')."""
    import re
    m = re.search(r"[Pp]lan-?(\d+)", name or "")
    return f"plan{m.group(1)}" if m else re.sub(r"[^a-z0-9]+", "_", (name or "sanity").lower())[:16]


def _capture_container_logs(ctx, containers=("sensor-ms", "streamprocessing-ms-1"), tag=""):
    """Save the FULL logs (from container start -- no --tail) of the key VIOS containers so a
    reader can trace a failed case to the service-side cause from the very beginning. MUST be
    called while the containers are still alive (a stop --clean or restore_configs recreate
    replaces them and drops the history). `tag` namespaces the file per plan. Returns
    {container: http_link}."""
    import subprocess
    links = {}
    suffix = f"_{tag}" if tag else ""
    for c in containers:
        try:
            out = subprocess.run(["docker", "logs", c],   # full log, from container start
                                 capture_output=True, text=True, timeout=120)
            log_path = ctx.out_dir / f"{c}{suffix}.log"
            log_path.write_text((out.stdout or "") + (out.stderr or ""))
            links[c] = ctx.publish(log_path, f"vios_sanity_{c}{suffix}.log")
        except Exception as e:  # noqa: BLE001
            log.warning("capture logs for %s failed: %s", c, e)
    return links


def _dump_failures(results, ctx, path):
    """Write and publish a timestamped, request-complete failure manifest.

    Each failed case includes UTC start/end times and the exact prepared REST request
    or browser automation invocation. Container logs are captured per plan while containers are
    still alive. Returns {'link', 'count'} for the PDF renderer.
    """
    import socket

    fails = [r for r in results if r.status == "FAIL"]
    now = datetime.now(timezone.utc)
    starts = [r.started_at for r in results if getattr(r, "started_at", "")]
    finishes = [r.finished_at for r in results if getattr(r, "finished_at", "")]
    rows = [{
        "name": r.name,
        "plan": getattr(r, "plan", "") or "",
        "status": r.status,
        "group": getattr(r, "group", "") or "",
        "started_at": getattr(r, "started_at", "") or None,
        "finished_at": getattr(r, "finished_at", "") or None,
        "duration_s": round(r.duration_s, 3),
        "request": getattr(r, "request", {}) or {},
        "detail": r.detail,
    } for r in fails]
    manifest = {
        "schema_version": 2,
        "generated_at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "timezone": {"name": "UTC", "utc_offset": "+0000"},
        "system": {"hostname": socket.gethostname(), "host_ip": ctx.host_ip,
                   "base_url": ctx.base_url},
        "run": {"started_at": min(starts) if starts else None,
                "finished_at": max(finishes) if finishes else None},
        "summary": {"total": len(results),
                    "pass": sum(r.status == "PASS" for r in results),
                    "fail": len(fails),
                    "skip": sum(r.status == "SKIP" for r in results)},
        "failed_cases": rows,
    }
    Path(path).write_text(json.dumps(manifest, indent=2))
    if not fails:
        log.info("no failed cases")
        return {"link": "", "count": 0}
    link = ""
    try:
        link = ctx.publish(Path(path), "vios_sanity_failed_cases.json")
    except Exception as e:  # noqa: BLE001
        log.warning("publish failures manifest failed: %s", e)
    log.info("%d failed case(s) -> %s", len(fails), link or path)
    return {"link": link, "count": len(fails)}


def _cleanup_transient_artifacts(*roots):
    """Remove only reproducible harness-owned files; preserve reports and evidence."""
    import shutil

    removed_files = 0
    removed_bytes = 0
    seen = set()
    for root in roots:
        root = Path(root).resolve()
        if root in seen or root == Path(root.anchor) or root == Path.home():
            continue
        seen.add(root)
        provision = root / "provision"
        if provision.is_dir():
            for item in provision.rglob("*"):
                if item.is_file():
                    removed_files += 1
                    removed_bytes += item.stat().st_size
            shutil.rmtree(provision)
        for pattern in ("**/*_control.mp4", "**/*_control.mkv"):
            for item in root.glob(pattern):
                if not item.is_file():
                    continue
                removed_files += 1
                removed_bytes += item.stat().st_size
                item.unlink()
    log.info("transient cleanup: removed %d generated file(s), %.1f MiB; "
             "reports and published evidence preserved",
             removed_files, removed_bytes / (1024 * 1024))
    return {"files": removed_files, "bytes": removed_bytes}


def _load_results(path):
    from sanity_common import UseCaseResult
    d = json.loads(Path(path).read_text())
    results = []
    for x in d["results"]:
        r = UseCaseResult(name=x["name"], status=x["status"], detail=x["detail"])
        r.duration_s = x.get("duration_s", 0.0)
        r.image = Path(x["image"]) if x.get("image") else None
        r.links = x.get("links", []); r.plan = x.get("plan", ""); r.group = x.get("group", "")
        r.metrics = x.get("metrics", {}); r.evidence = x.get("evidence", False)
        r.request = x.get("request", {})
        r.started_at = x.get("started_at", "")
        r.finished_at = x.get("finished_at", "")
        results.append(r)
    ctx = SanityContext(host_ip=d["host_ip"], base_url=d["base_url"],
                        stream_id=d["stream_id"], broker=d["broker"])
    return results, d.get("plan_meta", {}), ctx, d["when"]


def _health(base_url, timeout: int = 120):
    """Wait (retrying) until VIOS answers health, so a fresh deploy is fully up before
    provisioning. The oneclick deploy already blocks on docker healthchecks, but the HTTP
    API can lag a few seconds past 'healthy'."""
    import requests
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        try:
            h = requests.get(f"{base_url}/health", timeout=10)
            if h.status_code == 200:
                log.info("VST %s /health -> 200", base_url)
                return
        except Exception:  # noqa: BLE001
            pass
        _t.sleep(3)
    log.warning("VST health not ready after %ds (%s); continuing", timeout, base_url)


def _verify_direct_mode_runtime():
    """Fail Plan-3 unless direct mode is active and Redis/SDRC are absent."""
    import re
    import subprocess

    compose_env = (Path(__file__).resolve().parents[1]
                   / "deployment/stream-processing/docker-compose/compose.env")
    text = compose_env.read_text()

    def active_value(key):
        match = re.search(rf"(?m)^{re.escape(key)}=(.*)$", text)
        return match.group(1).strip() if match else ""

    expected = {
        "VST_USE_SDRC": "false",
        "NGINX_MODE": "vst",
        "STREAM_PROCESSOR_MODULE_ENDPOINT": "http://${HOST_IP}:30001",
    }
    wrong = {key: active_value(key) for key, value in expected.items()
             if active_value(key) != value}
    names = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True,
        text=True, check=True, timeout=30,
    ).stdout.splitlines()
    forbidden = sorted(name for name in names
                       if name == "redis-server" or "sdr-controller" in name)
    if wrong or forbidden:
        raise RuntimeError(f"Plan-3 direct-mode gate failed: env={wrong}, running={forbidden}")
    log.info("PLAN-3 DIRECT READY: no Redis, no SDRC, ingress mode=vst, endpoint=:30001")


def _start_metadata_service(ctx, wait_s: int = 12):
    """Start the event-driven overlay plugin (the DeepStream stand-in). It receives VIOS
    camera lifecycle events through Redis or direct-mode webhooks and, per stream, reads SEI
    off the VIOS RTSP proxy and
    publishes a per-frame bbox (objectId = the incrementing frame number) to the plan's broker
    AND the fake-ES on :19200. The overlay use-cases consume this. Idempotent per ctx (returns
    the already-running proc); start it with wait_s=0 BEFORE the VIOS deploy so it is
    subscribed before camera_streaming fires. Returns the Popen (or None)."""
    existing = getattr(ctx, "_mds", None)
    if existing is not None and existing.poll() is None:
        return existing
    import socket
    import subprocess
    import time as _t
    svc = Path(__file__).resolve().parents[1] / "test/bdd_tests/scripts/overlay/metadata_service.py"
    retention_hours = float(os.environ.get("VIOS_SANITY_ES_RETENTION_HOURS", "3"))
    if retention_hours <= 0:
        raise ValueError("VIOS_SANITY_ES_RETENTION_HOURS must be greater than zero")
    event_transport = getattr(ctx, "event_transport", "redis")
    cmd = [sys.executable, str(svc), "--broker", ctx.broker, "--base-url", ctx.base_url,
           "--nvstreamer-url", ctx.nvstreamer_url, "--es-port", "19200",
           "--es-retention-hours", f"{retention_hours:g}",
           "--event-transport", event_transport]
    if event_transport == "webhook":
        cmd += ["--webhook-host", "0.0.0.0", "--webhook-port", "18088"]
    if ctx.broker == "kafka" and getattr(ctx, "kafka_brokers", None):
        cmd += ["--kafka", ctx.kafka_brokers]
    log.info(
        "starting event-driven overlay plugin (metadata broker=%s, events=%s, "
        "retention=%g hours/sensor)",
        ctx.broker,
        event_transport,
        retention_hours,
    )
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    ctx._mds = proc
    if event_transport == "webhook":
        deadline = _t.time() + 30
        while _t.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("overlay webhook plugin exited before binding port 18088")
            try:
                with socket.create_connection(("127.0.0.1", 18088), timeout=1):
                    break
            except OSError:
                _t.sleep(0.25)
        else:
            proc.terminate()
            raise RuntimeError("overlay webhook plugin did not bind 127.0.0.1:18088")
        log.info("overlay webhook receivers ready on 127.0.0.1:18088")
    if wait_s:
        _t.sleep(wait_s)   # let it bind fake-ES + subscribe before use-cases run
    return proc


def _stop_metadata_service(proc):
    if not proc:
        return
    try:
        proc.terminate()          # SIGTERM -> service XTRIMs the broker + clears ES on exit
        proc.wait(timeout=20)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    log.info("stopped continuous metadata service (broker + ES cleared)")


def _run_plans(plans_path: str, deploy_only: bool = False, images: dict = None,
               host_ip: str = "", keep_deployment: bool = False,
               leave_deployment: bool = False):
    from plans import load_plans, expand_usecases
    from provision import backup_configs, restore_configs, apply_images, apply_deploy_env
    defaults, plans = load_plans(plans_path)
    results = []
    plan_meta = {}
    enabled_runs = [(plan, sysname, system) for plan in plans if plan.get("enabled")
                    for sysname, system in _plan_systems(plan)]
    if keep_deployment:
        if len(enabled_runs) != 1:
            raise ValueError("--keep-deployment requires exactly one enabled plan/system; "
                             "multi-plan runs must transition between deployments")
        if images:
            raise ValueError("--keep-deployment cannot apply image overrides without restarting")
        log.info("KEEP DEPLOYMENT: reusing the active stack; no stop, config write, deploy, "
                 "restore, streamprocessing recreate, or nvstreamer recreate will run")
        return _run_plans_inner(plans, deploy_only, results, plan_meta, expand_usecases,
                                keep_deployment=True, leave_deployment=True)

    # Make the host-specific bits implicit: set HOST_IP + repo-local paths, and point the
    # deployment at the requested images (CLI overrides sanity_plans.yaml `defaults.images`).
    backup_configs()
    try:
        apply_deploy_env(host_ip)
        imgs = {**(defaults.get("images") or {}), **(images or {})}
        if imgs:
            apply_images(imgs.get("streamprocessing"), imgs.get("sensor"), imgs.get("nvstreamer"))
        return _run_plans_inner(plans, deploy_only, results, plan_meta, expand_usecases,
                                keep_deployment=False, leave_deployment=leave_deployment)
    finally:
        if deploy_only or leave_deployment:
            mode = "deploy-only" if deploy_only else "leave-deployment"
            log.info("%s: Plan configuration and running services left in place "
                     "(.sanity-bak kept; run --restore-config to revert)", mode)
        else:
            restore_configs()   # revert config changes; recreation is allowed in transition mode


def _plan_systems(plan):
    """A plan targets one or more systems. New schema: `systems:` map (name -> conf with
    `enabled`). Back-compat: a single `system:` dict is treated as one 'local' system.
    Returns [(sysname, sysconf), ...] for the ENABLED systems only."""
    systems = plan.get("systems")
    if not systems:
        return [("local", plan.get("system", {}) or {})]
    out = []
    for sysname, sysconf in systems.items():
        sysconf = sysconf or {}
        if sysconf.get("enabled", False):
            out.append((sysname, sysconf))
    return out


def _adopt_running_nvstreamer(ctx):
    """Adopt an already-running VIOS/NVStreamer plan without mutating its lifecycle."""
    import requests

    _health(ctx.base_url, timeout=30)
    vst_resp = requests.get(f"{ctx.base_url}/vst/api/v1/sensor/list", timeout=20,
                            verify=ctx.verify_ssl)
    vst_resp.raise_for_status()
    sensors = vst_resp.json() or []
    sensors = sensors if isinstance(sensors, list) else sensors.get("sensors", [])
    online = [x for x in sensors if str(x.get("state", "")).lower() == "online"]
    rtsp = [x for x in online if str(x.get("type", "")).endswith("rtsp")]
    if not rtsp:
        raise RuntimeError("keep-deployment requested, but no ONLINE VIOS RTSP sensors exist")

    ctx.provisioned_streams = [x.get("sensorId") or x.get("name") for x in rtsp]
    ctx.stream_names = {(x.get("sensorId") or x.get("name")): x.get("name", "") for x in rtsp}
    files = [x for x in online if str(x.get("type", "")).endswith("file")]
    preferred = [x for x in files if "h264" in str(x.get("name", "")).lower()]
    if preferred or files:
        chosen = (preferred or files)[0]
        ctx.file_sensor = chosen.get("sensorId") or chosen.get("name")

    try:
        nvs_resp = requests.get(f"{ctx.nvstreamer_url}/api/v1/sensor/list", timeout=15)
        nvs_resp.raise_for_status()
        nvs = nvs_resp.json() or []
        nvs = nvs if isinstance(nvs, list) else nvs.get("sensors", [])
        for item in nvs:
            name = item.get("name")
            sid = item.get("sensorId")
            if not name or not sid or name not in ctx.provisioned_streams:
                continue
            mi = requests.get(f"{ctx.nvstreamer_url}/api/v1/storage/file/mediainfo",
                              params={"sensorId": sid}, timeout=15).json() or {}
            width, height = int(mi.get("Width") or 0), int(mi.get("Height") or 0)
            if width and height:
                ctx.stream_res[name] = (width, height)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read all active NVStreamer resolutions: %s", exc)

    log.info("adopted active deployment without restart: %d RTSP stream(s), file sensor=%s",
             len(ctx.provisioned_streams), ctx.file_sensor)


def _deploy_provision_nvstreamer(ctx, name, setup, plan, sync_wall):
    """The NVStreamer-first flow: write config, stop --clean, deploy NVStreamer, provision +
    verify its RTSP sources, then deploy VIOS against a live NVStreamer, then the recording
    gate. Provisions ctx.provisioned_streams / ctx.file_sensor. (Plans 1, 2, and 3.)"""
    from provision import (apply_vst_config, apply_nvstreamer_config,
                           apply_vst_notification_config,
                           apply_nvstreamer_notification_config, configure_overlay_webhooks,
                           clean_stop, deploy_target,
                           wipe_nvstreamer_videos, recreate_service, _wait_ready,
                           _wait_recording_current, verify_nvstreamer_stream_count)
    import requests as _rq
    video_path = plan.get("video_path") or plan.get("input_mp4")
    direct_mode = setup.get("deployment_mode") == "direct"
    # Point VIOS's download/replay overlay at the plugin's fake-ES (host bridge :19200).
    # Without this VIOS never queries it, so replay/download of recorded windows draw no box
    # (the live/recent path goes through the broker and is unaffected).
    vst_over = {"video_metadata_server": _FAKE_ES}
    notification_over = {
        "enable_notification": True if direct_mode else ctx.event_transport != "webhook",
        "use_message_broker": "kafka" if direct_mode else "redis",
        "message_broker_topic": "vst_events",
    }
    if setup.get("consumer"):
        notification_over.update({
            "use_message_broker_consumer": setup["consumer"],
            "enable_notification_consumer": True,
            "message_broker_topic_consumer": "vst-overlay-test",
        })
        if setup["consumer"] == "kafka":
            notification_over["kafka_server_address"] = setup.get(
                "broker_addr", "172.17.0.1:9092"
            )
    vst_over.update(setup.get("vst_config", {}) or {})
    notification_over.update(setup.get("vst_notification_config", {}) or {})
    try:
        apply_vst_config(vst_over, recreate=False)
        apply_vst_notification_config(notification_over, recreate=False)
        configure_overlay_webhooks(ctx.event_transport == "webhook")
        apply_nvstreamer_config(setup.get("nvstreamer_config", {}) or {}, recreate=False)
        apply_nvstreamer_notification_config(
            setup.get("nvstreamer_notification_config", {}) or {}, recreate=False
        )
    except Exception as e:  # noqa: BLE001
        log.warning("config write failed: %s", e)
    clean_stop()
    wipe_nvstreamer_videos(video_path)
    deploy_target("nvstreamer")
    _wait_ready(f"{ctx.nvstreamer_url}/vst/api/v1/sensor/list", 120)

    if direct_mode:
        # Validate the live sensor-ms to streamprocessing-ms proxy-add flow. VIOS must be
        # healthy while NVStreamer is still empty so a failed initial proxy-add cannot be
        # hidden by streamprocessing database recovery.
        _start_metadata_service(ctx, wait_s=0)
        deploy_target("vst")

        service_deadline = time.time() + 180
        for service_name, ready_url in (
            ("sensor-ms", "http://127.0.0.1:30000/v1/ready"),
            ("streamprocessing-ms", "http://127.0.0.1:30001/v1/ready"),
        ):
            while time.time() < service_deadline:
                try:
                    ready = _rq.get(ready_url, timeout=10)
                    if ready.status_code == 200:
                        log.info("Plan-3 pre-provision gate: %s %s -> 200", service_name,
                                 ready_url)
                        break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(2)
            else:
                raise RuntimeError(
                    f"Plan-3 pre-provision gate failed: {service_name} not ready at {ready_url}"
                )
        _health(ctx.base_url, timeout=180)
        _verify_direct_mode_runtime()

    if not (video_path and setup.get("nvstreamer")):
        return

    def _bring_up_vios(copy_names):
        if sync_wall:
            recreate_service("nvstreamer")
            _wait_ready(f"{ctx.nvstreamer_url}/vst/api/v1/sensor/list", 90)
        if direct_mode:
            verify_nvstreamer_stream_count(ctx.nvstreamer_url, len(copy_names) or 1)
            return
        verify_nvstreamer_stream_count(ctx.nvstreamer_url, len(copy_names) or 4)
        # event-driven overlay plugin subscribes BEFORE VIOS fires camera_streaming.
        _start_metadata_service(ctx, wait_s=0)
        deploy_target("vst")
        _health(ctx.base_url, timeout=180)
        if setup.get("deployment_mode") == "direct":
            _verify_direct_mode_runtime()

    _provision(ctx, video_path, int(setup.get("rtsp_copies", _RTSP_COPIES)), sync_wall,
               plan.get("max_streams", 4), bool(setup.get("variants")),
               deploy_vios=_bring_up_vios)
    if setup.get("deployment_mode") == "direct":
        if len(ctx.provisioned_streams) != 1 or not ctx.file_sensor:
            raise RuntimeError(
                "Plan-3 stream gate failed: expected exactly one RTSP stream and one file "
                f"sensor, got rtsp={ctx.provisioned_streams}, file={ctx.file_sensor}")
    # DEPLOYMENT-READY GATE: proceed only once every provisioned RTSP stream is recording
    # continuous-to-now with >=60s of history -- a full minute so the overlay use-cases (whose
    # historical window sits early in the recording) have settled metadata everywhere, and the
    # first-live-frame backfill has bridged the ramp-up gap.
    if ctx.provisioned_streams:
        _wait_recording_current(ctx.base_url, ctx.provisioned_streams[0],
                                ctx.verify_ssl, timeout=300, min_span_s=60)
        not_rec = [s for s in ctx.provisioned_streams
                   if not _wait_recording_current(ctx.base_url, s, ctx.verify_ssl,
                                                  timeout=90, min_span_s=55)]
        try:
            sensors = _rq.get(f"{ctx.base_url}/vst/api/v1/sensor/list",
                              timeout=20, verify=ctx.verify_ssl).json() or []
        except Exception:  # noqa: BLE001
            sensors = []
        rtsp = [s for s in sensors if str(s.get("type", "")).endswith("rtsp")]
        if not_rec:
            log.warning("DEPLOYMENT NOT READY: %d sensor(s), %d RTSP; NOT recording: %s",
                        len(sensors), len(rtsp), not_rec)
        else:
            log.info("DEPLOYMENT READY: %d sensor(s) (%d RTSP), all RTSP recording",
                     len(sensors), len(rtsp))


def _deploy_adaptor_plan(ctx, name, adaptor, setup):
    """Deploy VIOS with a VMS adaptor enabled -- no NVStreamer. VIOS discovers cameras from
    the adaptor: 'milestone' (Milestone server via milestone_onvif, no overlay) or 'onvif'
    (ONVIF network discovery, full overlay). For onvif, the overlay plugin is started before
    VIOS. Populates ctx.provisioned_streams with the ONLINE camera ids."""
    from provision import (apply_vst_config, apply_vst_notification_config,
                           apply_adaptor_config, configure_overlay_webhooks, clean_stop, deploy_target,
                           discover_online_cameras)
    overlay = adaptor != "milestone"
    vst_over = {"video_metadata_server": _FAKE_ES} if overlay else {}
    notification_over = {}
    if overlay and setup.get("consumer"):
        notification_over.update({
            "enable_notification": True,
            "use_message_broker": "redis",
            "message_broker_topic": "vst_events",
            "use_message_broker_consumer": setup["consumer"],
            "enable_notification_consumer": True,
            "message_broker_topic_consumer": "vst-overlay-test",
        })
        if setup["consumer"] == "kafka":
            notification_over["kafka_server_address"] = setup.get(
                "broker_addr", "172.17.0.1:9092"
            )
    vst_over.update(setup.get("vst_config", {}) or {})
    notification_over.update(setup.get("vst_notification_config", {}) or {})
    try:
        apply_vst_config(vst_over, recreate=False)
        apply_vst_notification_config(notification_over, recreate=False)
        configure_overlay_webhooks(False)
        apply_adaptor_config(adaptor, setup)     # enable adaptor + write server config
    except Exception as e:  # noqa: BLE001
        log.warning("adaptor config write failed: %s", e)
    clean_stop()
    if overlay:                                  # onvif: plugin subscribes before VIOS
        _start_metadata_service(ctx, wait_s=0)
    deploy_target("vst")
    _health(ctx.base_url, timeout=240)           # adaptor connect/discovery takes longer
    cams = discover_online_cameras(ctx.base_url, verify_ssl=ctx.verify_ssl, timeout=180)
    ctx.provisioned_streams = cams
    log.info("plan '%s' (adaptor=%s): %d ONLINE camera(s): %s", name, adaptor, len(cams), cams)


def _run_plan_on_system(plan, base_name, sysname, system, deploy_only,
                        results, plan_meta, expand_usecases, keep_deployment=False,
                        leave_deployment=False):
    setup = plan.get("setup", {}) or {}
    name = base_name if sysname in ("local", "default") else f"{base_name} @ {sysname}"
    run_namespace = os.environ.get("VIOS_SANITY_ARTIFACT_NAMESPACE", "").strip("/")
    plan_namespace = "/".join(filter(None, (run_namespace, _plan_tag(name))))
    ctx = SanityContext(base_url=system.get("base_url", "http://localhost:30888"),
                        broker=setup.get("consumer", "redis"),
                        event_transport=setup.get("event_transport", "redis"),
                        stream_id=plan.get("stream_id", "warehouse_sample"),
                        artifact_namespace=plan_namespace)
    if setup.get("broker_addr"):
        ctx.kafka_brokers = setup["broker_addr"]
    target = system.get("target", "local")
    adaptor = setup.get("adaptor")               # None -> NVStreamer; 'milestone' | 'onvif'
    sync_wall = bool(setup.get("sync_wall"))
    overlay = adaptor != "milestone"             # milestone has NO overlay
    log.info("===================== PLAN: %s (target=%s, adaptor=%s) =====================",
             name, target, adaptor or "nvstreamer")
    managed_kafka = _ensure_kafka_broker(setup, target)

    if target == "local" and not keep_deployment:
        from provision import apply_deployment_mode
        apply_deployment_mode(setup.get("deployment_mode", "sdrc"))

    if keep_deployment:
        if target != "local" or adaptor or not setup.get("nvstreamer"):
            raise ValueError("--keep-deployment currently supports one local NVStreamer plan")
        _adopt_running_nvstreamer(ctx)
    elif target == "local" and not adaptor and setup.get("nvstreamer"):
        _deploy_provision_nvstreamer(ctx, name, setup, plan, sync_wall)
    elif target == "local" and adaptor in ("milestone", "onvif"):
        _deploy_adaptor_plan(ctx, name, adaptor, setup)
    elif target == "remote":
        log.warning("plan '%s' is remote: ssh deploy not implemented; running API use-cases "
                    "against %s", name, ctx.base_url)

    plan_meta[name] = {
        "consumer": setup.get("consumer", ctx.broker), "target": target, "system": sysname,
        "event_transport": ctx.event_transport,
        "deployment_mode": setup.get("deployment_mode", "sdrc"),
        "adaptor": adaptor or "nvstreamer", "base_url": system.get("base_url", ctx.base_url),
        "nvstreamer": ctx.nvstreamer_url, "streams": list(ctx.provisioned_streams),
        "file_sensor": ctx.file_sensor, "stream_id": ctx.stream_id,
    }
    if deploy_only:
        log.info("deploy-only: plan '%s' provisioned (%s); skipping use-cases",
                 name, ctx.provisioned_streams)
        if managed_kafka:
            import atexit
            atexit.unregister(_stop_managed_kafka)
            log.info("leaving local Kafka-compatible sanity broker running")
        return

    # Overlay plugin only for overlay-capable plans (nvstreamer/onvif) -- milestone has none.
    mds = _start_metadata_service(ctx) if (target == "local" and overlay) else None
    try:
        if plan.get("usecases"):
            suite = expand_usecases(plan["usecases"], ctx)
        elif adaptor == "milestone":
            from usecases import milestone_suite
            suite = milestone_suite(ctx)
        else:
            from usecases import default_suite
            # ONVIF adaptor has no NVStreamer/file sensor -> nvstreamer=False (RTSP + overlay only).
            suite = default_suite(ctx, sync_wall, nvstreamer=(adaptor is None))
        for label, fn, meta in suite:
            res = run_usecase(label, fn, ctx)
            res.plan = name
            res.evidence = bool(meta.get("evidence"))
            results.append(res)
    finally:
        if leave_deployment and mds is not None and mds.poll() is None:
            log.info("leave-deployment: metadata service left running (pid=%d)", mds.pid)
        else:
            if ctx.event_transport == "webhook" and target == "local":
                from provision import delete_all_vios_sensors
                delete_all_vios_sensors(ctx.base_url, ctx.verify_ssl)
                time.sleep(3)  # let camera_remove reach the plugin before it exits
            _stop_metadata_service(mds)
    # Capture THIS plan-run's container logs while the containers are still alive.
    if target == "local" and any(r.plan == name and r.status == "FAIL" for r in results):
        plan_meta[name]["logs"] = _capture_container_logs(ctx, tag=_plan_tag(name))
    if managed_kafka:
        if deploy_only or leave_deployment:
            import atexit
            atexit.unregister(_stop_managed_kafka)
            log.info("leaving local Kafka-compatible sanity broker running")
        else:
            _stop_managed_kafka(True)


def _run_plans_inner(plans, deploy_only, results, plan_meta, expand_usecases,
                     keep_deployment=False, leave_deployment=False):
    for plan in plans:
        base_name = plan.get("name", "plan")
        if not plan.get("enabled"):
            log.info("skip disabled plan: %s", base_name)
            continue
        for sysname, system in _plan_systems(plan):    # one run per enabled system
            _run_plan_on_system(plan, base_name, sysname, system, deploy_only,
                                results, plan_meta, expand_usecases, keep_deployment,
                                leave_deployment)
    return results, plan_meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", help="run all ENABLED plans from a sanity_plans.yaml")
    ap.add_argument("--base-url", default="http://localhost:30888")
    ap.add_argument("--stream-id", default="warehouse_sample")
    ap.add_argument("--broker", default="redis", choices=["redis", "kafka"])
    ap.add_argument("--host-ip", default="",
                    help="host IP for evidence links (default: $VIOS_SANITY_HOST_IP or auto-detect)")
    ap.add_argument("--only", help="comma-separated subset of use-case names")
    ap.add_argument("--input-mp4", help="a clip/dir to provision from (RTSP copies/set + file sensor)")
    ap.add_argument("--max-streams", type=int, default=4,
                    help="cap RTSP streams provisioned from a video directory")
    ap.add_argument("--out", default="/tmp/vios_sanity/report.pdf")
    ap.add_argument("--from-json", help="re-render the PDF from a saved results.json (no run)")
    ap.add_argument("--deploy-only", action="store_true",
                    help="apply plan config + provision streams, but do NOT run use-cases")
    ap.add_argument("--keep-deployment", action="store_true",
                    help="reuse one active local plan without stopping, deploying, or recreating services")
    ap.add_argument("--leave-deployment", action="store_true",
                    help="deploy and test normally, then leave the final plan and configs running")
    ap.add_argument("--restore-config", action="store_true",
                    help="restore the configs the sanity backed up (.sanity-bak) + recreate, then exit")
    # Containers under test -> written into compose.env + pulled automatically (no hand-editing).
    ap.add_argument("--streamprocessing-image", help="VST stream-processor image to test")
    ap.add_argument("--sensor-image", help="VST sensor image to test")
    ap.add_argument("--nvstreamer-image", help="NVStreamer image to test")
    ap.add_argument("--no-serve", action="store_true",
                    help="do NOT auto-start the evidence file server (serve share_dir on :18080)")
    ap.add_argument("--keep-transients", action="store_true",
                    help="retain generated provisioning copies and comparison-control videos")
    ap.add_argument(
        "--es-retention-hours",
        type=float,
        default=os.environ.get("VIOS_SANITY_ES_RETENTION_HOURS", "3"),
        help="rolling fake-ES metadata history retained per sensor (default: 3 hours)",
    )
    ap.add_argument("--install-deps", action="store_true",
                    help="install all prerequisites (pip deps, playwright chromium, Google Chrome, ffmpeg) and exit")
    a = ap.parse_args()
    if a.es_retention_hours <= 0:
        ap.error("--es-retention-hours must be greater than zero")
    os.environ["VIOS_SANITY_ES_RETENTION_HOURS"] = f"{a.es_retention_hours:g}"
    _configure_artifact_namespace(a.out)

    if a.install_deps:
        return _install_deps()

    if a.restore_config:
        from provision import restore_configs
        restore_configs()
        log.info("configs restored to their pre-sanity snapshot")
        return 0

    # Re-render only: rebuild the PDF from a saved run (format tweaks need no re-run).
    if a.from_json:
        results, plan_meta, ctx, when = _load_results(a.from_json)
        if not a.no_serve:
            ctx.file_server_base = _start_file_server(a.host_ip or ctx.host_ip)
        out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
        fails_info = _dump_failures(results, ctx, out.with_name("failed_cases.json"))
        build_pdf(results, ctx, when, out, plan_meta, failures=fails_info)
        link = ctx.publish(out, out.name)
        try:
            _verify_delivery(link, results)
        except RuntimeError as exc:
            log.error("%s", exc)
            return 2
        print(f"\nPDF report: {out}\nOpen in browser: {link}\nDownload PDF:   {link}?download=1")
        return 0

    _check_prereqs()
    if not a.no_serve:
        _start_file_server(a.host_ip)

    if a.plans:
        images = {k: v for k, v in (("streamprocessing", a.streamprocessing_image),
                                    ("sensor", a.sensor_image),
                                    ("nvstreamer", a.nvstreamer_image)) if v}
        results, plan_meta = _run_plans(a.plans, deploy_only=a.deploy_only,
                                        images=images, host_ip=a.host_ip,
                                        keep_deployment=a.keep_deployment,
                                        leave_deployment=a.leave_deployment)
        if a.deploy_only:
            log.info("deploy-only complete: stack deployed + plan(s) provisioned; no use-cases run")
            return 0
        ctx = SanityContext()   # for publish() + PDF metadata only
    else:
        ctx = SanityContext(base_url=a.base_url, stream_id=a.stream_id, broker=a.broker, host_ip=a.host_ip)
        _health(a.base_url)
        if a.input_mp4:
            _provision(ctx, a.input_mp4, _RTSP_COPIES, max_streams=a.max_streams)
        wanted = set(a.only.split(",")) if a.only else None
        # Same single metadata source as plan mode: ONE continuous metadata_service (generator
        # + fake-ES + broker publisher) for the whole run; the overlay verbs consume it.
        mds = _start_metadata_service(ctx)
        try:
            results = [run_usecase(n, f, ctx) for n, f in USECASES if not (wanted and n not in wanted)]
        finally:
            _stop_metadata_service(mds)
        plan_meta = {"Sanity": {"consumer": a.broker, "target": "local", "base_url": a.base_url,
                                "nvstreamer": ctx.nvstreamer_url, "streams": list(ctx.provisioned_streams),
                                "file_sensor": ctx.file_sensor, "stream_id": a.stream_id}}

    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    _dump_results(results, plan_meta, ctx, when, out.with_suffix(".results.json"))
    fails_info = _dump_failures(results, ctx, out.with_name("failed_cases.json"))
    build_pdf(results, ctx, when, out, plan_meta, failures=fails_info)
    pdf_link = ctx.publish(out, out.name)
    try:
        _verify_delivery(pdf_link, results)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2
    if not a.keep_transients:
        _cleanup_transient_artifacts(out.parent,
                                     Path(os.environ.get("VIOS_SANITY_OUT_DIR", "/tmp/vios_sanity")))

    npass = sum(1 for r in results if r.status == "PASS")
    nfail = sum(1 for r in results if r.status == "FAIL")
    nskip = sum(1 for r in results if r.status == "SKIP")
    log.info("SANITY SUMMARY: %d PASS / %d FAIL / %d SKIP", npass, nfail, nskip)
    for r in results:
        log.info("  [%s] %-24s %-4s  %s", (r.plan or "-")[:20], r.name, r.status, r.detail[:60])
    print(f"\nPDF report: {out}\nOpen in browser: {pdf_link}\nDownload PDF:   {pdf_link}?download=1")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())

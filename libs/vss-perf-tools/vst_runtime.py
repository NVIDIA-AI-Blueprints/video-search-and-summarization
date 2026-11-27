#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate Thor CUDA access before starting VIOS; never change host configuration."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import uuid


CUDA_PROBE = r'''
import ctypes, json
cuda = ctypes.CDLL("libcuda.so.1")
# VIOS also loads this absolute path; testing only the loader search path can
# otherwise pass while launch_vst still reaches an empty image placeholder.
if __import__("os").environ.get("PERF_PROBE_VIOS") == "true":
    ctypes.CDLL("/usr/lib/aarch64-linux-gnu/libcuda.so")
cuda.cuInit.argtypes = [ctypes.c_uint]
cuda.cuInit.restype = ctypes.c_int
status = cuda.cuInit(0)
if status:
    raise RuntimeError("cuInit returned %s" % status)
count = ctypes.c_int()
cuda.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
cuda.cuDeviceGetCount.restype = ctypes.c_int
status = cuda.cuDeviceGetCount(ctypes.byref(count))
if status:
    raise RuntimeError("cuDeviceGetCount returned %s" % status)
names = []
cuda.cuDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
cuda.cuDeviceGetName.restype = ctypes.c_int
for device in range(count.value):
    name = ctypes.create_string_buffer(256)
    status = cuda.cuDeviceGetName(name, len(name), device)
    if status:
        raise RuntimeError("cuDeviceGetName returned %s" % status)
    names.append(name.value.decode())
print(json.dumps({"devices": names}))
'''


def command(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def selected_thor(devices, machine=None):
    """Return selected Thor identity, preserving all non-Thor behavior."""
    if (machine or platform.machine()) not in {"aarch64", "arm64"}:
        return None
    result = command(["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader"])
    if result.returncode:
        raise RuntimeError("Cannot identify the selected GPU: " + result.stderr[-2000:])
    inventory = {}
    for line in result.stdout.splitlines():
        index, separator, name = line.partition(",")
        if separator:
            inventory[index.strip()] = name.strip()
    chosen = [index.strip() for index in devices.split(",")]
    thor = [inventory[index] for index in chosen
            if re.search(r"thor|t5000|t7000", inventory.get(index, ""), re.I)]
    if not thor:
        return None
    if len(chosen) != 1:
        raise RuntimeError("Thor VIOS setup requires one explicitly selected GPU")
    return {"host_gpu": chosen[0], "name": thor[0]}


def probe_image(image, runtime, devices, docker=command, *, vios=True):
    inspected = docker(["docker", "image", "inspect", image,
                        "--format", "{{json .}}"])
    if inspected.returncode:
        pulled = docker(["docker", "pull", image], timeout=600)
        if pulled.returncode:
            raise RuntimeError("Cannot pull VIOS image: " + pulled.stderr[-2000:])
        inspected = docker(["docker", "image", "inspect", image, "--format", "{{json .}}"])
    if inspected.returncode:
        raise RuntimeError("Cannot inspect VIOS image: " + inspected.stderr[-2000:])
    identity = json.loads(inspected.stdout)
    if identity.get("Architecture") != "arm64" or identity.get("Os") != "linux":
        raise RuntimeError("Thor requires a linux/arm64 VIOS image: " + image)
    name = "vss-perf-cuda-probe-" + uuid.uuid4().hex
    try:
        probe = docker([
            "docker", "run", "--rm", "--pull=never", "--network", "none",
            "--name", name, "--runtime", runtime,
            "-e", "NVIDIA_VISIBLE_DEVICES=" + devices,
            "-e", "NVIDIA_DRIVER_CAPABILITIES=compute,utility,video",
            "-e", "CUDA_VISIBLE_DEVICES=0",
            "-e", "PERF_PROBE_VIOS=" + ("true" if vios else "false"),
            "--entrypoint", "python3", image, "-c", CUDA_PROBE,
        ], timeout=60)
    finally:
        # A timed-out Docker client need not stop its container. Only remove our
        # uniquely named probe; never touch other workloads or change the daemon.
        docker(["docker", "rm", "-f", name], timeout=15)
    if probe.returncode:
        raise RuntimeError("Thor container CUDA preflight failed: " +
                           (probe.stderr or probe.stdout)[-4000:])
    try:
        payload = json.loads(probe.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("CUDA probe did not return device identity") from exc
    names = payload.get("devices", [])
    if len(names) != 1 or not re.search(r"thor|t5000|t7000", names[0], re.I):
        raise RuntimeError("CUDA probe must expose only the selected Thor GPU; got " + repr(names))
    return {"image": image, "image_id": identity["Id"],
            "architecture": "linux/arm64", **payload}


def prepare(root, devices, images, report, runtime="nvidia", rtvi_image=None):
    identity = selected_thor(devices)
    if identity is None:
        return
    result = {"platform": "thor", **identity, "runtime": runtime, "images": []}
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", runtime):
            raise RuntimeError("Invalid PERF_THOR_DOCKER_RUNTIME")
        available = command(["docker", "info", "--format", "{{json .Runtimes}}"])
        if available.returncode or runtime not in json.loads(available.stdout):
            raise RuntimeError("Thor Docker runtime is not registered: " + runtime)
        for image in dict.fromkeys(images):
            result["images"].append(probe_image(image, runtime, devices))
        if rtvi_image:
            result["images"].append(probe_image(rtvi_image, runtime, devices, vios=False))
        for component, services in (
            ("nvstreamer", ["nvstreamer-1"]),
            ("stream-processing", ["sensor-ms", "streamprocessing-ms-1"]),
        ):
            overlay = {"services": {service: {
                "runtime": runtime,
                "environment": {
                    "NVIDIA_VISIBLE_DEVICES": devices,
                    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility,video",
                    "CUDA_VISIBLE_DEVICES": "0",
                },
            } for service in services}}
            (root / component / "thor-runtime.json").write_text(json.dumps(overlay, indent=2) + "\n")
        result["status"] = "PASS"
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        result.update(status="ERROR", reason=str(exc))
        raise RuntimeError(
            str(exc) + "\nVerify host CUDA initialization and Thor device/library injection. "
            "PERF_THOR_DOCKER_RUNTIME may select a separately provisioned runtime; "
            "this setup never changes Docker's global runtime or installs drivers."
        ) from exc
    finally:
        report.write_text(json.dumps(result, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--devices", required=True)
    parser.add_argument("--image", action="append", required=True)
    parser.add_argument("--rtvi-image")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root, args.devices, args.image, args.report,
            os.environ.get("PERF_THOR_DOCKER_RUNTIME", "nvidia"), args.rtvi_image)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print("[PERF] " + str(exc), file=sys.stderr)
        sys.exit(1)

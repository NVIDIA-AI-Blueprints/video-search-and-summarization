# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run exactly one worker per role. SQLite state is scoped to its owner."""

import logging
import os
import subprocess
import sys
from urllib.parse import urlsplit

PORTS = {"ingest": 8020, "analytics": 8021}


def configure_vss(origin):
    """Discover VIOS through the supplied VSS deployment origin."""
    url = urlsplit(origin)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise SystemExit("D1_VSS_ORIGIN must be an HTTP(S) VSS deployment origin without credentials.")
    subprocess.run(
        [os.getenv("VSS_CLI", "vss"), "configure", "--base-url", origin],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def check_capture_runtime():
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    required = ("rtspsrc", "rtph264depay", "h264parse", "nvv4l2decoder", "nvvidconv", "jpegenc")
    missing = [factory for factory in required if Gst.ElementFactory.find(factory) is None]
    if missing:
        raise SystemExit("Required Jetson GStreamer plugins missing: " + ", ".join(missing))


def main(argv):
    if not argv:
        raise SystemExit("Specify one role: ingest, analytics or indexer.")
    role = argv[0]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if len(argv) == 1 and role in PORTS:
        import uvicorn

        if role == "analytics":
            if not (origin := os.getenv("D1_VSS_ORIGIN")):
                raise SystemExit("Set D1_VSS_ORIGIN to the VSS deployment origin for clip retrieval.")
            configure_vss(origin)
        else:
            check_capture_runtime()
        uvicorn.run(
            f"agent_pipeline.{role}:create_app",
            factory=True,
            host="0.0.0.0",
            port=PORTS[role],
            workers=1,
        )
    elif argv == ["indexer"]:
        from agent_pipeline.indexer import main as index

        index()
    elif role == "python" and len(argv) > 1:
        # A separate bootstrap container may run the profile's checked-in init script.
        os.execv(sys.executable, [sys.executable, *argv[1:]])
    else:
        raise SystemExit("Specify one role: ingest, analytics or indexer.")


if __name__ == "__main__":
    main(sys.argv[1:])

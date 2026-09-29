# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RTSP/TCP input with real media PTS and a bounded latest-frame queue."""
from __future__ import annotations

import math
import queue
import time
from datetime import datetime, timezone

from .exterior import exterior_pixels


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def put_latest(frame_queue, packet):
    dropped = 0
    while True:
        try:
            frame_queue.put_nowait(packet)
            return dropped
        except queue.Full:
            try:
                frame_queue.get_nowait()
                dropped += 1
            except queue.Empty:
                pass


def read_stream(source, frame_queue, stop, notify):
    import av
    connection = int(source.get("epoch_offset", 0))
    frame_seq = 0
    while not stop.is_set():
        container = None
        try:
            notify("connecting", {})
            container = av.open(source["url"], mode="r", timeout=(5., 3.),
                                options={"rtsp_transport": "tcp"})
            video = next(s for s in container.streams if s.type == "video")
            connection += 1
            origin_pts = previous_pts = None
            origin_utc = None
            notify("connected", {"connection_epoch": connection})
            for decoded in container.decode(video):
                if stop.is_set():
                    break
                received_monotonic, received_at = time.monotonic(), utc_now()
                if decoded.pts is None or decoded.time_base is None:
                    notify("missing_pts", {})
                    continue
                seconds = float(decoded.pts * decoded.time_base)
                if not math.isfinite(seconds):
                    notify("missing_pts", {})
                    continue
                if decoded.width != source["width"] or decoded.height != source["height"]:
                    raise ValueError("Allowlisted stream dimensions changed")
                if previous_pts is not None and seconds == previous_pts:
                    notify("duplicate_pts", {})
                    continue
                if previous_pts is not None and seconds < previous_pts:
                    connection += 1
                    origin_pts = None
                    notify("pts_reset", {"connection_epoch": connection})
                if origin_pts is None:
                    origin_pts, origin_utc = seconds, received_at
                frame_seq += 1
                packet = {"frame_seq": frame_seq, "connection_epoch": connection,
                          "pts": int(decoded.pts), "time_base": str(decoded.time_base),
                          "pts_seconds": seconds, "epoch_pts_origin": origin_pts,
                          "epoch_receiver_utc": origin_utc, "t": seconds - origin_pts,
                          "received_at": received_at, "received_monotonic": received_monotonic,
                          "image": decoded.to_ndarray(format="bgr24")}
                if source.get("calibration", {}).get("exterior"):
                    packet["exterior_pixels"] = exterior_pixels(packet["image"], source["calibration"]["exterior"])
                    # This independent cheap signal sees every decoded frame before GPU backpressure.
                    notify("exterior_frame", packet)
                dropped = put_latest(frame_queue, packet)
                notify("decoded", {"dropped": dropped, "frame_seq": frame_seq})
                previous_pts = seconds
            if not stop.is_set():
                notify("disconnected", {"reason": "RTSP input ended"})
        except Exception as exc:
            if not stop.is_set():
                # Library exception text can contain an authenticated RTSP URL.
                notify("disconnected", {"reason": type(exc).__name__})
        finally:
            if container is not None:
                container.close()
        if not stop.is_set():
            stop.wait(1.)

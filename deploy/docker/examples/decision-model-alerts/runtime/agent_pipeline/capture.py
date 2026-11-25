# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded Jetson GPU capture. Capture continues independently of model requests."""
import threading
import time
from collections import deque


class Capture:
    def __init__(self, uri, _endpoint=None):
        self.uri = uri
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.frame = None
        self.frame_time = 0
        self.seq = 0
        self.camera = "connecting"
        # The decoder emits at most 10 JPEGs/s. This covers the longest allowed
        # 16-frame, 0.5fps input while bounding both count and retained bytes.
        self.history = deque()
        self.history_bytes = 0

    def accept_frame(self, data, captured=None):
        """Save an actual decoded frame; never synthesize repeated samples."""
        captured = time.monotonic() if captured is None else captured
        with self.lock:
            self.frame = data
            self.frame_time = captured
            self.seq += 1
            self.camera = "streaming"
            self.history.append((self.seq, captured, data))
            self.history_bytes += len(data)
            while len(self.history) > 320 or self.history_bytes > 32 * 1024 * 1024:
                self.history_bytes -= len(self.history.popleft()[2])

    def sample_window(self, frame_count, fps, now=None):
        """Select a near-uniform window of real frames or report why it is unusable.

        Times are host arrival times, not authoritative camera timestamps. The
        effective sample fps describes the chosen window and is never advertised
        as a measurement of the camera's original frame rate or vehicle speed.
        """
        now = time.monotonic() if now is None else now
        with self.lock:
            history = list(self.history)
        if not history or now - history[-1][1] > 5:
            return None, "Waiting for fresh video frames"
        interval = 1.0 / fps
        tolerance = min(0.25, interval * 0.4)
        end = history[-1][1]
        targets = [end - (frame_count - 1 - index) * interval for index in range(frame_count)]
        if history[0][1] > targets[0] + tolerance:
            return None, "Collecting the video input window"
        selected = [min(history, key=lambda frame: abs(frame[1] - target)) for target in targets]
        if any(abs(frame[1] - target) > tolerance for frame, target in zip(selected, targets)):
            return None, "Video input window has a frame gap; waiting for continuous footage"
        if any(b[0] <= a[0] for a, b in zip(selected, selected[1:])):
            return None, "Video input window contains repeated or out-of-order frames"
        if all(frame[2] == selected[0][2] for frame in selected[1:]):
            # Distinct decoder sequences alone do not establish fresh footage.
            # Only exact full-frame duplicates are rejected; stopped vehicles
            # or similar-looking scenes must not be treated as a frozen feed.
            return None, "Video input window is unchanged; waiting for fresh visual evidence"
        duration = selected[-1][1] - selected[0][1]
        if duration <= 0:
            return None, "Video input window has invalid timing"
        effective_fps = (frame_count - 1) / duration
        # Validate the selected frames against the actual span as well as the
        # requested sampling grid. Do not hide irregular arrival gaps with fps.
        if any(abs(frame[1] - (selected[0][1] + index / effective_fps)) > tolerance
               for index, frame in enumerate(selected)):
            return None, "Video input window has irregular timing"
        return {"frames": [frame[2] for frame in selected],
                "sequences": [frame[0] for frame in selected],
                "times": [frame[1] for frame in selected], "fps": effective_fps}, None

    def snapshot(self):
        with self.lock:
            return {"camera": self.camera, "frame_age_ms": (time.monotonic() - self.frame_time) * 1000 if self.frame else None,
                    "history_frames": len(self.history),
                    "history_span_seconds": self.history[-1][1] - self.history[0][1] if self.history else 0}

    def capture(self):
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        while not self.stop.is_set():
            pipeline = None
            try:
                pipeline = Gst.parse_launch(
                    "rtspsrc name=source protocols=tcp latency=200 ! rtph264depay ! h264parse ! "
                    "nvv4l2decoder name=decoder ! nvvidconv ! "
                    "capsfilter name=native caps=video/x-raw,format=I420 ! "
                    "videorate drop-only=true ! video/x-raw,framerate=10/1 ! "
                    "videoscale add-borders=true ! video/x-raw,width=960,height=540,pixel-aspect-ratio=1/1 ! "
                    "jpegenc quality=85 ! "
                    "appsink name=frames emit-signals=true max-buffers=1 drop=true sync=false async=false")
                pipeline.get_by_name("source").set_property("location", self.uri)

                # Without an explicit native-size cap, negotiation can make
                # nvvidconv stretch a 4:3 source to the downstream 16:9 size
                # before videoscale has an opportunity to add borders.
                native_caps = pipeline.get_by_name("native")
                last_dimensions = None

                def preserve_source_dimensions(_pad, info):
                    nonlocal last_dimensions
                    event = info.get_event()
                    if event.type == Gst.EventType.CAPS:
                        structure = event.parse_caps().get_structure(0)
                        width, height = structure.get_value('width'), structure.get_value('height')
                        ok, par_num, par_den = structure.get_fraction('pixel-aspect-ratio')
                        par_num, par_den = (par_num, par_den) if ok else (1, 1)
                        dimensions = (width, height, par_num, par_den)
                        if dimensions != last_dimensions:
                            native_caps.set_property('caps', Gst.Caps.from_string(
                                f'video/x-raw,format=I420,width={width},height={height},'
                                f'pixel-aspect-ratio={par_num}/{par_den}'))
                            last_dimensions = dimensions
                    return Gst.PadProbeReturn.OK

                pipeline.get_by_name('decoder').get_static_pad('src').add_probe(
                    Gst.PadProbeType.EVENT_DOWNSTREAM, preserve_source_dimensions)

                def sample(sink):
                    sample = sink.emit("pull-sample")
                    buffer = sample.get_buffer()
                    data = buffer.extract_dup(0, buffer.get_size())
                    self.accept_frame(data)
                    return Gst.FlowReturn.OK

                pipeline.get_by_name("frames").connect("new-sample", sample)
                pipeline.set_state(Gst.State.PLAYING)
                bus = pipeline.get_bus()
                started = time.monotonic()
                while not self.stop.is_set():
                    message = bus.timed_pop_filtered(500 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
                    with self.lock:
                        last = self.frame_time
                    if message or time.monotonic() - max(started, last) > 20:
                        raise RuntimeError("Capture disconnected")
            except Exception:
                with self.lock:
                    self.camera = "reconnecting"
                    self.frame = None
                    self.frame_time = 0
                    self.history.clear()
                    self.history_bytes = 0
            finally:
                if pipeline:
                    pipeline.set_state(Gst.State.NULL)
            self.stop.wait(2)

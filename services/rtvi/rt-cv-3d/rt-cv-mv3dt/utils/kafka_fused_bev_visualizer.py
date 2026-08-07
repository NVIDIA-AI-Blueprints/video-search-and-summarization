#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#
# kafka_fused_bev_visualizer.py — BEV visualizer for the FUSED topic (mdx-bev,
# published by the BEV Fusion component).
#
# Much simpler than kafka_bev_visualizer.py (the raw mdx-raw consumer): every
# fused Frame is already a complete scene instant — measurement fusion has
# bucketed the per-sensor messages by timestamp and merged same-object
# measurements across cameras — so there is no cross-sensor frame buffering,
# no expected-sensors (msgconv) config, and no timestamp-bucket grouping here.
# One Kafka message = one rendered frame.
#
# Reuses the map loading / scaling / drawing helpers and the Kafka consumer
# factory (with its BEV_KAFKA_BROKER / BEV_KAFKA_TOPIC env overrides) from
# kafka_bev_visualizer.py; that module is imported, not modified.

import os, argparse, signal, time
from datetime import datetime
from collections import defaultdict

import numpy as np, cv2

from schema_pb2 import Frame
from kafka_bev_visualizer import (
    load_map_and_transforms, setup_map_scaling, draw_objects_on_map,
    draw_overlay, create_kafka_consumer, _frame_to_dict, _readable_timestamp)

WINDOW = "Bird-Eye View of Multi-View 3D Tracking (Fused)"


def _parse_fused(msg_value, last_bucket, verbose=False):
    """Parse one mdx-bev message into (frame_dict, bucket_id). The fused Frame's
    id is the fusion bucket key (monotonically increasing), used only as an
    out-of-order/duplicate guard against `last_bucket`. Returns (None, last_bucket)
    for unparseable or stale messages."""
    try:
        frame = Frame()
        frame.ParseFromString(msg_value)
        frame_dict = _frame_to_dict(frame)
    except Exception:
        return None, last_bucket
    try:
        bucket = int(frame_dict.get("id"))
    except (TypeError, ValueError):
        bucket = None
    if bucket is not None:
        if last_bucket is not None and bucket <= last_bucket:
            if verbose:
                print(f"Discarding out-of-order fused frame {bucket} (last {last_bucket})")
            return None, last_bucket
        last_bucket = bucket
    return frame_dict, last_bucket


def _render(frame_dict, T_ov2px, map_img, trajectories, object_colors,
            frame_history, display_count, show_ids):
    """Draw one fused frame. The sequential display counter is used as the
    trajectory frame number (the bucket key is a huge, sparsely-spaced integer,
    useless for the trail-length bookkeeping) and shown in the overlay."""
    frame_data = {frame_dict.get("sensorId", "fused"): frame_dict}
    vis_img, frame_history = draw_objects_on_map(
        frame_data, T_ov2px, map_img, trajectories, object_colors,
        display_count, frame_history, show_ids, average_multi_cam=False)
    return vis_img, frame_history, _readable_timestamp(frame_data)


def real_time_visualization(dataset_path, output_path, show_ids, verbose=False):
    map_img, T_ov2px = load_map_and_transforms(dataset_path)
    map_img_resized, scale_matrix, new_width, new_height = setup_map_scaling(map_img)
    T_ov2px_scaled = np.dot(scale_matrix, T_ov2px)

    trajectories, object_colors, frame_history = defaultdict(list), {}, []

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, new_width, new_height)
    cv2.imshow(WINDOW, map_img_resized.copy())
    cv2.waitKey(1)

    # Live tail (latest + fresh group): only fused frames produced from now on.
    consumer = create_kafka_consumer(f"mv3dt_fused_visualizer_{os.getpid()}",
                                     consumer_timeout_ms=50, auto_offset_reset="latest")
    video_writer = None
    last_bucket = None
    display_count = 0
    print("Controls: 'q'-quit, 'c'-clear, 'r'-record")
    try:
        while True:
            try:
                batch = consumer.poll(timeout_ms=10)
            except Exception:
                batch = {}
            for _, messages in batch.items():
                for msg in messages:
                    frame_dict, last_bucket = _parse_fused(msg.value, last_bucket, verbose)
                    if frame_dict is None:
                        continue
                    display_count += 1
                    vis_img, frame_history, ts = _render(
                        frame_dict, T_ov2px_scaled, map_img_resized, trajectories,
                        object_colors, frame_history, display_count, show_ids)
                    draw_overlay(vis_img, display_count, ts, "REC" if video_writer else "")
                    cv2.imshow(WINDOW, vis_img)
                    if video_writer:
                        video_writer.write(vis_img)

            key = cv2.waitKey(1) & 0xFF
            try:
                window_closed = cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1
            except cv2.error:
                window_closed = True
            if key == ord("q") or window_closed:
                break
            elif key == ord("c"):
                trajectories.clear(); object_colors.clear(); frame_history.clear()
                print("Cleared trajectories")
            elif key == ord("r"):
                if video_writer is None:
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    path = os.path.join(output_path, f"live_fused_trajectory_{stamp}.mp4")
                    os.makedirs(output_path, exist_ok=True)
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    video_writer = cv2.VideoWriter(path, fourcc, 30, (new_width, new_height))
                    print(f"Started recording: {path}" if video_writer.isOpened()
                          else "Recording failed")
                else:
                    video_writer.release(); video_writer = None
                    print("Stopped recording")
    finally:
        if video_writer:
            video_writer.release()
        if consumer:
            consumer.close()
        cv2.destroyAllWindows()


def record_video_headless(dataset_path, output_path, show_ids, verbose=False, fps=30,
                          exit_on_idle=15.0):
    """Headless capture: write each fused frame to an mp4 as it arrives. Finalize on Ctrl+C, or
    once the stream has started, after `exit_on_idle` seconds with no new message (a file source
    plays once then stops; exit_on_idle<=0 disables the auto-exit)."""
    map_img, T_ov2px = load_map_and_transforms(dataset_path)
    map_img_resized, scale_matrix, new_width, new_height = setup_map_scaling(
        map_img, allow_headless=True)
    T_ov2px_scaled = np.dot(scale_matrix, T_ov2px)

    os.makedirs(output_path, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_path = os.path.join(output_path, f"fused_trajectory_video_{stamp}.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    video_writer = cv2.VideoWriter(video_path, fourcc, fps, (new_width, new_height))
    if not video_writer.isOpened():
        print(f"ERROR: could not open VideoWriter at {video_path}")
        return

    stop = {"flag": False}
    def _stop(_signum, _frame):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    trajectories, object_colors, frame_history = defaultdict(list), {}, []
    consumer = None
    written = 0
    last_bucket = None
    waiting_logged = False
    last_msg_time = None   # for the idle auto-exit
    try:
        consumer = create_kafka_consumer(f"mv3dt_fused_rec_{os.getpid()}",
                                         consumer_timeout_ms=50, auto_offset_reset="latest")
        idle_note = f", auto-stop after {exit_on_idle:.0f}s idle" if exit_on_idle > 0 else ""
        print(f"Recording fused BEV → {video_path}  (Ctrl+C to stop{idle_note}) ...",
              flush=True)
        while not stop["flag"]:
            try:
                batch = consumer.poll(timeout_ms=50)
            except Exception:
                batch = {}
            got = False
            for _, messages in batch.items():
                for msg in messages:
                    got = True
                    frame_dict, last_bucket = _parse_fused(msg.value, last_bucket, verbose)
                    if frame_dict is None:
                        continue
                    written += 1
                    vis_img, frame_history, ts = _render(
                        frame_dict, T_ov2px_scaled, map_img_resized, trajectories,
                        object_colors, frame_history, written, show_ids)
                    draw_overlay(vis_img, written, ts)
                    video_writer.write(vis_img)
                    if written % 300 == 0:   # ~10 s at 30 FPS: show recording is alive
                        print(f"  recorded {written} frames ...", flush=True)
            if got:
                last_msg_time = time.time()
            if written == 0 and not waiting_logged:
                print("Waiting for first fused message ...", flush=True)
                waiting_logged = True
            # stream ended once no new message has arrived for exit_on_idle seconds
            if (exit_on_idle > 0 and last_msg_time is not None
                    and (time.time() - last_msg_time) > exit_on_idle):
                print(f"\nNo fused messages for {exit_on_idle:.0f}s — stream ended, finalizing.",
                      flush=True)
                break
    finally:
        # finalize the mp4 first, then hard-exit (consumer.close() can block on a
        # torn-down broker)
        video_writer.release()
        if written == 0:
            try: os.remove(video_path)
            except OSError: pass
            print("\nNo fused frames captured — no video written.", flush=True)
        else:
            print(f"\nVideo saved: {video_path}  ({written} frames)", flush=True)
        os._exit(0)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Kafka BEV visualizer for the fused (mdx-bev) topic")
    parser.add_argument("--dataset-path", type=str, default="datasets/mtmc_4cam",
                        help="Dir with map.png + transforms.yml (T_ov2px)")
    parser.add_argument("--output-path", type=str, default="output_videos",
                        help="Output directory for videos")
    parser.add_argument("--offline", action="store_true",
                        help="Headless: write an mp4 instead of a live window")
    parser.add_argument("--show-ids", action="store_true",
                        help="Show object IDs near trajectory heads")
    parser.add_argument("--verbose", action="store_true",
                        help="Print warnings and diagnostic messages")
    parser.add_argument("--exit-on-idle", type=float, default=15.0,
                        help="Offline mode: finalize and exit after this many seconds with no new "
                             "message once the stream started (default 15; 0 disables).")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.offline:
        record_video_headless(args.dataset_path, args.output_path, args.show_ids,
                              args.verbose, exit_on_idle=args.exit_on_idle)
    else:
        real_time_visualization(args.dataset_path, args.output_path, args.show_ids,
                                args.verbose)


if __name__ == "__main__":
    main()

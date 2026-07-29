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

import os, argparse, signal
from datetime import datetime, timezone
from confluent_kafka import Consumer as _ConfluentConsumer
from schema_pb2 import Frame
from collections import defaultdict
import numpy as np, yaml, cv2, time


class _Msg:
    """kafka-python-style message: only ``.value`` (and a few attrs) are used here."""
    __slots__ = ("value", "topic", "partition", "offset", "timestamp")

    def __init__(self, value, topic, partition, offset, timestamp):
        self.value = value
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.timestamp = timestamp


class KafkaConsumer:
    """Minimal confluent-kafka-backed consumer with the kafka-python ``KafkaConsumer``
    API subset this tool was written against (batch ``poll`` → {(topic, partition):
    [msg]}, ``assignment``, ``topics``, ...).

    Why not kafka-python itself: against a Kafka 4.0 broker (cp-kafka 8.x) it connects
    and assigns partitions but fetches 0 records (KIP-516 topic-id fetch), whereas
    librdkafka / confluent-kafka reads it fine."""

    def __init__(self, *topics, **config):
        self._deser = config.get("value_deserializer") or (lambda x: x)
        # accepted for API-compatibility; the tool drives its own timeouts via poll()
        self._consumer_timeout_ms = config.get("consumer_timeout_ms")
        bs = config.get("bootstrap_servers", "localhost:9092")
        if isinstance(bs, (list, tuple)):
            bs = ",".join(bs)
        conf = {
            "bootstrap.servers": bs,
            "group.id": config.get("group_id") or "bev_visualizer",
            "auto.offset.reset": config.get("auto_offset_reset", "latest"),
            "enable.auto.commit": bool(config.get("enable_auto_commit", True)),
        }
        self._c = _ConfluentConsumer(conf)
        if topics:
            self._c.subscribe(list(topics))

    def subscribe(self, topics):
        self._c.subscribe(list(topics))

    def poll(self, timeout_ms=1000, max_records=1000):
        """Return {(topic, partition): [msg, ...]} like kafka-python's batch poll."""
        msgs = self._c.consume(num_messages=max_records, timeout=max(int(timeout_ms), 1) / 1000.0)
        batch = {}
        for m in msgs:
            if m is None or m.error():
                continue
            batch.setdefault((m.topic(), m.partition()), []).append(
                _Msg(self._deser(m.value()), m.topic(), m.partition(), m.offset(), m.timestamp())
            )
        return batch

    def assignment(self):
        return set(self._c.assignment())

    def topics(self):
        return set(self._c.list_topics(timeout=5).topics.keys())

    def partitions_for_topic(self, topic):
        t = self._c.list_topics(topic=topic, timeout=5).topics.get(topic)
        return set(t.partitions.keys()) if t and not t.error else set()

    def list_consumer_group_offsets(self, *a, **k):  # only used in a try/except debug path
        return {}

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass


def extract_expected_sensors(msgconv_config):
    """Extract expected sensor IDs from config_msgconv.txt file."""
    try:
        expected_sensors = []
        
        with open(msgconv_config, 'r') as f:
            lines = f.readlines()
        
        current_section = None
        for line in lines:
            line = line.strip()
            
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
                
            # Check for section header [sensorX]
            if line.startswith('[sensor') and line.endswith(']'):
                current_section = line[1:-1]  # Remove brackets
                continue
                
            # Look for id= lines within sensor sections
            if current_section and line.startswith('id='):
                sensor_id = line.split('=', 1)[1].strip()
                if sensor_id:
                    expected_sensors.append(sensor_id)
        
        if not expected_sensors:
            raise ValueError("No sensor IDs found in msgconv config file")
            
        return expected_sensors
        
    except (FileNotFoundError, ValueError) as e:
        raise Exception(f"Could not parse msgconv config file {msgconv_config}: {e}")


def _rfc3339_to_posix(s):
    """Parse an RFC3339 UTC timestamp (e.g. '2024-06-10T06:13:20.000000005Z', as produced
    by MessageToDict for the well-known Timestamp) to POSIX seconds (float). The fractional
    part is kept as a float so sub-microsecond precision survives the ms-bucket rounding."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if "." in s:
        base, frac = s.split(".", 1)
        frac_sec = float("0." + frac)
    else:
        base, frac_sec = s, 0.0
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.timestamp() + frac_sec


def _frame_posix(frame_dict):
    """POSIX seconds from a frame_dict's timestamp. MessageToDict renders the well-known
    Timestamp as an RFC3339 string; tolerate a {seconds, nanos} dict too. None if absent."""
    ts = frame_dict.get("timestamp")
    if isinstance(ts, str):
        try:
            return _rfc3339_to_posix(ts)
        except (ValueError, TypeError):
            return None
    if isinstance(ts, dict):
        return int(ts.get("seconds", 0)) + int(ts.get("nanos", 0)) / 1e9
    return None


def _frame_to_dict(frame):
    """Minimal dict with ONLY the fields the visualizer uses, via direct protobuf access.

    Replaces MessageToDict(frame), which converts the entire Frame — including each object's
    embedding / pose / gaze and bbox3d.embeddings vectors — to nested Python dicts on every
    message. At high camera counts (e.g. 28 cams x 2 instances ~ 840 msg/s) that conversion is
    the consumer's throughput bottleneck: the client falls behind real time, a broker backlog
    builds up, and the BEV view freezes / jumps as it processes stale messages in bursts.
    Extracting just id/sensorId/timestamp/objects[].(id,bbox3d.coordinates) is far cheaper, and
    keeping timestamp as {seconds,nanos} also avoids the RFC3339 string round-trip in
    _frame_posix. The returned shape matches the keys the rest of the tool expects."""
    return {
        'id': frame.id,
        'sensorId': frame.sensorId,
        'timestamp': {'seconds': frame.timestamp.seconds, 'nanos': frame.timestamp.nanos},
        'objects': [
            {'id': o.id, 'bbox3d': {'coordinates': list(o.bbox3d.coordinates)}}
            for o in frame.objects
        ],
    }


def _readable_timestamp(frame_data):
    """Human-readable UTC timestamp for a frame (its group of per-sensor messages).
    Uses the earliest sensor timestamp, to ms precision. '' if none is parseable."""
    posixes = [p for fd in frame_data.values() if (p := _frame_posix(fd)) is not None]
    if not posixes:
        return ""
    dt = datetime.fromtimestamp(min(posixes), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d} UTC"


class FrameBuffer:
    def __init__(self, expected_sensors=None, timeout=0.5, lookahead_frames=1,
                 timestamp_mode=False, bucket_ms=17.0, flush_delay_ms=100.0):
        """
        A buffered frame t is flushed when 1 of these conditions is met:
            1. Messages from all expected sensors are received at frame t
            2. Current time - first message time at frame t > timeout (wall-clock fallback)
            3. Frame-id mode:   the frame t+lookahead_frames has been received.
               Timestamp mode:  data at least `flush_delay_ms` newer (in bucket units) has
                                been received — a time watermark, NOT "any newer bucket".

        Keying (the "t" above):
            timestamp_mode=False (default) — key by integer frame id (frames are grouped only
                if they share the exact same id; correct for single-container MV3DT).
            timestamp_mode=True            — key by timestamp bucket, round(posix_ms/bucket_ms),
                mirroring measurement-fusion: frame-id counters diverge across containers but
                wall-clock timestamps stay aligned, so same-instant frames bucket together.

        Why the watermark (timestamp mode): with N containers and sparse per-camera output
        (MV3DT only emits a camera that has objects), condition 1 (ALL expected sensors) can
        never fire, and the containers' timestamps skew by up to a couple of frames. Flushing
        on "any newer bucket" then (a) emits a bucket before the lagging container's messages
        for that instant arrive (half the objects flicker) and (b) leaves the live-edge bucket
        to stall for the full `timeout` (~0.5 s freeze, then a burst). Holding each bucket
        until ~flush_delay_ms of newer data has arrived fixes both: it lets the skewed
        containers' messages land, and keeps a steady cadence without the timeout stall.
        """

        self.frame_data = defaultdict(dict)
        self.expected_sensors = expected_sensors or set()
        self.timeout = timeout
        self.timestamps = {}
        self.lookahead_frames = lookahead_frames
        self.timestamp_mode = timestamp_mode
        self.bucket_ms = bucket_ms
        # number of bucket steps that constitute the flush watermark (>= 1)
        self.lag_buckets = max(1, round(flush_delay_ms / bucket_ms)) if bucket_ms > 0 else 1

    def _key_for(self, frame_id, frame_dict):
        """Buffer key for a message: a timestamp bucket index (timestamp_mode) or the integer
        frame id. Falls back to the frame id if the timestamp can't be read."""
        if self.timestamp_mode:
            posix = _frame_posix(frame_dict)
            if posix is not None:
                return round(posix * 1000.0 / self.bucket_ms)
        try:
            return int(frame_id)
        except (ValueError, TypeError):
            return frame_id

    def add_frame(self, frame_id, sensor_id, frame_dict):
        key = self._key_for(frame_id, frame_dict)
        if key not in self.timestamps:
            self.timestamps[key] = time.time()
        self.frame_data[key][sensor_id] = frame_dict
        if not self.expected_sensors:
            self.expected_sensors.add(sensor_id)
    
    def get_complete_frame(self):
        current_time = time.time()
        num_expected = len(self.expected_sensors)
        # Process frames in ascending order when possible
        def _key_fn(x):
            try:
                return int(x)
            except Exception:
                return float('inf')
        for frame_id in sorted(list(self.frame_data.keys()), key=_key_fn):
            sensors = set(self.frame_data[frame_id].keys())
            frame_time = self.timestamps.get(frame_id, current_time)
            # Condition 1: full set received for this frame
            full_received = (num_expected > 0 and len(sensors) >= num_expected and sensors.issubset(self.expected_sensors))
            # Condition 2: timeout
            timed_out = (current_time - frame_time) > self.timeout
            # Condition 3: a future frame has been received. Frame-id mode looks for the exact
            # frame t+lookahead_frames; timestamp mode treats any strictly-newer bucket as the
            # "future" (buckets aren't unit-spaced, so an exact +1 rarely exists).
            future_received = False
            if num_expected > 0:
                if self.timestamp_mode:
                    # time watermark: flush once data >= lag_buckets newer has arrived, so the
                    # time-skewed containers' messages for this instant land first and the
                    # live-edge bucket isn't held for the full `timeout` (see __init__).
                    newest = max(self.frame_data) if self.frame_data else frame_id
                    future_received = (newest - frame_id) >= self.lag_buckets
                else:
                    try:
                        base_id = int(frame_id)
                        future_id = base_id + self.lookahead_frames
                        future_data = self.frame_data.get(future_id)
                        if future_data is not None:
                            future_received = True
                    except Exception:
                        pass
            if full_received or timed_out or future_received:
                # print ('frame_id', frame_id, 'full_received', full_received, 'timed_out', timed_out, 'future_received', future_received)
                frame_data = self.frame_data.pop(frame_id)
                self.timestamps.pop(frame_id, None)
                return frame_id, frame_data
        return None, None
    
    def get_all_complete_frames(self):
        complete_frames = []
        for frame_id, sensors_data in self.frame_data.items():
            if len(sensors_data) > 0:
                try:
                    complete_frames.append((int(frame_id), frame_id, sensors_data))
                except (ValueError, TypeError):
                    complete_frames.append((0, frame_id, sensors_data))
        complete_frames.sort(key=lambda x: x[0])
        return [(frame_id, data) for _, frame_id, data in complete_frames]
    
    def get_frame(self, frame_id):
        """Get frame data for specific frame_id without waiting for conditions, and remove it from buffer"""
        if frame_id in self.frame_data:
            frame_data = self.frame_data.pop(frame_id)
            self.timestamps.pop(frame_id, None)
            return frame_id, frame_data
        return None, None

def draw_overlay(vis_img, frame_id, timestamp_str="", recording=""):
    """Bottom-left overlay: 'Frame: <n>', plus the readable timestamp when given
    (timestamp-buffer mode, where <n> is a sequential display counter rather than the
    meaningless bucket key). Drawing only — no display/write (used by both the live window
    and the headless recorder)."""
    info = f"Frame: {frame_id}"
    if timestamp_str:
        info += f"  {timestamp_str}"
    info = f"{info} {recording}".rstrip()
    cv2.putText(vis_img, info, (10, vis_img.shape[0] - 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)


def display_frame(vis_img, frame_id, video_writer, timestamp_str=""):
    """Draw the overlay, then show in the live window and write to video."""
    draw_overlay(vis_img, frame_id, timestamp_str, "REC" if video_writer else "")
    cv2.imshow('Bird-Eye View of Multi-View 3D Tracking', vis_img)
    if video_writer:
        video_writer.write(vis_img)

def load_map_and_transforms(dataset_path):
    """Load map image and transformation matrix from dataset"""
    map_path = os.path.join(dataset_path, 'map.png')
    transforms_path = os.path.join(dataset_path, 'transforms.yml')

    with open(transforms_path, 'r') as f:
        transforms = yaml.safe_load(f)
    T_ov2px = np.array(transforms['T_ov2px']).reshape(3, 3)

    map_img = cv2.imread(map_path)
    if map_img is None:
        raise FileNotFoundError(f"Map image not found at {map_path}")
    
    return map_img, T_ov2px

def setup_map_scaling(map_img, target_width_ratio=0.8, target_height_ratio=0.8, allow_headless=False):
    """Setup map scaling based on screen size"""
    try:
        import tkinter as tk
        root = tk.Tk()
        screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
        root.destroy()
    except Exception:
        # No X display (headless). The offline/video path doesn't need one — fall back to a
        # fixed canvas size (override via MV3DT_BEV_WIDTH/HEIGHT). Real-time mode keeps raising.
        if not allow_headless:
            raise
        screen_width = int(os.environ.get('MV3DT_BEV_WIDTH', 1280))
        screen_height = int(os.environ.get('MV3DT_BEV_HEIGHT', 720))

    target_width = int(screen_width * target_width_ratio)
    target_height = int(screen_height * target_height_ratio)

    map_height, map_width = map_img.shape[:2]
    scale = min(target_width / map_width, target_height / map_height)
    new_width, new_height = int(map_width * scale), int(map_height * scale)

    map_img_resized = cv2.resize(map_img, (new_width, new_height))
    scale_matrix = np.array([[scale, 0, 0], [0, scale, 0], [0, 0, 1]])

    return map_img_resized, scale_matrix, new_width, new_height

def create_kafka_consumer(group_id, consumer_timeout_ms=None, auto_offset_reset='earliest'):
    """Create and configure Kafka consumer"""
    # [rt-cv-3d-mv3dt-standalone] broker/topic overridable via env so this reference tool can
    # consume the testbed's sink (e.g. mdx-raw) without editing its logic. Defaults
    # preserve the upstream behavior (localhost:9092, topic 'mv3dt').
    bev_broker = os.environ.get('BEV_KAFKA_BROKER', 'localhost:9092')
    bev_topic = os.environ.get('BEV_KAFKA_TOPIC', 'mv3dt')
    config = {
        'bootstrap_servers': bev_broker,
        'auto_offset_reset': auto_offset_reset,
        'value_deserializer': lambda x: x,
        'group_id': group_id,
        'enable_auto_commit': True
    }

    if consumer_timeout_ms is not None:
        config['consumer_timeout_ms'] = consumer_timeout_ms

    consumer = KafkaConsumer(**config)
    consumer.subscribe([bev_topic])
    print(f"Connected to Kafka ({bev_broker}) and subscribed to '{bev_topic}' topic")
    return consumer

def draw_objects_on_map(frame_data, T_ov2px, map_img, trajectories, object_colors, frame_id, frame_history, show_ids=False, average_multi_cam=False):
    vis_img = map_img.copy()
    colors = [
        (255, 0, 0), (0, 255, 255), (139, 69, 19), (0, 255, 0), (255, 0, 255),
        (50, 205, 50), (255, 140, 0), (0, 0, 255), (255, 165, 0), (255, 105, 180),
        (75, 0, 130), (255, 255, 0), (0, 128, 128), (0, 191, 255), (154, 205, 50),
        (255, 20, 147), (30, 144, 255), (128, 0, 128), (220, 20, 60), (0, 206, 209)
    ]
    
    all_objects = [obj for frame_dict in frame_data.values() for obj in frame_dict.get('objects', [])]
    
    try:
        current_frame_num = int(frame_id)
        frame_history.append(current_frame_num)
        if len(frame_history) > 240:
            frame_history = frame_history[-240:]
    except:
        current_frame_num = 0
    
    current_objects = set()
    
    if average_multi_cam:
        # Group objects by ID across all cameras for averaging
        objects_by_id = defaultdict(list)
        for obj in all_objects:
            bbox_3d = obj.get('bbox3d', {}).get('coordinates', {})
            if bbox_3d:
                try:
                    world_x, world_y = bbox_3d[:2]
                    object_id = obj.get('id', 0)
                    objects_by_id[object_id].append((world_x, world_y))
                except:
                    continue
        
        # Calculate average positions and add to trajectories
        for object_id, positions in objects_by_id.items():
            if positions:
                # Calculate average world position
                avg_world_x = sum(pos[0] for pos in positions) / len(positions)
                avg_world_y = sum(pos[1] for pos in positions) / len(positions)
                
                try:
                    # Convert to pixel coordinates
                    pt_ov_h = np.array([avg_world_x, avg_world_y, 1.0])
                    pt_px_h = np.dot(T_ov2px, pt_ov_h)
                    pt_px_h /= pt_px_h[2]
                    px_x, px_y = int(pt_px_h[0]), int(pt_px_h[1])
                    
                    current_objects.add(object_id)
                    
                    if object_id not in object_colors:
                        object_colors[object_id] = colors[len(object_colors) % len(colors)]
                    
                    trajectories[object_id].append((px_x, px_y, current_frame_num))
                except:
                    continue
    else:
        # Original behavior: show all trajectory points from all cameras
        for obj in all_objects:
            bbox_3d = obj.get('bbox3d', {}).get('coordinates', {})
            if not bbox_3d:
                continue
            try:
                world_x, world_y = bbox_3d[:2]
                pt_ov_h = np.array([world_x, world_y, 1.0])
                pt_px_h = np.dot(T_ov2px, pt_ov_h)
                pt_px_h /= pt_px_h[2]
                px_x, px_y = int(pt_px_h[0]), int(pt_px_h[1])
                
                object_id = obj.get('id', 0)
                current_objects.add(object_id)
                
                if object_id not in object_colors:
                    object_colors[object_id] = colors[len(object_colors) % len(colors)]
                
                trajectories[object_id].append((px_x, px_y, current_frame_num))
            except:
                continue
    
    # Cleanup old trajectory points
    frame_threshold = current_frame_num - 240
    for object_id in list(trajectories.keys()):
        trajectories[object_id] = [(x, y, f) for x, y, f in trajectories[object_id] if f >= frame_threshold]
        if not trajectories[object_id]:
            del trajectories[object_id]
            object_colors.pop(object_id, None)
    
    # Draw trajectories
    for object_id, traj_points in trajectories.items():
        if not traj_points:
            continue
        color = object_colors.get(object_id, (128, 128, 128))
        base_alpha = 0.9 if object_id in current_objects else 0.6
        min_alpha = 0.3  # Minimum brightness to prevent complete black
        
        for i, (x, y, _) in enumerate(traj_points):
            # Slower fade: use square root for gentler curve
            fade_ratio = (i / max(1, len(traj_points) - 1)) ** 0.5
            fade = min_alpha + (base_alpha - min_alpha) * fade_ratio
            fade_color = tuple(int(c * fade) for c in color)
            cv2.circle(vis_img, (x, y), 1, fade_color, -1)
    
    # Draw current positions with ID labels
    if average_multi_cam:
        # Draw averaged positions for each object
        objects_by_id = defaultdict(list)
        for obj in all_objects:
            bbox_3d = obj.get('bbox3d', {}).get('coordinates', {})
            if bbox_3d:
                try:
                    world_x, world_y = bbox_3d[:2]
                    object_id = obj.get('id', 0)
                    objects_by_id[object_id].append((world_x, world_y))
                except:
                    continue
        
        for object_id, positions in objects_by_id.items():
            if positions and object_id in object_colors:
                # Calculate average world position
                avg_world_x = sum(pos[0] for pos in positions) / len(positions)
                avg_world_y = sum(pos[1] for pos in positions) / len(positions)
                
                try:
                    # Convert to pixel coordinates
                    pt_ov_h = np.array([avg_world_x, avg_world_y, 1.0])
                    pt_px_h = np.dot(T_ov2px, pt_ov_h)
                    pt_px_h /= pt_px_h[2]
                    px_x, px_y = int(pt_px_h[0]), int(pt_px_h[1])
                    
                    # Draw object circle
                    cv2.circle(vis_img, (px_x, px_y), 3, object_colors[object_id], -1)
                    
                    # Draw ID label near the object (if enabled)
                    if show_ids:
                        label_x = px_x + 8
                        label_y = px_y - 8
                        cv2.putText(vis_img, str(object_id), (label_x, label_y), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
                except:
                    continue
    else:
        # Original behavior: draw all object positions from all cameras
        for obj in all_objects:
            bbox_3d = obj.get('bbox3d', {}).get('coordinates', {})
            if bbox_3d:
                try:
                    world_x, world_y = bbox_3d[:2]
                    pt_ov_h = np.array([world_x, world_y, 1.0])
                    pt_px_h = np.dot(T_ov2px, pt_ov_h)
                    pt_px_h /= pt_px_h[2]
                    px_x, px_y = int(pt_px_h[0]), int(pt_px_h[1])
                    
                    object_id = obj.get('id', 0)
                    if object_id in object_colors:
                        # Draw object circle
                        cv2.circle(vis_img, (px_x, px_y), 3, object_colors[object_id], -1)
                        
                        # Draw ID label near the object (if enabled)
                        if show_ids:
                            label_x = px_x + 8
                            label_y = px_y - 8
                            cv2.putText(vis_img, str(object_id), (label_x, label_y), 
                                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
                except:
                    continue
    
    return vis_img, frame_history

def record_video_headless(dataset_path, output_path, show_ids, expected_sensors, average_multi_cam,
                          verbose=False, timestamp_mode=False, bucket_ms=17.0, flush_delay_ms=100.0,
                          fps=30, exit_on_idle=15.0):
    """Headless offline capture: write every completed frame to the mp4 as it drains (lossless).
    Finalize on Ctrl+C, or once the stream has started, after `exit_on_idle` seconds with no new
    message (a file source plays once then stops; exit_on_idle<=0 disables the auto-exit)."""
    map_img, T_ov2px = load_map_and_transforms(dataset_path)
    map_img_resized, scale_matrix, new_width, new_height = setup_map_scaling(map_img, allow_headless=True)
    T_ov2px_scaled = np.dot(scale_matrix, T_ov2px)

    os.makedirs(output_path, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_path = os.path.join(output_path, f"trajectory_video_{stamp}.mp4")
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    video_writer = cv2.VideoWriter(video_path, fourcc, fps, (new_width, new_height))
    if not video_writer.isOpened():
        print(f"ERROR: could not open VideoWriter at {video_path}")
        return

    stop = {"flag": False}   # Ctrl+C (SIGINT/SIGTERM) -> finalize the mp4
    def _stop(_signum, _frame):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    frame_buffer = FrameBuffer(expected_sensors=expected_sensors, timestamp_mode=timestamp_mode,
                               bucket_ms=bucket_ms, flush_delay_ms=flush_delay_ms)
    trajectories, object_colors, frame_history = defaultdict(list), {}, []
    consumer = None
    written = 0
    last_frame_id = None
    waiting_logged = False
    last_msg_time = None   # for the idle auto-exit
    try:
        # live tail, fresh group: capture from subscribe onward (no stale backlog)
        consumer = create_kafka_consumer(f'mv3dt_bev_rec_{os.getpid()}', consumer_timeout_ms=50,
                                         auto_offset_reset='latest')
        idle_note = f", auto-stop after {exit_on_idle:.0f}s idle" if exit_on_idle > 0 else ""
        print(f"Recording BEV → {video_path}  (Ctrl+C to stop{idle_note}) ...", flush=True)

        while not stop["flag"]:
            got = False
            try:
                batch = consumer.poll(timeout_ms=50)
                for _, messages in batch.items():
                    for msg in messages:
                        try:
                            frame = Frame()
                            frame.ParseFromString(msg.value)
                            frame_dict = _frame_to_dict(frame)
                            frame_buffer.add_frame(frame_dict.get('id', 'unknown'),
                                                   frame_dict.get('sensorId', 'unknown'), frame_dict)
                            got = True
                        except Exception:
                            continue
            except Exception:
                pass
            if got:
                last_msg_time = time.time()

            # Write every ready frame (offline recording is lossless — the live window keeps
            # only the newest bucket to stay real-time). Buckets drain in ascending order.
            while True:
                frame_id, frame_data = frame_buffer.get_complete_frame()
                if not frame_data:
                    break
                if timestamp_mode:
                    if last_frame_id is not None and frame_id <= last_frame_id:
                        continue
                    last_frame_id = frame_id
                    vis_img, frame_history = draw_objects_on_map(
                        frame_data, T_ov2px_scaled, map_img_resized, trajectories, object_colors,
                        frame_id, frame_history, show_ids, average_multi_cam)
                    # timestamp mode: sequential counter + readable timestamp (bucket key is meaningless)
                    draw_overlay(vis_img, written + 1, _readable_timestamp(frame_data))
                    video_writer.write(vis_img); written += 1
                else:
                    if last_frame_id is not None and 0 < last_frame_id - frame_id < 30:
                        continue
                    last_frame_id = frame_id
                    vis_img, frame_history = draw_objects_on_map(
                        frame_data, T_ov2px_scaled, map_img_resized, trajectories, object_colors,
                        frame_id, frame_history, show_ids, average_multi_cam)
                    draw_overlay(vis_img, frame_id)   # frame-id mode: raw frame id
                    video_writer.write(vis_img); written += 1
                if written % 300 == 0:   # ~10 s at 30 FPS: show recording is alive
                    print(f"  recorded {written} frames ...", flush=True)

            if written == 0 and not waiting_logged:
                print("Waiting for first message ...", flush=True)
                waiting_logged = True

            # stream ended once no new message has arrived for exit_on_idle seconds
            if (exit_on_idle > 0 and last_msg_time is not None
                    and (time.time() - last_msg_time) > exit_on_idle):
                print(f"\nNo messages for {exit_on_idle:.0f}s — stream ended, finalizing.", flush=True)
                break
    finally:
        # finalize the mp4 first, then hard-exit (consumer.close() can block on a torn-down broker)
        video_writer.release()
        if written == 0:
            try: os.remove(video_path)
            except OSError: pass
            print("\nNo frames captured — no video written.", flush=True)
        else:
            print(f"\nVideo saved: {video_path}  ({written} frames)", flush=True)
        os._exit(0)


def real_time_visualization(dataset_path, output_path, show_ids, expected_sensors, average_multi_cam, verbose=False,
                            timestamp_mode=False, bucket_ms=17.0, flush_delay_ms=100.0):
    # Setup common components
    map_img, T_ov2px = load_map_and_transforms(dataset_path)
    map_img_resized, scale_matrix, new_width, new_height = setup_map_scaling(map_img)
    T_ov2px_scaled = np.dot(scale_matrix, T_ov2px)

    frame_buffer = FrameBuffer(expected_sensors=expected_sensors,
                               timestamp_mode=timestamp_mode, bucket_ms=bucket_ms,
                               flush_delay_ms=flush_delay_ms)
    trajectories, object_colors, frame_history = defaultdict(list), {}, []
    
    cv2.namedWindow('Bird-Eye View of Multi-View 3D Tracking', cv2.WINDOW_NORMAL)
    cv2.resizeWindow('Bird-Eye View of Multi-View 3D Tracking', new_width, new_height)
    
    # Display blank map initially
    initial_img = map_img_resized.copy()
    cv2.imshow('Bird-Eye View of Multi-View 3D Tracking', initial_img)
    cv2.waitKey(1)  # Process window events

    consumer = None
    video_writer = None
    
    try:
        # Real-time mode consumes the LIVE TAIL (auto_offset_reset='latest') with a fresh
        # per-process group, so it never replays the historical backlog. That backlog is the
        # pipeline's startup phase, where instance0 starts earlier and races through its
        # backlog at >30 FPS — so the two instances' timestamps are seconds apart and only
        # converge after ~50s. Replaying it (the old 'earliest') made the view fast-forward
        # through one instance then freeze while the other caught up. Tailing live skips it;
        # once the instances are synced the tail is smooth. (Offline/video keeps 'earliest'.)
        consumer = create_kafka_consumer(f'mv3dt_visualizer_{os.getpid()}',
                                         consumer_timeout_ms=50, auto_offset_reset='latest')
        
        # Wait for partition assignment (up to 5 seconds)
        assignment_timeout = 5.0
        start_time = time.time()
        while not consumer.assignment() and (time.time() - start_time) < assignment_timeout:
            consumer.poll(timeout_ms=100)  # This triggers partition assignment
            time.sleep(0.1)
        
        print(f"Consumer assignment: {consumer.assignment()}")
        if not consumer.assignment():
            print("Warning: No partitions assigned. Topic might not exist or have no partitions.")
            
            # Try to get topic metadata for debugging
            try:
                metadata = consumer.list_consumer_group_offsets()
                topics = consumer.topics()
                print(f"Available topics: {topics}")
                if 'mv3dt' in topics:
                    partitions = consumer.partitions_for_topic('mv3dt')
                    print(f"Partitions for 'mv3dt' topic: {partitions}")
                else:
                    print("Topic 'mv3dt' not found!")
            except Exception as debug_e:
                print(f"Could not get topic metadata: {debug_e}")
            
    except Exception as e:
        print(f"Kafka connection failed: {e}")
    
    print("Controls: 'q'-quit, 'c'-clear, 'r'-record")
    
    last_update = time.time()
    last_frame_id = None
    display_count = 0   # sequential frame counter shown in timestamp-buffer mode
    try:
        while True:
            current_time = time.time()
            
            if consumer:
                try:
                    batch = consumer.poll(timeout_ms=10)
                    for _, messages in batch.items():
                        for msg in messages:
                            try:
                                frame = Frame()
                                frame.ParseFromString(msg.value)
                                frame_dict = _frame_to_dict(frame)
                                # print ('Received frame', frame_dict.get('id', 'unknown'), 'from sensor', frame_dict.get('sensorId', 'unknown'))
                                frame_buffer.add_frame(frame_dict.get('id', 'unknown'),
                                                     frame_dict.get('sensorId', 'unknown'), frame_dict)
                            except:
                                continue
                except:
                    pass
            
            pending_ts = None   # newest ready bucket this cycle (timestamp-mode frame-drop)
            while True:
                frame_id, frame_data = frame_buffer.get_complete_frame()
                if not frame_data:
                    break

                if not timestamp_mode:
                    # Frame-id keying: contiguous integer counter, so apply the previous-run
                    # discard, missing-frame fill, and late-message window heuristics.
                    # If the first frame is > 100, it's likely from previous run.
                    if last_frame_id is None and frame_id > 100:
                        if verbose:
                            print(f"Discarding frame {frame_id} (from previous run)")
                        continue

                    if last_frame_id is not None and frame_id - last_frame_id > 1:
                        # Process missing frames in between
                        for missing_frame_id in range(last_frame_id + 1, frame_id):
                            missing_id, missing_data = frame_buffer.get_frame(missing_frame_id)
                            if missing_data:
                                # Render frame with tracking data
                                vis_img, frame_history = draw_objects_on_map(missing_data, T_ov2px_scaled,
                                                                            map_img_resized, trajectories, object_colors, missing_frame_id, frame_history, show_ids, average_multi_cam)
                                display_frame(vis_img, missing_frame_id, video_writer)
                            else:
                                # Render empty frame with correct frame_id
                                empty_vis_img = map_img_resized.copy()
                                display_frame(empty_vis_img, missing_frame_id, video_writer)
                    if last_frame_id is not None and 0 < last_frame_id - frame_id < 30:
                        if verbose:
                            print(f"Received late message from frame {frame_id}, last_frame_id was {last_frame_id}")
                            print(f"Discarding frame {frame_id} (late message)")
                        continue
                    last_frame_id = frame_id
                    display_count += 1
                    vis_img, frame_history = draw_objects_on_map(frame_data, T_ov2px_scaled,
                                                                map_img_resized, trajectories, object_colors, frame_id, frame_history, show_ids, average_multi_cam)
                    display_frame(vis_img, frame_id, video_writer)
                else:
                    # Timestamp-bucket keying: buckets are time-quantized (not unit-spaced) and
                    # already flushed in ascending order, so there's no synthetic gap fill — just
                    # drop any out-of-order / duplicate bucket older-or-equal than the last shown.
                    if last_frame_id is not None and frame_id <= last_frame_id:
                        if verbose:
                            print(f"Discarding out-of-order bucket {frame_id} (last shown {last_frame_id})")
                        continue
                    last_frame_id = frame_id
                    # Keep ONLY the newest ready bucket; draw it after the drain. When rendering
                    # can't keep up (e.g. 28 cams x 2 GPUs) several buckets become ready per poll,
                    # and drawing them all in a burst is what froze the view and made the counter
                    # jump ~10. Drawing just the latest keeps the BEV display real-time.
                    pending_ts = (frame_id, frame_data)

            # timestamp mode: draw only the newest bucket from this cycle (frame-drop). The
            # sequential counter advances by 1 per shown frame, so it stays smooth instead of
            # leaping; the readable-timestamp overlay reflects the (possibly skipped) real time.
            if timestamp_mode and pending_ts is not None:
                frame_id, frame_data = pending_ts
                display_count += 1
                vis_img, frame_history = draw_objects_on_map(frame_data, T_ov2px_scaled,
                                                            map_img_resized, trajectories, object_colors, frame_id, frame_history, show_ids, average_multi_cam)
                display_frame(vis_img, display_count, video_writer, _readable_timestamp(frame_data))
            
            last_update = current_time
            
            key = cv2.waitKey(1) & 0xFF
            try:
                window_closed = cv2.getWindowProperty('Bird-Eye View of Multi-View 3D Tracking', cv2.WND_PROP_VISIBLE) < 1
            except cv2.error:
                window_closed = True
            if key == ord('q') or window_closed:
                break
            elif key == ord('c'):
                trajectories.clear()
                object_colors.clear()
                frame_history.clear()
                print("Cleared trajectories")
            elif key == ord('r'):
                if video_writer is None:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    live_video_path = os.path.join(output_path, f"live_trajectory_{timestamp}.mp4")
                    os.makedirs(output_path, exist_ok=True)
                    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                    video_writer = cv2.VideoWriter(live_video_path, fourcc, 30, (new_width, new_height))
                    print(f"Started recording: {live_video_path}" if video_writer.isOpened() else "Recording failed")
                else:
                    video_writer.release()
                    video_writer = None
                    print("Stopped recording")
                
    finally:
        if video_writer:
            video_writer.release()
        if consumer:
            consumer.close()
        cv2.destroyAllWindows()
        
def parse_args():
    parser = argparse.ArgumentParser(description='Kafka BEV Online Visualizer')
    parser.add_argument('--dataset-path', type=str, 
                       default="datasets/mtmc_4cam",
                       help='Path to dataset)')
    parser.add_argument('--msgconv-config', type=str, 
                       default='config_msgconv.txt',
                       help='Path to message converter config file (config_msgconv.txt)')
    parser.add_argument('--output-path', type=str, 
                       default='output_videos',
                       help='Output directory for videos')
    parser.add_argument('--offline', action='store_true',
                       help='Run in offline mode (save a video from all messages instead of real-time visualization). \
                           Please run this script after launching the MV3DT app.')
    parser.add_argument('--show-ids', action='store_true',
                       help='Show object IDs near trajectory heads')
    parser.add_argument('--average-multi-cam', action='store_true',
                       help='Average trajectory points from multiple cameras for the same object')
    parser.add_argument('--verbose', action='store_true',
                       help='Print warnings and diagnostic messages')
    parser.add_argument('--timestamp-buffer', action='store_true',
                       help='Buffer/group messages by timestamp bucket (round(posix_ms/bucket-ms)) '
                            'instead of by frame id. Needed for multi-container MV3DT, where frame-id '
                            'counters diverge but wall-clock timestamps stay aligned (mirrors '
                            'measurement-fusion). Default off = group by frame id, as before.')
    parser.add_argument('--bucket-ms', type=float, default=17.0,
                       help='Timestamp bucket width in ms when --timestamp-buffer is set '
                            '(default 17 = half a 30-FPS frame).')
    parser.add_argument('--timestamp-buffer-delay-ms', type=float, default=100.0,
                       help='Flush watermark in ms for --timestamp-buffer mode (default 100): a '
                            'bucket is emitted once data this much newer has arrived, so the '
                            'time-skewed multi-container messages for an instant land first and '
                            'the live-edge bucket is not stalled for the full timeout. Should '
                            'exceed the cross-container timestamp skew (~2 frame periods).')
    parser.add_argument('--exit-on-idle', type=float, default=15.0,
                       help='Offline mode: finalize and exit after this many seconds with no new '
                            'message once the stream started (default 15; 0 disables).')

    return parser.parse_args()

def main():
    args = parse_args()
    expected_sensors = extract_expected_sensors(args.msgconv_config)
    print(f"Expected sensors: {expected_sensors}")
    if args.timestamp_buffer:
        print(f"Buffering by timestamp bucket ({args.bucket_ms} ms)")
    if args.offline:
        # Incremental headless capture (continuous stream → mp4). Finalizes on Ctrl+C or,
        # once the stream ends, after --exit-on-idle seconds of silence.
        record_video_headless(args.dataset_path, args.output_path, args.show_ids, expected_sensors,
                              args.average_multi_cam, args.verbose,
                              timestamp_mode=args.timestamp_buffer, bucket_ms=args.bucket_ms,
                              flush_delay_ms=args.timestamp_buffer_delay_ms,
                              exit_on_idle=args.exit_on_idle)
    else:
        real_time_visualization(args.dataset_path, args.output_path, args.show_ids, expected_sensors, args.average_multi_cam, args.verbose,
                                timestamp_mode=args.timestamp_buffer, bucket_ms=args.bucket_ms,
                                flush_delay_ms=args.timestamp_buffer_delay_ms)


if __name__ == "__main__":
    main()

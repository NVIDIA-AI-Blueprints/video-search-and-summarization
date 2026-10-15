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
Extract cropped object patches from video frames using TrackedObject
metadata.

The sensor_id / stream_id field is treated as the video file path.
The frame_id is used to seek into the video, and the bbox
(leftX, topY, rightX, bottomY) is used to crop the object region.

Standalone usage (from repo root or from this directory)::

    python secondary_embedding_generation/secondary_embedding_generation.py \\
        --video /path/to/video.mp4 --frame 120 \\
        --bbox 100 200 300 400 --object-id 42 \\
        --output-dir ./out

Or with a JSON dict matching the service's TrackedObject keys::

    python secondary_embedding_generation/secondary_embedding_generation.py \\
        --tracked-json '{"object_id":1,"stream_id":"/path/video.mp4",\\
        "frame_id":0,"leftX":10,"topY":20,"rightX":100,"bottomY":200}' \\
        --output-dir ./out

Options ``--model``, ``--no-fp16``, and ``--rebuild-engine`` apply when
``--save-embedding`` is set (TensorRT path). Without ``--save-embedding``,
only the patch PNG is written and the TRT engine is not loaded.
"""

import argparse
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from PIL import Image
from kafka import KafkaProducer
from kafka.errors import KafkaError
from google.protobuf import json_format
import requests

# Optional: only needed when MESSAGE_BROKER=redis. Guarded so a Kafka-only
# deployment with an older venv keeps importing this module cleanly.
try:
    import redis as redis_lib
except ImportError:  # pragma: no cover - exercised only without the dep
    redis_lib = None

# Sibling modules (embed_image_trt) live in this directory; repo-root imports
# (e.g. multiprocessing worker in main.py) do not put it on sys.path by default.
_pkg_dir = str(Path(__file__).resolve().parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

# Make the source root (src/) importable so ``proto_utils`` resolves when this
# module is launched as a multiprocessing worker (cwd may not be on sys.path).
_src_root = str(Path(__file__).resolve().parent.parent)
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

import proto_utils.ds_schema_pb2 as schema_pb2  # noqa: E402
from config import MESSAGE_BROKER, split_host_port  # noqa: E402
from embed_image_trt import (  # noqa: E402
    MODEL_CONFIGS,
    TRTInference,
    get_engine,
    get_model_config,
    preprocess_image,
)


logger = logging.getLogger(__name__)

KAFKA_PRODUCER_SERVER = os.getenv("KAFKA_PRODUCER_SERVER", "localhost:9092")
KAFKA_PRODUCER_TOPIC = os.getenv("KAFKA_PRODUCER_TOPIC", "compressed-embeddings")

# Redis publish target, used when MESSAGE_BROKER=redis. Kept alongside the
# Kafka producer settings above rather than in config.py, matching where this
# module already reads its own producer configuration from.
REDIS_PRODUCER_SERVER = os.getenv("REDIS_PRODUCER_SERVER", "localhost:6379")
REDIS_PRODUCER_TOPIC = os.getenv("REDIS_PRODUCER_TOPIC", "compressed-embeddings")
REDIS_PRODUCER_PASSWORD = os.getenv("REDIS_PRODUCER_PASSWORD") or None
# Field name each entry is stored under. Defaults to DeepStream's `payloadkey`
# convention so a downstream nvmsgbroker subscriber needs no special casing.
REDIS_PRODUCER_PAYLOAD_KEY = os.getenv("REDIS_PRODUCER_PAYLOAD_KEY", "metadata")
# We are the publisher here, so trimming is OURS to do: Redis Streams grow
# without bound otherwise, and these entries carry full embedding vectors.
# Mirrors the `streamsize` default of DeepStream's own adapter. 0 disables
# trimming (only sensible if something else trims the stream).
REDIS_PRODUCER_MAXLEN = int(os.getenv("REDIS_PRODUCER_MAXLEN", "10000"))
# VIOS_SERVER = os.getenv("VIOS_SERVER", "localhost:30888")
VIOS_SERVER = os.getenv("VIOS_SERVER", None)
DUMP_OBJ_PATCHES = os.getenv("DUMP_OBJ_PATCHES", "false").lower() in ("true", "1", "yes")
DUMP_EMBEDDINGS = os.getenv("DUMP_EMBEDDINGS", "false").lower() in ("true", "1", "yes")
SECONDARY_EMBEDDING_MIN_VISIBILITY = float(
    os.getenv("SECONDARY_EMBEDDING_MIN_VISIBILITY", "0.7")
)  # default visibility is 0.7

# Log producer configuration for debugging
logger.info("=" * 50)
if MESSAGE_BROKER == "redis":
    logger.info("REDIS PRODUCER CONFIGURATION")
    logger.info(f"REDIS_PRODUCER_SERVER: {REDIS_PRODUCER_SERVER}")
    logger.info(f"REDIS_PRODUCER_TOPIC: {REDIS_PRODUCER_TOPIC}")
    logger.info(f"REDIS_PRODUCER_PASSWORD: {'<set>' if REDIS_PRODUCER_PASSWORD else '<unset>'}")
    logger.info(f"REDIS_PRODUCER_PAYLOAD_KEY: {REDIS_PRODUCER_PAYLOAD_KEY}")
    logger.info(f"REDIS_PRODUCER_MAXLEN: {REDIS_PRODUCER_MAXLEN}")
else:
    logger.info("KAFKA PRODUCER CONFIGURATION")
    logger.info(f"KAFKA_PRODUCER_SERVER: {KAFKA_PRODUCER_SERVER}")
    logger.info(f"KAFKA_PRODUCER_TOPIC: {KAFKA_PRODUCER_TOPIC}")
logger.info("=" * 50)

# Log VIOS server configuration for debugging
logger.info("=" * 50)
logger.info("VIOS SERVER CONFIGURATION")
logger.info(f"VIOS_SERVER: {VIOS_SERVER}")
logger.info("=" * 50)


class SecondaryEmbeddingGeneration:
    """
    Patch extraction from tracked objects; ``__init__`` is reserved for
    heavy one-time setup (e.g. TensorRT engine load) and
    ``process_object`` for per-frame work.
    """

    _VIOS_METADATA_TIMEOUT_SEC = 5

    def __init__(
        self,
        model_name: str = "siglip2",
        onnx_path: Optional[str] = None,
        fp16: bool = True,
        rebuild_engine: bool = False,
        *,
        enable_trt: bool = True,
    ) -> None:

        # Mapping from camera name (e.g. "Camera_01") -> stream UUID
        # (e.g. "15a38157-2812-4888-b190-50674876ba58"). Populated lazily
        # from BIOS/VIOS at __init__ and refreshed on cache miss.
        self.sensor_name_to_id: Dict[str, str] = {}
        if VIOS_SERVER:
            # Best-effort warm-up; failures here are non-fatal and the
            # cache will be refreshed on the first process_object() miss.
            self._refresh_sensor_name_to_id()

        self.config = get_model_config(model_name)
        if onnx_path is not None:
            self.config["onnx_path"] = onnx_path
        if enable_trt:
            self.engine = get_engine(
                self.config["onnx_path"],
                fp16=fp16,
                rebuild=rebuild_engine,
            )
            self.trt_inference = TRTInference(self.engine)
        else:
            self.engine = None
            self.trt_inference = None

        # Publish target for enriched frames. Single branch on
        # MESSAGE_BROKER; at most one of these ends up non-None.
        self._kafka_producer = None
        self._redis_producer = None
        if MESSAGE_BROKER == "redis":
            self._init_redis_producer()
        else:
            self._init_kafka_producer()

    # ------------------------------------------------------------------
    # Producer setup (one initializer per broker)
    # ------------------------------------------------------------------

    def _init_kafka_producer(self) -> None:
        """Open a ``KafkaProducer``, or leave ``_kafka_producer`` as None."""
        if KAFKA_PRODUCER_SERVER and KAFKA_PRODUCER_TOPIC:
            try:
                bootstrap_servers_list = [KAFKA_PRODUCER_SERVER]
                # Try to create producer with idempotence first (requires Kafka >= 0.11)
                producer_config = {
                    "bootstrap_servers": bootstrap_servers_list,
                    "value_serializer": lambda v: v,  # Already serialized protobuf bytes
                    "acks": "all",  # Wait for all replicas
                    "retries": 3,
                    "max_in_flight_requests_per_connection": 1,
                    "request_timeout_ms": 60000,  # 60 second timeout for metadata
                    "metadata_max_age_ms": 300000,  # Refresh metadata every 5 minutes
                    "connections_max_idle_ms": 540000,  # Close idle connections after 9 minutes
                }
                try:
                    # Try with idempotence first
                    producer_config["enable_idempotence"] = True
                    self._kafka_producer = KafkaProducer(**producer_config)
                except KafkaError as idempotence_error:
                    if "Idempotent" in str(idempotence_error) or "0.11" in str(idempotence_error):
                        logger.warning(
                            "Kafka broker version < 0.11 detected or idempotence not supported. "
                            "Creating producer without idempotence. Error: %s",
                            idempotence_error,
                        )
                        # Fall back to non-idempotent producer for older Kafka versions
                        producer_config.pop("enable_idempotence", None)
                        producer_config["max_in_flight_requests_per_connection"] = (
                            5  # Can be higher without idempotence
                        )
                        self._kafka_producer = KafkaProducer(**producer_config)
                    else:
                        raise
                # KafkaProducer will connect lazily on first send
                # Log success - actual connection will be tested on first message send
                logger.info(
                    "Kafka producer initialized successfully. Topic: %s, Bootstrap servers: %s. "
                    "Producer is ready and will connect automatically when sending messages.",
                    KAFKA_PRODUCER_TOPIC,
                    bootstrap_servers_list,
                )
            except KafkaError as e:
                logger.error(
                    "Kafka error initializing producer: %s. "
                    "Check that Kafka is running and KAFKA_BOOTSTRAP_SERVERS is correct. "
                    "Bootstrap servers: %s. "
                    "Troubleshooting: 1) Verify Kafka is running: docker ps | grep kafka "
                    "2) Check connectivity: telnet <host> <port> "
                    "3) Verify KAFKA_BOOTSTRAP_SERVERS format: host:port",
                    e,
                    bootstrap_servers_list,
                    exc_info=True,
                )
                self._kafka_producer = None
        else:
            logger.warning(
                "Kafka producer not initialized. KAFKA_PRODUCER_SERVER and KAFKA_PRODUCER_TOPIC are not set."
            )
            self._kafka_producer = None

    def _init_redis_producer(self) -> None:
        """Open a Redis client for XADD, or leave ``_redis_producer`` as None.

        Connection failures are non-fatal and mirror the Kafka arm: patch
        extraction and embedding generation still run, only the republish is
        skipped (with a warning per attempt from :meth:`_publish`).
        """
        if not (REDIS_PRODUCER_SERVER and REDIS_PRODUCER_TOPIC):
            logger.warning(
                "Redis producer not initialized. REDIS_PRODUCER_SERVER and " "REDIS_PRODUCER_TOPIC are not set."
            )
            return
        if redis_lib is None:
            logger.error(
                "MESSAGE_BROKER=redis but the 'redis' package is not installed; "
                "enriched frames will not be republished. Install it "
                "(pip install redis) or set MESSAGE_BROKER=kafka."
            )
            return
        try:
            host, port = split_host_port(REDIS_PRODUCER_SERVER, "REDIS_PRODUCER_SERVER")
            client = redis_lib.Redis(
                host=host,
                port=port,
                password=REDIS_PRODUCER_PASSWORD,
                socket_keepalive=True,
                socket_timeout=10.0,
                socket_connect_timeout=10.0,
            )
            client.ping()
            self._redis_producer = client
            logger.info(
                "Redis producer initialized successfully. Stream: %s, Server: %s, " "payload key: %s, maxlen: %s",
                REDIS_PRODUCER_TOPIC,
                REDIS_PRODUCER_SERVER,
                REDIS_PRODUCER_PAYLOAD_KEY,
                REDIS_PRODUCER_MAXLEN or "<untrimmed>",
            )
        except Exception as e:
            logger.error(
                "Failed to initialize Redis producer: %s. "
                "Check that Redis is running and REDIS_PRODUCER_SERVER is correct "
                "(got %r). Troubleshooting: 1) docker ps | grep redis "
                "2) redis-cli -h <host> -p <port> ping",
                e,
                REDIS_PRODUCER_SERVER,
                exc_info=True,
            )
            self._redis_producer = None

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def _has_producer(self) -> bool:
        return self._kafka_producer is not None or self._redis_producer is not None

    def _publish(self, payload: bytes, object_id, frame_id) -> None:
        """Republish one serialized ``Frame`` to the configured broker.

        Failures are logged, never raised: a dropped republish must not abort
        patch extraction for the remaining objects.

        The Redis arm is a synchronous XADD round-trip, unlike Kafka's
        fire-and-forget ``send()``. That is deliberate -- the caller has just
        run TRT inference on this patch, so a sub-millisecond local XADD is
        noise against that, and it keeps failures attributable to the frame
        that caused them.
        """
        if self._redis_producer is not None:
            try:
                self._redis_producer.xadd(
                    REDIS_PRODUCER_TOPIC,
                    {REDIS_PRODUCER_PAYLOAD_KEY: payload},
                    # Redis Streams are in-memory and unbounded by default;
                    # `approximate` trims at node granularity, which is much
                    # cheaper than exact and is what DeepStream itself uses.
                    maxlen=REDIS_PRODUCER_MAXLEN or None,
                    approximate=True,
                )
                logger.info(
                    f"Sent embedding for object_id={object_id} "
                    f"frame_id={frame_id} to Redis stream "
                    f"{REDIS_PRODUCER_TOPIC}"
                )
            except Exception as send_err:
                logger.error(
                    f"Failed to send Redis message for " f"object_id={object_id} frame_id={frame_id}: " f"{send_err}",
                    exc_info=True,
                )
            return

        try:
            self._kafka_producer.send(KAFKA_PRODUCER_TOPIC, payload)
            logger.info(
                f"Sent embedding for object_id={object_id} "
                f"frame_id={frame_id} to Kafka topic "
                f"{KAFKA_PRODUCER_TOPIC}"
            )
        except KafkaError as send_err:
            logger.error(
                f"Failed to send Kafka message for " f"object_id={object_id} frame_id={frame_id}: " f"{send_err}",
                exc_info=True,
            )

    def _refresh_sensor_name_to_id(self) -> bool:
        """Pull the full stream list from BIOS/VIOS and rebuild the
        ``sensor_name_to_id`` cache (``{camera_name: streamId}``).

        BIOS returns a list shaped like::

            [
                {"<uuid>": [
                    {"name": "Camera_01", "streamId": "<uuid>",
                     "isMain": true, ...},
                    ...   # may include sub-streams
                ]},
                ...
            ]

        We pick the entry with ``isMain == true`` per UUID; if none is
        flagged main we fall back to the first entry. Entries missing a
        ``name`` are skipped (can't index by a missing key).

        Returns True on success.
        """
        if not VIOS_SERVER:
            return False

        url = f"http://{VIOS_SERVER}/vst/api/v1/live/streams"
        try:
            response = requests.get(url, timeout=self._VIOS_METADATA_TIMEOUT_SEC)
        except requests.RequestException as e:
            logger.error(f"Error fetching streams from VIOS ({url}): {e}")
            return False
        if response.status_code != 200:
            logger.error(f"VIOS /streams returned HTTP {response.status_code}: " f"{response.text[:200]}")
            return False
        try:
            payload = response.json()
        except ValueError as e:
            logger.error(f"Could not decode VIOS /streams JSON: {e}")
            return False
        if not isinstance(payload, list):
            logger.warning(
                "Unexpected BIOS /streams payload type: %s; expected list",
                type(payload).__name__,
            )
            return False

        mapping: Dict[str, str] = {}
        for entry in payload:
            if not isinstance(entry, dict) or not entry:
                continue
            for _stream_id, streams in entry.items():
                if not isinstance(streams, list) or not streams:
                    continue
                # Prefer the main stream; fall back to the first.
                main = next(
                    (s for s in streams if isinstance(s, dict) and s.get("isMain")),
                    streams[0] if isinstance(streams[0], dict) else None,
                )
                if not main:
                    continue
                name = main.get("name")
                resolved_id = main.get("streamId")
                if name and resolved_id:
                    mapping[name] = resolved_id

        if not mapping:
            logger.warning("VIOS /streams returned no usable entries")
            return False

        self.sensor_name_to_id = mapping
        logger.info(f"Refreshed sensor_name_to_id from VIOS: {len(mapping)} cameras " f"({sorted(mapping.keys())})")
        return True

    def process_object(
        self,
        tracked_obj: Dict[str, Any],
        output_dir: str,
        filename_template: Optional[str] = None,
        *,
        generate_embedding: bool = True,
    ) -> Optional[str]:
        """
        Read a video frame from VIOS/stream id and crop the object patch
        get the embedding from the patch and publish the frame to Kafka.

        Args:
            tracked_obj: Dict with at least the keys:
                object_id, stream_id (video path), frame_id,
                leftX, topY, rightX, bottomY, and ``kafka_raw`` (the
                original protobuf ``Object`` as a dict, minus the
                embedding field).
            output_dir: Directory where cropped images are saved.
            filename_template: Optional custom filename pattern.
                Supports {object_id}, {stream_id}, {frame_id}.
                Defaults to
                "obj_{object_id}_{stream_name}_frame_{frame_id}.png".
            generate_embedding: If True, also write ``<base>_embedding.json``
                with the L2-normalized embedding (list of floats). Requires
                TensorRT (``enable_trt=True`` in ``__init__``).

        Returns:
            Success message or None on failure.

        Raises:
            ValueError: If ``generate_embedding`` is True and ``kafka_raw`` is
                missing from ``tracked_obj`` (we need the original object
                payload to rebuild a valid Frame for downstream consumers).
        """
        object_id = tracked_obj["object_id"]
        video_path = tracked_obj["stream_id"]
        frame_id = int(tracked_obj["frame_id"])
        timestamp = tracked_obj["timestamp"]
        left = int(tracked_obj["leftX"])
        top = int(tracked_obj["topY"])
        right = int(tracked_obj["rightX"])
        bottom = int(tracked_obj["bottomY"])
        visibility = tracked_obj.get("visibility", 1.0)
        stream_name = os.path.splitext(os.path.basename(video_path))[0]
        if visibility < SECONDARY_EMBEDDING_MIN_VISIBILITY:
            logger.info(f"Object {object_id} has visibility {visibility}, skipping secondary embedding generation")
            return None

        # ``kafka_raw`` is required when we'll be republishing the enriched
        # frame to Kafka; fail fast so callers don't silently lose the
        # original object metadata (type, info, bbox confidence, etc.).
        kafka_raw = tracked_obj.get("kafka_raw")
        if generate_embedding and not kafka_raw:
            raise ValueError(
                f"tracked_obj is missing 'kafka_raw' for object_id={object_id} "
                f"frame_id={frame_id}; cannot rebuild Frame for Kafka publish."
            )

        # Read the video frame from VIOS/stream id
        # check the health of the VIOS server
        if VIOS_SERVER:
            try:
                health_url = f"http://{VIOS_SERVER}/health"
                response = requests.get(health_url, timeout=5)
                if response.status_code != 200:
                    logger.error(f"VIOS server health check failed: {response.status_code}")
                    return None
            except Exception as e:
                logger.error(f"Error checking VIOS server health: {e}")
                return None
            sensor_name = tracked_obj["stream_id"]
            sensor_id = self.sensor_name_to_id.get(sensor_name)
            if not sensor_id:
                # Cache miss: refresh the whole map (VIOS returns the
                # full list per call, so a single GET resolves every
                # camera at once) and retry the lookup.
                logger.info(f"Sensor {sensor_name!r} not in sensor_name_to_id; " f"refreshing from BIOS/VIOS")
                if not self._refresh_sensor_name_to_id():
                    return None
                sensor_id = self.sensor_name_to_id.get(sensor_name)
                if not sensor_id:
                    logger.error(
                        f"Sensor {sensor_name!r} still not found after BIOS "
                        f"refresh; known cameras: "
                        f"{sorted(self.sensor_name_to_id.keys())}"
                    )
                    return None
            # Replay endpoint expects the stream UUID (sensor_id), not
            # the human-readable camera name.
            frame_url = f"http://{VIOS_SERVER}/vst/api/v1/replay/stream/" f"{sensor_id}/picture?startTime={timestamp}"
            logger.info(f"Fetching frame from VIOS: {frame_url}")
            try:
                response = requests.get(frame_url, timeout=5)
            except requests.RequestException as e:
                logger.error(f"Error fetching video frame from VIOS: {e}")
                return None
            if response.status_code != 200:
                logger.error(
                    f"Error getting video frame from VIOS: HTTP {response.status_code} "
                    f"({len(response.content)} bytes)"
                )
                return None
            if not response.content:
                logger.error("VIOS returned an empty frame body")
                return None
            # Decode the JPEG bytes into a BGR numpy image. ``convert("RGB")``
            # collapses grayscale/CMYK JPEGs to 3 channels, and the channel
            # flip leaves the crop in BGR because preprocess_image assumes BGR.
            try:
                rgb_frame = Image.open(io.BytesIO(response.content)).convert("RGB")
            except Exception as e:
                logger.error(
                    f"Failed to decode JPEG frame from VIOS for {video_path} " f"at startTime={timestamp}: {e}"
                )
                return None
            frame = np.asarray(rgb_frame)[:, :, ::-1]
        else:
            logger.error(f"Cannot read frame {frame_id} from {video_path}: " f"VIOS_SERVER is not set")
            return None

        # crop the object patch from the frame
        h, w = frame.shape[:2]
        left = max(0, min(left, w))
        top = max(0, min(top, h))
        right = max(0, min(right, w))
        bottom = max(0, min(bottom, h))

        patch = frame[top:bottom, left:right]
        if patch.size == 0:
            logger.warning(f"Empty patch for object_id={object_id} " f"frame_id={frame_id}")
            return None

        patch_name = f"obj_{object_id}_{stream_name}_frame_{frame_id}.jpg"
        if DUMP_OBJ_PATCHES:
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, patch_name)
            # Pillow wants RGB. quality=95 is the JPEG default.
            Image.fromarray(np.ascontiguousarray(patch[:, :, ::-1])).save(output_path, quality=95)
            logger.info(f"Saved patch: {output_path}")

        if generate_embedding:
            if self.trt_inference is None:
                raise RuntimeError("generate_embedding requires TensorRT; construct with " "enable_trt=True (default).")
            pixel_values = preprocess_image(patch, self.config)
            embedding = self.trt_inference.infer(pixel_values).squeeze(0)
            if DUMP_EMBEDDINGS:
                os.makedirs(output_dir, exist_ok=True)
                emb_path = os.path.join(output_dir, f"{os.path.splitext(patch_name)[0]}_embedding.json")
                with open(emb_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "object_id": object_id,
                            "frame_id": frame_id,
                            "stream_id": video_path,
                            "embedding": embedding.tolist(),
                        },
                        f,
                        indent=2,
                    )
                logger.info(f"Saved embedding: {emb_path}")
            else:
                emb_path = None

            # Now that we have the embedding, republish the enriched
            # frame over the configured broker so downstream consumers see
            # the original object payload plus the freshly computed embedding.
            if self._has_producer():
                # Rebuild the protobuf Object from the raw dict captured
                # upstream (everything except the embedding field).
                obj_proto = json_format.ParseDict(
                    dict(kafka_raw),
                    schema_pb2.Object(),
                    ignore_unknown_fields=True,
                )
                # Inject the newly computed embedding vector.
                del obj_proto.embedding.vector[:]
                obj_proto.embedding.vector.extend(float(v) for v in embedding.tolist())

                frame_proto = schema_pb2.Frame(
                    version=str(tracked_obj.get("version") or ""),
                    id=str(frame_id),
                    sensorId=str(tracked_obj.get("stream_id")),
                    objects=[obj_proto],
                )
                # ``Frame.timestamp`` is a google.protobuf.Timestamp
                # submessage; parse the RFC 3339 string captured upstream
                # back into it.
                frame_proto.timestamp.FromJsonString(tracked_obj["timestamp"])
                self._publish(frame_proto.SerializeToString(), object_id, frame_id)
            else:
                logger.warning(f"No {MESSAGE_BROKER} producer initialized. " f"Could not send embedding.")

        return video_path


def _parse_tracked_json(s: str) -> Dict[str, Any]:
    return json.loads(s)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Crop a tracked-object patch from a video frame and optionally " "run TensorRT ReID embedding."),
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--tracked-json",
        type=str,
        metavar="JSON",
        help=("Single JSON object with keys: object_id, stream_id, frame_id, " "leftX, topY, rightX, bottomY"),
    )
    src.add_argument("--video", type=str, help="Video file path (stream_id)")

    parser.add_argument(
        "--frame",
        type=int,
        default=None,
        help="Frame index (required with --video)",
    )
    parser.add_argument(
        "--bbox",
        type=int,
        nargs=4,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        default=None,
        help="Bounding box in pixels (required with --video)",
    )
    parser.add_argument(
        "--object-id",
        type=int,
        default=0,
        help="Object id (only used with --video)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="extracted_patches",
        help="Directory for the patch PNG (and optional embedding JSON)",
    )
    parser.add_argument(
        "--model",
        type=str,
        choices=list(MODEL_CONFIGS.keys()),
        default="siglip2",
        help="Model key for TensorRT preprocessing / engine",
    )
    parser.add_argument(
        "--no-fp16",
        action="store_true",
        help="Build/run TensorRT in FP32",
    )
    parser.add_argument(
        "--rebuild-engine",
        action="store_true",
        help="Force TensorRT engine rebuild from ONNX",
    )
    parser.add_argument(
        "--generate-embedding",
        action="store_true",
        help="Run TRT on the patch and write *_embedding.json next to the PNG",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Log warnings and errors only",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.tracked_json:
        tracked = _parse_tracked_json(args.tracked_json)
    else:
        if args.frame is None or args.bbox is None:
            parser.error("--video requires --frame and --bbox")
        left, top, right, bottom = args.bbox
        tracked = {
            "object_id": args.object_id,
            "stream_id": args.video,
            "frame_id": args.frame,
            "leftX": left,
            "topY": top,
            "rightX": right,
            "bottomY": bottom,
        }

    gen = SecondaryEmbeddingGeneration(
        model_name=args.model,
        fp16=not args.no_fp16,
        rebuild_engine=args.rebuild_engine,
        enable_trt=args.generate_embedding,
    )
    out = gen.process_object(
        tracked,
        args.output_dir,
        generate_embedding=args.generate_embedding,
    )
    if out is None:
        raise SystemExit(1)
    print(out)


if __name__ == "__main__":
    main()

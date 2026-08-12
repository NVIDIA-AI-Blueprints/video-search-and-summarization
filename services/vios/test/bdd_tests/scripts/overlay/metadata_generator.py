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
Overlay metadata generator for VIOS overlay integration tests.

Produces DeepStream-shaped bounding-box metadata for a synthetic recorded clip so
the VIOS overlay path can be exercised deterministically. The same neutral spec
feeds both:

  * the download / replay path  -> Elasticsearch ``_source`` documents
    (served by ``fake_es_server``), and
  * (phase 2) the live path      -> ``nv.Frame`` protobuf published to a broker.

Field shapes here match a real ``mdx-bev`` document and a real ``nv.Frame`` message
captured from a running warehouse deployment: an object carries a 2D ``bbox``
(``leftX/topY/rightX/bottomY``, pixel corners on the source frame) and a 3D
``bbox3d.coordinates`` (12 doubles). For a deterministic, pixel-assertable test we
populate the 2D ``bbox`` with a centered box; ``bbox3d`` is left zeroed (as it is in
a 2D stream). Each frame's metadata is stamped with the frame's wall-clock epoch so
it matches the recorded frame PTS within ``bbox_tolerance_ms``.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Matches download_test_utils.epoch_ms_to_iso_z (kept local so this module has no
# test-package import dependency and can be run standalone).
def epoch_ms_to_iso_z(epoch_ms: int) -> str:
    """Epoch milliseconds -> ISO-8601 UTC string with millisecond precision."""
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{epoch_ms % 1000:03d}Z"


@dataclass
class OverlaySpec:
    """Describes the synthetic metadata stream to generate.

    A centered box is the default because it makes the downstream pixel assertion
    robust to encoder scaling/anti-aliasing: the box lands in the middle of the
    frame regardless of the exact output resolution.
    """

    sensor_id: str
    start_epoch_ms: int
    fps: float = 30.0
    frame_count: int = 60
    width: int = 640
    height: int = 480
    # Normalized box (left, top, right, bottom) in [0,1]; default = centered 20%.
    box_norm: Tuple[float, float, float, float] = (0.40, 0.40, 0.60, 0.60)
    obj_type: str = "Person"
    obj_id: str = "1"
    confidence: float = 0.99
    # If set, emit metadata only every Nth frame (to model a slower detector);
    # None => one metadata doc per video frame.
    metadata_every_n: int = 1

    def frame_epoch_ms(self, i: int) -> int:
        """Wall-clock epoch (ms) of frame ``i`` for a clip starting at start_epoch_ms."""
        return int(self.start_epoch_ms + round(i * 1000.0 / self.fps))

    def pixel_box(self) -> Tuple[int, int, int, int]:
        """Centered box in source-frame pixel corners (leftX, topY, rightX, bottomY)."""
        l, t, r, b = self.box_norm
        return (
            int(round(l * self.width)),
            int(round(t * self.height)),
            int(round(r * self.width)),
            int(round(b * self.height)),
        )

    def frame_indices_with_metadata(self) -> List[int]:
        step = max(1, int(self.metadata_every_n))
        return list(range(0, self.frame_count, step))


def _object_structured(spec: OverlaySpec) -> Dict:
    """One object in the structured (``use_video_metadata_protobuf=true``) schema,
    matching a real mdx-bev object. The 2D bbox is what the 2D overlay draws."""
    lx, ty, rx, by = spec.pixel_box()
    return {
        "id": spec.obj_id,
        "type": spec.obj_type,
        "confidence": spec.confidence,
        "info": {"classConfidence": f"{spec.confidence:.6f}"},
        "bbox": {
            "leftX": float(lx),
            "topY": float(ty),
            "rightX": float(rx),
            "bottomY": float(by),
            "confidence": spec.confidence,
        },
        # Present but zeroed, exactly as a 2D stream leaves it in mdx-bev.
        "bbox3d": {"coordinates": [0.0] * 12, "confidence": 0.0},
    }


def _object_pipe_string(spec: OverlaySpec) -> str:
    """One object in the pipe-delimited (``use_video_metadata_protobuf=false``)
    schema parsed by vst_common::parseMetadataObject:
    ``objectId|leftX|topY|rightX|bottomY|type|confidence``."""
    lx, ty, rx, by = spec.pixel_box()
    return f"{spec.obj_id}|{lx}|{ty}|{rx}|{by}|{spec.obj_type}|{spec.confidence:.6f}"


def generate_es_docs(spec: OverlaySpec, protobuf: bool = True) -> List[Dict]:
    """Build the Elasticsearch ``_source`` documents for the clip.

    Args:
        spec: the clip/box description.
        protobuf: when True use the structured object schema and the ``timestamp``
            time field (matches ``use_video_metadata_protobuf=true`` deployments,
            e.g. mdx-bev). When False use pipe-delimited object strings and the
            ``@timestamp`` field (matches ``use_video_metadata_protobuf=false``).

    Returns one document per metadata-bearing frame, ascending by time. Each doc's
    time field equals the frame's epoch so the overlay matches it to the frame PTS.
    """
    time_field = "timestamp" if protobuf else "@timestamp"
    docs: List[Dict] = []
    for i in spec.frame_indices_with_metadata():
        epoch_ms = spec.frame_epoch_ms(i)
        obj = _object_structured(spec) if protobuf else _object_pipe_string(spec)
        docs.append(
            {
                "version": "4.0",
                time_field: epoch_ms_to_iso_z(epoch_ms),
                "sensorId": spec.sensor_id,
                "id": str(epoch_ms),
                "objects": [obj],
                # epoch_ms is not part of the real _source; the fake ES exposes it
                # as the numeric `sort` value (what the overlay reads as epocTime).
                "_epoch_ms": epoch_ms,
            }
        )
    logger.info(
        "Generated %d overlay metadata docs for sensor=%s window=[%s .. %s] (%s schema)",
        len(docs), spec.sensor_id,
        epoch_ms_to_iso_z(spec.frame_epoch_ms(0)),
        epoch_ms_to_iso_z(spec.frame_epoch_ms(max(0, spec.frame_count - 1))),
        "structured" if protobuf else "pipe-string",
    )
    return docs


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Generate VIOS overlay ES metadata docs")
    p.add_argument("--sensor-id", required=True)
    p.add_argument("--start-epoch-ms", type=int, required=True)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--frames", type=int, default=60)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--no-protobuf", action="store_true",
                   help="emit pipe-string objects + @timestamp (protobuf=false schema)")
    p.add_argument("--out", help="write docs as JSON array to this path (default: stdout)")
    args = p.parse_args()

    spec = OverlaySpec(
        sensor_id=args.sensor_id,
        start_epoch_ms=args.start_epoch_ms,
        fps=args.fps,
        frame_count=args.frames,
        width=args.width,
        height=args.height,
    )
    docs = generate_es_docs(spec, protobuf=not args.no_protobuf)
    payload = json.dumps(docs, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(payload)
        logger.info("Wrote %d docs to %s", len(docs), args.out)
    else:
        print(payload)


if __name__ == "__main__":
    _cli()

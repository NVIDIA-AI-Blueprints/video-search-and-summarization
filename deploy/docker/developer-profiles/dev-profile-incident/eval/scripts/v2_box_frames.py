# /// script
# requires-python = ">=3.12"
# dependencies = ["opencv-python-headless==4.10.0.84", "numpy>=1.26,<3", "pillow>=10,<12"]
# ///
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
"""Frames and numbered marks for Evaluation V2 box references (throwaway environment; OpenCV is not an eval
dependency, as for ``gt_frames.py``).

- ``extract PLAN OUT``: native-resolution JPEGs at each planned time (``{"clips": [{"clip", "video", "times"}]}``),
  plus ``frames.json`` with each frame's SHA-256 and size;
- ``draw PACKET FRAMES OUT``: each packet frame with its numbered proposal boxes drawn (number tags at the box
  corner), written to the packet's image paths under ``OUT``;
- ``sheet RECORDS FRAMES OUT``: one contact sheet per box record for its human review (X6): every second the passes
  disagreed on (red border), then agreed seconds spread over the record (the reference box in green), up to 12
  frames, plus ``links.json`` mapping each record id to its sheet for ``python -m evalv2 review export --links``.

Output goes under the gitignored data root. Running it on the clips is part of an approved reference run (RA1)::

    uv run --no-project --script scripts/v2_box_frames.py extract plan.json eval_data/v2/box-frames
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PALETTE = ((230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180),
           (70, 240, 240), (240, 50, 230))


def extract(plan_path: Path, out_dir: Path) -> dict:
    import cv2

    plan = json.loads(plan_path.read_text())
    index: dict[str, dict] = {}
    for clip in plan["clips"]:
        capture = cv2.VideoCapture(str(clip["video"]))
        if not capture.isOpened():
            raise SystemExit(f"cannot open {clip['video']}")
        for t in clip["times"]:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000.0)
            ok, frame = capture.read()
            if not ok:
                continue
            path = out_dir / clip["clip"] / f"t{float(t):08.3f}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            height, width = frame.shape[:2]
            index[str(path.relative_to(out_dir))] = {"clip": clip["clip"], "t": float(t), "width": width,
                                                     "height": height,
                                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        capture.release()
    (out_dir / "frames.json").write_text(json.dumps(index, indent=1, sort_keys=True))
    return index


def draw(packet_path: Path, frames_dir: Path, out_dir: Path) -> int:
    from PIL import Image, ImageDraw

    packet = json.loads(packet_path.read_text())
    packet = packet.get("packet", packet)  # a reference run writes {"packet_id", "packet_sha256", "packet"}
    clip = packet["item"]["clip"]
    for frame in packet["frames"]:
        source = frames_dir / clip / f"t{float(frame['t']):08.3f}.jpg"
        image = Image.open(source).convert("RGB")
        width, height = image.size
        canvas = ImageDraw.Draw(image)
        for number, box in sorted(frame["marks"].items(), key=lambda kv: int(kv[0])):
            ymin, xmin, ymax, xmax = (v / 1000.0 for v in box)
            colour = PALETTE[(int(number) - 1) % len(PALETTE)]
            rect = (xmin * width, ymin * height, xmax * width, ymax * height)
            canvas.rectangle(rect, outline=colour, width=max(2, width // 320))
            tag = (rect[0], max(0, rect[1] - 18), rect[0] + 10 + 9 * len(number), max(18, rect[1]))
            canvas.rectangle(tag, fill=colour)
            canvas.text((tag[0] + 4, tag[1] + 3), number, fill=(0, 0, 0))
        target = out_dir / frame["image"]
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, quality=92)
    return len(packet["frames"])


def review_seconds(payload: dict, limit: int = 12) -> list[int]:
    """The seconds a box record's review sheet shows: every disagreed second, then agreed seconds evenly spread."""
    disagreed = sorted(int(s) for s in payload.get("disagreed_seconds", []))[:limit]
    agreed = sorted(int(s) for s in payload.get("boxes", {}))
    room = limit - len(disagreed)
    if room <= 0 or not agreed:
        return disagreed
    picked = agreed if len(agreed) <= room else [agreed[round(i * (len(agreed) - 1) / (room - 1))] if room > 1
                                                 else agreed[len(agreed) // 2] for i in range(room)]
    return sorted(set(disagreed) | set(picked))


def sheet(records_path: Path, frames_dir: Path, out_dir: Path, *, limit: int = 12, tile_width: int = 320) -> dict:
    from PIL import Image, ImageDraw

    links: dict[str, str] = {}
    missing = 0
    for line in records_path.read_text().splitlines():
        record = json.loads(line) if line.strip() else None
        if not record or record.get("kind") != "box":
            continue
        payload = record["payload"]
        tiles = []
        for second in review_seconds(payload, limit):
            source = frames_dir / payload["clip"] / f"t{float(second):08.3f}.jpg"
            if not source.exists():
                missing += 1
                continue
            image = Image.open(source).convert("RGB")
            width, height = image.size
            canvas = ImageDraw.Draw(image)
            box = payload["boxes"].get(str(second))
            line_width = max(2, width // 240)
            if box:
                ymin, xmin, ymax, xmax = (v / 1000.0 for v in box)
                canvas.rectangle((xmin * width, ymin * height, xmax * width, ymax * height), outline=(0, 200, 0),
                                 width=line_width)
            else:  # the passes disagreed here: no reference box
                canvas.rectangle((0, 0, width - 1, height - 1), outline=(220, 0, 0), width=line_width * 2)
            canvas.rectangle((0, 0, 44, 16), fill=(0, 0, 0))
            canvas.text((4, 3), f"{second} s", fill=(255, 255, 255))
            tiles.append(image.resize((tile_width, max(1, round(height * tile_width / width)))))
        if not tiles:
            continue
        columns = min(4, len(tiles))
        rows = -(-len(tiles) // columns)
        tile_height = max(tile.size[1] for tile in tiles)
        board = Image.new("RGB", (columns * tile_width, rows * tile_height), (32, 32, 32))
        for index, tile in enumerate(tiles):
            board.paste(tile, ((index % columns) * tile_width, (index // columns) * tile_height))
        name = "".join(c if c.isalnum() or c in "._-" else "_" for c in record["record_id"]) + ".jpg"
        out_dir.mkdir(parents=True, exist_ok=True)
        board.save(out_dir / name, quality=90)
        links[record["record_id"]] = name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "links.json").write_text(json.dumps(links, indent=1, sort_keys=True))
    return {"sheets": len(links), "missing_frames": missing}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("extract", "draw", "sheet"):
        print(__doc__)
        return 2
    if sys.argv[1] == "extract":
        print(json.dumps({"frames": len(extract(Path(sys.argv[2]), Path(sys.argv[3])))}))
    elif sys.argv[1] == "draw":
        print(json.dumps({"drawn": draw(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))}))
    else:
        print(json.dumps(sheet(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
"""generate_transforms.py — derive the BEV visualizer's transforms.yml from a
VSS calibration.json plus the BEV map image (map.png).

VSS calibration is done against the floor-plan/map image, so each sensor already
carries the world->map mapping: `scaleFactor` (map pixels per meter) and
`translationToGlobalCoordinates` (world-origin offset in meters). With the map
image height H (the pixel y axis points down):

              | s   0    s*tx     |      s        = scaleFactor
    T_ov2px = | 0  -s    H - s*ty |      (tx, ty) = translationToGlobalCoordinates
              | 0   0    1        |      H        = map.png height in pixels

This reproduces the official VSS 4-cam sample dataset's transforms.yml exactly.
It is only correct when map.png IS the same image used during calibration — the
sanity check below (projecting the calibration's own ground-plane reference
points through the transform) warns when it does not line up.

map.png is optional: without it the map size defaults to 1920x1080. Only the
image height enters the transform (the y offset, H - s*ty), so the default
gives correct offsets only if the real map is also 1080 px tall; the sanity
check flags a mismatch.

Pure stdlib — no third-party deps. Driven by scripts/generate-transforms.sh.
"""
import argparse
import json
import struct
import sys
from pathlib import Path


def png_size(path):
    """(width, height) from the PNG IHDR header."""
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG file (map.png must be a PNG)")
    return struct.unpack(">II", head[16:24])


def yaml_matrix(name, t):
    """Emit the 3x3 as one YAML row per line — a nested sequence that reshapes
    to the same matrix as a flat 9-list, but reads like the matrix it is."""
    rows = [t[0:3], t[3:6], t[6:9]]
    width = max(len(f"{v:.6f}") for v in t)
    lines = [f"{name}:"]
    for row in rows:
        cells = ", ".join(f"{v:>{width}.6f}" for v in row)
        lines.append(f"  - [ {cells} ]")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description="Generate transforms.yml (T_ov2px) for the BEV visualizer "
                    "from a VSS calibration.json + the BEV map image.")
    ap.add_argument("calibration", help="path to calibration.json")
    ap.add_argument("map_png", nargs="?", default=None,
                    help="path to the BEV map image (map.png). If omitted, the "
                         "map size defaults to 1920x1080.")
    ap.add_argument("-o", "--output", default=None,
                    help="output path (default: transforms.yml next to map.png, "
                         "or ./transforms.yml when no map.png is given)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite the output file if it exists")
    args = ap.parse_args()

    cal = json.load(open(args.calibration))
    sensors = cal.get("sensors") or []
    if not sensors:
        sys.exit("ERROR: no sensors[] in calibration.json")

    # scaleFactor / translation should be identical across sensors (one map);
    # warn and use the first sensor's values if they are not.
    triples = {(round(s.get("scaleFactor", 0), 6),
                round(s.get("translationToGlobalCoordinates", {}).get("x", 0), 6),
                round(s.get("translationToGlobalCoordinates", {}).get("y", 0), 6))
               for s in sensors}
    if len(triples) > 1:
        print(f"WARNING: scaleFactor/translation differ across sensors "
              f"({len(triples)} variants) — using the first sensor's values",
              file=sys.stderr)

    s0 = sensors[0]
    s = s0.get("scaleFactor")
    trans = s0.get("translationToGlobalCoordinates") or {}
    tx, ty = trans.get("x"), trans.get("y")
    if not s or tx is None or ty is None:
        sys.exit("ERROR: calibration.json lacks scaleFactor / "
                 "translationToGlobalCoordinates — it was not calibrated against "
                 "a scaled map image; write transforms.yml manually")

    if args.map_png:
        W, H = png_size(args.map_png)
        map_desc = f"{args.map_png}  ({W}x{H})"
    else:
        W, H = 1920, 1080
        map_desc = f"(no map.png — assumed {W}x{H})"

    T = [s, 0.0, s * tx,
         0.0, -s, H - s * ty,
         0.0, 0.0, 1.0]
    print(f"scaleFactor={s} px/m  translation=({tx}, {ty}) m  map={W}x{H} px"
          + ("" if args.map_png else "  (default size — no map.png given)"))

    # Sanity check: the calibration's own ground-plane reference points
    # (globalCoordinates) must land inside the map image.
    pts = [(p["x"], p["y"]) for sn in sensors
           for p in sn.get("globalCoordinates", [])
           if "x" in p and "y" in p]
    if pts:
        inside = sum(1 for x, y in pts
                     if 0 <= T[0] * x + T[2] <= W and 0 <= T[4] * y + T[5] <= H)
        frac = inside / len(pts)
        where = "the map image" if args.map_png else f"a {W}x{H} canvas"
        print(f"sanity check: {inside}/{len(pts)} calibration ground points "
              f"land inside {where}")
        if frac < 0.95:
            if args.map_png:
                print("WARNING: many points fall outside — this map.png is "
                      "likely NOT the image used during calibration, and the "
                      "generated offsets may be misaligned. Verify the BEV "
                      "overlay visually and adjust the T_ov2px offsets if "
                      "needed.", file=sys.stderr)
            else:
                print(f"WARNING: many points fall outside a {W}x{H} canvas — "
                      "the real map is probably a different size. Pass the "
                      "actual map.png for correct offsets.", file=sys.stderr)

    if args.output:
        out = Path(args.output)
    elif args.map_png:
        out = Path(args.map_png).parent / "transforms.yml"
    else:
        out = Path("transforms.yml")
    if out.exists() and not args.force:
        sys.exit(f"ERROR: {out} already exists — pass --force to overwrite")

    content = (
        "# Generated by scripts/generate-transforms.sh from:\n"
        f"#   calibration: {args.calibration}\n"
        f"#   map image:   {map_desc}\n"
        "#\n"
        "# T_ov2px maps world/overview coordinates (meters, ground plane) to\n"
        "# map.png pixel coordinates. Row-major 3x3, consumed by the BEV\n"
        "# visualizer (scripts/bev-visualizer.sh).\n"
        "#\n"
        + yaml_matrix("T_ov2px", T)
        + "\n"
        "# Same transform for ground-truth coordinates (same world frame).\n"
        + yaml_matrix("T_gt2px", T)
    )
    out.write_text(content)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()

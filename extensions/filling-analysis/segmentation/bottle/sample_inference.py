# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded real-video inference and honest visual evidence; no UI mutations."""
import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw

from segmenter import BottleSegmenter

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--checkpoint")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    model = BottleSegmenter(checkpoint=args.checkpoint)
    (args.out / "model.json").write_text(json.dumps(model.identity, indent=2))
    sources = {
        "original": ("orangejuice.mp4", [20.0, 30.0, 42.0, 55.0, 65.0, 95.0]),
        "sammy": ("orange_juice_5min_balanced_vios_20260922.mp4",
                  [11.0, 29.0, 85.0, 107.25, 182.5, 235.0]),
    }
    records, tiles = [], []
    for name, (filename, times) in sources.items():
        source = args.media_root / filename
        sha = hashlib.file_digest(source.open("rb"), "sha256").hexdigest()
        cap = cv2.VideoCapture(str(source))
        for seconds in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"Cannot decode {source} at {seconds}")
            stem = f"{name}-{seconds:07.2f}"
            cv2.imwrite(str(args.out / (stem + "-frame.jpg")), frame)
            bottles = model.predict(frame)
            record = {"source": name, "source_sha256": sha, "requested_seconds": seconds,
                      "frame_index": cap.get(cv2.CAP_PROP_POS_FRAMES) - 1,
                      "bottles": [b.to_json() for b in bottles],
                      "timing": model.last_timing}
            records.append(record)
            (args.out / (stem + ".json")).write_text(json.dumps(record, indent=2))
            masks = np.stack([b.raw_mask for b in bottles]) if bottles else np.zeros((0, *frame.shape[:2]), bool)
            np.savez_compressed(args.out / (stem + "-masks.npz"), masks=masks)
            overlay = frame.copy()
            colors = [(80,220,80),(210,140,50),(190,70,220),(40,200,220),(200,200,60)]
            for i, b in enumerate(bottles):
                color = np.array(colors[i % len(colors)])
                overlay[b.raw_mask] = (overlay[b.raw_mask] * .56 + color * .44).astype(np.uint8)
                contours, _ = cv2.findContours(b.raw_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(overlay, contours, -1, tuple(int(x) for x in color), max(1, frame.shape[1]//640))
                x,y,_,_=b.box
                cv2.putText(overlay, f"{b.id} {b.confidence:.2f}",
                    (int(x*frame.shape[1]),max(25,int(y*frame.shape[0])-5)),
                    cv2.FONT_HERSHEY_SIMPLEX, max(.5, frame.shape[1]/1600),
                    tuple(int(v) for v in color), max(1,frame.shape[1]//700))
            cv2.imwrite(str(args.out / (stem + "-overlay.jpg")), overlay)
            tile = Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB))
            tile.thumbnail((640, 405))
            canvas=Image.new("RGB",(640,440),(16,18,22));canvas.paste(tile,((640-tile.width)//2,30))
            ImageDraw.Draw(canvas).text((10,9),f"{name} {seconds:.2f}s | {len(bottles)} bottles | {model.last_timing['inference_ms']:.0f}ms",fill="white")
            tiles.append(canvas)
            print(json.dumps({"source":name,"t":seconds,"bottles":len(bottles),"timing":model.last_timing}),flush=True)
        cap.release()
    sheet=Image.new("RGB",(640*3,440*4),(16,18,22))
    for i,tile in enumerate(tiles):sheet.paste(tile,((i%3)*640,(i//3)*440))
    sheet.save(args.out/"contact-sheet.jpg",quality=92)
    summary={"model":model.identity,"load_seconds":model.load_seconds,"samples":records,
      "warm_median_inference_ms":statistics.median(r["timing"]["inference_ms"] for r in records[1:]),
      "max_gpu_allocated_mib":max(r["timing"]["gpu_peak_allocated_mib"] for r in records),
      "notice":"Real pretrained masks; confidence is model score, not demonstrated precision. No ground-truth segmentation benchmark."}
    (args.out/"RESULTS.json").write_text(json.dumps(summary,indent=2))
    print("DONE",flush=True)

if __name__=="__main__":main()

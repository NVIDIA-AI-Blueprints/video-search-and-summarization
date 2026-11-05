# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

"""Persistent frame store: extract sampled frames once, serve reranks from disk.

Rerank latency is dominated by H.264 decode, not model scoring. Reranked
windows repeat (top-k hits cluster on the same clips, and every setting of an
fps x max-frames sweep re-reads the same windows), so frames can be extracted
once and served with pixels bit-identical to the live path:

    frame -> cv2 JPEG (quality 95, identical bytes to the live encode)
          -> stored JPEG bytes + the PIL-decoded array

The stored JPEG bytes keep the base64 payload identical to the live path, and
the stored array is exactly what vLLM would produce by decoding them, so
scores stay at the engine's own rerun noise floor.

CLI (pre-populate before a run):

    python -m cosmos_reranker.reranking.frame_store build \\
        --store artifacts/frame-store --videos <clips dir> \\
        --fps 1 --max-frames 32 [--windows windows.jsonl]

    windows.jsonl (optional): one {"video": "<filename>", "start": s, "end": s}
    per line; without it, every video is cached for whole-clip sampling.

The store is also write-through during a rerank run: any sampled window that
misses the store is decoded live and persisted, so repeat windows hit.
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class _WindowPlan:
    frame_indices: list[int]
    source_fps: float
    sample_fps: float
    duration: float


def encode_frame(frame_rgb: np.ndarray, jpeg_quality: int) -> tuple[str, np.ndarray]:
    """The live path's encode, split out: (base64 JPEG part, decoded array)."""
    import cv2
    from PIL import Image

    ok, buffer = cv2.imencode(
        ".jpg",
        cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR),
        [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)],
    )
    if not ok:
        raise ValueError("Could not JPEG-encode sampled frame")
    raw = buffer.tobytes()
    return base64.b64encode(raw).decode("ascii"), np.asarray(Image.open(BytesIO(raw)))


class FrameStore:
    """Disk-backed (one .npz per video) frame cache with a per-video RAM LRU."""

    def __init__(
        self,
        root: Path | str,
        *,
        jpeg_quality: int = 95,
        max_videos_in_ram: int = 24,
    ) -> None:
        self.root = Path(root)
        self.jpeg_quality = int(jpeg_quality)
        self.max_videos_in_ram = int(max_videos_in_ram)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ram: OrderedDict[str, dict[int, tuple[str, np.ndarray]]] = OrderedDict()

    # -- internals ---------------------------------------------------------
    def _key(self, video: Path) -> str:
        return f"{video.stem}__{abs(hash(str(video.resolve()))) & 0xFFFFFFFF:x}"

    def _npz(self, video: Path) -> Path:
        return self.root / f"{self._key(video)}.npz"

    def _entries(self, video: Path) -> dict[int, tuple[str, np.ndarray]]:
        key = self._key(video)
        with self._lock:
            hit = self._ram.pop(key, None)
            if hit is not None:
                self._ram[key] = hit
                return hit
        entries: dict[int, tuple[str, np.ndarray]] = {}
        npz = self._npz(video)
        if npz.is_file():
            with np.load(npz, allow_pickle=False) as data:
                for name in data.files:
                    if name.startswith("jpg_"):
                        raw = data[name].tobytes()
                        from PIL import Image

                        entries[int(name[len("jpg_") :])] = (
                            base64.b64encode(raw).decode("ascii"),
                            np.asarray(Image.open(BytesIO(raw))),
                        )
        with self._lock:
            self._ram[key] = entries
            while len(self._ram) > self.max_videos_in_ram:
                self._ram.popitem(last=False)
        return entries

    def _persist(self, video: Path, entries: dict[int, tuple[str, np.ndarray]]) -> None:
        payload = {
            f"jpg_{idx}": np.frombuffer(base64.b64decode(part), dtype=np.uint8)
            for idx, (part, _arr) in entries.items()
        }
        tmp = self._npz(video).with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **payload)
        tmp.replace(self._npz(video))

    def _plan(self, video: Path, candidate: Any, fps: float, max_frames: int) -> _WindowPlan:
        import cv2

        from .frames import controlled_frame_indices, sample_metadata

        cap = cv2.VideoCapture(str(video))
        try:
            video_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        finally:
            cap.release()
        indices = controlled_frame_indices(
            candidate, fps=fps, max_frames=max_frames, total_frames=total_frames, video_fps=video_fps
        )
        sample_fps, duration = sample_metadata(indices, video_fps, fps)
        return _WindowPlan([int(i) for i in indices], video_fps, sample_fps, duration)

    # -- public API --------------------------------------------------------
    def covers(self, video: Path, candidate: Any, fps: float, max_frames: int) -> bool:
        plan = self._plan(video, candidate, fps, max_frames)
        return self.has_all(video, plan.frame_indices)

    def has_all(self, video: Path, frame_indices: list[int]) -> bool:
        entries = self._entries(video)
        return all(int(i) in entries for i in frame_indices)

    def serve(
        self, video: Path, candidate: Any, fps: float, max_frames: int
    ) -> tuple[str, list[np.ndarray], float]:
        """(data URL, decoded arrays, sample_fps) with pixels identical to the live path."""
        plan = self._plan(video, candidate, fps, max_frames)
        entries = self._entries(video)
        parts, arrays = [], []
        for idx in plan.frame_indices:
            entry = entries.get(idx)
            if entry is None:
                raise KeyError(f"frame store miss: {video}#{idx}")
            parts.append(entry[0])
            arrays.append(entry[1])
        return "data:video/jpeg;base64," + ",".join(parts), arrays, plan.sample_fps

    def put_sample(self, video: Path, sample: Any) -> None:
        """Write-through: persist a live sample's frames exactly as the live encode would."""
        want = sorted({int(i) for i in sample.frame_indices})
        by_index = dict(zip(sorted({int(i) for i in sample.frame_indices}), list(sample.frames)))
        entries = self._entries(video)
        fresh = 0
        for idx in want:
            if idx in entries:
                continue
            entries[idx] = encode_frame(by_index[idx], self.jpeg_quality)
            fresh += 1
        if fresh:
            self._persist(video, entries)
            with self._lock:
                self._ram[self._key(video)] = entries

    def build(
        self,
        videos: list[Path],
        *,
        sampler,
        fps: float,
        max_frames: int,
        windows: list[tuple[Path, float | None, float | None]] | None = None,
        workers: int = 8,
    ) -> dict[str, int]:
        """Pre-populate for the given windows (default: whole-video sampling)."""
        from concurrent.futures import ThreadPoolExecutor

        from .base import Segment

        jobs: list[tuple[Path, float | None, float | None]] = (
            [(video, None, None) for video in videos] if windows is None else windows
        )
        added: dict[str, int] = {}
        lock = threading.Lock()

        def one(job: tuple[Path, float | None, float | None]) -> None:
            video, start, end = job
            candidate = type("C", (), {"video_path": video, "segment": Segment(0, video.stem, start, end)})
            sample = sampler(candidate, fps, max_frames)
            want = sorted({int(i) for i in sample.frame_indices})
            by_index = dict(zip(want, list(sample.frames)))
            entries = self._entries(video)
            fresh = [idx for idx in want if idx not in entries]
            if not fresh:
                return
            for idx in fresh:
                entries[idx] = encode_frame(by_index[idx], self.jpeg_quality)
            self._persist(video, entries)
            with self._lock:
                self._ram[self._key(video)] = entries
            with lock:
                added[str(video)] = added.get(str(video), 0) + len(fresh)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, jobs))
        return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--videos", type=Path, required=True, help="directory of video files")
    parser.add_argument("--windows", type=Path, default=None, help="jsonl of {video,start,end}")
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=32)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    args = parser.parse_args()

    from .frames import sampled_video_array

    videos = sorted(p for p in args.videos.iterdir() if p.suffix == ".mp4")
    windows = None
    if args.windows is not None:
        by_name = {p.name: p for p in videos}
        windows = [
            (by_name[row["video"]], row.get("start"), row.get("end"))
            for row in (json.loads(line) for line in args.windows.read_text().splitlines() if line)
        ]
    store = FrameStore(args.store, jpeg_quality=args.jpeg_quality)
    added = store.build(
        videos, sampler=sampled_video_array, fps=args.fps, max_frames=args.max_frames, windows=windows
    )
    print(f"frame store ready at {args.store}: {sum(added.values())} frames across {len(added)} videos")


if __name__ == "__main__":
    main()

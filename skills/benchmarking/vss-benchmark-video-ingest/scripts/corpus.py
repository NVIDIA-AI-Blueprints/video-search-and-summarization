# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Corpus discovery and ffprobe measurement.

Throughput in this benchmark is counted in video-minutes, so a guessed clip
length corrupts every number in the run. Every file in a selected video class
is measured with ``ffprobe`` before the sweep starts, and a file that cannot be
read raises rather than being skipped -- skipping would silently change the
denominator of ``video_min_per_sec``.

Videos reach a run by one of two routes, and both end in the same
:class:`VideoItem` rows so nothing downstream has to care which was used:

``load_class``
    A folder under the corpus root. The four predefined size classes are just
    the folders the skill ships with; any validly-named folder works.

``load_user_videos``
    Files or directories the user named directly with ``--video``. No corpus
    root, no size-class layout -- the path someone takes when they want their
    own footage benchmarked rather than the reference corpus.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import json
import re
from pathlib import Path
import shutil
import subprocess

# The VSS service ingest route accepts these two MIME types only.
CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
}

#: The size classes the skill ships with. A run may also use a class name of
#: its own -- see ``validate_class_name`` -- so that a user can benchmark their
#: own footage without pretending it is one of these four.
PREDEFINED_VIDEO_CLASSES = ("50MB", "500MB", "2GB", "10GB")

#: Kept as the historical name so existing callers and configs keep working.
VALID_VIDEO_CLASSES = PREDEFINED_VIDEO_CLASSES

#: A class name becomes a directory name, a CSV cell, and a chart tick label,
#: so it is restricted to characters that are safe in all three.
_CLASS_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

#: Default label for videos supplied with ``--video`` rather than found under a
#: class folder. It is a real class everywhere downstream: it appears in
#: ``ingest_corpus.csv``, in the sweep matrix, and on the charts.
USER_CLASS_NAME = "custom"


class CorpusError(RuntimeError):
    """A corpus file is missing, unreadable, or not a supported video."""


@dataclass
class VideoItem:
    """One measured source video. Serialized as a row of ``ingest_corpus.csv``."""

    video_id: str
    video_class: str
    source_path: str
    bytes: int
    duration_sec: float
    fps: float
    width: int
    height: int
    resolution: str
    codec: str
    container: str
    content_type: str
    probe_status: str = "ok"
    extra: dict = field(default_factory=dict, repr=False)

    def as_row(self) -> dict:
        row = asdict(self)
        row.pop("extra", None)
        return row


def ffprobe_available() -> bool:
    """Return True when ``ffprobe`` is on PATH and answers ``-version``."""
    if shutil.which("ffprobe") is None:
        return False
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return True


def _parse_frame_rate(value: object) -> float:
    """Parse an ffprobe rational frame rate such as ``30000/1001``."""
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return 0.0
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            numerator, denominator = float(num), float(den)
        except ValueError:
            return 0.0
        return numerator / denominator if denominator else 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def probe_video(path: Path) -> dict:
    """Return ffprobe format/stream metadata for one file.

    Raises :class:`CorpusError` when ffprobe cannot read the file or the file
    carries no video stream with a positive duration.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=format_name,duration,bit_rate:stream=codec_type,codec_name,avg_frame_rate,width,height",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except subprocess.SubprocessError as exc:
        raise CorpusError(f"ffprobe failed on {path}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else f"exit {completed.returncode}"
        raise CorpusError(f"ffprobe failed on {path}: {reason}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise CorpusError(f"ffprobe returned non-JSON for {path}: {exc}") from exc

    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    video_stream = next(
        (s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise CorpusError(f"{path} carries no video stream")

    try:
        duration_sec = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration_sec = 0.0
    if duration_sec <= 0:
        raise CorpusError(
            f"{path} reports a non-positive duration; video-minute throughput would be wrong"
        )

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    return {
        "duration_sec": round(duration_sec, 3),
        "fps": round(_parse_frame_rate(video_stream.get("avg_frame_rate")), 3),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else "",
        "codec": str(video_stream.get("codec_name") or ""),
        "container": str(fmt.get("format_name") or ""),
    }


def content_type_for(path: Path) -> str:
    """Return the ingest Content-Type for a corpus file, or raise."""
    suffix = path.suffix.lower()
    content_type = CONTENT_TYPES.get(suffix)
    if content_type is None:
        raise CorpusError(
            f"{path} has unsupported extension {suffix!r}; "
            f"the VSS ingest route accepts only {', '.join(sorted(CONTENT_TYPES))}"
        )
    return content_type


def class_dir(corpus_root: Path, video_class: str) -> Path:
    return corpus_root / video_class


def validate_class_name(video_class: str) -> str:
    """Return ``video_class`` if it is usable as a class label, else raise.

    Any name is allowed, not just the four predefined size classes: a user
    benchmarking their own footage should be able to call the class what it is
    (``warehouse-4k``) instead of mislabelling it ``500MB``. The name still has
    to be a safe directory name, CSV cell, and chart tick label.
    """
    name = str(video_class or "").strip()
    if not _CLASS_NAME_RE.match(name):
        raise CorpusError(
            f"invalid video class name {video_class!r}: use 1-32 characters from "
            "A-Z, a-z, 0-9, dot, dash, or underscore, starting with a letter or "
            f"digit. The predefined classes are {', '.join(PREDEFINED_VIDEO_CLASSES)}."
        )
    return name


def _measure(
    paths: list[Path], video_class: str, measurements: dict[Path, dict] | None = None
) -> list[VideoItem]:
    """Reuse validated ffprobe measurements or measure paths, in the given order."""
    items: list[VideoItem] = []
    for path in paths:
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise CorpusError(f"{path} is empty")
        metadata = (measurements or {}).get(path.resolve())
        if metadata is None:
            metadata = probe_video(path)
        items.append(
            VideoItem(
                video_id=path.stem,
                video_class=video_class,
                source_path=str(path),
                bytes=size_bytes,
                content_type=content_type_for(path),
                **metadata,
            )
        )
    return items


def collect_video_paths(sources: list[str | Path]) -> list[Path]:
    """Expand user-supplied ``--video`` arguments into a sorted file list.

    Each source is either a single video file or a directory of them. A
    directory is read one level deep only, so nothing is pulled in from a
    subfolder the user did not mean to include. A named file with an
    unsupported extension is an error rather than a silent skip -- dropping it
    would quietly change the video-minute denominator.
    """
    files: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        path = Path(source).expanduser()
        if path.is_dir():
            found = sorted(
                p for p in path.iterdir() if p.is_file() and p.suffix.lower() in CONTENT_TYPES
            )
            if not found:
                raise CorpusError(f"--video directory {path} contains no .mp4 or .mkv files")
        elif path.is_file():
            content_type_for(path)  # raises on an unsupported extension
            found = [path]
        else:
            raise CorpusError(f"--video path not found: {path}")
        for item in found:
            resolved = item.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(item)
    if not files:
        raise CorpusError("--video was given but resolved to no video files")
    return files


def load_user_videos(
    sources: list[str | Path],
    video_class: str = USER_CLASS_NAME,
    limit: int | None = None,
    *,
    measurements: dict[Path, dict] | None = None,
) -> list[VideoItem]:
    """Measure videos the user named directly, as one ad-hoc class.

    This is the ``--video`` path: it needs no corpus root and no size-class
    folder layout, but produces exactly the same :class:`VideoItem` rows as
    :func:`load_class`, so every downstream metric, CSV, and chart treats a
    user-provided clip the same as a predefined-class one.
    """
    video_class = validate_class_name(video_class)
    paths = collect_video_paths(sources)
    if limit is not None:
        if limit <= 0:
            raise CorpusError("--limit must be a positive integer")
        paths = paths[:limit]
    return _measure(paths, video_class, measurements)


def load_class(
    corpus_root: Path, video_class: str, limit: int | None = None,
    *, measurements: dict[Path, dict] | None = None,
) -> list[VideoItem]:
    """Measure every video in one class folder and return the manifest.

    ``limit`` takes the first N files in sorted order -- the "corpus subset"
    sweep dimension. It is recorded in run-metadata.json because it changes the
    upload count for the sweep point.

    ``video_class`` is any folder under the corpus root, not only one of the
    four predefined sizes, so a user can drop their own clips into
    ``<corpus>/<name>/`` and sweep it by name.
    """
    video_class = validate_class_name(video_class)
    directory = class_dir(corpus_root, video_class)
    if not directory.is_dir():
        raise CorpusError(f"video class folder not found: {directory}")

    paths = sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in CONTENT_TYPES
    )
    if not paths:
        raise CorpusError(
            f"video class folder {directory} contains no .mp4 or .mkv files"
        )
    if limit is not None:
        if limit <= 0:
            raise CorpusError("--limit must be a positive integer")
        paths = paths[:limit]

    return _measure(paths, video_class, measurements)


def projected_bytes(items: list[VideoItem], concurrency: int) -> int:
    """Bytes on the wire for one sweep point: concurrency x class bytes."""
    return concurrency * sum(item.bytes for item in items)

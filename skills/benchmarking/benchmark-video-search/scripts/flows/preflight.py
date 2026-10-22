# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Non-destructive input and source-scope checks shared by the staged runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import flows

RTSP_SCHEME = "rtsp://"
VIDEO_SUFFIXES = (".mp4", ".mkv")


def _is_rtsp(source: dict[str, Any]) -> bool:
    return _source_type(source) == "rtsp"


def _source_type(source: dict[str, Any]) -> str | None:
    """Classify known media forms; unknown sources are never deletable."""
    url = str(source.get("url") or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() in {"rtsp", "rtsps"}:
        return "rtsp"
    if (
        url
        and parsed.scheme.lower() in {"", "file", "http", "https"}
        and Path(parsed.path).suffix.lower()
        in {".mp4", ".mkv", ".mov", ".avi", ".webm"}
    ):
        return "video_file"
    return None


def file_sources(vst_url: str) -> tuple[dict[str, str], list[str]]:
    """``({stream_id: name}`` for every uploaded file source``, [rtsp names])``.

    Only file sources are this eval's to delete. An RTSP camera indexes into
    dated indexes a ``video_file`` search never reads, and deleting one
    through the agent also deletes its VST storage.
    """
    try:
        sources = flows.list_sensor_sources(vst_url)
    except Exception as e:
        raise SystemExit(
            f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})"
        ) from e
    unknown = [sid for sid, source in sources.items() if _source_type(source) is None]
    if unknown:
        raise SystemExit(
            f"ABORTED: cannot safely classify source(s) for cleanup: {unknown[:10]}"
        )
    files = {
        sid: s["name"] for sid, s in sources.items() if _source_type(s) == "video_file"
    }
    rtsp = sorted(s["name"] for s in sources.values() if _is_rtsp(s))
    return files, rtsp


def local_videos(video_dir: Path) -> list[Path]:
    """The fixture videos under ``video_dir``, mp4 first, each group sorted."""
    if not video_dir.is_dir():
        return []
    return [
        f for suffix in VIDEO_SUFFIXES for f in sorted(video_dir.glob(f"*{suffix}"))
    ]


def check_inputs_before_wipe(
    video_dir: Path, data_dir: Path, dataset: str, subset: str
) -> None:
    """Abort before deleting anything if this run could not ingest and score.

    The wipe deletes every file source, so a missing video folder or unreadable
    dataset file found after it would leave the deployment empty for nothing.
    """
    try:
        annotations, _ = flows.unpack_dataset(
            flows.load_dataset_file(data_dir, dataset, subset)
        )
    except (OSError, ValueError) as e:
        raise SystemExit(
            f"ABORTED before deleting anything: could not read the {dataset} dataset under {data_dir} "
            f"({type(e).__name__}: {e})."
        ) from e
    if not annotations:
        raise SystemExit(
            f"ABORTED before deleting anything: the {dataset} dataset has no queries."
        )
    if not local_videos(video_dir):
        raise SystemExit(
            f"ABORTED before deleting anything: no .mp4 or .mkv files under {video_dir}, so there is "
            "nothing to ingest. Run without --skip-download, or pass --skip-ingest to reuse the index."
        )


def dataset_video_names(
    video_dir: Path, data_dir: Path, dataset: str, subset: str
) -> list[str]:
    """The dataset's video stems: its ground truth's names, else its local files.

    The ground truth is what gets scored, so a partial local download must not
    narrow the set. Local files are used only when no annotation names a video.
    """
    annotations, _ = flows.unpack_dataset(
        flows.load_dataset_file(data_dir, dataset, subset)
    )
    names = sorted(
        {
            (
                str(s["video_name"]).rsplit(".", 1)[0]
                if str(s["video_name"]).lower().endswith(VIDEO_SUFFIXES)
                else str(s["video_name"])
            )
            for segs in annotations.values()
            for s in segs
            if s.get("video_name")
        }
    )
    return names or [f.stem for f in local_videos(video_dir)]


def check_reused_index(
    vst_url: str, names: list[str], source_type: str | None = None
) -> dict[str, Any]:
    """For --skip-ingest: every dataset video must already be registered.

    A missing one would score as "nothing found" rather than "never indexed".
    Other sources are left in place, so they are reported: search can return
    them, and every such hit is a false positive.
    """
    try:
        if source_type is None:
            registered = flows.list_sensor_names(vst_url)
        else:
            registered = {
                source["name"].lower()
                for source in flows.list_sensor_sources(vst_url).values()
                if _source_type(source) == source_type
            }
    except Exception as e:
        raise SystemExit(
            f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})"
        ) from e
    missing = [n for n in names if not flows.is_registered(n, registered)]
    if missing:
        raise SystemExit(
            f"ABORTED: --skip-ingest reuses the index, but {len(missing)} dataset video(s) are not "
            f"registered: {missing[:10]}\n"
            "  Their queries would score zero. Use an explicitly confirmed cleanup and fresh ingestion to isolate the dataset."
        )
    ours: set[str] = set()
    for n in names:
        ours |= flows.name_variants(n)
    others = sorted(r for r in registered if r not in ours)
    if others:
        print("\n" + "!" * 60)
        print(
            f"WARNING: {len(others)} registered source(s) are not in this dataset, and --skip-ingest keeps them."
        )
        for name in others[:10]:
            print(f"  - {name}")
        if len(others) > 10:
            print(f"  ... and {len(others) - 10} more")
        print("  Search can return them, and every such hit is a false positive.")
        print(
            "  Use an explicitly confirmed cleanup and fresh ingestion to isolate the dataset."
        )
        print("!" * 60 + "\n")
    else:
        print(
            f"  Reusing the index: all {len(names)} dataset video(s) registered, nothing else."
        )
    return {"dataset_sources": len(names), "other_sources": others}


def decomposition_sources(
    annotations: dict[str, Any],
    vst_url: str | None,
    source_type: str = "video_file",
) -> list[str]:
    """The source list the decomposition prompt shows, as the agent builds it.

    The agent lists every VST source of the search's ``source_type``, in
    VST's spelling -- not only the ones holding an answer, which would hand
    the model a shortlist a real user never gets. Falls back to the dataset's
    names, and says so, when VST cannot be read.
    """
    if vst_url:
        try:
            sources = flows.list_sensor_sources(vst_url).values()
            return sorted(
                {
                    s["name"]
                    for s in sources
                    if s["name"] and _source_type(s) == source_type
                }
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"  WARNING: could not list VST sources for decomposition ({type(e).__name__}: {e});"
            )
            print("           the prompt lists only the dataset's annotated videos.")
    return sorted(
        {
            s["video_name"]
            for segs in annotations.values()
            for s in segs
            if s.get("video_name")
        }
    )

"""Shared helpers for agent tools."""

from __future__ import annotations


def format_timecode(ms: int) -> str:
    seconds, ms_part = divmod(int(ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms_part:03d}"


def make_citation(
    video_id: str,
    start_ms: int,
    end_ms: int,
    quote: str = "",
    quote_limit: int = 140,
) -> dict:
    end_ms = max(end_ms, start_ms)
    return {
        "video_id": video_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "quote": (quote or "")[:quote_limit],
        "timestamp": format_timecode(start_ms),
    }

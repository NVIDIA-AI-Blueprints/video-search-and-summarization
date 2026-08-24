"""Time normalization helpers.

Legacy video-summarization tracked chunk boundaries in ffmpeg pts
nanoseconds (ChunkInfo.start_pts/end_pts). The AWS pipeline standardizes on
integer milliseconds everywhere; these converters keep the two worlds
explicit.
"""

SECONDS_TO_MS = 1000
PTS_NS_TO_MS = 1_000_000


def pts_ns_to_ms(pts_ns: int) -> int:
    return int(pts_ns) // PTS_NS_TO_MS


def seconds_to_ms(seconds: float) -> int:
    return int(round(seconds * SECONDS_TO_MS))


def format_timecode(ms: int) -> str:
    total_ms = max(0, int(ms))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, ms_part = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms_part:03d}"


def describe_range(start_ms: int, end_ms: int) -> str:
    return f"[{format_timecode(start_ms)} - {format_timecode(end_ms)}]"

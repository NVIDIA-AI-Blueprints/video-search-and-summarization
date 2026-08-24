from workers.chunking.handler import _boundaries, build_chunks
from workers.common.artifacts import TranscriptSegment, VisualEvent
from workers.common.timeutil import describe_range


def _segment(start_ms: int, end_ms: int, text: str) -> TranscriptSegment:
    return TranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text)


def test_build_chunks_merges_transcript_and_visual():
    transcript = [
        _segment(0, 5000, "Welcome to the warehouse tour"),
        _segment(6000, 12000, "Here we see the forklift safety zone"),
        _segment(40000, 45000, "Finally, the shipping area"),
    ]
    visual = [
        VisualEvent(start_ms=7000, end_ms=11000, label="forklift", description="forklift moving", confidence=0.9)
    ]

    chunks = build_chunks("video-1", transcript, visual)

    assert chunks, "expected at least one chunk"
    first = chunks[0]
    assert first.start_ms == 0
    assert first.chunk_id.startswith("video-1-chunk-")
    assert "warehouse tour" in first.text or "forklift" in first.visual_summary


def test_boundaries_are_sorted_and_capped():
    transcript = [_segment(i * 1000, i * 1000 + 900, f"seg {i}") for i in range(100)]
    boundaries = _boundaries(transcript, [])

    assert boundaries == sorted(boundaries)
    for start, end in zip(boundaries, boundaries[1:]):
        assert end - start <= 30_000 + 1_000  # cap with tolerance for segment overshoot


def test_describe_range_formatting():
    assert describe_range(0, 65_500) == "[00:00:00.000 - 00:01:05.500]"

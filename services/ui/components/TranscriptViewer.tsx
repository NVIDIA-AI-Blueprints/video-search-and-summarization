"use client";

import { formatTimecode, type TranscriptSegment } from "@/lib/types";

export function TranscriptViewer({
  segments,
  activeIndex,
  onSeek,
}: {
  segments: TranscriptSegment[];
  activeIndex: number;
  onSeek?: (ms: number) => void;
}) {
  if (segments.length === 0) {
    return <div className="transcript-empty">Transcript will appear after processing completes.</div>;
  }

  return (
    <div className="transcript">
      {segments.map((segment, index) => (
        <button
          key={`${segment.start_ms}-${index}`}
          type="button"
          className={`transcript-row ${index === activeIndex ? "active" : ""}`}
          onClick={() => onSeek?.(segment.start_ms)}
        >
          <span className="transcript-time">{formatTimecode(segment.start_ms)}</span>
          <span className="transcript-text">{segment.text}</span>
        </button>
      ))}
    </div>
  );
}

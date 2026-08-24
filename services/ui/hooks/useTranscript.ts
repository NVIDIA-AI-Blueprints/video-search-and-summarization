"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { TranscriptSegment } from "@/lib/types";

export function useTranscript(videoId: string) {
  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    (async () => {
      // Transcript artifacts are served through the API in a later phase;
      // for now the viewer tolerates an empty transcript.
      try {
        const response = await fetch(`/api/transcripts/${videoId}`);
        if (response.ok && !cancelled) {
          const data = await response.json();
          setSegments(data.segments ?? []);
        }
      } catch {
        /* transcript unavailable */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [videoId]);

  const seekTo = useCallback(
    (ms: number) => {
      setActiveIndex(segments.findIndex((s) => s.start_ms <= ms && s.end_ms >= ms));
      return segments.find((s) => s.start_ms <= ms && s.end_ms >= ms);
    },
    [segments],
  );

  return { segments, activeIndex, seekTo, loading };
}

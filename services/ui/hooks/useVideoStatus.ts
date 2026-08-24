"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";
import type { ProcessingStatus, VideoMetadata } from "@/lib/types";

const ACTIVE_STATUSES = new Set<ProcessingStatus>([
  "UPLOADED",
  "TRANSCRIBING",
  "VISION_PROCESSING",
  "CHUNKING",
  "EMBEDDING",
]);

export function useVideoStatus(
  videoId: string | null,
  onStatus?: (video: VideoMetadata) => void,
) {
  useEffect(() => {
    if (!videoId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const video = await api.getVideo(videoId);
        if (cancelled) return;
        onStatus?.(video);
        if (ACTIVE_STATUSES.has(video.status)) {
          timer = setTimeout(poll, 5000);
        }
      } catch {
        if (!cancelled) timer = setTimeout(poll, 10000);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [videoId, onStatus]);
}

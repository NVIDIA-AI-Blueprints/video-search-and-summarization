"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { VideoMetadata } from "@/lib/types";

export function useVideos() {
  const [videos, setVideos] = useState<VideoMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const { videos: list } = await api.listVideos();
      setVideos(list);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load videos");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { videos, loading, error, refresh };
}

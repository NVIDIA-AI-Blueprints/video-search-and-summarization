"use client";

import { VideoLibrary } from "@/components/video/VideoLibrary";
import { useVideos } from "@/hooks/useVideos";

export default function LibraryPage() {
  const { videos, loading, error, refresh } = useVideos();

  return (
    <main className="page">
      <VideoLibrary
        videos={videos}
        loading={loading}
        error={error}
        onRefresh={() => void refresh()}
      />
    </main>
  );
}

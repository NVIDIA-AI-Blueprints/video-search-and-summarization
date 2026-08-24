"use client";

import { VideoLibraryGrid } from "@/features/library/VideoLibraryGrid";
import { useVideos } from "@/hooks/useVideos";

export default function LibraryPage() {
  const { videos, loading, error, refresh } = useVideos();

  return (
    <main className="page">
      <div className="cta-row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ margin: 0 }}>Video Library</h1>
        <button className="secondary" onClick={() => void refresh()}>Refresh</button>
      </div>
      {error && <p className="error">{error}</p>}
      <VideoLibraryGrid videos={videos} loading={loading} />
    </main>
  );
}

"use client";

import { VideoCard } from "@/components/video/VideoCard";
import type { VideoMetadata } from "@/lib/types";

export function VideoLibrary({
  videos,
  loading,
  error,
  onRefresh,
}: {
  videos: VideoMetadata[];
  loading: boolean;
  error?: string | null;
  onRefresh?: () => void;
}) {
  return (
    <>
      <div className="cta-row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ margin: 0 }}>Video Library</h1>
        <button className="secondary" onClick={onRefresh}>Refresh</button>
      </div>
      {error && <p className="error">{error}</p>}
      {loading ? (
        <div className="muted">Loading library…</div>
      ) : videos.length === 0 ? (
        <div className="muted">No videos yet. Upload your first one.</div>
      ) : (
        <div className="video-grid">
          {videos.map((video) => (
            <VideoCard key={video.video_id} video={video} />
          ))}
        </div>
      )}
    </>
  );
}

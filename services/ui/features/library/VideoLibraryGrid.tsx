"use client";

import Link from "next/link";
import { StatusBadge } from "@/components/StatusBadge";
import type { VideoMetadata } from "@/lib/types";

export function VideoLibraryGrid({
  videos,
  loading,
}: {
  videos: VideoMetadata[];
  loading: boolean;
}) {
  if (loading) return <div className="muted">Loading library…</div>;
  if (videos.length === 0) {
    return <div className="muted">No videos yet. Upload your first one.</div>;
  }

  return (
    <div className="video-grid">
      {videos.map((video) => (
        <Link key={video.video_id} href={`/videos/${video.video_id}`} className="video-card">
          <div className="video-card-title">{video.title ?? video.filename}</div>
          <div className="video-card-meta">
            <StatusBadge status={video.status} />
            <span>{new Date(video.created_at).toLocaleDateString()}</span>
          </div>
          {video.error_message && <p className="error">{video.error_message}</p>}
        </Link>
      ))}
    </div>
  );
}

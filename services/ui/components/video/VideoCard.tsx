"use client";

import Link from "next/link";
import { StatusBadge } from "@/components/StatusBadge";
import type { VideoMetadata } from "@/lib/types";

export function VideoCard({ video }: { video: VideoMetadata }) {
  return (
    <Link href={`/videos/${video.video_id}`} className="video-card">
      <div className="video-card-title">{video.title ?? video.filename}</div>
      <div className="video-card-meta">
        <StatusBadge status={video.status} />
        <span>{new Date(video.created_at).toLocaleDateString()}</span>
      </div>
      {video.error_message && <p className="error">{video.error_message}</p>}
    </Link>
  );
}

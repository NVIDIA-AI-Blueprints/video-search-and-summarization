"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ChatPanel } from "@/components/ChatPanel";
import { StatusBadge } from "@/components/StatusBadge";
import { TranscriptViewer } from "@/components/TranscriptViewer";
import { VideoPlayer } from "@/components/VideoPlayer";
import { useChat } from "@/hooks/useChat";
import { useTranscript } from "@/hooks/useTranscript";
import { useVideoStatus } from "@/hooks/useVideoStatus";
import { api } from "@/lib/api";
import type { Citation, VideoMetadata } from "@/lib/types";

export default function VideoDetailPage() {
  const params = useParams<{ id: string }>();
  const videoId = params.id;

  const [video, setVideo] = useState<VideoMetadata | null>(null);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [seekMs, setSeekMs] = useState<number | null>(null);

  const { segments, activeIndex, seekTo } = useTranscript(videoId);
  const { messages, send, busy } = useChat(videoId);

  const onStatus = useCallback((next: VideoMetadata) => setVideo(next), []);
  useVideoStatus(videoId, onStatus);

  useEffect(() => {
    void (async () => {
      try {
        const [{ url }] = await Promise.all([api.getStreamUrl(videoId)]);
        setStreamUrl(url);
      } catch {
        setStreamUrl(null);
      }
      try {
        setVideo(await api.getVideo(videoId));
      } catch {
        /* handled by status polling */
      }
    })();
  }, [videoId]);

  const onCitationClick = useCallback(
    (citation: Citation) => {
      const segment = seekTo(citation.start_ms);
      setSeekMs(segment ? segment.start_ms : citation.start_ms);
    },
    [seekTo],
  );

  return (
    <main className="page">
      <div className="cta-row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ margin: 0 }}>{video?.title ?? video?.filename ?? "Video"}</h1>
        {video && <StatusBadge status={video.status} />}
      </div>
      {video?.error_message && <p className="error">{video.error_message}</p>}

      <div className="detail-layout" style={{ marginTop: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <VideoPlayer src={streamUrl} seekToMs={seekMs} />
          <TranscriptViewer segments={segments} activeIndex={activeIndex} onSeek={(ms) => setSeekMs(ms)} />
        </div>
        <ChatPanel messages={messages} busy={busy} onSend={(q) => void send(q)} onCitationClick={onCitationClick} />
      </div>
    </main>
  );
}

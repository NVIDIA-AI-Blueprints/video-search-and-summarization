"use client";

import { useEffect, useRef, useState } from "react";

export function VideoPlayer({
  src,
  seekToMs,
}: {
  src: string | null;
  seekToMs?: number | null;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (ref.current && typeof seekToMs === "number" && Number.isFinite(seekToMs)) {
      ref.current.currentTime = seekToMs / 1000;
      void ref.current.play().catch(() => undefined);
    }
  }, [seekToMs]);

  if (!src || error) {
    return <div className="player-empty">Video unavailable</div>;
  }

  return (
    <video
      ref={ref}
      src={src}
      controls
      className="player"
      onError={() => setError(true)}
    />
  );
}

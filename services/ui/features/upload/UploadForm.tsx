"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useRouter } from "next/navigation";

export function UploadForm({ onUploaded }: { onUploaded?: () => void }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    try {
      setProgress("Requesting upload URL…");
      const created = await api.createVideo(file.name, file.type || "video/mp4");
      setProgress("Uploading…");
      await api.uploadFile(created.upload_url, file);
      onUploaded?.();
      router.push(`/videos/${created.video.video_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  return (
    <div className="upload-form">
      <input
        type="file"
        accept="video/*"
        onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        disabled={busy}
      />
      <button type="button" onClick={submit} disabled={!file || busy}>
        {busy ? "Working…" : "Upload"}
      </button>
      {progress && <p className="muted">{progress}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

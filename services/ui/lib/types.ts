export type ProcessingStatus =
  | "UPLOADED"
  | "TRANSCRIBING"
  | "VISION_PROCESSING"
  | "CHUNKING"
  | "EMBEDDING"
  | "READY"
  | "FAILED";

export interface VideoMetadata {
  video_id: string;
  owner_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: ProcessingStatus;
  duration_ms: number | null;
  title: string | null;
  description: string | null;
  error_message: string | null;
  created_at: number;
  updated_at: number;
}

export interface VideoCreated {
  video: VideoMetadata;
  upload_url: string;
}

export interface TranscriptSegment {
  start_ms: number;
  end_ms: number;
  text: string;
  speaker?: string | null;
}

export interface Citation {
  video_id: string;
  start_ms: number;
  end_ms: number;
  quote?: string | null;
  timestamp?: string;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
}

export function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.floor(ms));
  const h = Math.floor(total / 3_600_000);
  const m = Math.floor((total % 3_600_000) / 60_000);
  const s = Math.floor((total % 60_000) / 1000);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

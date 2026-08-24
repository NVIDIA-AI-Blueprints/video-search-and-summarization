import type { ProcessingStatus } from "@/lib/types";

const LABELS: Record<ProcessingStatus, string> = {
  UPLOADED: "Queued",
  TRANSCRIBING: "Transcribing",
  VISION_PROCESSING: "Analyzing visuals",
  CHUNKING: "Chunking",
  EMBEDDING: "Indexing",
  READY: "Ready",
  FAILED: "Failed",
};

export function StatusBadge({ status }: { status: ProcessingStatus }) {
  const tone =
    status === "READY"
      ? "badge-ok"
      : status === "FAILED"
        ? "badge-err"
        : "badge-busy";
  return <span className={`badge ${tone}`}>{LABELS[status] ?? status}</span>;
}

"use client";

import { formatTimecode, type Citation } from "@/lib/types";

export function Citation({
  citation,
  onClick,
}: {
  citation: Citation;
  onClick?: (citation: Citation) => void;
}) {
  return (
    <button
      type="button"
      className="citation-chip"
      title={citation.quote ?? undefined}
      onClick={() => onClick?.(citation)}
    >
      {formatTimecode(citation.start_ms)}
    </button>
  );
}

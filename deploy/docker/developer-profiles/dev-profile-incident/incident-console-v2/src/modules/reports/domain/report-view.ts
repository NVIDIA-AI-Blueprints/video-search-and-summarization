// SPDX-License-Identifier: Apache-2.0

// Report view types and browser-safe helpers. Built server-side by
// lib/reports/view.ts (which reads notes and validates the contract); this
// module must not import server-only code.
//
// The report view of one revision: three sources, never merged into one another.
// - Class A (structured): the revision's payload (incident, entities,
//   instruments, assets) with each item's evidence. A human revision holds the
//   reviewer's values; an AI revision the model's.
// - Class B (modelOutput): read-only fields from the immutable original model
//   report in model_runs.notes (title, severity rationale, location, timeline,
//   uncertainties), plus the model's original structured values so edits can
//   be shown against them.
// - Class C (run, revision): run, revision and provenance metadata.

import type { RevisionSummary } from '@/modules/reports/domain/revision';
import type { ContractRunRequest, ContractRunResponse } from '@/modules/reports/domain/run-notes';

export interface StructuredIncident {
  type: string | null;
  startSeconds: number | null;
  endSeconds: number | null;
  duration: number | null;
  description: string | null;
  severityLevel: number | null;
  confidenceScore: number | null;
}

/**
 * An item's evidence in this revision: the whole video frame (key and signed URL) the box is drawn on, the crop (key and signed
 * URL; what evidence search embeds), and the box details. Items analysed before frames were stored have no frame.
 */
export interface ItemEvidence {
  image: string | null;
  imageUrl: string | null;
  /** R2 key of the whole frame at native size; null for an item analysed before frames were stored. */
  frame: string | null;
  frameUrl: string | null;
  /** False when the detector found nothing (the frame has no box); null when the entry predates the flag. */
  located: boolean | null;
  /** The second the frame was read at (a retry second when the requested one found nothing); null when not recorded. */
  boxSeconds: number | null;
  /** Why the item has no image or box, when the agent said. */
  imageGap: string | null;
  boundingBox: number[] | null;
  timestampSeconds: number | null;
  /** True for an image a reviewer uploaded (no bounding box in the video frame). */
  uploaded: boolean;
  /** R2 key of the person's track (<itemId>-track.json); null for an item with no track (not matched, or analysed without tracking). */
  track: string | null;
  /** Same-origin URL the browser reads the track's samples from (R2 has no CORS rule for the console); null without a track. */
  trackUrl: string | null;
  /** Console route that cuts (on first play) and signs the item's clip; null when it has no second or no stored evidence to keep a clip beside. */
  clipUrl: string | null;
  /** The track's span on screen in video seconds, beside the report's own first_seen/last_seen (never rewritten). */
  trackFirstSeenSeconds: number | null;
  trackLastSeenSeconds: number | null;
}

// timestampSeconds: when the item is most clearly visible; label: its short detector label.
interface ItemDetails {
  timestampSeconds: number | null;
  label: string;
  evidence: ItemEvidence | null;
}
/** firstSeenSeconds / lastSeenSeconds: the span the entity is on screen; null for a revision saved without one (e.g. a human edit). */
export interface StructuredEntity extends ItemDetails {
  entityId: string;
  type: string;
  description: string;
  firstSeenSeconds: number | null;
  lastSeenSeconds: number | null;
}
export interface StructuredInstrument extends ItemDetails {
  instrumentId: string;
  entityId: string | null;
  name: string;
  description: string;
  threatLevel: number | null;
}
export interface StructuredAsset extends ItemDetails {
  assetId: string;
  name: string;
  description: string;
}

export interface ModelOutput {
  source: 'contract' | 'legacy';
  title: string | null;
  location: string | null;
  severityReason: string | null;
  /** The model's own structured values, for comparison with the (possibly edited) projection. */
  original: {
    type: string | null;
    severityLevel: number | null;
    startSeconds: number | null;
    endSeconds: number | null;
    description: string | null;
  };
}

export interface RunDetails {
  videoId: string;
  /** Null only for a revision whose run is not recorded. */
  modelRunId: string | null;
  filename: string;
  playbackUrl: string;
  model: string;
  promptVersion: string | null;
  generatedAt: string;
  notesKind: 'contract' | 'failed-attempt' | 'invalid-contract' | 'legacy' | 'none';
  /** Contract runs only: how the stored report was obtained. */
  status?: 'valid_first_pass' | 'valid_after_structural_repair' | 'contract_failed' | 'request_failed';
  statusInferred?: boolean;
  firstPassViolations?: Array<{ code: string; path: string; message: string; scope?: string }>;
  /** Contract runs: how the original response was judged (absent before validation policy core-scored-v1). */
  validation?: { policy?: string; fullContractValid?: boolean; coreValid?: boolean; videoBoundsChecked?: boolean };
  repair?: Record<string, unknown> | null;
  notesError?: string;
  request?: ContractRunRequest;
  response?: ContractRunResponse;
  rawModelOutput?: string;
  originalReport?: unknown;
  legacyReviewerEdits?: Record<string, unknown>;
}

export interface ReportView {
  videoId: string;
  revisionId: number;
  modelRunId: string | null;
  structured: {
    incident: StructuredIncident;
    entities: StructuredEntity[];
    instruments: StructuredInstrument[];
    assets: StructuredAsset[];
  };
  modelOutput: ModelOutput | null;
  run: RunDetails;
  revision: RevisionSummary;
  /** The incident's one official revision (D6); never inferred from recency. */
  official: { officialRevisionId: number | null; isOfficial: boolean };
  /** Where the current structured values differ from the model's original ones. */
  differences: { type?: { model: string; current: string }; severity?: { model: number; current: number }; fields: string[] };
}

/** Heading: the model's title when it gave one, else "<Type> report". */
export function reportHeading(view: ReportView): string {
  return (
    view.modelOutput?.title ||
    `${view.structured.incident.type ? view.structured.incident.type[0].toUpperCase() + view.structured.incident.type.slice(1) : 'Incident'} report`
  );
}

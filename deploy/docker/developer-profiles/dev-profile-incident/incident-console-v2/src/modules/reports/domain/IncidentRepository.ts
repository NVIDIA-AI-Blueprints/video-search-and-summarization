// SPDX-License-Identifier: Apache-2.0

// IncidentRepository: the report library, and one incident's report
// revisions (report_revisions). A video is one incident; each analysis adds an
// AI revision and each reviewer save adds a human revision. Revisions
// are append-only: nothing here updates or deletes one.

import type { Page } from '@/modules/shared/domain/common';
import type { StructuredAsset, StructuredEntity, StructuredIncident, StructuredInstrument } from '@/modules/reports/domain/report-view';
import type { EvidenceEntry, OfficialCrop, ReportPayload, RevisionKind, RevisionSummary } from '@/modules/reports/domain/revision';
import type { RunNotes } from '@/modules/reports/domain/run-notes';
import type { ReportLibraryItem } from '@/modules/reports/domain/storage';

export type ReportSort = 'newest' | 'oldest' | 'severity-high' | 'severity-low' | 'confidence-high' | 'confidence-low';

/** The report library's filters, validated by the route; null means no filter. */
export interface ReportListQuery {
  page: number;
  pageSize: number;
  /** Matched against title, type, summary, filename and evidence text. */
  search: string | null;
  type: string | null;
  /** A band ('low' | 'medium' | 'high') or an exact level ('1' to '5'). */
  severity: string | null;
  /** The shown revision's kind: 'ai' (as generated) or 'human' (saved by a reviewer). */
  kind: RevisionKind | null;
  /** Bounds on the shown revision's creation time, as stored timestamps (naive UTC). */
  generatedAfter: string | null;
  generatedBefore: string | null;
  /** Time-of-day bounds, read in `timezone`. */
  timeFrom: string | null;
  timeTo: string | null;
  /** Evidence text: substrings, or exact names when evidenceMatch is 'exact'. */
  entities: string[] | null;
  instruments: string[] | null;
  assets: string[] | null;
  /** 0 (Sunday) to 6, in `timezone`. */
  dayOfWeek: number | null;
  sort: ReportSort;
  /** 'official' keeps only incidents with an official revision. */
  scope: 'all' | 'official' | 'not_official';
  /** Canonical entity types (human, animal, unknown). */
  entityTypes: string[] | null;
  evidenceMatch: 'contains' | 'exact';
  timezone: string;
}

/** One page of the report library: one card per incident, its official revision or else its newest. Thumbnails are not signed here. */
export interface ReportList extends Page<ReportLibraryItem> {
  /** Every incident type in the store, for the type filter. */
  incidentTypes: string[];
}

/**
 * Everything stored for one revision, translated: the facts the report view
 * is built from. The payload's items are already mapped to the structured
 * shapes (each with its evidence entry; images are not signed here) and the
 * run's immutable model_runs.notes are already decoded.
 */
export interface RevisionRecord {
  revision: RevisionSummary;
  /** The stored payload and evidence, for building the next revision from this one. */
  payload: ReportPayload;
  evidence: EvidenceEntry[];
  video: {
    /** videos.filepath when it is a non-empty string, else null. */
    storageKey: string | null;
    filename: string;
    officialRevisionId: number | null;
  };
  /** Null when the revision names no run or its run is not recorded. */
  modelRun: { modelName: string | null; promptVersion: string | null; runAt: string | null; notes: RunNotes } | null;
  incident: StructuredIncident;
  entities: StructuredEntity[];
  instruments: StructuredInstrument[];
  assets: StructuredAsset[];
}

/** A human revision to append. */
export interface HumanRevisionInput {
  videoId: string;
  /** The revision the reviewer opened (Save). */
  parentId: number;
  payload: ReportPayload;
  evidence: EvidenceEntry[];
  /**
   * The searchable crops, stored in the same write as the revision when the video has no official
   * revision (the new revision is then the searchable one). Empty when there is nothing to store.
   */
  crops: OfficialCrop[];
  createdBy: string;
  note: string | null;
}

export interface IncidentRepository {
  /** One filtered, sorted page of incident cards. */
  listReports(query: ReportListQuery): Promise<ReportList>;
  /**
   * One revision of the incident. With revisionId null: the official revision,
   * else the newest. Null when the video, or that revision of it, is missing.
   */
  getRevision(videoId: string, revisionId: number | null): Promise<RevisionRecord | null>;
  /** The incident's History: every revision, newest first. Empty for an unknown video. */
  listRevisions(videoId: string): Promise<RevisionSummary[]>;
  /**
   * Every evidence R2 key (evidence/...) the video's stored records can still show: any string in
   * the evidence of any revision (older ones included) and the image of any evidence_crops row. Null for an unknown
   * video. Throws, rather than answering with fewer keys, when a record cannot be read in full:
   * a caller deletes whatever is not listed here.
   */
  listReferencedEvidenceImages(videoId: string): Promise<string[] | null>;
  /**
   * Appends a human revision with the next revision number, the given parent
   * and the parent's run. Throws StoreRejection: 'not-found' for an unknown
   * video or a parent that is not one of its revisions, 'invalid' for content
   * the store refuses (e.g. an empty reviewer name).
   */
  saveHumanRevision(input: HumanRevisionInput): Promise<RevisionSummary>;
}

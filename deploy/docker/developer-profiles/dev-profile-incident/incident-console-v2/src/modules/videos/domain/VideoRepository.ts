// SPDX-License-Identifier: Apache-2.0

// VideoRepository: the video registry (1 video = 1 incident), its one official
// revision, and whole-video deletion. R2 objects are never touched here.

import type { Page } from '@/modules/shared/domain/common';
import type { OfficialCrop } from '@/modules/reports/domain/revision';

export type VideoListFilter = 'all' | 'without-report' | 'awaiting-selection' | 'with-report' | 'needs-analysis' | 'failed';

/** One row of a video list. */
export interface VideoListItem {
  videoId: string;
  filename: string;
  uploadedAt: string | null;
  source: string | null;
  /** Report revisions of the incident, AI and human; failed attempts are counted in the video's history. */
  revisions: number;
  officialRevisionId: number | null;
}

export interface VideoRecord {
  videoId: string;
  /** videos.filepath as stored: an R2 key for uploads, but legacy rows may hold a VST path (check with isValidR2Key). */
  storageKey: string | null;
  uploadedAt: string | null;
  source: string | null;
  officialRevisionId: number | null;
}

export interface OfficialChange {
  videoId: string;
  officialRevisionId: number | null;
  /** evidence_crops rows written for the new official revision (0 when cleared). */
  crops: number;
}

export interface RevisionCropsChange {
  videoId: string;
  revisionId: number;
  /** evidence_crops rows now stored for the revision (0 when it has none). */
  crops: number;
}

/** One AI report revision of a video with the model run that produced it. */
export interface RevisionRunRecord {
  revisionId: number;
  modelRunId: string;
  incidentType: string | null;
  severityLevel: number | null;
  createdAt: string | null;
  modelName: string | null;
  runDatetime: string | null;
  /** The model run's notes record as stored (JSON text). */
  notes: string | null;
}

/** A model run whose notes name a video: a candidate for a failed attempt in its history. */
export interface AttemptRunRecord {
  modelRunId: string;
  modelName: string | null;
  runDatetime: string | null;
  notes: string | null;
}

/** The stored records behind a video's analysis history, read before the history is built. */
export interface RunHistoryRecords {
  video: VideoRecord;
  revisions: RevisionRunRecord[];
  attempts: AttemptRunRecord[];
  /** Model runs that more than one video uses (batch eval/seed runs). */
  sharedRunIds: Set<string>;
}

export interface VideoRepository {
  listVideos(filter: VideoListFilter, page: number, pageSize: number): Promise<Page<VideoListItem>>;
  getVideo(videoId: string): Promise<VideoRecord | null>;
  /** The records a video's analysis history is built from; null for an unknown video. */
  listRunHistory(videoId: string): Promise<RunHistoryRecords | null>;
  /**
   * Makes revisionId the video's one official revision (null clears it) and,
   * in the same transaction, replaces the video's evidence_crops with the
   * given crops (they carry the agent's vectors). Throws StoreRejection: 'not-found' for an unknown
   * video or a revision that is not one of its own.
   */
  setOfficialRevision(videoId: string, revisionId: number | null, crops: OfficialCrop[]): Promise<OfficialChange>;
  /**
   * Replaces the video's evidence_crops with the given crops for revisionId, which must be the
   * video's searchable revision (the official one, else the newest), without changing the
   * official pointer. Throws StoreRejection: 'not-found' for an unknown video, 'invalid' for a
   * revision that is not the searchable one.
   */
  setRevisionCrops(videoId: string, revisionId: number, crops: OfficialCrop[]): Promise<RevisionCropsChange>;
  /** Deletes the video row (its revisions, evidence crops and query results cascade), then the run records that name only this video. */
  deleteVideo(videoId: string): Promise<void>;
}

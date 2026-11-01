// SPDX-License-Identifier: Apache-2.0

// AnalysisRunRepository: saving what one incident-contract analysis produced.
// Every attempt that reaches the model is saved with its outcome; only a valid
// one gets an AI report revision. The saves are separate writes, in the order
// the adapter documents, not one transaction: a transactional save RPC would
// close that gap but is a database change and is on hold.

import type { RepairIneligibility, RepairOperation } from '@/modules/contract/domain/repair';
import type { DerivedContractReport } from '@/modules/contract/domain/types';
import type { ContractViolation, ContractViolationCode, ViolationScope } from '@/modules/contract/domain/validate';
import type { EvidenceEntry } from '@/modules/reports/domain/revision';

/** The four analysis outcomes. Only the two valid ones ever produce report rows. */
export type AttemptStatus = 'valid_first_pass' | 'valid_after_structural_repair' | 'contract_failed' | 'request_failed';

/** Where a failed attempt was decided. */
export type AttemptStage = 'gateway' | 'upstream_response' | 'contract_validation' | 'structural_repair';

export type AttemptRepair =
  | { eligible: false; ruleSet: string; reason: RepairIneligibility; blockingCodes: ContractViolationCode[]; detail: string }
  | { eligible: true; ruleSet: string; operations: RepairOperation[]; revalidation: ContractViolation[] };

/** Everything recorded for one incident-contract-v4 analysis attempt, whatever its outcome. */
export interface AnalysisAttemptRecord {
  status: AttemptStatus;
  stage: AttemptStage | null;
  failure: { code: string; message: string } | null;
  contractVersion: string;
  videoId: string;
  r2Key: string;
  modelRunId: string;
  attemptedAt: string;
  request: {
    model: string;
    inferenceConfig: Record<string, unknown>;
    additionalInstruction: string | null;
    promptSha256: string;
    schemaSha256: string;
    /** The video's length from its MP4 header; null when it could not be read (video-length rule not applied). */
    videoDurationSeconds?: number | null;
    /** Present (true) only when the run asked the agent to blur faces (the video's privacy toggle). */
    blurFaces?: true;
  };
  /** Only the fields the gateway actually returned; the content is the original, never altered. */
  response: {
    httpStatus?: number;
    errorBody?: string;
    content?: string | null;
    reasoningContent?: string | null;
    finishReason?: string | null;
    usage?: Record<string, unknown> | null;
    /** Agent mode, person tracking on: the agent's tracking summary ({enabled, matched, tracks, elapsed_ms, skipped}) as returned. */
    tracking?: Record<string, unknown>;
    /** Agent mode, blur_faces asked: the agent's privacy summary ({blur_faces, regions, frames, heads, head_queries, failed}) as returned. */
    privacy?: Record<string, unknown>;
    /** Agent mode, object tracking on: the agent's object-tracking summary ({enabled, seeds, tracked, tracker, items, ...}) as returned. */
    object_tracking?: Record<string, unknown>;
  } | null;
  /**
   * firstPass: every violation of the original response, each with its
   * core-scored-v1 scope. The outcome follows core validity; fullContractValid
   * records incident-contract validity (schema + every cross-field rule) of the
   * original response for comparison with eval. Absent on records written
   * before the policy existed (those required full contract validity).
   */
  validation: {
    firstPass: Array<ContractViolation & { scope?: ViolationScope }>;
    policy?: string;
    fullContractValid?: boolean;
    coreValid?: boolean;
    videoBoundsChecked?: boolean;
  };
  repair: AttemptRepair | null;
}

/** A valid attempt: it also carries the final validated report. Its AI revision id is known only once the revision is stored. */
export interface ContractRunRecord extends AnalysisAttemptRecord {
  status: 'valid_first_pass' | 'valid_after_structural_repair';
  generatedAt: string;
  report: DerivedContractReport;
}

/** A newly uploaded video, recorded before its first analysis. */
export interface UploadedVideo {
  videoId: string;
  r2Key: string;
  uploadedAt: string;
}

/** What the agent stored or cropped itself (agent mode); the revision is stored by the repository when the agent did not. */
export interface AgentStoredRevision {
  revisionId: number | null;
  evidence: EvidenceEntry[] | null;
}

export interface AnalysisRunRepository {
  /**
   * Records the uploaded video once, before inference, so it exists even if
   * that analysis fails. An existing video is never updated, so re-runs cannot
   * reset its upload time.
   */
  recordUploadedVideo(video: UploadedVideo): Promise<void>;
  /**
   * Saves a contract_failed or request_failed attempt: the run record only,
   * never a report revision, so it can never appear as a report or in incident
   * statistics. Throws for a valid attempt.
   */
  saveFailedAttempt(attempt: AnalysisAttemptRecord): Promise<void>;
  /**
   * Saves one valid run as a new model run, stores its AI revision unless the
   * agent already did, then reads it back and throws if any part is missing or
   * the video's storage key is not run.r2Key. onStep receives a description of
   * each step before it starts, for the caller's error message. Returns the
   * AI revision id.
   */
  saveRun(run: ContractRunRecord, agent: AgentStoredRevision, onStep: (step: string) => void): Promise<number>;
}

// SPDX-License-Identifier: Apache-2.0

// Reads model_runs.notes, which holds either the immutable incident-contract-v2
// attempt record (modules/analysis/infrastructure/supabase/analysis-runs.ts attemptNotes) or a legacy console
// report. Nothing here writes notes.

import type { DerivedContractReport } from '@/modules/contract/domain/types';
import { validateCore, type ContractViolation } from '@/modules/contract/domain/validate';
import { reportFromNotes, type LegacyAnalysisReport } from '@/modules/reports/domain/storage';

export interface ContractRunRequest {
  model?: string;
  inferenceConfig?: Record<string, unknown>;
  additionalInstruction?: string | null;
  promptSha256?: string;
  schemaSha256?: string;
  /** The video's length read before validation; null or absent when unknown. */
  videoDurationSeconds?: number | null;
  /** True when the run asked the agent to blur faces; absent otherwise. */
  blurFaces?: boolean;
}

export interface ContractRunResponse {
  httpStatus?: number;
  errorBody?: string;
  content?: string | null;
  reasoningContent?: string | null;
  finishReason?: string | null;
  usage?: Record<string, unknown> | null;
  /** Agent mode with person tracking on: the agent's tracking summary {enabled, matched, tracks, elapsed_ms, skipped}. */
  tracking?: Record<string, unknown>;
  /** Agent mode with blur_faces asked: the agent's privacy summary {blur_faces, regions, frames, heads, head_queries, failed}. */
  privacy?: Record<string, unknown>;
  /** Agent mode with object tracking on: the agent's object-tracking summary {enabled, seeds, tracked, tracker, elapsed_ms, time_cap_reached, skipped, detector, items}. */
  object_tracking?: Record<string, unknown>;
}

export type ValidStatus = 'valid_first_pass' | 'valid_after_structural_repair';
export type FailedStatus = 'contract_failed' | 'request_failed';

export interface AttemptAudit {
  /** Stage-1 records predate the status field; the only outcome that stage could persist was valid_first_pass. */
  statusInferred: boolean;
  validation: {
    firstPass: Array<{ code: string; path: string; message: string; scope?: string }>;
    /** Present on records written under validation policy core-scored-v1. */
    policy?: string;
    fullContractValid?: boolean;
    coreValid?: boolean;
    videoBoundsChecked?: boolean;
  };
  repair: Record<string, unknown> | null;
}

export type RunNotes =
  | ({
      kind: 'contract';
      status: ValidStatus;
      contractVersion: string;
      videoId: string | null;
      attemptedAt: string | null;
      report: DerivedContractReport;
      request: ContractRunRequest;
      response: ContractRunResponse;
      /** Violations confined to the unscored enrichment fields of the stored report (e.g. the timeline); the report itself is unaltered. */
      enrichment: ContractViolation[];
    } & AttemptAudit)
  | ({
      kind: 'failed-attempt';
      status: FailedStatus;
      contractVersion: string;
      videoId: string | null;
      attemptedAt: string | null;
      stage: string | null;
      failure: { code: string; message: string } | null;
      request: ContractRunRequest;
      response: ContractRunResponse | null;
    } & AttemptAudit)
  | { kind: 'invalid-contract'; contractVersion: string; error: string; request: ContractRunRequest; response: ContractRunResponse }
  | { kind: 'legacy'; report: LegacyAnalysisReport; rawModelOutput?: string; legacyReviewerEdits?: Record<string, unknown> }
  | { kind: 'none' };

function asObject(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

/**
 * The videoId a run record explicitly names in its notes (attempt records:
 * incidentConsoleV2.videoId; legacy console reports: incidentConsoleV2.report.videoId
 * or a flat incidentConsoleV2.videoId), without validating the report. Null when absent.
 */
export function explicitVideoId(notes: unknown): string | null {
  if (typeof notes !== 'string') return null;
  try {
    const wrapper = asObject(asObject(JSON.parse(notes))?.incidentConsoleV2);
    if (!wrapper) return null;
    if (typeof wrapper.videoId === 'string' && wrapper.videoId) return wrapper.videoId;
    const report = asObject(wrapper.report);
    return typeof report?.videoId === 'string' && report.videoId ? report.videoId : null;
  } catch {
    return null;
  }
}

/**
 * Cheap outcome lookup for listings (no contract re-validation): the recorded
 * status, 'valid_first_pass' for pre-outcome contract records, and 'legacy'
 * for everything recorded before outcomes existed.
 */
export function recordedOutcome(notes: unknown): ValidStatus | FailedStatus | 'legacy' {
  if (typeof notes !== 'string') return 'legacy';
  try {
    const wrapper = asObject(asObject(JSON.parse(notes))?.incidentConsoleV2);
    if (!wrapper || typeof wrapper.contractVersion !== 'string') return 'legacy';
    const status = wrapper.status;
    if (status === 'valid_after_structural_repair' || status === 'contract_failed' || status === 'request_failed') return status;
    return 'valid_first_pass';
  } catch {
    return 'legacy';
  }
}

export function parseRunNotes(notes: unknown): RunNotes {
  if (typeof notes !== 'string' || !notes.trim()) return { kind: 'none' };
  let wrapper: Record<string, unknown> | undefined;
  try {
    wrapper = asObject(asObject(JSON.parse(notes))?.incidentConsoleV2);
  } catch {
    return { kind: 'none' };
  }
  if (!wrapper) return { kind: 'none' };

  if (typeof wrapper.contractVersion === 'string') {
    const request = (asObject(wrapper.request) ?? {}) as ContractRunRequest;
    const response = (asObject(wrapper.response) ?? {}) as ContractRunResponse;
    const rawStatus = typeof wrapper.status === 'string' ? wrapper.status : null;
    const validation = asObject(wrapper.validation);
    const flag = (key: string) => (typeof validation?.[key] === 'boolean' ? (validation[key] as boolean) : undefined);
    const audit: AttemptAudit = {
      statusInferred: rawStatus === null,
      validation: {
        firstPass: (Array.isArray(validation?.firstPass) ? validation.firstPass : []) as AttemptAudit['validation']['firstPass'],
        ...(typeof validation?.policy === 'string' ? { policy: validation.policy } : {}),
        ...(flag('fullContractValid') !== undefined ? { fullContractValid: flag('fullContractValid') } : {}),
        ...(flag('coreValid') !== undefined ? { coreValid: flag('coreValid') } : {}),
        ...(flag('videoBoundsChecked') !== undefined ? { videoBoundsChecked: flag('videoBoundsChecked') } : {}),
      },
      repair: asObject(wrapper.repair) ?? null,
    };
    if (rawStatus === 'contract_failed' || rawStatus === 'request_failed') {
      const failure = asObject(wrapper.failure);
      return {
        kind: 'failed-attempt',
        status: rawStatus,
        contractVersion: wrapper.contractVersion,
        videoId: typeof wrapper.videoId === 'string' ? wrapper.videoId : null,
        attemptedAt: typeof wrapper.attemptedAt === 'string' ? wrapper.attemptedAt : null,
        stage: typeof wrapper.stage === 'string' ? wrapper.stage : null,
        failure: failure && typeof failure.code === 'string' ? { code: failure.code, message: String(failure.message ?? '') } : null,
        request,
        response: asObject(wrapper.response) ? response : null,
        ...audit,
      };
    }
    const status: ValidStatus = rawStatus === 'valid_after_structural_repair' ? 'valid_after_structural_repair' : 'valid_first_pass';
    if (rawStatus !== null && rawStatus !== 'valid_first_pass' && rawStatus !== 'valid_after_structural_repair') {
      return {
        kind: 'invalid-contract',
        contractVersion: wrapper.contractVersion,
        error: `unknown attempt status ${rawStatus}`,
        request,
        response,
      };
    }
    const stored = asObject(wrapper.report);
    const incident = asObject(stored?.incident);
    try {
      if (!stored || !incident) throw new Error('the run record has no contract report');
      // The stored report carries the derived incident.duration, which the
      // schema forbids from the model; validate the model-shaped part and
      // check the derivation separately. Validation follows core-scored-v1
      // (as when the run was recorded): enrichment violations such as a
      // timeline outside the incident window are reported, not fatal.
      const { duration, ...modelIncident } = incident;
      const videoDurationSeconds = typeof request.videoDurationSeconds === 'number' ? request.videoDurationSeconds : null;
      const { report, enrichment } = validateCore(
        { ...stored, incident: modelIncident },
        { videoDurationSeconds, contractVersion: wrapper.contractVersion },
      );
      const expected = report.incident.end_timestamp - report.incident.start_timestamp;
      if (duration !== expected) throw new Error(`stored incident.duration ${String(duration)} is not ${expected}`);
      return {
        kind: 'contract',
        status,
        contractVersion: wrapper.contractVersion,
        videoId: typeof wrapper.videoId === 'string' ? wrapper.videoId : null,
        attemptedAt:
          typeof wrapper.attemptedAt === 'string'
            ? wrapper.attemptedAt
            : typeof wrapper.generatedAt === 'string'
              ? wrapper.generatedAt
              : null,
        report: { ...report, incident: { ...report.incident, duration: expected } },
        request,
        response,
        enrichment,
        ...audit,
      };
    } catch (error) {
      return {
        kind: 'invalid-contract',
        contractVersion: wrapper.contractVersion,
        error: error instanceof Error ? error.message : String(error),
        request,
        response,
      };
    }
  }

  const report = reportFromNotes(notes);
  if (!report) return { kind: 'none' };
  return {
    kind: 'legacy',
    report,
    rawModelOutput: report.rawModelOutput,
    legacyReviewerEdits: asObject(wrapper.editedReport),
  };
}

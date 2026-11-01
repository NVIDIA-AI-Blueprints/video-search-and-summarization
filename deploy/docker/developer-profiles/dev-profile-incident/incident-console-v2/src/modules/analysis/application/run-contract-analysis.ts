// SPDX-License-Identifier: Apache-2.0

// The single incident-contract-v4 analysis path, shared by the upload flow and
// re-runs of an existing video: allowlisted model -> signed R2 URL -> P1
// request identical to eval's -> validation under policy core-scored-v1 ->
// optional id-normalization-v1 repair (re-validated) -> a new, unique model run.
//
// core-scored-v1 (modules/contract/domain/validate.ts validateCore, same as eval): the
// database-backed fields decide the outcome. A cross-field failure confined to
// the unscored enrichment fields (e.g. a timeline outside the incident window)
// is recorded, not fatal, and never repaired; the incident window must also lie
// within the video when its length can be read from the MP4 header.
//
// Every attempt that reaches the model is recorded with one of four outcomes:
//   valid_first_pass | valid_after_structural_repair  -> AI report revision + notes
//   contract_failed  | request_failed                 -> model_runs notes only
// The original model response is stored unaltered in every case.
//
// The model, agent, video store and persistence are ports; the composition root
// (lib/container.ts) chooses the adapters.

import { buildP1Request, normalizeAdditionalInstruction, p1InferenceConfig } from '@/modules/analysis/application/build-request';
import type {
  AgentStoredRevision,
  AnalysisAttemptRecord,
  AnalysisRunRepository,
  AttemptRepair,
  ContractRunRecord,
} from '@/modules/analysis/domain/AnalysisRunRepository';
import { AgentRequestError, type AgentHttpResponse, type IncidentAgent } from '@/modules/analysis/domain/IncidentAgent';
import { GatewayError, type GatewayCompletionResponse, type ModelGateway } from '@/modules/analysis/domain/ModelGateway';
import type { VideoObjectStore } from '@/modules/analysis/domain/VideoObjectStore';
import type { ContractCatalog } from '@/modules/contract/domain/ContractCatalog';
import { attemptIdNormalization, REPAIR_RULE_SET } from '@/modules/contract/domain/repair';
import type { IncidentContractReport } from '@/modules/contract/domain/types';
import {
  ContractError,
  decodeContent,
  validateCore,
  VALIDATION_POLICY,
  violationScope,
  withDerivedFields,
  type ContractViolation,
  type ValidationContext,
  type ViolationScope,
} from '@/modules/contract/domain/validate';
import { compactId } from '@/lib/ids';
import { evidenceEntries } from '@/modules/reports/domain/revision';

/** A request the caller must fix, or an attempt whose outcome is a recorded failure. */
export class AnalysisRequestError extends Error {
  status: number;
  details?: Record<string, unknown>;

  constructor(status: number, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = 'AnalysisRequestError';
    this.status = status;
    this.details = details;
  }
}

/** The outbound ports one analysis uses. A null model port means its URL is not configured. */
export interface AnalysisPorts {
  runs: AnalysisRunRepository;
  videos: VideoObjectStore;
  /** Gateway mode's model call. */
  gateway: ModelGateway | null;
  /** Agent mode's model call. */
  agent: IncidentAgent | null;
  /** Contract catalog port providing prompts, schemas, version, and model configurations. */
  catalog: ContractCatalog;
  /** Port for generating random UUIDs. */
  randomUUID: () => string;
}

export type AnalysisMode = 'gateway' | 'agent';

export interface ContractAnalysisInput {
  videoId: string;
  r2Key: string;
  model: unknown;
  /** The reviewer prompt typed on re-generation: appended to the gateway request, or sent to the agent as reviewer_prompt. */
  additionalInstruction?: unknown;
  /** Present only for a newly uploaded video; its row is created once and never reset. */
  newVideo?: { uploadedAt: string };
  /** The video's privacy toggle: agent mode asks the agent to blur the faces in the stored evidence (gateway mode stores none). */
  blurFaces?: boolean;
}

export interface ContractAnalysisResult {
  run: ContractRunRecord;
  playbackUrl: string;
  /** The new AI revision (report_revisions.id). */
  revisionId: number;
}

export function resolveAllowedModel(model: unknown, catalog: ContractCatalog) {
  const allowed = typeof model === 'string' ? catalog.findAllowedModel(model.trim()) : undefined;
  if (!allowed)
    throw new AnalysisRequestError(400, `Unsupported model: ${typeof model === 'string' && model.trim() ? model.trim() : '(none)'}`);
  return allowed;
}

/** Summary of the original response under core-scored-v1, recorded on every validated attempt. */
export interface PolicyVerdict {
  firstPass: Array<ContractViolation & { scope: ViolationScope }>;
  fullContractValid: boolean;
  videoBoundsChecked: boolean;
}

type Outcome =
  | ({ kind: 'valid'; report: IncidentContractReport; repair: AttemptRepair | null } & PolicyVerdict)
  | ({
      kind: 'contract_failed';
      stage: 'contract_validation' | 'structural_repair';
      repair: AttemptRepair | null;
      message: string;
    } & PolicyVerdict);

const withScope = (violations: ContractViolation[]) =>
  violations.map((violation) => ({ ...violation, scope: violationScope(violation.code) }));

/**
 * core-scored-v1 validation, then id-normalization-v1 only when every core
 * first-pass violation is eligible, then core validation again. Enrichment
 * violations are recorded but never block, trigger or are touched by repair.
 */
export function evaluateContent(content: string, context: ValidationContext = {}): Outcome {
  const videoBoundsChecked = typeof context.videoDurationSeconds === 'number' && context.videoDurationSeconds > 0;
  let decoded: unknown;
  try {
    decoded = decodeContent(content);
    const core = validateCore(decoded, context);
    return {
      kind: 'valid',
      report: core.report,
      repair: null,
      firstPass: withScope(core.violations),
      fullContractValid: core.fullContractValid,
      videoBoundsChecked: core.videoBoundsChecked,
    };
  } catch (error) {
    if (!(error instanceof ContractError)) throw error;
    const firstPass = withScope(error.violations);
    const coreViolations = firstPass.filter((violation) => violation.scope === 'core');
    const structural = firstPass.every((violation) => !['EMPTY_CONTENT', 'INVALID_JSON', 'SCHEMA_VIOLATION'].includes(violation.code));
    // Full incident-contract validity of the original response: the contextual
    // video rules never affect it, so it fails only on a contract violation.
    const verdict: PolicyVerdict = {
      firstPass,
      fullContractValid:
        structural &&
        !firstPass.some((violation) => violation.code !== 'WINDOW_BEYOND_VIDEO' && violation.code !== 'TIMELINE_BEYOND_VIDEO'),
      videoBoundsChecked: structural && videoBoundsChecked,
    };
    if (!structural) {
      const blockingCodes = [...new Set(firstPass.map((violation) => violation.code))];
      return {
        kind: 'contract_failed',
        stage: 'contract_validation',
        repair: { eligible: false, ruleSet: REPAIR_RULE_SET, reason: 'INELIGIBLE_VIOLATION', blockingCodes, detail: error.message },
        message: error.message,
        ...verdict,
      };
    }
    const attempt = attemptIdNormalization(decoded as IncidentContractReport, coreViolations);
    if (!attempt.eligible) {
      const { eligible, ruleSet, reason, blockingCodes, detail } = attempt;
      return {
        kind: 'contract_failed',
        stage: 'contract_validation',
        repair: { eligible, ruleSet, reason, blockingCodes, detail },
        message: error.message,
        ...verdict,
      };
    }
    try {
      const repaired = validateCore(attempt.repaired, context);
      return {
        kind: 'valid',
        report: repaired.report,
        repair: { eligible: true, ruleSet: attempt.ruleSet, operations: attempt.operations, revalidation: repaired.violations },
        ...verdict,
      };
    } catch (revalidationError) {
      if (!(revalidationError instanceof ContractError)) throw revalidationError;
      return {
        kind: 'contract_failed',
        stage: 'structural_repair',
        repair: { eligible: true, ruleSet: attempt.ruleSet, operations: attempt.operations, revalidation: revalidationError.violations },
        message: `${error.message}; after ${attempt.ruleSet}: ${revalidationError.message}`,
        ...verdict,
      };
    }
  }
}

function validationRecord(outcome: Outcome): AnalysisAttemptRecord['validation'] {
  return {
    firstPass: outcome.firstPass,
    policy: VALIDATION_POLICY,
    fullContractValid: outcome.fullContractValid,
    // The original response's core validity, before any ID repair.
    coreValid: outcome.kind === 'valid' && outcome.repair === null,
    videoBoundsChecked: outcome.videoBoundsChecked,
  };
}

function completionResponse(completion: GatewayCompletionResponse): NonNullable<AnalysisAttemptRecord['response']> {
  const choice = completion.choices[0];
  const response: NonNullable<AnalysisAttemptRecord['response']> = { content: choice.message.content ?? null };
  if ('reasoning_content' in choice.message) response.reasoningContent = choice.message.reasoning_content ?? null;
  if ('finish_reason' in choice) response.finishReason = choice.finish_reason ?? null;
  if ('usage' in completion) response.usage = completion.usage ?? null;
  return response;
}

/** The FastAPI error detail of an agent error body; undefined when the body is not JSON. */
function agentErrorDetail(text: string): unknown {
  try {
    return (JSON.parse(text) as { detail?: unknown }).detail;
  } catch {
    return undefined;
  }
}

async function recordFailure(runs: AnalysisRunRepository, attempt: AnalysisAttemptRecord): Promise<boolean> {
  try {
    await runs.saveFailedAttempt(attempt);
    return true;
  } catch {
    return false;
  }
}

export async function runContractAnalysis(
  input: ContractAnalysisInput,
  ports: AnalysisPorts,
  mode: AnalysisMode,
): Promise<ContractAnalysisResult> {
  let model = resolveAllowedModel(input.model, ports.catalog);
  let additionalInstruction: string | null;
  try {
    additionalInstruction = normalizeAdditionalInstruction(input.additionalInstruction);
  } catch (error) {
    throw new AnalysisRequestError(400, error instanceof Error ? error.message : String(error));
  }

  let operation = 'verifying the R2 video object';
  try {
    const { contentLength } = await ports.videos.verify(input.r2Key);
    if (input.newVideo) {
      operation = 'recording the uploaded video';
      await ports.runs.recordUploadedVideo({
        videoId: input.videoId,
        r2Key: input.r2Key,
        uploadedAt: input.newVideo.uploadedAt,
      });
    }
    operation = 'creating the signed R2 video URL';
    const playbackUrl = await ports.videos.playbackUrl(input.r2Key);

    operation = 'reading the video length';
    const videoDurationSeconds = await ports.videos.durationSeconds(input.r2Key, contentLength);

    const attemptedAt = new Date().toISOString();
    const modelRunId = compactId('m', `${input.videoId}:${attemptedAt}:${ports.randomUUID()}`);
    const base: Omit<AnalysisAttemptRecord, 'status' | 'stage' | 'failure' | 'response' | 'validation' | 'repair'> = {
      contractVersion: ports.catalog.contractVersion(),
      videoId: input.videoId,
      r2Key: input.r2Key,
      modelRunId,
      attemptedAt,
      request: {
        model: model.id,
        inferenceConfig: p1InferenceConfig(ports.catalog),
        additionalInstruction,
        promptSha256: ports.catalog.promptSha256(),
        schemaSha256: ports.catalog.schemaSha256(),
        videoDurationSeconds,
        ...(input.blurFaces ? { blurFaces: true as const } : {}),
      },
    };

    let response: NonNullable<AnalysisAttemptRecord['response']>;
    // What the agent stored or cropped itself (agent mode); the console stores the revision when it did not.
    const fromAgent: AgentStoredRevision = { revisionId: null, evidence: null };

    if (mode === 'agent') {
      operation = 'calling the incident agent';
      if (!ports.agent) throw new Error('INCIDENT_AGENT_BASE_URL is not configured');
      let agentResponse: AgentHttpResponse;
      try {
        agentResponse = await ports.agent.analyze(
          {
            videoId: input.videoId,
            videoKey: input.r2Key,
            videoUrl: playbackUrl,
            modelRunId,
            model: model.id,
            additionalInstruction,
            videoDurationSeconds,
            blurFaces: input.blurFaces === true,
          },
          AbortSignal.timeout(model.timeout_seconds * 1000),
        );
      } catch (error) {
        if (!(error instanceof AgentRequestError)) throw error;
        const { code, message } = error;
        const attempt: AnalysisAttemptRecord = {
          ...base,
          status: 'request_failed',
          stage: 'gateway',
          failure: { code, message },
          response: null,
          validation: { firstPass: [] },
          repair: null,
        };
        const recorded = await recordFailure(ports.runs, attempt);
        throw new AnalysisRequestError(code === 'AGENT_TIMEOUT' ? 504 : 502, `The agent request failed (${code}): ${message}`, {
          outcome: 'request_failed',
          code,
          model: model.id,
          attemptId: modelRunId,
          attemptRecorded: recorded,
        });
      }

      // A 422 is the agent rejecting the model output under core-scored-v1; its detail
      // carries the raw output, which is validated and recorded here like a success.
      type AgentP1 = {
        raw_output?: unknown;
        finish_reason?: unknown;
        model?: unknown;
        contract_version?: unknown;
        revision_id?: unknown;
        evidence?: unknown;
        tracking?: unknown;
        privacy?: unknown;
        object_tracking?: unknown;
      };
      let agentP1: AgentP1 | null = null;
      if (agentResponse.ok) {
        agentP1 = JSON.parse(agentResponse.text) as AgentP1;
      } else if (agentResponse.status === 422) {
        const detail = agentErrorDetail(agentResponse.text);
        if (detail && typeof detail === 'object' && typeof (detail as AgentP1).raw_output === 'string') agentP1 = detail as AgentP1;
      }
      if (!agentP1) {
        const errorText = agentResponse.text;
        const code = agentResponse.status === 504 ? 'AGENT_TIMEOUT' : `AGENT_HTTP_${agentResponse.status}`;
        const attempt: AnalysisAttemptRecord = {
          ...base,
          status: 'request_failed',
          stage: 'gateway',
          failure: { code, message: errorText || agentResponse.statusText },
          response: { httpStatus: agentResponse.status, errorBody: errorText },
          validation: { firstPass: [] },
          repair: null,
        };
        const recorded = await recordFailure(ports.runs, attempt);
        throw new AnalysisRequestError(
          agentResponse.status === 504 ? 504 : 502,
          `The agent returned HTTP ${agentResponse.status}: ${errorText}`,
          {
            outcome: 'request_failed',
            code,
            model: model.id,
            attemptId: modelRunId,
            attemptRecorded: recorded,
          },
        );
      }

      // P1 ran on the agent's own VLM, so its model is the one recorded.
      if (typeof agentP1.model === 'string' && agentP1.model) {
        base.request.model = agentP1.model;
        model = { ...model, id: agentP1.model };
      }

      response = { content: typeof agentP1.raw_output === 'string' ? agentP1.raw_output : null };
      if (typeof agentP1.revision_id === 'number' && Number.isInteger(agentP1.revision_id)) fromAgent.revisionId = agentP1.revision_id;
      if (Array.isArray(agentP1.evidence)) fromAgent.evidence = evidenceEntries(agentP1.evidence);
      if (typeof agentP1.finish_reason === 'string') response.finishReason = agentP1.finish_reason;
      // Person tracking (agent INCIDENT_TRACKING_ENABLED): its summary {enabled, matched, tracks, elapsed_ms, skipped}, as returned.
      if (agentP1.tracking && typeof agentP1.tracking === 'object' && !Array.isArray(agentP1.tracking))
        response.tracking = agentP1.tracking as Record<string, unknown>;
      // Privacy blur (blur_faces asked): the agent's summary, so the report can say its images were blurred.
      if (agentP1.privacy && typeof agentP1.privacy === 'object' && !Array.isArray(agentP1.privacy))
        response.privacy = agentP1.privacy as Record<string, unknown>;
      // Object tracking (agent INCIDENT_OBJECT_TRACKING_ENABLED): its summary {enabled, seeds, tracked, tracker, items, ...}, as returned.
      if (agentP1.object_tracking && typeof agentP1.object_tracking === 'object' && !Array.isArray(agentP1.object_tracking))
        response.object_tracking = agentP1.object_tracking as Record<string, unknown>;

      if (agentP1.contract_version !== ports.catalog.contractVersion()) {
        const message = `Agent contract version (${String(agentP1.contract_version ?? 'missing')}) does not match console (${ports.catalog.contractVersion()})`;
        const attempt: AnalysisAttemptRecord = {
          ...base,
          status: 'request_failed',
          stage: 'gateway',
          failure: { code: 'CONTRACT_VERSION_MISMATCH', message },
          response: { httpStatus: agentResponse.status, ...response },
          validation: { firstPass: [] },
          repair: null,
        };
        const recorded = await recordFailure(ports.runs, attempt);
        throw new AnalysisRequestError(502, message, {
          outcome: 'request_failed',
          code: 'CONTRACT_VERSION_MISMATCH',
          model: model.id,
          attemptId: modelRunId,
          attemptRecorded: recorded,
        });
      }
    } else {
      operation = `calling the VLM gateway (${model.id})`;
      if (!ports.gateway) throw new Error('VLM_GATEWAY_URL is not configured');
      let completion: GatewayCompletionResponse;
      try {
        completion = await ports.gateway.complete(
          buildP1Request(model.id, playbackUrl, additionalInstruction, ports.catalog),
          AbortSignal.timeout(model.timeout_seconds * 1000),
        );
      } catch (error) {
        if (!(error instanceof GatewayError)) throw error;
        const resp: AnalysisAttemptRecord['response'] =
          error.httpStatus !== undefined || error.errorBody !== undefined
            ? {
                ...(error.httpStatus !== undefined ? { httpStatus: error.httpStatus } : {}),
                ...(error.errorBody !== undefined ? { errorBody: error.errorBody } : {}),
              }
            : null;
        const attempt: AnalysisAttemptRecord = {
          ...base,
          status: 'request_failed',
          stage: 'gateway',
          failure: { code: error.code, message: error.message },
          response: resp,
          validation: { firstPass: [] },
          repair: null,
        };
        const recorded = await recordFailure(ports.runs, attempt);
        throw new AnalysisRequestError(
          error.code === 'GATEWAY_TIMEOUT' ? 504 : 502,
          `The model request failed (${error.code}): ${error.message}`,
          {
            outcome: 'request_failed',
            code: error.code,
            model: model.id,
            attemptId: modelRunId,
            attemptRecorded: recorded,
          },
        );
      }
      response = completionResponse(completion);
    }

    const content = response.content ?? '';
    const finishReason = response.finishReason ?? null;
    if ((finishReason !== null && finishReason !== 'stop') || !content) {
      const code = content ? `FINISH_${String(finishReason).toUpperCase()}` : 'UPSTREAM_EMPTY_CONTENT';
      const message = content
        ? `The model stopped with finish_reason "${finishReason}" before a usable response`
        : 'The model returned no response content';
      const attempt: AnalysisAttemptRecord = {
        ...base,
        status: 'request_failed',
        stage: 'upstream_response',
        failure: { code, message },
        response,
        validation: { firstPass: [] },
        repair: null,
      };
      const recorded = await recordFailure(ports.runs, attempt);
      throw new AnalysisRequestError(502, `${message} (${code}).`, {
        outcome: 'request_failed',
        code,
        model: base.request.model,
        finishReason,
        attemptId: modelRunId,
        attemptRecorded: recorded,
      });
    }

    operation = 'validating the VLM response against the incident contract';
    const outcome = evaluateContent(content, {
      videoDurationSeconds,
      contractVersion: ports.catalog.contractVersion(),
      catalog: ports.catalog,
    });
    if (outcome.kind === 'contract_failed') {
      // The failure is decided by core violations; enrichment ones stay in validation.firstPass.
      const codes = [
        ...new Set(
          [...outcome.firstPass, ...(outcome.repair?.eligible ? outcome.repair.revalidation : [])]
            .filter((violation) => violationScope(violation.code) === 'core')
            .map((violation) => violation.code),
        ),
      ];
      const attempt: AnalysisAttemptRecord = {
        ...base,
        status: 'contract_failed',
        stage: outcome.stage,
        failure: { code: codes.join(','), message: outcome.message },
        response,
        validation: validationRecord(outcome),
        repair: outcome.repair,
      };
      const recorded = await recordFailure(ports.runs, attempt);
      throw new AnalysisRequestError(422, `The model's response did not satisfy the incident contract: ${outcome.message}`, {
        outcome: 'contract_failed',
        codes,
        model: base.request.model,
        finishReason,
        rawContent: content.slice(0, 20000),
        attemptId: modelRunId,
        attemptRecorded: recorded,
      });
    }

    const run: ContractRunRecord = {
      ...base,
      status: outcome.repair ? 'valid_after_structural_repair' : 'valid_first_pass',
      stage: null,
      failure: null,
      response,
      validation: validationRecord(outcome),
      repair: outcome.repair,
      generatedAt: attemptedAt,
      report: withDerivedFields(outcome.report),
    };
    const revisionId = await ports.runs.saveRun(run, fromAgent, (nextOperation) => {
      operation = nextOperation;
    });
    return { run, playbackUrl, revisionId };
  } catch (error) {
    if (error instanceof AnalysisRequestError) throw error;
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`${operation} failed: ${detail}`);
  }
}

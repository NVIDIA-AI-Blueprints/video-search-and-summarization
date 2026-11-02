// SPDX-License-Identifier: Apache-2.0

// IncidentAgent over HTTP: POST /api/v1/incidents/{videoId}/analyze and /reblur on the
// native vss-agent. Only a network failure or a timeout is thrown (as
// AgentRequestError); every HTTP answer is returned for the caller to judge.

import {
  AgentRequestError,
  type AgentAnalyzeRequest,
  type AgentHttpResponse,
  type AgentReblurRequest,
  type IncidentAgent,
} from '@/modules/analysis/domain/IncidentAgent';

/** The agent accepts only a finite length above zero; anything else is left for it to read itself. */
function isUsableDuration(seconds: number | null): seconds is number {
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0;
}

export class HttpIncidentAgent implements IncidentAgent {
  private readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  async analyze(request: AgentAnalyzeRequest, signal: AbortSignal): Promise<AgentHttpResponse> {
    return this.post(
      `${this.baseUrl}/api/v1/incidents/${request.videoId}/analyze`,
      {
        video_url: request.videoUrl,
        video_key: request.videoKey,
        model_run_id: request.modelRunId,
        model: request.model,
        skip_rp1: true,
        ...(request.additionalInstruction ? { reviewer_prompt: request.additionalInstruction } : {}),
        ...(isUsableDuration(request.videoDurationSeconds) ? { video_duration_seconds: request.videoDurationSeconds } : {}),
        ...(request.blurFaces ? { blur_faces: true } : {}),
      },
      signal,
    );
  }

  async reblur(request: AgentReblurRequest, signal: AbortSignal): Promise<AgentHttpResponse> {
    return this.post(
      `${this.baseUrl}/api/v1/incidents/${request.videoId}/reblur`,
      {
        source_revision_id: request.sourceRevisionId,
        video_url: request.videoUrl,
        video_key: request.videoKey,
        blur_faces: request.blurFaces,
        model_run_id: request.modelRunId,
        model: request.model,
        ...(isUsableDuration(request.videoDurationSeconds) ? { video_duration_seconds: request.videoDurationSeconds } : {}),
      },
      signal,
    );
  }

  private async post(endpoint: string, body: Record<string, unknown>, signal: AbortSignal): Promise<AgentHttpResponse> {
    let response: Response;
    try {
      response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal,
      });
    } catch (error) {
      const isTimeout = error instanceof DOMException && error.name === 'TimeoutError';
      const message = error instanceof Error ? error.message : String(error);
      throw new AgentRequestError(isTimeout ? 'AGENT_TIMEOUT' : 'AGENT_FETCH_FAILED', message);
    }
    return {
      status: response.status,
      ok: response.ok,
      statusText: response.statusText,
      text: await response.text().catch(() => ''),
    };
  }
}

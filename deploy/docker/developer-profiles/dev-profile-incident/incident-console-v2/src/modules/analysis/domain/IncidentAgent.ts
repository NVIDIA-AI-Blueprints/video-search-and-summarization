// SPDX-License-Identifier: Apache-2.0

// IncidentAgent: the native vss-agent's incident analysis (agent mode), P1 only
// (skip_rp1). The port returns the raw
// HTTP outcome; the caller decides what a success, a contract refusal (422 with the raw
// output) or a failure means.

export interface AgentAnalyzeRequest {
  videoId: string;
  /** The video's R2 key; the agent names its evidence crops after it. */
  videoKey: string;
  /** The signed R2 playback URL the agent fetches the video from. */
  videoUrl: string;
  modelRunId: string;
  model: string;
  /** The reviewer prompt, sent only when present. */
  additionalInstruction: string | null;
  /**
   * The video length the console read from the MP4 header, so the agent applies the
   * video-bounds rules with the same length; null when unknown (the agent then reads
   * the header itself).
   */
  videoDurationSeconds: number | null;
}

export interface AgentHttpResponse {
  status: number;
  ok: boolean;
  statusText: string;
  /** The response body as text; JSON is parsed by the caller. */
  text: string;
}

/** The agent could not be reached or did not answer in time; code names which. */
export class AgentRequestError extends Error {
  code: 'AGENT_TIMEOUT' | 'AGENT_FETCH_FAILED';

  constructor(code: 'AGENT_TIMEOUT' | 'AGENT_FETCH_FAILED', message: string) {
    super(message);
    this.name = 'AgentRequestError';
    this.code = code;
  }
}

export interface IncidentAgent {
  analyze(request: AgentAnalyzeRequest, signal: AbortSignal): Promise<AgentHttpResponse>;
}

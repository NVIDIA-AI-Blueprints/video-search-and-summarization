// SPDX-License-Identifier: Apache-2.0

// IncidentAgent: the native vss-agent's incident analysis (agent mode), P1 only
// (skip_rp1), and the evidence re-blur of a stored revision. The port returns the raw
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
  /** Ask the agent to blur the faces in the stored evidence (sent as blur_faces only when true). */
  blurFaces?: boolean;
}

export interface AgentReblurRequest {
  videoId: string;
  /** The video's R2 key; the new images go under evidence/<videoKey>/run-<n>/. */
  videoKey: string;
  /** The signed R2 playback URL the agent reads the frames from. */
  videoUrl: string;
  /** The revision whose report and evidence entries are kept (the one the report page shows). */
  sourceRevisionId: number;
  modelRunId: string;
  model: string;
  /** True to blur every head in the new images; false to store them unblurred. */
  blurFaces: boolean;
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
  /**
   * POST /api/v1/incidents/{videoId}/reblur: new evidence images of an existing revision, blurred or not, stored as a
   * new AI revision with the same report. No model reads the video's content: only the head detector, when blurring.
   */
  reblur(request: AgentReblurRequest, signal: AbortSignal): Promise<AgentHttpResponse>;
}

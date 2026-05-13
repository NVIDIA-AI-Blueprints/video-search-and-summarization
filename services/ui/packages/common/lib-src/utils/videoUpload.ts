/**
 * Chat video upload helpers.
 *
 * The chat upload is a three-step handshake against the agent:
 *
 *  1. POST {agent}/videos with {filename} → returns the VST nvstreamer
 *     upload URL. The browser doesn't need to know where VST lives — the
 *     agent owns URL resolution per profile.
 *  2. POST each file chunk to that URL (nvstreamer protocol; agent is
 *     not in the upload data path). VST returns sensorId on the
 *     final-chunk response — that's the VST stream id.
 *  3. POST {agent}/videos/{stream-id}/complete to trigger post-processing
 *     (timeline lookup → storage URL resolution → optional RTVI-CV
 *     register → optional embedding generation, each step self-skipping
 *     when its backing service isn't configured).
 *
 * `uploadFileChunked` orchestrates all three. Callers don't need
 * VST's URL.
 */

import { chunkedUpload } from './chunkedUpload';
import type { ChunkedUploadResponse } from './chunkedUpload';

interface AgentUploadUrlResponse {
  url: string;
}

export interface FileUploadResult {
  filename: string;
  bytes: number;
  sensorId: string;
  streamId: string;
  filePath: string;
  timestamp: string;
}

/**
 * Step 1 of the chunked upload: POST {agent}/videos to get the VST
 * upload URL. The agent picks the right URL per profile (typically
 * `{vst_external_url}/v1/storage/file`).
 */
export async function getUploadUrl(
  filename: string,
  agentApiUrl: string,
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch(`${agentApiUrl.replace(/\/$/, '')}/videos`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
    signal,
  });

  if (!response.ok) {
    let message = response.statusText;
    try {
      const errorData = await response.json();
      if (errorData?.detail != null) {
        message =
          typeof errorData.detail === 'string' ? errorData.detail : JSON.stringify(errorData.detail);
      }
    } catch {
      // use statusText
    }
    throw new Error(message);
  }

  const data: AgentUploadUrlResponse = await response.json();
  if (!data?.url) {
    throw new Error('Upload-URL response missing "url"');
  }
  return data.url;
}

/**
 * Step 3 of the chunked upload: POST {agent}/videos/{sensor_id}/complete
 * so the agent can run post-upload processing (embeddings, RTVI
 * registration, etc.).
 *
 * `sensorId` is the VST sensor id returned in the final-chunk response.
 * The path mirrors VST's `/vst/api/v1/sensor/...` surface. For uploaded
 * videos one sensor maps to a single stream so sensor_id is unambiguous;
 * for live RTSP, one VST sensor can fan out to multiple streams — but
 * that lifecycle goes through /api/v1/rtsp-streams, not this route.
 *
 * The VST upload response is forwarded as the request body so the UI
 * stays decoupled from the storage API shape; the agent reads `filename`
 * (for the response message and RTVI camera_name) and `custom_params`
 * (per-upload params from the dialog template) and ignores the rest.
 */
export async function notifyGenericUploadComplete(
  agentApiUrl: string,
  sensorId: string,
  filename: string,
  uploadResponse: ChunkedUploadResponse,
  formData?: Record<string, any>,
  signal?: AbortSignal,
): Promise<void> {
  if (!sensorId) {
    throw new Error('notifyGenericUploadComplete: sensorId is required');
  }
  const url = `${agentApiUrl.replace(/\/$/, '')}/videos/${encodeURIComponent(sensorId)}/complete`;

  const body: Record<string, any> = { ...uploadResponse, filename };
  if (formData && Object.keys(formData).length > 0) {
    body.custom_params = formData;
  }

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    let message = `Post-processing failed with status ${response.status}`;
    try {
      const errorData = await response.json();
      if (errorData?.detail) {
        message = typeof errorData.detail === 'string' ? errorData.detail : JSON.stringify(errorData.detail);
      }
    } catch { /* use default */ }
    throw new Error(message);
  }
}

/**
 * Chunked upload via the agent handshake. Runs the three-step flow:
 * agent → VST URL → chunked upload → agent /complete with the VST
 * stream id. Each chunk is its own short HTTP request so the
 * Cloudflare 100s timeout doesn't apply to large files.
 *
 * `formData` is forwarded to /complete as `custom_params` so per-upload
 * parameters from the chat dialog template reach the agent.
 */
export async function uploadFileChunked(
  file: File,
  agentApiUrl: string,
  formData: Record<string, any>,
  onProgress?: (progress: number) => void,
  abortSignal?: AbortSignal,
  requestFilename?: string,
): Promise<FileUploadResult> {
  const filenameForRequest = requestFilename?.trim() || file.name;

  if (abortSignal?.aborted) {
    throw new Error('Upload was cancelled');
  }

  const chunkUploadUrl = await getUploadUrl(filenameForRequest, agentApiUrl, abortSignal);

  if (abortSignal?.aborted) {
    throw new Error('Upload was cancelled');
  }

  const uploadResponse = await chunkedUpload({
    file,
    fileName: filenameForRequest,
    uploadUrl: chunkUploadUrl,
    onProgress,
    abortSignal,
  });

  if (abortSignal?.aborted) {
    throw new Error('Upload was cancelled');
  }

  const sensorId = uploadResponse.sensorId as string;
  await notifyGenericUploadComplete(
    agentApiUrl,
    sensorId,
    filenameForRequest,
    uploadResponse,
    formData,
    abortSignal,
  );

  return {
    filename: (uploadResponse.filename as string) ?? filenameForRequest,
    bytes: (uploadResponse.bytes as number) ?? file.size,
    sensorId,
    streamId: (uploadResponse.streamId as string) ?? sensorId,
    filePath: (uploadResponse.filePath as string) ?? '',
    timestamp: '2025-01-01T00:00:00.000Z',
  };
}

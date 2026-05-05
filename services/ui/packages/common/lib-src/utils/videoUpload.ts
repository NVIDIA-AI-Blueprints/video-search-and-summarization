/**
 * Shared video upload utilities.
 *
 * Two entry points:
 *
 *  - uploadFile: legacy two-step (POST /videos → PUT presigned URL).
 *    Single monolithic PUT — hits Cloudflare's 100s request timeout on
 *    large files over slow connections.
 *
 *  - uploadFileChunkedViaAgent: posts the file in chunks to the agent's
 *    /api/v1/videos/chunked/upload endpoint (the agent proxies each chunk
 *    to VST's nvstreamer reassembler), then calls
 *    /api/v1/videos/{filename}/complete for post-processing. Keeps the UI
 *    talking to one backend (the agent) while avoiding the 100s cutoff.
 */

import { uploadFileChunked } from './chunkedUpload';
import type { ChunkedUploadResponse } from './chunkedUpload';

/**
 * Response from agent API when getting upload URL
 */
interface AgentUploadUrlResponse {
  url: string;
}

/**
 * Response from agent API after file upload
 */
export interface FileUploadResult {
  filename: string;
  bytes: number;
  sensorId: string;
  streamId: string;
  filePath: string;
  timestamp: string;
}

/**
 * Get upload URL from Agent API
 * This is step 1 for agent API uploads (search profile)
 */
export async function getUploadUrl(
  filename: string,
  uploadUrl: string,
  formData?: Record<string, any>,
  signal?: AbortSignal
): Promise<string> {
  const response = await fetch(`${uploadUrl}/videos`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ filename, ...formData }),
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
      // ignore JSON parse failure, use statusText
    }
    throw new Error(message);
  }

  const data: AgentUploadUrlResponse = await response.json();
  return data.url;
}

/**
 * Upload file (two-step process)
 * Step 1: Get upload URL
 * Step 2: PUT file to the URL
 * @param requestFilename - Optional filename to send to the API (defaults to file.name)
 */
export async function uploadFile(
  file: File,
  uploadUrl: string,
  formData: Record<string, any>,
  onProgress?: (progress: number) => void,
  abortSignal?: AbortSignal,
  requestFilename?: string
): Promise<FileUploadResult> {
  const filenameForRequest = requestFilename?.trim() || file.name;

  // Create AbortController for the getUploadUrl request
  const getUrlController = new AbortController();

  // If parent signal is aborted, abort the getUploadUrl request
  if (abortSignal?.aborted) {
    throw new Error('Upload was cancelled');
  }

  const abortListener = () => getUrlController.abort();
  abortSignal?.addEventListener('abort', abortListener);

  try {
    // Step 1: Get upload URL
    const presignedUrl = await getUploadUrl(
      filenameForRequest,
      uploadUrl,
      formData,
      getUrlController.signal
    );

    // Clean up abort listener after getting URL
    abortSignal?.removeEventListener('abort', abortListener);

    // Check if aborted between steps
    if (abortSignal?.aborted) {
      throw new Error('Upload was cancelled');
    }

    // Step 2: Upload file using XHR (for progress tracking)
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();

      // Listen to parent abort signal
      if (abortSignal) {
        abortSignal.addEventListener('abort', () => xhr.abort());
      }

      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable && onProgress) {
          const progress = Math.round((event.loaded / event.total) * 100);
          onProgress(progress);
        }
      });

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const result: FileUploadResult = JSON.parse(xhr.responseText);
            resolve(result);
          } catch {
            reject(new Error('Failed to parse upload response'));
          }
        } else {
          reject(new Error(`Upload failed with status: ${xhr.status}`));
        }
      });

      xhr.addEventListener('error', () => {
        reject(new Error('Network error during upload'));
      });

      xhr.addEventListener('abort', () => {
        reject(new Error('Upload was cancelled'));
      });

      xhr.open('PUT', presignedUrl);
      xhr.setRequestHeader('Content-Type', file.type || 'video/mp4');
      xhr.send(file);
    });
  } finally {
    abortSignal?.removeEventListener('abort', abortListener);
  }
}

/**
 * Notify the agent that a chunked upload completed, so it can run
 * post-upload processing (embeddings, RTVI registration, etc.). The
 * UI forwards the receiver's upload response as the request body
 * without interpretation — the agent picks out the fields it needs.
 *
 * Hits the universal route at /api/v1/videos/{filename}/upload-complete
 * so this works across profiles (search, alerts, lvs, base). For search
 * profiles, the agent's hook on this endpoint drives ingestion (RTVI-CV
 * register + embedding generation); on other profiles each post-processing
 * step gracefully no-ops when its backing service isn't configured.
 *
 * The agent also keeps the legacy /videos/{filename}/complete path as a
 * deprecated alias for backward compatibility with previously shipped UI
 * builds — this client always uses the canonical /upload-complete path.
 *
 * `formData` carries any per-upload custom parameters collected by the
 * UI (e.g. from the chat upload dialog's env-configurable template). It
 * is sent as a top-level `custom_params` field alongside the upload
 * response, so the agent can read it via a dedicated model field when
 * needed. Omitted entirely when empty so the body stays minimal.
 */
export async function notifyGenericUploadComplete(
  agentApiUrl: string,
  filename: string,
  uploadResponse: ChunkedUploadResponse,
  formData?: Record<string, any>,
  signal?: AbortSignal,
): Promise<void> {
  const dotIndex = filename.lastIndexOf('.');
  const filenameWithoutExt = dotIndex > 0 ? filename.substring(0, dotIndex) : filename;
  const url = `${agentApiUrl.replace(/\/$/, '')}/videos/${encodeURIComponent(filenameWithoutExt)}/upload-complete`;

  const body: Record<string, any> = { ...uploadResponse };
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
 * Chunked upload via the agent proxy — drop-in replacement for
 * `uploadFile` that bypasses Cloudflare's 100s request timeout.
 *
 * Posts chunks to `{agentApiUrl}/api/v1/videos/chunked/upload`, which
 * forwards each chunk to VST's nvstreamer endpoint. After the final
 * chunk lands, POSTs to `/videos/{filename}/upload-complete` for
 * post-processing.
 *
 * The signature mirrors `uploadFile` so callers can swap one for the
 * other with minimal diff. `formData` is forwarded to `/upload-complete`
 * as a top-level `custom_params` field so per-upload custom parameters
 * from the dialog template reach the agent (mirrors the search-profile
 * Video Management path).
 */
export async function uploadFileChunkedViaAgent(
  file: File,
  agentApiUrl: string,
  formData: Record<string, any>,
  onProgress?: (progress: number) => void,
  abortSignal?: AbortSignal,
  requestFilename?: string
): Promise<FileUploadResult> {
  const filenameForRequest = requestFilename?.trim() || file.name;
  const chunkUploadUrl = `${agentApiUrl.replace(/\/$/, '')}/videos/chunked/upload`;

  const uploadResponse = await uploadFileChunked({
    file,
    uploadUrl: chunkUploadUrl,
    onProgress,
    abortSignal,
  });

  if (abortSignal?.aborted) {
    throw new Error('Upload was cancelled');
  }

  await notifyGenericUploadComplete(agentApiUrl, filenameForRequest, uploadResponse, formData, abortSignal);

  // Reshape into the same FileUploadResult contract uploadFile returns.
  return {
    filename: (uploadResponse.filename as string) ?? filenameForRequest,
    bytes: (uploadResponse.bytes as number) ?? file.size,
    sensorId: uploadResponse.sensorId as string,
    streamId: (uploadResponse.streamId as string) ?? (uploadResponse.sensorId as string),
    filePath: (uploadResponse.filePath as string) ?? '',
    timestamp: '2025-01-01T00:00:00.000Z',
  };
}

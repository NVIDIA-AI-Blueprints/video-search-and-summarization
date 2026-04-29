// SPDX-License-Identifier: MIT
import type { FileUploadResponse } from './types';
import { CHUNK_SIZE_BYTES, MAX_CHUNK_RETRIES } from './constants';
import { generateUUID } from './utils';

export interface ChunkedUploadOptions {
  file: File;
  uploadUrl: string;
  chunkSize?: number;
  maxRetries?: number;
  onProgress?: (progress: number) => void;
  abortSignal?: AbortSignal;
}

async function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Upload a single chunk to VST with nvstreamer headers.
 * Returns the parsed JSON response from VST.
 */
async function uploadChunk(
  chunk: Blob,
  url: string,
  fileName: string,
  identifier: string,
  chunkNumber: number,
  totalChunks: number,
  onChunkProgress?: (loaded: number) => void,
  abortSignal?: AbortSignal,
): Promise<FileUploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    if (abortSignal) {
      if (abortSignal.aborted) {
        reject(new Error('Upload was cancelled'));
        return;
      }
      const onAbort = () => xhr.abort();
      abortSignal.addEventListener('abort', onAbort);
      const cleanup = () => abortSignal.removeEventListener('abort', onAbort);
      xhr.addEventListener('load', cleanup);
      xhr.addEventListener('error', cleanup);
      xhr.addEventListener('abort', cleanup);
    }

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onChunkProgress) {
        onChunkProgress(event.loaded);
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as FileUploadResponse);
        } catch {
          reject(new Error('Failed to parse upload response'));
        }
      } else {
        let message = `Upload failed with status ${xhr.status}`;
        try {
          const errorData = JSON.parse(xhr.responseText);
          if (errorData.error_message) message = errorData.error_message;
        } catch { /* use default message */ }
        reject(new Error(message));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
    xhr.addEventListener('abort', () => reject(new Error('Upload was cancelled')));

    const formData = new FormData();
    formData.append('mediaFile', chunk, fileName);
    formData.append('filename', fileName);
    formData.append('metadata', '{"timestamp":"2025-01-01T00:00:00"}');

    const isLastChunk = chunkNumber === totalChunks;

    xhr.open('POST', url);
    xhr.setRequestHeader('nvstreamer-chunk-number', String(chunkNumber));
    xhr.setRequestHeader('nvstreamer-total-chunks', String(totalChunks));
    xhr.setRequestHeader('nvstreamer-is-last-chunk', String(isLastChunk));
    xhr.setRequestHeader('nvstreamer-identifier', identifier);
    xhr.setRequestHeader('nvstreamer-file-name', fileName);
    xhr.send(formData);
  });
}

/**
 * Upload a file to VST in chunks using the nvstreamer chunked upload protocol.
 *
 * Each chunk is sent as a separate POST request with nvstreamer-* headers.
 * Files smaller than chunkSize are sent as a single chunk.
 * Failed chunks are retried with exponential backoff.
 *
 * Returns the response from the last chunk (contains sensorId, filename, etc.).
 */
export async function uploadFileChunked(options: ChunkedUploadOptions): Promise<FileUploadResponse> {
  const {
    file,
    uploadUrl,
    chunkSize = CHUNK_SIZE_BYTES,
    maxRetries = MAX_CHUNK_RETRIES,
    onProgress,
    abortSignal,
  } = options;

  const totalChunks = Math.max(1, Math.ceil(file.size / chunkSize));
  const identifier = generateUUID();
  let lastResponse: FileUploadResponse | null = null;

  for (let i = 0; i < totalChunks; i++) {
    // Check abort before each chunk
    if (abortSignal?.aborted) {
      throw new Error('Upload was cancelled');
    }

    const start = i * chunkSize;
    const end = Math.min(start + chunkSize, file.size);
    const chunk = file.slice(start, end);
    const chunkNumber = i + 1;

    let lastError: Error | null = null;

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      if (abortSignal?.aborted) {
        throw new Error('Upload was cancelled');
      }

      if (attempt > 0) {
        // Exponential backoff: 1s, 2s, 4s
        const delay = Math.pow(2, attempt - 1) * 1000;
        await sleep(delay);
      }

      try {
        lastResponse = await uploadChunk(
          chunk,
          uploadUrl,
          file.name,
          identifier,
          chunkNumber,
          totalChunks,
          (loaded) => {
            if (onProgress) {
              const completedBytes = i * chunkSize;
              const totalProgress = Math.round(((completedBytes + loaded) / file.size) * 100);
              onProgress(Math.min(totalProgress, 100));
            }
          },
          abortSignal,
        );
        lastError = null;
        break;
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        // Don't retry abort/cancel errors
        if (lastError.message === 'Upload was cancelled') {
          throw lastError;
        }
      }
    }

    if (lastError) {
      throw lastError;
    }
  }

  if (!lastResponse) {
    throw new Error('Upload produced no response');
  }
  if (typeof lastResponse.sensorId !== 'string' || !lastResponse.sensorId) {
    // VST returns sensorId only on the final-chunk response. Guard against a
    // protocol change silently propagating undefined into notifyUploadComplete.
    throw new Error('Upload response missing sensorId');
  }

  return lastResponse;
}

/**
 * Notify the agent that a chunked upload is complete, so it can trigger
 * post-upload processing (embeddings, RTVI registration, etc.).
 *
 * The upload API response is forwarded to the agent as the request body
 * without interpretation — the agent extracts whatever fields it needs
 * (e.g. sensorId). This keeps the UI generic with respect to the backend
 * upload target (VST today, potentially others).
 *
 * `formData` carries the per-upload custom parameters collected by the
 * UploadFilesDialog from the env-configurable template (previously
 * NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON, forwarded to the
 * agent's legacy `POST /videos` endpoint). It is sent as a top-level
 * `custom_params` field in the body alongside the full upload response,
 * so the agent can read it via a dedicated model field when needed.
 * With the current agent's `extra="ignore"` on VideoUploadCompleteInput
 * it's a forward-compatible envelope — silently dropped until consumed.
 */
export async function notifyUploadComplete(
  agentApiUrl: string,
  filename: string,
  videoUploadApiResponse: FileUploadResponse,
  formData?: Record<string, any>,
  signal?: AbortSignal,
): Promise<void> {
  const dotIndex = filename.lastIndexOf('.');
  const filenameWithoutExt = dotIndex > 0 ? filename.substring(0, dotIndex) : filename;
  // agentApiUrl already includes /api/v1, so just append the resource path
  const url = `${agentApiUrl.replace(/\/$/, '')}/videos-for-search/${encodeURIComponent(filenameWithoutExt)}/complete`;

  // Body = full upload response + custom_params (if any). custom_params is
  // omitted entirely when formData is undefined/empty so the body stays
  // minimal on profiles that don't use the dialog's config template.
  const body: Record<string, any> = { ...videoUploadApiResponse };
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

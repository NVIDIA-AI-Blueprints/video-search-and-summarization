/**
 * Shared chunked-upload helper.
 *
 * Slices a File into chunks and POSTs each one with `nvstreamer-*` headers
 * so the receiver (either VST directly or a proxy like the agent) can
 * reassemble. Each chunk is a self-contained HTTP request that completes
 * well under the Cloudflare 100-second limit that would otherwise kill a
 * monolithic upload of a large video.
 *
 * Consumers:
 *  - video-management/chunkedUpload.ts: uploads straight to VST
 *  - videoUpload.ts#uploadFileChunkedViaAgent: uploads via an agent proxy
 */

export const CHUNK_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB
export const MAX_CHUNK_RETRIES = 3;

export interface ChunkedUploadResponse {
  sensorId?: string;
  filename?: string;
  bytes?: number;
  filePath?: string;
  chunkCount?: string;
  chunkIdentifier?: string;
  [extra: string]: unknown;
}

export interface ChunkedUploadOptions {
  file: File;
  /** Fully-qualified URL to POST each chunk to (receiver handles nvstreamer reassembly). */
  uploadUrl: string;
  chunkSize?: number;
  maxRetries?: number;
  onProgress?: (progress: number) => void;
  abortSignal?: AbortSignal;
}

function randomIdentifier(): string {
  // crypto.randomUUID is available in all modern browsers over HTTPS; see also
  // the jest.setup.js polyfill for tests.
  return crypto.randomUUID();
}

async function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Upload a single chunk of a file with nvstreamer-* headers.
 * Resolves with the parsed JSON response from the receiver.
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
): Promise<ChunkedUploadResponse> {
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
          resolve(JSON.parse(xhr.responseText) as ChunkedUploadResponse);
        } catch {
          reject(new Error('Failed to parse upload response'));
        }
      } else {
        let message = `Upload failed with status ${xhr.status}`;
        try {
          const errorData = JSON.parse(xhr.responseText);
          if (errorData.error_message) message = errorData.error_message;
          else if (errorData.detail) {
            message = typeof errorData.detail === 'string' ? errorData.detail : JSON.stringify(errorData.detail);
          }
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
 * Upload a file in chunks using the nvstreamer chunked-upload protocol.
 *
 * Each chunk is sent as a separate POST request. Files smaller than
 * `chunkSize` are sent as a single chunk. Failed chunks are retried with
 * exponential backoff (1s, 2s, 4s). Returns the response from the final
 * chunk, which by protocol contains `sensorId` (the upload's stream id).
 *
 * The runtime guard on `sensorId` catches servers that silently return a
 * malformed final-chunk response — without it, `undefined` would propagate
 * into downstream calls like `notifyUploadComplete`.
 */
export async function uploadFileChunked(options: ChunkedUploadOptions): Promise<ChunkedUploadResponse> {
  const {
    file,
    uploadUrl,
    chunkSize = CHUNK_SIZE_BYTES,
    maxRetries = MAX_CHUNK_RETRIES,
    onProgress,
    abortSignal,
  } = options;

  const totalChunks = Math.max(1, Math.ceil(file.size / chunkSize));
  const identifier = randomIdentifier();
  let lastResponse: ChunkedUploadResponse | null = null;

  for (let i = 0; i < totalChunks; i++) {
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
    // Receiver returns sensorId only on the final-chunk response. Guard against
    // a protocol change silently propagating undefined into downstream calls.
    throw new Error('Upload response missing sensorId');
  }

  return lastResponse;
}

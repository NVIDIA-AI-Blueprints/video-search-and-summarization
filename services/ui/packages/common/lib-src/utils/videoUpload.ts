/**
 * Chat video upload helpers.
 *
 * Uploads each file chunk directly to VST using the nvstreamer protocol.
 */

import { chunkedUpload } from './chunkedUpload';
import type { ChunkedUploadResponse } from './chunkedUpload';

export interface FileUploadResult {
  filename: string;
  bytes: number;
  sensorId: string;
  streamId: string;
  filePath: string;
  timestamp: string;
}

/**
 * Chunked upload directly to VST. Each chunk is its own short HTTP request so the
 * Cloudflare 100s timeout doesn't apply to large files.
 */
export async function uploadFileChunked(
  file: File,
  uploadUrl: string,
  onProgress?: (progress: number) => void,
  abortSignal?: AbortSignal,
  requestFilename?: string,
): Promise<FileUploadResult> {
  const filenameForRequest = requestFilename?.trim() || file.name;

  if (abortSignal?.aborted) {
    throw new Error('Upload was cancelled');
  }

  const uploadResponse = await chunkedUpload({
    file,
    fileName: filenameForRequest,
    uploadUrl,
    onProgress,
    abortSignal,
  });

  const sensorId = uploadResponse.sensorId as string;
  if (!sensorId) {
    throw new Error('VST upload response missing sensorId');
  }

  return {
    filename: (uploadResponse.filename as string) ?? filenameForRequest,
    bytes: (uploadResponse.bytes as number) ?? file.size,
    sensorId,
    streamId: (uploadResponse.streamId as string) ?? sensorId,
    filePath: (uploadResponse.filePath as string) ?? '',
    timestamp: '2025-01-01T00:00:00.000Z',
  };
}

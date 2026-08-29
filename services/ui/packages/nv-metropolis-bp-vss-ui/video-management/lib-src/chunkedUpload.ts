// SPDX-License-Identifier: MIT
//
// Chunked upload helper for the Video Management tab. The core chunking
// logic lives in the shared package so the Chat upload path can reuse it.

import type { FileUploadResponse } from './types';
import { chunkedUpload as sharedChunkedUpload } from '@nemo-agent-toolkit/ui';
import type { ChunkedUploadOptions, ChunkedUploadResponse } from '@nemo-agent-toolkit/ui';

export type { ChunkedUploadOptions };

/**
 * Upload a file to VST in chunks using the nvstreamer chunked upload protocol.
 *
 * Thin wrapper around the shared primitive that re-types the response as the
 * package-local FileUploadResponse for existing call sites.
 */
export async function chunkedUpload(options: ChunkedUploadOptions): Promise<FileUploadResponse> {
  const response: ChunkedUploadResponse = await sharedChunkedUpload(options);
  return response as unknown as FileUploadResponse;
}

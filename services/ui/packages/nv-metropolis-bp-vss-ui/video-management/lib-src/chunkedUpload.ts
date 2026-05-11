// SPDX-License-Identifier: MIT
//
// Chunked upload helpers for the Video Management tab. The core chunking
// logic lives in the shared `@nemo-agent-toolkit/ui` package so the Chat
// upload path can reuse it; this file wraps it with notifyUploadComplete(),
// which posts to the universal /api/v1/videos/{video_id}/complete endpoint
// (video_id = VST stream id) so VM upload works on every profile
// (search/lvs/base/alerts).

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

/**
 * Notify the agent that a chunked upload to VST is complete, so it can trigger
 * post-upload processing (embeddings, RTVI registration, etc.).
 *
 * The path param is the VST stream id (``sensorId`` from the final-chunk
 * response). The upload API response is forwarded as the request body so the
 * agent can read what it needs (e.g. ``filename`` for the response message
 * and RTVI ``camera_name``); the rest is ignored. ``formData`` is sent as
 * top-level ``custom_params`` for per-upload params from the dialog template.
 */
export async function notifyUploadComplete(
  agentApiUrl: string,
  filename: string,
  videoUploadApiResponse: FileUploadResponse,
  formData?: Record<string, any>,
  signal?: AbortSignal,
): Promise<void> {
  const sensorId = (videoUploadApiResponse as unknown as { sensorId?: string }).sensorId;
  if (!sensorId) {
    throw new Error('notifyUploadComplete: VST upload response missing sensorId');
  }
  // Universal /complete endpoint — works on every profile.
  // agentApiUrl already includes /api/v1, so just append the resource path.
  const url = `${agentApiUrl.replace(/\/$/, '')}/videos/${encodeURIComponent(sensorId)}/complete`;

  // Body = full upload response + filename + custom_params (if any).
  // custom_params is omitted entirely when formData is undefined/empty so the
  // body stays minimal on profiles that don't use the dialog's config template.
  const body: Record<string, any> = { ...videoUploadApiResponse, filename };
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

// SPDX-License-Identifier: MIT
//
// Chunked upload helpers for the Video Management tab. The core chunking
// logic lives in the shared `@nemo-agent-toolkit/ui` package so the Chat
// upload path can reuse it; this file wraps it with the search-specific
// notifyUploadComplete() that hits /videos-for-search/.../complete.

import type { FileUploadResponse } from './types';
import { uploadFileChunked as sharedUploadFileChunked } from '@nemo-agent-toolkit/ui';
import type { ChunkedUploadOptions, ChunkedUploadResponse } from '@nemo-agent-toolkit/ui';

export type { ChunkedUploadOptions };

/**
 * Upload a file to VST in chunks using the nvstreamer chunked upload protocol.
 *
 * Thin wrapper around the shared helper that re-types the response as the
 * package-local FileUploadResponse for existing call sites.
 */
export async function uploadFileChunked(options: ChunkedUploadOptions): Promise<FileUploadResponse> {
  const response: ChunkedUploadResponse = await sharedUploadFileChunked(options);
  return response as unknown as FileUploadResponse;
}

/**
 * Notify the agent that a chunked upload to VST is complete, so it can trigger
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

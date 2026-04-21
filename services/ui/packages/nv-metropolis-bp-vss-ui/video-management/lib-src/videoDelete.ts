// SPDX-License-Identifier: MIT
/**
 * Delete uploaded video via Agent API.
 *
 * Backend: DELETE /api/v1/videos/{video_id}
 * Handles VST (sensor + storage) and in "search" mode also ES + RTVI-CV.
 */

export interface DeleteVideoResult {
  status: string;
  message: string;
  video_id: string;
}

/**
 * Delete an uploaded video by sensor/video ID (UUID) via Agent API.
 * DELETE /api/v1/videos/{video_id}
 *
 * @param agentApiUrl - Base URL of the agent API (e.g., http://<IP>:8000/api/v1)
 * @param videoId - The sensor/video UUID (e.g., from the upload response)
 * @param signal - Optional AbortSignal for cancellation
 */
export async function deleteVideo(
  agentApiUrl: string,
  videoId: string,
  signal?: AbortSignal
): Promise<DeleteVideoResult> {
  if (signal?.aborted) {
    throw new Error('Delete video was cancelled');
  }

  const response = await fetch(`${agentApiUrl}/videos/${encodeURIComponent(videoId)}`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
    },
    signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(text || `Failed to delete video: ${response.statusText}`);
  }

  const result: DeleteVideoResult = await response.json();

  if (result.status === 'failure') {
    throw new Error(result.message || `Failed to delete video: ${result.video_id}`);
  }

  return result;
}

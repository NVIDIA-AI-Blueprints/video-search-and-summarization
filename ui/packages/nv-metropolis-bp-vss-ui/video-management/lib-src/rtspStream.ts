// SPDX-License-Identifier: MIT
/**
 * Shared RTSP stream utilities
 * Agent API RTSP add/delete - single API calls that handle VST and RTVI services internally
 *
 * API Endpoints:
 * - Add:    POST   /api/v1/rtsp-streams/add     { sensorUrl, name }
 * - Delete: DELETE /api/v1/rtsp-streams/delete/{sensorName}
 */

/**
 * Request body for adding RTSP stream
 */
export interface AddRtspStreamRequest {
  sensorUrl: string;
  name?: string;
}

/**
 * Response from adding RTSP stream
 */
export interface AddRtspStreamResult {
  status?: string;
  message?: string;
  sensorId?: string;
  vst_sensor_id?: string;
  streamId?: string;
  name?: string;
  url?: string;
  error?: string;
}

/**
 * Response from deleting RTSP stream
 */
export interface DeleteRtspStreamResult {
  status?: string;
  message?: string;
  error?: string;
}

/**
 * Add RTSP stream via Agent API
 * POST /api/v1/rtsp-streams/add
 *
 * Backend handles VST and conditionally calls RTVI-embed/RTVI-CV for search profile
 */
export async function addRtspStream(
  agentApiUrl: string,
  request: AddRtspStreamRequest,
  signal?: AbortSignal
): Promise<AddRtspStreamResult> {
  if (signal?.aborted) {
    throw new Error('Add RTSP stream was cancelled');
  }

  const response = await fetch(`${agentApiUrl}/rtsp-streams/add`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      sensorUrl: request.sensorUrl,
      ...(request.name ? { name: request.name } : {}),
    }),
    signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(text || `Failed to add RTSP stream: ${response.statusText}`);
  }

  const result: AddRtspStreamResult = await response.json();

  if (result.status === 'failure') {
    throw new Error(result.message || result.error || 'Failed to add RTSP stream');
  }

  return result;
}

/**
 * Delete RTSP stream via Agent API
 * DELETE /api/v1/rtsp-streams/delete/{sensorName}
 *
 * @param agentApiUrl - Base URL of the agent API (e.g., http://<IP>:8000/api/v1)
 * @param sensorName - The sensor name used when the stream was created
 * @param signal - Optional AbortSignal for cancellation
 */
export async function deleteRtspStream(
  agentApiUrl: string,
  sensorName: string,
  signal?: AbortSignal
): Promise<DeleteRtspStreamResult> {
  if (signal?.aborted) {
    throw new Error('Delete RTSP stream was cancelled');
  }

  const response = await fetch(`${agentApiUrl}/rtsp-streams/delete/${encodeURIComponent(sensorName)}`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
    },
    signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(text || `Failed to delete RTSP stream: ${response.statusText}`);
  }

  const result: DeleteRtspStreamResult = await response.json();

  if (result.status === 'failure') {
    throw new Error(result.message || result.error || 'Failed to delete RTSP stream');
  }

  return result;
}

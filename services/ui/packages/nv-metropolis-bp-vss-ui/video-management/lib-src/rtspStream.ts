// SPDX-License-Identifier: MIT
/**
 * Agent-owned RTSP lifecycle utilities.
 *
 * API Endpoints:
 * - Add:    POST   /api/v1/rtsp-streams/add          { sensorUrl, name }
 * - Delete: DELETE /api/v1/rtsp-streams/delete/{name}
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
  status: string;
  message: string;
  error?: string | null;
  sensorId?: string;
}

/**
 * Response from deleting RTSP stream
 */
export interface DeleteRtspStreamResult {
  status: string;
  message: string;
  name: string;
}

async function getAgentError(response: Response, fallback: string): Promise<string> {
  const text = await response.text().catch(() => '');
  if (!text) return fallback;
  try {
    const data = JSON.parse(text);
    return data?.error_message || data?.message || data?.detail || text;
  } catch {
    return text;
  }
}

/**
 * Add through the agent so profile-specific RTVI registration happens too.
 */
export async function addRtspStream(
  agentApiUrl: string,
  request: AddRtspStreamRequest,
  signal?: AbortSignal
): Promise<AddRtspStreamResult> {
  if (signal?.aborted) {
    throw new Error('Add RTSP stream was cancelled');
  }

  const response = await fetch(`${agentApiUrl.replace(/\/$/, '')}/rtsp-streams/add`, {
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
    throw new Error(await getAgentError(
      response,
      `Failed to add RTSP stream: ${response.statusText || response.status}`,
    ));
  }

  const result: AddRtspStreamResult = await response.json();
  if (result.status === 'failure') {
    throw new Error(result.message || result.error || 'Failed to add RTSP stream');
  }
  return result;
}

/**
 * Delete through the agent so RTVI and VST are cleaned together.
 *
 * @param agentApiUrl - Agent API base URL (for example, http://host:8000/api/v1)
 * @param sensorName - Sensor name used when the stream was created
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

  const response = await fetch(`${agentApiUrl.replace(/\/$/, '')}/rtsp-streams/delete/${encodeURIComponent(sensorName)}`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
    },
    signal,
  });

  if (!response.ok) {
    throw new Error(await getAgentError(
      response,
      `Failed to delete RTSP stream: ${response.statusText || response.status}`,
    ));
  }

  const result: DeleteRtspStreamResult = await response.json();
  if (result.status === 'failure') {
    throw new Error(result.message || 'Failed to delete RTSP stream');
  }
  return result;
}

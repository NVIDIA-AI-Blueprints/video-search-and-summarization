// SPDX-License-Identifier: MIT
/**
 * Direct VST RTSP sensor utilities.
 *
 * API Endpoints:
 * - Add:    POST   /v1/sensor/add          { sensorUrl, name }
 * - Delete: DELETE /v1/sensor/{sensorId}
 */
import { createApiEndpoints } from './api';

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
  sensorId: string;
}

/**
 * Response from deleting RTSP stream
 */
export type DeleteRtspStreamResult = boolean;

async function getVstError(response: Response, fallback: string): Promise<string> {
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
 * Add an RTSP sensor directly to VST.
 */
export async function addRtspStream(
  vstApiUrl: string,
  request: AddRtspStreamRequest,
  signal?: AbortSignal
): Promise<AddRtspStreamResult> {
  if (signal?.aborted) {
    throw new Error('Add RTSP stream was cancelled');
  }

  const response = await fetch(createApiEndpoints(vstApiUrl).ADD_SENSOR, {
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
    throw new Error(await getVstError(
      response,
      `Failed to add RTSP stream: ${response.statusText || response.status}`,
    ));
  }

  const result: AddRtspStreamResult = await response.json();
  if (!result.sensorId) {
    throw new Error('VST add-sensor response missing sensorId');
  }
  return result;
}

/**
 * Delete an RTSP sensor directly from VST.
 *
 * @param vstApiUrl - VST API base URL (for example, http://host:7777/vst/api)
 * @param sensorId - VST sensor UUID
 * @param signal - Optional AbortSignal for cancellation
 */
export async function deleteRtspStream(
  vstApiUrl: string,
  sensorId: string,
  signal?: AbortSignal
): Promise<DeleteRtspStreamResult> {
  if (signal?.aborted) {
    throw new Error('Delete RTSP stream was cancelled');
  }

  const response = await fetch(createApiEndpoints(vstApiUrl).DELETE_SENSOR(sensorId), {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
    },
    signal,
  });

  if (!response.ok && response.status !== 404) {
    throw new Error(await getVstError(
      response,
      `Failed to delete RTSP stream: ${response.statusText || response.status}`,
    ));
  }

  return response.status === 404 ? true : response.json();
}

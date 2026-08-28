// SPDX-License-Identifier: MIT
/**
 * Delete an uploaded video's storage and sensor directly from VST.
 */
import { createApiEndpoints } from './api';

export interface DeleteVideoResult {
  sensorId: string;
  spaceSaved?: number;
}

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

export async function deleteVideo(
  vstApiUrl: string,
  sensorId: string,
  startTime: string,
  endTime: string,
  signal?: AbortSignal
): Promise<DeleteVideoResult> {
  if (signal?.aborted) {
    throw new Error('Delete video was cancelled');
  }

  const endpoints = createApiEndpoints(vstApiUrl);
  const storageResponse = await fetch(
    endpoints.DELETE_STORAGE_FILES(sensorId, startTime, endTime),
    {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      signal,
    },
  );

  if (!storageResponse.ok && storageResponse.status !== 404) {
    throw new Error(await getVstError(
      storageResponse,
      `Failed to delete video storage: ${storageResponse.statusText || storageResponse.status}`,
    ));
  }

  let spaceSaved: number | undefined;
  if (storageResponse.ok) {
    const storageResult = await storageResponse.json().catch(() => null);
    if (typeof storageResult?.spaceSaved === 'number') {
      spaceSaved = storageResult.spaceSaved;
    }
  }

  const sensorResponse = await fetch(endpoints.DELETE_SENSOR(sensorId), {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    signal,
  });

  // Storage deletion may already remove the file-backed sensor.
  if (!sensorResponse.ok && sensorResponse.status !== 404) {
    throw new Error(await getVstError(
      sensorResponse,
      `Failed to delete video sensor: ${sensorResponse.statusText || sensorResponse.status}`,
    ));
  }

  return { sensorId, spaceSaved };
}

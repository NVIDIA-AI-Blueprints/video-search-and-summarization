// SPDX-License-Identifier: MIT
import type { StreamInfo, StreamsApiResponse, FileUploadResponse, FileUploadError } from './types';
import { NUM_PARALLEL_GET_PICTURES } from './constants';
import { createApiEndpoints } from './api';

export function getFileExtension(path: string): string {
  const parts = path.split('.');
  return parts.length > 1 ? parts.at(-1)!.toUpperCase() : '';
}

export function isRtspStream(stream: StreamInfo): boolean {
  return (
    (stream.url ?? '').toLowerCase().startsWith('rtsp://') ||
    (stream.vodUrl ?? '').toLowerCase().startsWith('rtsp://')
  );
}

export function getStreamType(stream: StreamInfo): 'rtsp' | 'video' {
  return isRtspStream(stream) ? 'rtsp' : 'video';
}

export function filterStreams(
  streams: StreamInfo[],
  showVideos: boolean,
  showRtsps: boolean,
  searchQuery: string
): StreamInfo[] {
  return streams.filter((stream) => {
    const streamIsRtsp = isRtspStream(stream);

    if (!showVideos && !streamIsRtsp) return false;
    if (!showRtsps && streamIsRtsp) return false;

    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase().trim();
      const name = (stream.name ?? '').toLowerCase();
      return name.includes(query);
    }

    return true;
  });
}

export function parseStreamsResponse(data: StreamsApiResponse): StreamInfo[] {
  const allStreams: StreamInfo[] = [];

  for (const item of data) {
    const sensorId = Object.keys(item)[0];
    const streamInfoArray = item[sensorId];
    if (Array.isArray(streamInfoArray)) {
      allStreams.push(...streamInfoArray.map(stream => ({ ...stream, sensorId })));
    }
  }

  return allStreams;
}

interface ParsedErrorObj {
  error_code?: string;
  error_message?: string;
  message?: string;
  detail?: Array<{ type?: string; loc?: unknown[]; msg?: string }>;
}

/** FastAPI-style validation errors: { "detail": [ { "loc": ["body", "name"], "msg": "Field required" } ] } */
function parseValidationDetail(detail: ParsedErrorObj['detail']): string | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;

  const first = detail[0];
  const msg = first?.msg ?? '';
  const loc = first?.loc;

  if (Array.isArray(loc)) {
    const field = loc.at(-1);
    const isNameRequired = field === 'name' && (msg.toLowerCase().includes('required') || first?.type === 'missing');
    if (isNameRequired) return 'Sensor Name is required.';
    if (msg) return `${String(field)}: ${msg}`;
  }

  return msg || null;
}

function parseErrorCodeMessage(errorObj: ParsedErrorObj): string | null {
  const rawMessage = errorObj.error_message || errorObj.message;
  const isDuplicate =
    errorObj.error_code === 'InvalidParameterError' &&
    (rawMessage ?? '').toLowerCase().includes('exists');

  if (isDuplicate) return 'A sensor with this RTSP URL already exists.';
  return rawMessage || null;
}

export function parseApiError(text: string, defaultMessage: string): string {
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== 'object' || parsed === null) return text;

    const errorObj = parsed as ParsedErrorObj;

    const detailMessage = parseValidationDetail(errorObj.detail);
    if (detailMessage) return detailMessage;

    return parseErrorCodeMessage(errorObj) ?? text;
  } catch {
    return text || defaultMessage;
  }
}

function generateUUID(): string {
  return crypto.randomUUID();
}

export function generateUploadId(): string {
  return `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}

export async function uploadFile(
  file: File,
  vstApiUrl: string,
  onProgress?: (progress: number) => void,
  abortSignal?: AbortSignal
): Promise<FileUploadResponse> {
  const apiEndpoints = createApiEndpoints(vstApiUrl);
  const identifier = generateUUID();
  const fileName = file.name;

  const formData = new FormData();
  formData.append('mediaFile', file);
  formData.append('filename', fileName);
  formData.append('metadata', '{"timestamp":"2025-01-01T00:00:00"}');

  const headers: Record<string, string> = {
    'nvstreamer-chunk-number': '1',
    'nvstreamer-file-name': fileName,
    'nvstreamer-identifier': identifier,
    'nvstreamer-is-last-chunk': 'true',
    'nvstreamer-total-chunks': '1',
  };

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    if (abortSignal) {
      if (abortSignal.aborted) {
        reject(new Error('Upload was aborted'));
        return;
      }
      abortSignal.addEventListener('abort', () => xhr.abort());
    }

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as FileUploadResponse);
        } catch {
          reject(new Error('Failed to parse upload response'));
        }
      } else {
        try {
          const errorResponse = JSON.parse(xhr.responseText) as FileUploadError;
          reject(new Error(errorResponse.error_message || `Upload failed with status ${xhr.status}`));
        } catch {
          reject(new Error(`Upload failed with status ${xhr.status}`));
        }
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Network error during upload')));
    xhr.addEventListener('abort', () => reject(new Error('Upload was aborted')));

    xhr.open('POST', apiEndpoints.UPLOAD_FILE);
    Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
    xhr.send(formData);
  });
}

// Rate-limited picture fetch queue with request deduplication
class PictureFetchQueue {
  private readonly queue: Array<() => Promise<void>> = [];
  private activeCount = 0;
  private readonly maxConcurrent: number;
  private readonly inFlight = new Map<string, Promise<Blob>>();

  constructor(maxConcurrent: number = NUM_PARALLEL_GET_PICTURES) {
    this.maxConcurrent = maxConcurrent;
  }

  private async enqueue<T>(task: () => Promise<T>): Promise<T> {
    return new Promise((resolve, reject) => {
      const wrappedTask = async () => {
        try {
          resolve(await task());
        } catch (error) {
          reject(error);
        } finally {
          this.activeCount--;
          this.processNext();
        }
      };
      this.queue.push(wrappedTask);
      this.processNext();
    });
  }

  private processNext(): void {
    if (this.activeCount >= this.maxConcurrent || this.queue.length === 0) return;

    const task = this.queue.shift();
    if (task) {
      this.activeCount++;
      task();
    }
  }

  async fetch(url: string): Promise<Blob> {
    const inFlightPromise = this.inFlight.get(url);
    if (inFlightPromise) return inFlightPromise;

    const fetchPromise = this.enqueue(async () => {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Failed to fetch picture: ${response.status}`);
      return response.blob();
    }).then((blob) => {
      this.inFlight.delete(url);
      return blob;
    }).catch((error) => {
      this.inFlight.delete(url);
      throw error;
    });

    this.inFlight.set(url, fetchPromise);
    return fetchPromise;
  }
}

const pictureFetchQueue = new PictureFetchQueue();

export async function fetchPictureWithQueue(url: string): Promise<Blob> {
  return pictureFetchQueue.fetch(url);
}


// SPDX-License-Identifier: MIT
import { useState, useCallback, useRef } from 'react';
import { SearchByImageFrameData, BboxObject } from '../types';

interface UseSearchByImageOptions {
  vstApiUrl?: string;
  mdxWebApiUrl?: string;
}

interface SearchByImageState {
  active: boolean;
  loading: boolean;
  error: string | null;
  frameData: SearchByImageFrameData | null;
}

/**
 * Fetch the still-frame image from VST /picture API as an HTMLImageElement.
 */
async function fetchFrameImage(
  vstApiUrl: string,
  sensorId: string,
  timestamp: string,
  signal: AbortSignal
): Promise<HTMLImageElement> {
  const params = new URLSearchParams({ startTime: timestamp });
  const url = `${vstApiUrl}/v1/replay/stream/${sensorId}/picture?${params}`;
  const response = await fetch(url, { signal, headers: { streamId: sensorId } });

  if (!response.ok) {
    throw new Error(`Failed to fetch frame picture: ${response.status}`);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);

  return new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image();
    const cleanup = () => URL.revokeObjectURL(objectUrl);

    signal.addEventListener('abort', () => {
      cleanup();
      img.src = '';
      reject(new DOMException('Aborted', 'AbortError'));
    }, { once: true });

    img.onload = () => { cleanup(); resolve(img); };
    img.onerror = () => { cleanup(); reject(new Error('Failed to decode frame image')); };
    img.src = objectUrl;
  });
}

/**
 * Lookback window in ms assuming at least 10 fps. The /frames API is queried
 * with fromTimestamp = (t - 100ms) and toTimestamp = t (inclusive) so that at
 * least one indexed frame falls within the range.
 */
const FRAME_LOOKBACK_MS = 200; // Even though 100ms would suffice, to be conservative setting 2x=200ms as lookback

interface FrameApiBbox {
  leftX?: number;
  topY?: number;
  rightX?: number;
  bottomY?: number;
  left?: number;
  top?: number;
  right?: number;
  bottom?: number;
}

interface FrameApiObject {
  id?: string;
  objectId?: string;
  bbox?: FrameApiBbox;
  type?: string;
  class?: string;
  className?: string;
  objectType?: string;
}

interface FrameDataItem {
  timestamp?: string;
  frame_timestamp?: string;
  metadata?: { objects?: FrameApiObject[] };
  objects?: FrameApiObject[];
}

interface FrameMetadataResult {
  objects: BboxObject[];
  /** The actual indexed timestamp returned by the API (may differ from the requested one). */
  indexedTimestamp: string | null;
}

/**
 * Fetch bounding-box metadata for a frame from the /frames API.
 *
 * Uses a fromTimestamp/toTimestamp range (t-100ms .. t) so the API returns
 * the nearest indexed frame(s). Among those, the one closest to the requested
 * timestamp is selected for drawing bounding boxes.
 *
 * The /frames endpoint may not be deployed in all profiles. When unavailable
 * this returns an empty result so the overlay still shows the frame (without boxes).
 */
async function fetchFrameMetadata(
  mdxWebApiUrl: string,
  sensorName: string,
  timestamp: string,
  signal: AbortSignal
): Promise<FrameMetadataResult> {
  const empty: FrameMetadataResult = { objects: [], indexedTimestamp: null };
  try {
    const tsMs = new Date(timestamp).getTime();
    const fromTimestamp = new Date(tsMs - FRAME_LOOKBACK_MS).toISOString();
    const toTimestamp = timestamp;

    const params = new URLSearchParams({ sensorId: sensorName, fromTimestamp, toTimestamp });
    const url = `${mdxWebApiUrl}/frames?${params}`;
    const response = await fetch(url, { signal });

    if (!response.ok) {
      console.warn(`/frames API returned ${response.status}, bbox overlay will be empty`);
      return empty;
    }

    const data = await response.json();

    const frames: FrameDataItem[] = Array.isArray(data) ? data : data?.frames ? data.frames : [data];
    let bestFrame: FrameDataItem | undefined = frames[0];
    if (frames.length > 1) {
      let bestDelta = Infinity;
      for (const frame of frames) {
        const frameTimestamp = frame.timestamp ?? frame.frame_timestamp;
        if (!frameTimestamp) continue;
        const delta = Math.abs(new Date(frameTimestamp).getTime() - tsMs);
        if (delta < bestDelta) {
          bestDelta = delta;
          bestFrame = frame;
        }
      }
    }

    const indexedTimestamp = bestFrame?.timestamp ?? bestFrame?.frame_timestamp ?? null;

    const rawObjects = bestFrame?.metadata?.objects ?? bestFrame?.objects ?? [];
    const objects: BboxObject[] = rawObjects
      .filter((obj) => (obj.id != null || obj.objectId != null) && !!obj.bbox)
      .map((obj) => ({
        id: String(obj.id ?? obj.objectId),
        type: obj.type ?? obj.class ?? obj.className ?? obj.objectType,
        bbox: {
          leftX: Number(obj.bbox?.leftX ?? obj.bbox?.left ?? 0),
          topY: Number(obj.bbox?.topY ?? obj.bbox?.top ?? 0),
          rightX: Number(obj.bbox?.rightX ?? obj.bbox?.right ?? 0),
          bottomY: Number(obj.bbox?.bottomY ?? obj.bbox?.bottom ?? 0),
        },
      }));

    return { objects, indexedTimestamp };
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    console.warn('Failed to fetch frame metadata, bbox overlay will be empty:', err);
    return empty;
  }
}

/**
 * Extract the actual clip start time from a VST video URL.
 *
 * VST video URLs come in two flavours:
 *  1. Signed URL with query params: ...?startTime=2025-01-01T00:00:31.000Z&...
 *  2. Static file path with timestamp in filename:
 *     .../sample-warehouse-4min_20250101_000031_3945b.mp4
 *     The YYYYMMDD_HHMMSS portion encodes the actual keyframe-aligned start.
 *
 * The actual start can differ from the search-result start_time because the
 * video is cut at the nearest keyframe *before* the requested timestamp.
 */
function extractStartTimeFromVideoUrl(videoUrl: string): string | null {
  try {
    const url = new URL(videoUrl);
    const fromParams = url.searchParams.get('startTime');
    if (fromParams) return fromParams;
  } catch {
    // not a valid URL, fall through to filename parsing
  }

  const match = videoUrl.match(/(\d{8})_(\d{6})_[0-9a-f]+\.mp4/i);
  if (match) {
    const [, date, time] = match;
    const iso = `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}` +
      `T${time.slice(0, 2)}:${time.slice(2, 4)}:${time.slice(4, 6)}.000Z`;
    if (!isNaN(new Date(iso).getTime())) return iso;
  }

  return null;
}

export const useSearchByImage = ({ vstApiUrl, mdxWebApiUrl }: UseSearchByImageOptions) => {
  const [state, setState] = useState<SearchByImageState>({
    active: false,
    loading: false,
    error: null,
    frameData: null,
  });

  const abortRef = useRef<AbortController | null>(null);

  const startSearchByImage = useCallback(
    async (sensorId: string, sensorName: string, videoStartTime: string, pauseOffsetSeconds: number, videoUrl: string) => {
      if (!vstApiUrl || !mdxWebApiUrl) {
        setState((s) => ({ ...s, error: 'VST API URL or MDX Web API URL not configured', active: false }));
        return;
      }

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setState({ active: true, loading: true, error: null, frameData: null });

      try {
        const actualStart = extractStartTimeFromVideoUrl(videoUrl);
        const baseTime = actualStart || videoStartTime;
        const startMs = new Date(baseTime).getTime();
        if (isNaN(startMs)) {
          throw new Error(`Invalid video start time: ${baseTime}`);
        }
        const offsetMs = Math.round(pauseOffsetSeconds * 1000);
        const timestamp = new Date(startMs + offsetMs).toISOString();

        const [frameImage, frameResult] = await Promise.all([
          fetchFrameImage(vstApiUrl, sensorId, timestamp, controller.signal),
          fetchFrameMetadata(mdxWebApiUrl, sensorName, timestamp, controller.signal),
        ]);

        if (controller.signal.aborted) return;

        setState({
          active: true,
          loading: false,
          error: null,
          frameData: {
            frameImage,
            objects: frameResult.objects,
            sensorId,
            sensorName,
            timestamp: frameResult.indexedTimestamp || timestamp,
          },
        });
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        console.error('Search by Image fetch error:', err);
        setState((s) => ({
          ...s,
          loading: false,
          error: err instanceof Error ? err.message : 'Failed to load Search by Image data',
        }));
      }
    },
    [vstApiUrl, mdxWebApiUrl]
  );

  const cancelSearchByImage = useCallback(() => {
    abortRef.current?.abort();
    setState({ active: false, loading: false, error: null, frameData: null });
  }, []);

  return {
    searchByImageActive: state.active,
    searchByImageLoading: state.loading,
    searchByImageError: state.error,
    searchByImageFrameData: state.frameData,
    startSearchByImage,
    cancelSearchByImage,
  };
};

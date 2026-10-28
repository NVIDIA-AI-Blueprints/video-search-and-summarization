// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Shared utilities for video modal: URL checking and VIOS API fetch.
 */

export interface FetchVideoUrlParams {
  sensorId: string;
  startTime: string;
  endTime: string;
  objectIds?: string[];
  showObjectsBbox?: boolean;
}

/**
 * Check if a video URL is accessible by attempting to load it in a video element.
 * More reliable than HEAD requests as some servers don't support HEAD.
 * Supports cancellation via AbortSignal.
 */
export const checkVideoUrl = (
  url: string,
  signal?: AbortSignal,
  timeoutMs: number = 5000
): Promise<boolean> => {
  return new Promise((resolve) => {
    const video = document.createElement('video');
    let resolved = false;

    const cleanup = () => {
      if (resolved) return;
      resolved = true;
      video.onloadedmetadata = null;
      video.onerror = null;
      video.src = '';
      try {
        video.load();
      } catch {
        // JSDOM and some environments don't implement HTMLMediaElement.load
      }
    };

    const timeout = setTimeout(() => {
      cleanup();
      resolve(false);
    }, timeoutMs);

    if (signal) {
      if (signal.aborted) {
        cleanup();
        resolve(false);
        return;
      }
      signal.addEventListener(
        'abort',
        () => {
          clearTimeout(timeout);
          cleanup();
          resolve(false);
        },
        { once: true }
      );
    }

    video.onloadedmetadata = () => {
      clearTimeout(timeout);
      cleanup();
      resolve(true);
    };

    video.onerror = () => {
      clearTimeout(timeout);
      cleanup();
      resolve(false);
    };

    video.preload = 'metadata';
    video.src = url;
  });
};

/**
 * Replace the base URL (up to /vst) in videoUrl with the base from viosApiUrl.
 * Helps when UI can access only public IPs or different network.
 * Looks up "/vst/" (trailing slash) so hostnames like "vst-ingress" are not matched.
 */
export const replaceVideoUrlBase = (
  videoUrl: string,
  viosApiUrl: string
): string => {
  if (!videoUrl || !viosApiUrl) {
    return videoUrl;
  }

  const viosSegment = '/vst/';
  // Trailing slash avoids false matches on hosts like "vst-ingress" / "vios.test".
  const viosSegmentIndexInApiUrl = viosApiUrl.indexOf(viosSegment);
  const viosSegmentIndexInVideoUrl = videoUrl.indexOf(viosSegment);

  if (viosSegmentIndexInApiUrl === -1 || viosSegmentIndexInVideoUrl === -1) {
    console.error('Failed to replace video URL: /vst path segment not found in URLs', {
      viosApiUrl,
      videoUrl,
    });
    return videoUrl;
  }

  let viosBase = viosApiUrl.substring(0, viosSegmentIndexInApiUrl + viosSegment.length);
  // Normalize default ports via URL.origin, but keep any path prefix before /vst/
  // (e.g. https://proxy/some-prefix/vst/api -> https://proxy/some-prefix/vst/).
  try {
    const parsedApiUrl = new URL(viosApiUrl);
    const pathnameViosIndex = parsedApiUrl.pathname.indexOf(viosSegment);
    if (pathnameViosIndex !== -1) {
      viosBase =
        `${parsedApiUrl.origin}` +
        parsedApiUrl.pathname.substring(0, pathnameViosIndex + viosSegment.length);
    }
  } catch {
    // Keep string-derived base for relative viosApiUrl (e.g. "/vst/api").
  }

  const videoPathAfterVios = videoUrl.substring(
    viosSegmentIndexInVideoUrl + viosSegment.length
  );
  let finalVideoUrl = viosBase + videoPathAfterVios;

  if (finalVideoUrl !== videoUrl) {
    try {
      new URL(finalVideoUrl);
    } catch (e) {
      console.warn(
        'Constructed video URL is invalid, using original. Bad URL:',
        finalVideoUrl,
        'Original:',
        videoUrl,
        e
      );
      finalVideoUrl = videoUrl;
    }
  }

  return finalVideoUrl;
};

/**
 * Fetch video URL from VIOS API with optional overlay configuration.
 */
export const fetchVideoUrlFromVios = async (
  viosApiUrl: string,
  params: FetchVideoUrlParams,
  signal?: AbortSignal
): Promise<string> => {
  const { sensorId, startTime, endTime, objectIds, showObjectsBbox } = params;
  const hasObjectIds =
    showObjectsBbox === true &&
    Array.isArray(objectIds) &&
    objectIds.length > 0;

  const searchParams = new URLSearchParams({
    startTime,
    endTime,
    expiryMinutes: '60',
    container: 'mp4',
    disableAudio: 'false',
  });

  if (hasObjectIds) {
    searchParams.set(
      'configuration',
      JSON.stringify({
        overlay: {
          bbox: {
            showAll: false,
            showObjId: true,
            objectId: objectIds.map(String),
          },
          color: 'red',
          thickness: 5,
          debug: false,
          opacity: 254,
        },
      })
    );
  }

  const fetchUrl = `${viosApiUrl}/v1/storage/file/${sensorId}/url?${searchParams.toString()}`;
  const response = await fetch(fetchUrl, { signal });

  if (!response.ok) {
    throw new Error(`Failed to fetch video URL: ${response.status}`);
  }

  const data = await response.json();
  let finalUrl = data.videoUrl ?? '';

  if (data.videoUrl && viosApiUrl) {
    finalUrl = replaceVideoUrlBase(data.videoUrl, viosApiUrl);
  }

  return finalUrl;
};

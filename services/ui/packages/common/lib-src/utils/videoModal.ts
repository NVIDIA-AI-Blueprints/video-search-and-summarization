// SPDX-License-Identifier: MIT
/**
 * Shared utilities for video modal: URL checking and VST API fetch.
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
 * Replace the base URL (up to /vst) in videoUrl with the base from vstApiUrl.
 * Helps when UI can access only public IPs or different network.
 * Uses string-based logic so videoUrl can be relative. 
 */
export const replaceVideoUrlBase = (
  videoUrl: string,
  vstApiUrl: string
): string => {
  if (!videoUrl || !vstApiUrl) {
    return videoUrl;
  }

  const vstSegment = '/vst';
  // For vstApiUrl, search in pathname to avoid matching host (e.g. vst.test)
  let vstSegmentIndexInApiUrl = -1;
  try {
    const vstUrl = new URL(vstApiUrl);
    const idx = vstUrl.pathname.indexOf(vstSegment);
    if (idx !== -1) {
      vstSegmentIndexInApiUrl = vstUrl.origin.length + idx;
    }
  } catch {
    vstSegmentIndexInApiUrl = vstApiUrl.indexOf(vstSegment);
  }
  const vstSegmentIndexInVideoUrl = videoUrl.indexOf(vstSegment);

  if (vstSegmentIndexInApiUrl === -1 || vstSegmentIndexInVideoUrl === -1) {
    console.error('Failed to replace video URL: /vst path segment not found in URLs', {
      vstApiUrl,
      videoUrl,
    });
    return videoUrl;
  }

  const vstBase = vstApiUrl.substring(0, vstSegmentIndexInApiUrl + vstSegment.length);
  const videoPathAfterVst = videoUrl.substring(vstSegmentIndexInVideoUrl + vstSegment.length);
  let finalVideoUrl = vstBase + videoPathAfterVst;

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
 * Fetch video URL from VST API with optional overlay configuration.
 */
export const fetchVideoUrlFromVst = async (
  vstApiUrl: string,
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
    disableAudio: 'true',
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

  const fetchUrl = `${vstApiUrl}/v1/storage/file/${sensorId}/url?${searchParams.toString()}`;
  const response = await fetch(fetchUrl, { signal });

  if (!response.ok) {
    throw new Error(`Failed to fetch video URL: ${response.status}`);
  }

  const data = await response.json();
  let finalUrl = data.videoUrl ?? '';

  if (data.videoUrl && vstApiUrl) {
    finalUrl = replaceVideoUrlBase(data.videoUrl, vstApiUrl);
  }

  return finalUrl;
};

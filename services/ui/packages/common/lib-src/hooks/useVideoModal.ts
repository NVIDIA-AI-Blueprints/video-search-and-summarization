// SPDX-License-Identifier: MIT
/**
 * useVideoModal Hook - Video Playback Modal State Management
 *
 * Provides state management for video playback modal: visibility, URL generation
 * from VST API, and proper cleanup. Used by Search, Alerts, and other modules.
 *
 * Usage:
 * - Search: useVideoModal(vstApiUrl) -> openVideoModal(videoData, showObjectsBbox)
 * - Alerts: useVideoModal(vstApiUrl, { sensorMap, showObjectsBbox }) -> openVideoModalFromAlert(alert) 
 */

import { useRef, useState, useCallback } from 'react';
import {
  checkVideoUrl,
  fetchVideoUrlFromVst,
  replaceVideoUrlBase,
} from '../utils/videoModal';

export interface VideoModalState {
  isOpen: boolean;
  videoUrl: string;
  title: string;
}

/** Data required to fetch and display a video clip from VST API */
export interface VideoModalData {
  video_name: string;
  start_time: string;
  end_time: string;
  sensor_id: string;
  object_ids?: string[];
}

/** Minimal alert shape for video modal (AlertData from alerts package satisfies this) */
export interface AlertLike {
  id: string;
  timestamp?: string;
  end?: string;
  sensor: string;
  alertTriggered?: string;
  alertType?: string;
  metadata?: {
    info?: { videoSource?: string };
    objectIds?: string[];
  };
}

export interface UseVideoModalOptions {
  sensorMap?: Map<string, string>;
  showObjectsBbox?: boolean;
  /** Additional Compose/internal VST hostnames whose stored URLs need browser rewriting. */
  internalVstHostnames?: string[];
}

const INTERNAL_VST_HOSTNAMES = new Set([
  'vst-ingress',
  'vss-vios-nvstreamer',
]);
const EMPTY_INTERNAL_VST_HOSTNAMES: readonly string[] = [];

function isInternalVstVideoUrl(
  videoSource: string,
  additionalHostnames: readonly string[]
): boolean {
  try {
    const hostname = new URL(videoSource).hostname.toLowerCase();
    return (
      INTERNAL_VST_HOSTNAMES.has(hostname) ||
      additionalHostnames.some(
        (additionalHostname) => additionalHostname.toLowerCase() === hostname
      )
    );
  } catch {
    return false;
  }
}

export const useVideoModal = (
  vstApiUrl?: string,
  options?: UseVideoModalOptions
) => {
  const [videoModal, setVideoModal] = useState<VideoModalState>({
    isOpen: false,
    videoUrl: '',
    title: '',
  });
  const [loadingAlertId, setLoadingAlertId] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const sensorMap = options?.sensorMap;
  const showObjectsBbox = options?.showObjectsBbox ?? false;
  const internalVstHostnames =
    options?.internalVstHostnames ?? EMPTY_INTERNAL_VST_HOSTNAMES;

  /**
   * Opens the playback modal for a clip. Resolves `false` when no video could
   * be loaded (e.g. VST 404s because a freshly added stream has no recorded
   * footage yet) so callers can surface that instead of failing silently.
   */
  const openVideoModal = useCallback(
    async (videoData: VideoModalData, showBbox: boolean = false): Promise<boolean> => {
      if (!vstApiUrl) {
        console.error('VST API URL not available');
        return false;
      }

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      try {
        const { video_name, start_time, end_time, sensor_id, object_ids } =
          videoData;

        const finalVideoUrl = await fetchVideoUrlFromVst(
          vstApiUrl,
          {
            sensorId: sensor_id,
            startTime: start_time,
            endTime: end_time,
            objectIds: object_ids,
            showObjectsBbox: showBbox,
          },
          abortController.signal
        );

        if (abortController.signal.aborted) return false;

        if (!finalVideoUrl) {
          console.error('VST returned no video URL for', videoData);
          return false;
        }

        setVideoModal({
          isOpen: true,
          videoUrl: finalVideoUrl,
          title: video_name,
        });
        return true;
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          return false;
        }
        console.error('Error fetching video URL:', err);
        return false;
      } finally {
        if (abortControllerRef.current === abortController) {
          abortControllerRef.current = null;
        }
      }
    },
    [vstApiUrl]
  );

  const openVideoModalFromUrl = useCallback((title: string, videoUrl: string) => {
    setVideoModal({
      isOpen: true,
      videoUrl,
      title,
    });
  }, []);

  const openVideoModalFromAlert = useCallback(
    async (alert: AlertLike) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const abortController = new AbortController();
      abortControllerRef.current = abortController;
      setLoadingAlertId(alert.id);

      const title = alert.alertTriggered || alert.alertType || 'N/A';

      try {
        const videoSource = alert.metadata?.info?.videoSource;
        if (videoSource) {
          // Alert Bridge may persist a Compose-internal VST hostname. Rewrite
          // it through the browser-reachable VST base before probing so the UI
          // does not wait for a guaranteed timeout and regenerate the clip.
          const browserVideoSource = vstApiUrl && isInternalVstVideoUrl(
            videoSource,
            internalVstHostnames
          )
            ? replaceVideoUrlBase(videoSource, vstApiUrl)
            : videoSource;
          const isAccessible = await checkVideoUrl(
            browserVideoSource,
            abortController.signal
          );

          if (abortController.signal.aborted) return;

          if (isAccessible) {
            openVideoModalFromUrl(title, browserVideoSource);
            setLoadingAlertId(null);
            return;
          }
          console.warn(
            'Video source URL not accessible, falling back to VST API:',
            videoSource
          );
        }

        if (!vstApiUrl || !sensorMap) {
          console.error('VST API URL or sensor map not available');
          setLoadingAlertId(null);
          return;
        }

        const sensorId = sensorMap.get(alert.sensor);
        if (!sensorId) {
          console.error('Sensor ID not found for:', alert.sensor);
          setLoadingAlertId(null);
          return;
        }

        const startTime = alert.timestamp;
        const endTime = alert.end;

        if (!startTime || !endTime) {
          console.error('Start time or end time not found in alert metadata');
          setLoadingAlertId(null);
          return;
        }

        const objectIds = alert.metadata?.objectIds;

        const finalVideoUrl = await fetchVideoUrlFromVst(
          vstApiUrl,
          {
            sensorId,
            startTime,
            endTime,
            objectIds: Array.isArray(objectIds) ? objectIds : undefined,
            showObjectsBbox,
          },
          abortController.signal
        );

        if (abortController.signal.aborted) return;

        openVideoModalFromUrl(title, finalVideoUrl);
      } catch (err) {
        if (abortController.signal.aborted) {
          return;
        }
        console.error('Error fetching video URL:', err);
      } finally {
        if (abortControllerRef.current === abortController) {
          setLoadingAlertId(null);
          abortControllerRef.current = null;
        }
      }
    },
    [
      vstApiUrl,
      sensorMap,
      showObjectsBbox,
      internalVstHostnames,
      openVideoModalFromUrl,
    ]
  );

  const closeVideoModal = useCallback(() => {
    setVideoModal({
      isOpen: false,
      videoUrl: '',
      title: '',
    });
  }, []);

  return {
    videoModal,
    openVideoModal,
    openVideoModalFromUrl,
    openVideoModalFromAlert,
    closeVideoModal,
    loadingAlertId,
  };
};

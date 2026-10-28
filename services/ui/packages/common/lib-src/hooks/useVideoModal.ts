// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * useVideoModal Hook - Video Playback Modal State Management
 *
 * Provides state management for video playback modal: visibility, URL generation
 * from VIOS API, and proper cleanup. Used by Search, Alerts, and other modules.
 *
 * Usage:
 * - Search: useVideoModal(viosApiUrl) -> openVideoModal(videoData, showObjectsBbox)
 * - Alerts: useVideoModal(viosApiUrl, { sensorMap, showObjectsBbox }) -> openVideoModalFromAlert(alert) 
 */

import { useRef, useState, useCallback } from 'react';
import { checkVideoUrl, fetchVideoUrlFromVios } from '../utils/videoModal';

export interface VideoModalState {
  isOpen: boolean;
  videoUrl: string;
  title: string;
}

/** Data required to fetch and display a video clip from VIOS API */
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
}

export const useVideoModal = (
  viosApiUrl?: string,
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

  /**
   * Opens the playback modal for a clip. Resolves `false` when no video could
   * be loaded (e.g. VIOS 404s because a freshly added stream has no recorded
   * footage yet) so callers can surface that instead of failing silently.
   */
  const openVideoModal = useCallback(
    async (videoData: VideoModalData, showBbox: boolean = false): Promise<boolean> => {
      if (!viosApiUrl) {
        console.error('VIOS API URL not available');
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

        const finalVideoUrl = await fetchVideoUrlFromVios(
          viosApiUrl,
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
          console.error('VIOS returned no video URL for', videoData);
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
    [viosApiUrl]
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
          const isAccessible = await checkVideoUrl(
            videoSource,
            abortController.signal
          );

          if (abortController.signal.aborted) return;

          if (isAccessible) {
            openVideoModalFromUrl(title, videoSource);
            setLoadingAlertId(null);
            return;
          }
          console.warn(
            'Video source URL not accessible, falling back to VIOS API:',
            videoSource
          );
        }

        if (!viosApiUrl || !sensorMap) {
          console.error('VIOS API URL or sensor map not available');
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

        const finalVideoUrl = await fetchVideoUrlFromVios(
          viosApiUrl,
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
    [viosApiUrl, sensorMap, showObjectsBbox, openVideoModalFromUrl]
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

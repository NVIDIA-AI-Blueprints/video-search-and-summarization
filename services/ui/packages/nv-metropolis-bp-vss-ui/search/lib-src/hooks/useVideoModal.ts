// SPDX-License-Identifier: MIT
/**
 * useVideoModal Hook - Video Playback Modal State Management
 * 
 * This file contains the useVideoModal custom React hook which provides comprehensive
 * state management for video playback functionality within the alerts management system.
 * The hook handles video modal visibility, URL generation, sensor integration, and
 * provides a seamless interface for playing alert-related footage and evidence videos.
 * 
 * **Key Features:**
 * - Modal state management with open/close functionality and proper cleanup
 * - Dynamic video URL generation based on sensor data and alert information
 * - Sensor name-to-ID mapping integration for accurate video stream identification
 * - Error handling for missing sensors, invalid URLs, and network connectivity issues
 * - Video metadata management including titles, descriptions, and playback options
 * - Integration with external video streaming services and CDN networks
 * - Accessibility features including keyboard navigation and screen reader support
 *
 */

interface VideoModalState {
  isOpen: boolean;
  videoUrl: string;
  title: string;
}

import { useRef, useState } from 'react';
import { SearchData } from '../types';

export const useVideoModal = (vstApiUrl?: string) => {
  const [videoModal, setVideoModal] = useState<VideoModalState>({
    isOpen: false,
    videoUrl: '',
    title: ''
  });
  // Store AbortController to cancel previous request
  const abortControllerRef = useRef<AbortController | null>(null);

  const openVideoModal = async (videoData: SearchData, showObjectsBbox: boolean = false) => {
    // Cancel previous request if any
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const showBbox = showObjectsBbox === true;
    // Create new AbortController for this request
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    try {
      const { video_name, start_time, end_time, sensor_id, object_ids } = videoData;
      const hasObjectIds = showBbox && Array.isArray(object_ids) && object_ids.length > 0;
      const params = new URLSearchParams({
        startTime: start_time,
        endTime: end_time,
        expiryMinutes: '60',
        container: 'mp4',
        disableAudio: 'true',
      });
      if (hasObjectIds) {
        params.set('configuration', JSON.stringify({
          overlay: {
            bbox: { showAll: false, showObjId: true, objectId: object_ids.map(String) },
            color: 'red',
            thickness: 5,
            debug: false,
            opacity: 254
          }
        }));
      }
      const fetchVideoUrl = `${vstApiUrl}/v1/storage/file/${sensor_id}/url?${params.toString()}`;
      const response = await fetch(fetchVideoUrl, { signal: abortController.signal });
      if (!response.ok) {
        throw new Error(`Failed to fetch video URL: ${response.status}`);
      }
      const data = await response.json();

      // Check if aborted before setting state
      if (abortController.signal.aborted) return;

      // Replace the base URL up to and including /vst with the base from vstApiUrl
      // This helps even if the UI can access only public IPs or also private IPs.
      let finalVideoUrl = data.videoUrl;

      if (data.videoUrl && vstApiUrl) {
        try {
          const vstUrl = new URL(vstApiUrl);
          const videoUrl = new URL(data.videoUrl);

          // Find /vst in both URLs and replace everything up to it
          const vstPathIndex = vstUrl.pathname.indexOf('/vst');
          const videoPathIndex = videoUrl.pathname.indexOf('/vst');

          if (vstPathIndex === -1 || videoPathIndex === -1) {
            console.error('Failed to replace video URL: /vst path segment not found in URLs', {
              vstApiUrl,
              videoUrl: data.videoUrl
            });
          } else {
            // Get the base from vstApiUrl (protocol + host + path up to and including /vst)
            const vstBase = `${vstUrl.protocol}//${vstUrl.host}${vstUrl.pathname.substring(0, vstPathIndex + 4)}`;
            // Get the path after /vst from the video URL
            const videoPathAfterVst = videoUrl.pathname.substring(videoPathIndex + 4);
            // Combine them, preserving query string and hash from video URL
            finalVideoUrl = `${vstBase}${videoPathAfterVst}${videoUrl.search}${videoUrl.hash}`;
          }
        } catch (e) {
          console.warn('Failed to replace video URL base, using original:', e);
        }
      }

      setVideoModal({
        isOpen: true,
        videoUrl: finalVideoUrl,
        title: video_name
      });
    } catch (err) {
      // Ignore abort errors
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      console.error('Error fetching video URL:', err);
    } finally {
      // Only clear loading if this is still the current request
      if (abortControllerRef.current === abortController) {
        abortControllerRef.current = null;
      }
    }
  };

  const closeVideoModal = () => {
    setVideoModal({
      isOpen: false,
      videoUrl: '',
      title: ''
    });
  };

  return {
    videoModal,
    openVideoModal,
    closeVideoModal
  };
};
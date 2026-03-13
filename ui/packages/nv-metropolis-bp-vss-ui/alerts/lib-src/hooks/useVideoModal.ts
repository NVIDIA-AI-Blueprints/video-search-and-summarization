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

import { useState, useRef } from 'react';
import { AlertData, VideoModalState } from '../types';

/**
 * Check if a video URL is accessible by attempting to load it in a video element
 * This is more reliable than HEAD requests as some servers don't support HEAD
 * Supports cancellation via AbortSignal
 */
const checkVideoUrl = (url: string, signal?: AbortSignal, timeoutMs: number = 5000): Promise<boolean> => {
  return new Promise((resolve) => {
    const video = document.createElement('video');
    let resolved = false;
    
    const cleanup = () => {
      if (resolved) return;
      resolved = true;
      // Remove event listeners first
      video.onloadedmetadata = null;
      video.onerror = null;
      // Stop the video download completely
      video.src = '';
      video.load(); // Force browser to abort any pending requests
    };
    
    const timeout = setTimeout(() => {
      cleanup();
      resolve(false);
    }, timeoutMs);

    // Handle abort signal
    if (signal) {
      if (signal.aborted) {
        cleanup();
        resolve(false);
        return;
      }
      signal.addEventListener('abort', () => {
        clearTimeout(timeout);
        cleanup();
        resolve(false);
      }, { once: true });
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

export const useVideoModal = (vstApiUrl?: string, sensorMap?: Map<string, string>, showObjectsBbox: boolean = false) => {
  const [videoModal, setVideoModal] = useState<VideoModalState>({
    isOpen: false,
    videoUrl: '',
    title: ''
  });
  // Track which specific alert is loading (by ID)
  const [loadingAlertId, setLoadingAlertId] = useState<string | null>(null);
  
  // Store AbortController to cancel previous request
  const abortControllerRef = useRef<AbortController | null>(null);

  const openVideoModal = async (alert: AlertData) => {
    // Cancel previous request if any
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    
    // Create new AbortController for this request
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    
    setLoadingAlertId(alert.id);
    const title = alert.alertTriggered ? alert.alertTriggered : alert.alertType ? alert.alertType : 'N/A';

    try {
      // Check if videoSource exists in alert metadata and is accessible
      const videoSource = alert.metadata?.info?.videoSource;
      if (videoSource) {
        const isAccessible = await checkVideoUrl(videoSource, abortController.signal);
        
        // Check if aborted before continuing
        if (abortController.signal.aborted) return;
        
        if (isAccessible) {
          setVideoModal({
            isOpen: true,
            videoUrl: videoSource,
            title,
          });
          setLoadingAlertId(null);
          return;
        }
        // If not accessible, fall through to generate new URL from VST API
        console.warn('Video source URL not accessible, falling back to VST API:', videoSource);
      }

      // Fallback: fetch video URL from VST API
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

      // Build video URL with optional overlay configuration
      const objectIds = alert.metadata?.objectIds;
      const hasObjectIds = showObjectsBbox && Array.isArray(objectIds) && objectIds.length > 0;
      
      const params = new URLSearchParams({
        startTime,
        endTime,
        expiryMinutes: '60',
        container: 'mp4',
        disableAudio: 'true',
      });
      
      if (hasObjectIds) {
        params.set('configuration', JSON.stringify({
          overlay: {
            bbox: { showAll: false, showObjId: true, objectId: objectIds.map(String) },
            color: 'red',
            thickness: 5,
            debug: false,
            opacity: 254
          }
        }));
      }

      const fetchVideoUrl = `${vstApiUrl}/v1/storage/file/${sensorId}/url?${params.toString()}`;
      
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
          const vstUrl = new URL(vstApiUrl, window.location.origin);
          const videoUrl = new URL(data.videoUrl, window.location.origin);

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
        title,
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
        setLoadingAlertId(null);
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
    closeVideoModal,
    loadingAlertId
  };
};


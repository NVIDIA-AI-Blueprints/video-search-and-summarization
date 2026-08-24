// SPDX-License-Identifier: MIT
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Button as KaizenButton } from '@nvidia/foundations-react-core';
import { VideoModal, VideoModalTooltip } from '@aiqtoolkit-ui/common';

export interface SearchVideoModalProps {
  isOpen: boolean;
  videoUrl: string;
  title: React.ReactNode | string;
  onClose: () => void;
  searchByImageEnabled?: boolean;
  onSearchByImageRequest?: (pauseOffsetSeconds: number) => void;
  searchByImageFooter?: React.ReactNode;
  searchByImageOverlay?: React.ReactNode;
}

export const SearchVideoModal: React.FC<SearchVideoModalProps> = ({
  isOpen,
  videoUrl,
  title,
  onClose,
  searchByImageEnabled = false,
  onSearchByImageRequest,
  searchByImageFooter,
  searchByImageOverlay,
}) => {
  const [videoElement, setVideoElement] = useState<HTMLVideoElement | null>(null);
  const [paused, setPaused] = useState(false);
  const [pauseTime, setPauseTime] = useState(0);
  const handleVideoRef = useCallback((node: HTMLVideoElement | null) => {
    setVideoElement(node);
  }, []);

  useEffect(() => {
    setPaused(false);
    setPauseTime(0);
  }, [isOpen, videoUrl]);

  useEffect(() => {
    if (!videoElement) return;
    videoElement.style.opacity = searchByImageOverlay ? '0' : '1';
    videoElement.style.pointerEvents = searchByImageOverlay ? 'none' : 'auto';
    return () => {
      videoElement.style.opacity = '1';
      videoElement.style.pointerEvents = 'auto';
    };
  }, [videoElement, searchByImageOverlay]);

  const handleVideoPause = useCallback((currentTime: number) => {
    setPaused(true);
    setPauseTime(currentTime);
  }, []);

  const handleVideoPlay = useCallback(() => {
    setPaused(false);
  }, []);

  const handleSearchByImageClick = useCallback(() => {
    if (onSearchByImageRequest) onSearchByImageRequest(pauseTime);
  }, [onSearchByImageRequest, pauseTime]);

  const showSearchByImageButton =
    searchByImageEnabled && paused && !searchByImageOverlay && !!onSearchByImageRequest;
  const videoOverlayHost = useMemo(
    () => (videoElement?.parentElement as HTMLDivElement | null) ?? null,
    [videoElement]
  );

  if (!isOpen) return null;

  return (
    <>
      <VideoModal
        isOpen={isOpen}
        videoUrl={videoUrl}
        title={title}
        onClose={onClose}
        onVideoPause={handleVideoPause}
        onVideoPlay={handleVideoPlay}
        videoRef={handleVideoRef}
        footer={searchByImageFooter}
      />

      {videoOverlayHost && showSearchByImageButton && createPortal(
        <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
          <VideoModalTooltip
            content="Click to perform Search by Image on the paused video frame"
            wrapperClassName="pointer-events-auto"
          >
            <KaizenButton
              data-testid="image-search-perform-button"
              onClick={handleSearchByImageClick}
              kind="primary"
              size="small"
            >
              <span className="flex items-center gap-2">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                Search by Image
              </span>
            </KaizenButton>
          </VideoModalTooltip>
        </div>,
        videoOverlayHost
      )}

      {videoOverlayHost && searchByImageOverlay && createPortal(
        <div className="absolute inset-0 z-20 min-h-0 min-w-0">
          {searchByImageOverlay}
        </div>,
        videoOverlayHost
      )}
    </>
  );
};

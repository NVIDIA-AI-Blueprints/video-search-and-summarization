/**
 * VideoModal Component
 *
 * A popup modal component that renders a video player in an overlay window.
 * This component does NOT embed videos directly into the chat window, but instead
 * displays them in a separate popup modal.
 *
 * Uses native <dialog> with showModal() for top-layer rendering, focus trapping,
 * scroll lock, and implicit ARIA semantics (role="dialog", aria-modal="true").
 *
 * Key differences from Video.tsx:
 * - Video.tsx: Directly embeds video player inline within chat content
 * - VideoModal.tsx: Renders a popup overlay modal to play videos separately
 */

import React, { useEffect, useRef } from 'react';

export interface VideoModalProps {
  isOpen: boolean;
  videoUrl: string;
  title: string;
  onClose: () => void;
}

export const VideoModal: React.FC<VideoModalProps> = ({ isOpen, videoUrl, title, onClose }) => {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    try {
      if (!dialog.open) dialog.showModal();
    } catch {
      dialog.setAttribute('open', '');
    }
    return () => {
      try {
        if (dialog.open) dialog.close();
      } catch {
        // dialog may already be detached from DOM during unmount
      }
    };
  }, [isOpen]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const handleCancel = (e: Event) => {
      e.preventDefault();
      onClose();
    };
    const handleClick = (e: MouseEvent) => {
      if (e.target === dialog) onClose();
    };
    dialog.addEventListener('cancel', handleCancel);
    dialog.addEventListener('click', handleClick);
    return () => {
      dialog.removeEventListener('cancel', handleCancel);
      dialog.removeEventListener('click', handleClick);
    };
  }, [onClose]);

  if (!isOpen) return null;

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="video-modal-title"
      data-testid="video-modal"
      style={{
        position: 'fixed',
        inset: 0,
        width: '100vw',
        height: '100vh',
        maxWidth: 'none',
        maxHeight: 'none',
        margin: 0,
        padding: 0,
        border: 'none',
        background: 'transparent',
      }}
      className="z-50 grid place-items-center overflow-hidden backdrop:bg-black/60 backdrop:backdrop-blur-sm"
    >
      <div
        className="relative mx-4 flex w-[min(70vw,1200px)] max-h-[85vh] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-neutral-900"
      >
        <div className="shrink-0 bg-white dark:bg-neutral-900 text-black dark:text-white px-6 py-4 flex items-center justify-between border-b-2 border-brand-green">
          <div className="flex-1 pr-4">
            <h4 id="video-modal-title" data-testid="video-modal-title" className="text-lg font-semibold text-black dark:text-white">
              {title}
            </h4>
          </div>
          <button
            data-testid="video-modal-close"
            onClick={onClose}
            className="flex items-center justify-center w-10 h-10 rounded-lg transition-colors duration-150 text-gray-600 dark:text-gray-300 hover:bg-brand-green-dark hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-green-dark focus-visible:bg-brand-green-dark focus-visible:text-white"
            aria-label="Close video"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-hidden bg-black">
          <video
            controls
            autoPlay
            className="h-full w-full object-contain bg-black"
            onError={() => {
              console.error('Video failed to load:', videoUrl);
            }}
          >
            <source src={videoUrl} type="video/mp4" />
            <track kind="captions" />
            Your browser does not support the video tag.
          </video>
        </div>
      </div>
    </dialog>
  );
};

/**
 * VideoModal Component
 * 
 * A popup modal component that renders a video player in an overlay window.
 * This component does NOT embed videos directly into the chat window, but instead
 * displays them in a separate popup modal.
 * 
 * Key differences from Video.tsx:
 * - Video.tsx: Directly embeds video player inline within chat content
 * - VideoModal.tsx: Renders a popup overlay modal to play videos separately
 * 
 */

import React from 'react';

export interface VideoModalProps {
  isOpen: boolean;
  videoUrl: string;
  title: string;
  onClose: () => void;
}

export const VideoModal: React.FC<VideoModalProps> = ({ isOpen, videoUrl, title, onClose }) => {
  if (!isOpen) return null;

  return (
    <div 
      className="fixed inset-0 bg-black bg-opacity-60 dark:bg-black dark:bg-opacity-80 flex items-center justify-center z-50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div 
        className="relative w-full max-w-5xl mx-4 rounded-2xl overflow-hidden bg-white dark:bg-gray-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-white px-6 py-4 flex items-center justify-between">
          <div className="flex-1 pr-4">
            <h4 className="text-lg font-semibold text-gray-900 dark:text-white">
              {title}
            </h4>
          </div>
          <button
            onClick={onClose}
            className="flex items-center justify-center w-10 h-10 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors duration-150 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            title="Close video"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        
        {/* Video Container */}
        <div className="overflow-hidden">
          <video
            controls
            autoPlay
            className="w-full h-auto max-h-[75vh] min-h-[400px] object-contain bg-black"
            onError={(e) => {
              console.error('Video failed to load:', videoUrl);
            }}
          >
            <source src={videoUrl} type="video/mp4" />
            Your browser does not support the video tag.
          </video>
        </div>
      </div>
    </div>
  );
};

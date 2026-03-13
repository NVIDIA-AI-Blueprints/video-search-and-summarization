// SPDX-License-Identifier: MIT
import React, { useState, useRef, useCallback } from 'react';

interface EmptyStateProps {
  onFilesSelected: (files: File[]) => void;
  enableVideoUpload?: boolean;
}

export const EmptyState: React.FC<EmptyStateProps> = ({ onFilesSelected, enableVideoUpload = true }) => {
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      onFilesSelected(Array.from(files));
    }
    // Reset input so same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);

    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      onFilesSelected(Array.from(files));
    }
  }, [onFilesSelected]);

  if (!enableVideoUpload) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            No videos available
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex items-center justify-center">
      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".mp4,.mkv"
        onChange={handleFileInputChange}
        className="hidden"
      />

      <div
        onClick={handleClick}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`rounded-lg border-2 border-dashed text-center transition-colors cursor-pointer w-[580px] py-[60px] px-12 ${
          isDragOver
            ? 'border-green-500 bg-green-50 dark:bg-green-500/10'
            : 'border-gray-400 dark:border-gray-600 bg-transparent hover:border-gray-500'
        }`}
      >
        {/* Document icon with plus */}
        <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center">
          <svg
            className={isDragOver ? 'text-green-500' : 'text-gray-500 dark:text-gray-400'}
            width="40"
            height="40"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="12" y1="18" x2="12" y2="12" />
            <line x1="9" y1="15" x2="15" y2="15" />
          </svg>
        </div>
        <p className={`text-base font-medium mb-3 ${
          isDragOver 
            ? 'text-green-500' 
            : 'text-gray-700 dark:text-gray-200'
        }`}>
          {isDragOver ? 'Drop files to upload' : 'Drop files here'}
        </p>
        <p className="text-sm mb-2 text-gray-500 dark:text-gray-400">
          Movie Files (mp4, mkv)
        </p>
      </div>
    </div>
  );
};

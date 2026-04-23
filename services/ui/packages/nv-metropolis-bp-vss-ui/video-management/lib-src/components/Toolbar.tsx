// SPDX-License-Identifier: MIT
import React, { useRef, useState, useEffect } from 'react';
import { Button, TextInput } from '@nvidia/foundations-react-core';

interface ToolbarProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  onSearch: () => void;
  showVideos: boolean;
  showRtsps: boolean;
  onShowVideosChange: (value: boolean) => void;
  onShowRtspsChange: (value: boolean) => void;
  onFilesSelected: (files: File[]) => void;
  onAddRtspClick: () => void;
  selectedCount: number;
  onDeleteSelected: () => void;
  isDeleting?: boolean;
  enableAddRtspButton?: boolean;
  enableVideoUpload?: boolean;
  /** Only show Video option when API returned at least one video stream */
  hasVideoStreams?: boolean;
  /** Only show RTSP option when API returned at least one RTSP stream */
  hasRtspStreams?: boolean;
}

export const Toolbar: React.FC<ToolbarProps> = ({
  searchQuery,
  onSearchChange,
  onSearch,
  showVideos,
  showRtsps,
  onShowVideosChange,
  onShowRtspsChange,
  onFilesSelected,
  onAddRtspClick,
  selectedCount,
  onDeleteSelected,
  isDeleting = false,
  enableAddRtspButton = true,
  enableVideoUpload = true,
  hasVideoStreams = true,
  hasRtspStreams = true,
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [isFilterDropdownOpen, setIsFilterDropdownOpen] = useState(false);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsFilterDropdownOpen(false);
      }
    };

    if (isFilterDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isFilterDropdownOpen]);

  const handleUploadClick = () => {
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

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      onSearch();
    }
  };

  const showVideoOption = enableVideoUpload && hasVideoStreams;
  const showRtspOption = enableAddRtspButton && hasRtspStreams;
  const showDisplayFilter = showVideoOption || showRtspOption;

  const getFilterLabel = () => {
    const hasVideo = showVideoOption && showVideos;
    const hasRtsp = showRtspOption && showRtsps;
    if (hasVideo && hasRtsp) return 'Video, RTSP';
    if (hasVideo) return 'Video';
    if (hasRtsp) return 'RTSP';
    return 'Select File Type';
  };

  return (
    <div className="flex items-center justify-between gap-4 px-6 pt-6 pb-4 border-b border-gray-200 dark:border-gray-800">
      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".mp4,.mkv"
        onChange={handleFileInputChange}
        className="hidden"
      />

      {/* Left: primary actions */}
      <div className="flex items-center gap-3">
        {enableVideoUpload && (
          <Button
            kind="primary"
            onClick={handleUploadClick}
          >
            + Upload Video
          </Button>
        )}
        {enableAddRtspButton && (
          <Button
            kind="secondary"
            onClick={onAddRtspClick}
          >
            + Add RTSP
          </Button>
        )}
      </div>

      {/* Right: search + display filter + delete */}
      <div className="flex items-center gap-2">
        {/* Search input with clear button */}
        <div className="relative">
          <TextInput
            data-testid="search-video-input"
            value={searchQuery}
            onValueChange={(val: string) => onSearchChange(val)}
            onKeyPress={handleKeyPress}
            placeholder="Search Files"
          />
          {searchQuery && (
            <button
              onClick={() => onSearchChange('')}
              className="p-1.5 rounded transition-colors text-gray-400 hover:text-white hover:bg-neutral-700 dark:text-gray-400 dark:hover:text-white dark:hover:bg-neutral-700"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
        </div>

        {/* Search button */}
        <Button
          data-testid="search-video-button"
          kind="secondary"
          onClick={onSearch}
        >
          Search
        </Button>

        {showDisplayFilter && (
          /* Display filter dropdown - multi-select */
          <div className="relative flex items-center gap-2 ml-2">
            <label htmlFor="display-filter-toggle" className="text-sm font-medium text-gray-700 dark:text-gray-300">
              Display:
            </label>
            <div className="relative" ref={dropdownRef}>
              <Button
                kind="tertiary"
                id="display-filter-toggle"
                onClick={() => setIsFilterDropdownOpen(!isFilterDropdownOpen)}
                aria-expanded={isFilterDropdownOpen}
                aria-haspopup="true"
                aria-label={`Display file type: ${getFilterLabel()}`}
                className="flex items-center gap-2 pr-3" // Add `gap` for spacing between text and chevron, `pr-7` for chevron padding
              >
                <span className="truncate">{getFilterLabel()}</span>
                <span className="ml-2" /> {/* Ensures space after the text */}
                <svg
                  className={`absolute right-2 w-4 h-4 transition-transform ${isFilterDropdownOpen ? 'rotate-180' : ''}`}
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="#76b900"
                  strokeWidth="2"
                  aria-hidden
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </Button>

              {/* Dropdown menu - multi-select checkboxes */}
              {isFilterDropdownOpen && (
                <div
                  role="group"
                  aria-label="Display file type"
                  className="w-40 absolute left-0 top-full mt-1 rounded-md border shadow-lg z-50 py-1 bg-white dark:bg-black border-gray-200 dark:border-gray-600"
                >
                    {showVideoOption && (
                      <label
                        className="flex items-center gap-2 px-3 py-2 w-full text-left hover:bg-gray-50 dark:hover:bg-black cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={showVideos}
                          onChange={() => onShowVideosChange(!showVideos)}
                          onClick={(e) => e.stopPropagation()}
                          className="sr-only"
                          aria-label="Video"
                        />
                        <span className={`w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 ${
                          showVideos
                            ? 'bg-green-600 dark:bg-green-600 border-green-600 dark:border-green-600'
                            : 'bg-white dark:bg-black border-gray-300 dark:border-gray-500'
                        }`} aria-hidden>
                          {showVideos && (
                            <svg className="w-3 h-3 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          )}
                        </span>
                        <span className="text-sm text-gray-700 dark:text-gray-300">Video</span>
                      </label>
                    )}

                    {showRtspOption && (
                      <label
                        className="flex items-center gap-2 px-3 py-2 w-full text-left hover:bg-gray-50 dark:hover:bg-black cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={showRtsps}
                          onChange={() => onShowRtspsChange(!showRtsps)}
                          onClick={(e) => e.stopPropagation()}
                          className="sr-only"
                          aria-label="RTSP"
                        />
                        <span className={`w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 ${
                          showRtsps
                            ? 'bg-green-600 dark:bg-green-600 border-green-600 dark:border-green-600'
                            : 'bg-white dark:bg-black border-gray-300 dark:border-gray-500'
                        }`} aria-hidden>
                          {showRtsps && (
                            <svg className="w-3 h-3 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          )}
                        </span>
                        <span className="text-sm text-gray-700 dark:text-gray-300">RTSP</span>
                      </label>
                    )}
                  </div>
              )}
            </div>
          </div>
        )}

        {/* Delete Selected button */}
        <Button
          kind="secondary"
          onClick={onDeleteSelected}
          disabled={selectedCount === 0 || isDeleting}
        >
          {isDeleting ? (
            <svg
              className="animate-spin"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
              <path d="M12 2a10 10 0 0 1 10 10" strokeOpacity="1" />
            </svg>
          ) : (
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="15" y1="9" x2="9" y2="15" />
              <line x1="9" y1="9" x2="15" y2="15" />
            </svg>
          )}
          {isDeleting ? 'Deleting...' : 'Delete Selected'}
        </Button>
      </div>
    </div>
  );
};

// SPDX-License-Identifier: MIT
import React, { useRef, useState, useEffect } from 'react';

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

  // Consistent input/select styling matching project patterns
  const inputClass = 'rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 transition-all bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 focus:ring-blue-400 dark:focus:ring-cyan-500 hover:border-gray-400 dark:hover:border-gray-500';

  const buttonClass = 'inline-flex items-center px-4 py-2 text-sm font-medium rounded-md border focus:outline-none focus:ring-2 focus:ring-offset-2 border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-700 focus:ring-gray-300 dark:focus:ring-gray-500 focus:ring-offset-gray-50 dark:focus:ring-offset-gray-900';

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

  const getFilterLabel = () => {
    const hasVideo = enableVideoUpload && showVideos;
    const hasRtsp = enableAddRtspButton && showRtsps;
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
          <button
            type="button"
            onClick={handleUploadClick}
            className="inline-flex items-center px-4 py-2 text-sm font-medium rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-offset-2 bg-green-600 dark:bg-green-500 hover:bg-green-700 dark:hover:bg-green-600 text-white dark:text-gray-900 focus:ring-green-500 dark:focus:ring-green-400 focus:ring-offset-gray-50 dark:focus:ring-offset-gray-900 cursor-pointer"
          >
            + Upload Video
          </button>
        )}
        {enableAddRtspButton && (
          <button
            type="button"
            onClick={onAddRtspClick}
            className={buttonClass}
          >
            + Add RTSP
          </button>
        )}
      </div>

      {/* Right: search + display filter + delete */}
      <div className="flex items-center gap-2">
        {/* Search input with clear button */}
        <div className="relative">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Search Files"
            className={`w-56 pl-3 pr-8 ${inputClass}`}
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => onSearchChange('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
        </div>

        {/* Search button */}
        <button
          type="button"
          onClick={onSearch}
          className={buttonClass}
        >
          Search
        </button>

        {(enableVideoUpload || enableAddRtspButton) && (
          /* Display filter dropdown - multi-select */
          <div className="relative flex items-center gap-2 ml-2">
            <label htmlFor="display-filter-toggle" className="text-sm font-medium text-gray-700 dark:text-gray-300">
              Display:
            </label>
            <div className="relative" ref={dropdownRef}>
              <button
                id="display-filter-toggle"
                type="button"
                onClick={() => setIsFilterDropdownOpen(!isFilterDropdownOpen)}
                aria-expanded={isFilterDropdownOpen}
                aria-haspopup="true"
                aria-label={`Display file type: ${getFilterLabel()}`}
                className="w-40 flex items-center justify-between pl-3 pr-8 py-2 text-sm rounded-md border focus:outline-none focus:ring-2 transition-all cursor-pointer bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 focus:ring-blue-400 dark:focus:ring-cyan-500 hover:border-gray-400 dark:hover:border-gray-500"
              >
                <span className="truncate">{getFilterLabel()}</span>
                <svg
                  className={`absolute right-2 w-4 h-4 transition-transform ${isFilterDropdownOpen ? 'rotate-180' : ''}`}
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </button>

              {/* Dropdown menu - multi-select checkboxes */}
              {isFilterDropdownOpen && (
                <div
                  role="group"
                  aria-label="Display file type"
                  className="w-40 absolute left-0 top-full mt-1 rounded-md border shadow-lg z-50 py-1 bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-600"
                >
                    {enableVideoUpload && (
                      <label
                        className="flex items-center gap-2 px-3 py-2 w-full text-left hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
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
                            ? 'bg-blue-600 dark:bg-cyan-600 border-blue-600 dark:border-cyan-600'
                            : 'bg-white dark:bg-gray-700 border-gray-300 dark:border-gray-500'
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

                    {enableAddRtspButton && (
                      <label
                        className="flex items-center gap-2 px-3 py-2 w-full text-left hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
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
                            ? 'bg-blue-600 dark:bg-cyan-600 border-blue-600 dark:border-cyan-600'
                            : 'bg-white dark:bg-gray-700 border-gray-300 dark:border-gray-500'
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
        <button
          type="button"
          onClick={onDeleteSelected}
          disabled={selectedCount === 0 || isDeleting}
          className={`inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded border ${
            selectedCount === 0 || isDeleting
              ? 'border-gray-300 dark:border-gray-700 text-gray-400 dark:text-gray-600 cursor-not-allowed'
              : 'border-red-500 text-red-500 hover:bg-red-500 hover:text-white'
          }`}
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
        </button>
      </div>
    </div>
  );
};

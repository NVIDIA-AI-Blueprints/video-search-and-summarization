// SPDX-License-Identifier: MIT
import React, { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { Button } from '@nvidia/foundations-react-core';
import type { StreamInfo } from '../types';
import { StreamCard } from './StreamCard';

// Grid constants
const CARD_MIN_WIDTH = 240; // minmax(240px, 1fr)
const GRID_GAP = 16; // gap: 16px
const TARGET_ROWS = 4; // Target number of rows per page (reduced by ~25% from 5)

interface StreamsGridProps {
  streams: StreamInfo[];
  selectedStreams: Set<string>;
  vstApiUrl?: string | null;
  onSelectionChange: (streamId: string, selected: boolean) => void;
  onSelectAll: (selected: boolean) => void;
  showVideos: boolean;
  showRtsps: boolean;
  getEndTimeForStream: (streamId: string) => string | null;
  onPlayStream?: (stream: StreamInfo) => void;
  loadingStreamId?: string | null;
}

export const StreamsGrid: React.FC<StreamsGridProps> = ({
  streams,
  selectedStreams,
  vstApiUrl,
  onSelectionChange,
  onSelectAll,
  showVideos,
  showRtsps,
  getEndTimeForStream,
  onPlayStream,
  loadingStreamId,
}) => {
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerRow, setItemsPerRow] = useState(0); // 0 means not yet calculated
  const gridRef = useRef<HTMLDivElement>(null);
  const selectAllCheckboxRef = useRef<HTMLInputElement>(null);

  // Calculate items per row based on the actual grid element width
  const calculateItemsPerRow = useCallback(() => {
    if (!gridRef.current) return;
    
    // Use clientWidth which excludes borders but includes padding (which we don't have on the grid itself)
    const gridWidth = gridRef.current.clientWidth;
    
    // CSS grid auto-fill formula: how many columns fit
    // Each column needs at least CARD_MIN_WIDTH, plus gaps between them
    // gridWidth >= n * CARD_MIN_WIDTH + (n-1) * GRID_GAP
    // gridWidth >= n * CARD_MIN_WIDTH + n * GRID_GAP - GRID_GAP
    // gridWidth + GRID_GAP >= n * (CARD_MIN_WIDTH + GRID_GAP)
    // n <= (gridWidth + GRID_GAP) / (CARD_MIN_WIDTH + GRID_GAP)
    const calculatedItems = Math.floor((gridWidth + GRID_GAP) / (CARD_MIN_WIDTH + GRID_GAP));
    const newItemsPerRow = Math.max(1, calculatedItems);
    
    if (newItemsPerRow !== itemsPerRow) {
      setItemsPerRow(newItemsPerRow);
    }
  }, [itemsPerRow]);

  // Observe grid resize
  useEffect(() => {
    // Initial calculation after mount
    const timer = setTimeout(calculateItemsPerRow, 0);
    
    const resizeObserver = new ResizeObserver(() => {
      calculateItemsPerRow();
    });
    
    if (gridRef.current) {
      resizeObserver.observe(gridRef.current);
    }
    
    return () => {
      clearTimeout(timer);
      resizeObserver.disconnect();
    };
  }, [calculateItemsPerRow]);

  // Calculate dynamic items per page (must be multiple of itemsPerRow for full rows)
  const itemsPerPage = useMemo(() => {
    if (itemsPerRow === 0) {
      // Not yet calculated, use a reasonable default
      return TARGET_ROWS * 4;
    }
    return itemsPerRow * TARGET_ROWS;
  }, [itemsPerRow]);

  // Calculate pagination
  const totalPages = Math.ceil(streams.length / itemsPerPage);
  
  // Get streams for current page only - these are the only ones that will fetch images
  const paginatedStreams = useMemo(() => {
    const startIndex = (currentPage - 1) * itemsPerPage;
    return streams.slice(startIndex, startIndex + itemsPerPage);
  }, [streams, currentPage, itemsPerPage]);

  // Reset to page 1 when streams change significantly (e.g., filter applied)
  useEffect(() => {
    if (currentPage > totalPages && totalPages > 0) {
      setCurrentPage(1);
    }
  }, [totalPages, currentPage]);

  const allSelected = streams.length > 0 && selectedStreams.size === streams.length;

  // Never show indeterminate (dash) — with separate Select All / Deselect All buttons it's confusing
  useEffect(() => {
    const el = selectAllCheckboxRef.current;
    if (el) el.indeterminate = false;
  }, [selectedStreams.size, streams.length]);

  const handleSelectAllChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onSelectAll(e.target.checked);
  };

  const canSelectAll = streams.length > 0 && selectedStreams.size < streams.length;
  const canDeselectAll = selectedStreams.size > 0;

  // Get viewing label based on filter state
  const getViewingLabel = () => {
    if (showVideos && showRtsps) return 'All Videos and RTSPs';
    if (showVideos) return 'Videos only';
    if (showRtsps) return 'RTSPs only';
    return 'None';
  };

  const handlePrevPage = () => {
    setCurrentPage((prev) => Math.max(1, prev - 1));
  };

  const handleNextPage = () => {
    setCurrentPage((prev) => Math.min(totalPages, prev + 1));
  };

  const handlePageClick = (page: number) => {
    setCurrentPage(page);
  };

  // Generate page numbers to display
  const getPageNumbers = (): (number | 'ellipsis')[] => {
    const pages: (number | 'ellipsis')[] = [];
    const maxVisiblePages = 5;

    if (totalPages <= maxVisiblePages) {
      // Show all pages if total is small
      for (let i = 1; i <= totalPages; i++) {
        pages.push(i);
      }
    } else {
      // Always show first page
      pages.push(1);

      if (currentPage > 3) {
        pages.push('ellipsis');
      }

      // Show pages around current page
      const start = Math.max(2, currentPage - 1);
      const end = Math.min(totalPages - 1, currentPage + 1);

      for (let i = start; i <= end; i++) {
        pages.push(i);
      }

      if (currentPage < totalPages - 2) {
        pages.push('ellipsis');
      }

      // Always show last page
      pages.push(totalPages);
    }

    return pages;
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 pt-6 pb-4">
        <div className="flex items-center">
          <div className="flex items-center gap-3">
            <input
              ref={selectAllCheckboxRef}
              type="checkbox"
              checked={allSelected}
              onChange={handleSelectAllChange}
              className="w-4 h-4 rounded border-2 cursor-pointer bg-white dark:bg-gray-700 border-gray-300 dark:border-gray-600 text-green-600 dark:text-green-500 focus:ring-green-500"
            />
            {canSelectAll && (
              <Button
                kind="tertiary"
                onClick={() => onSelectAll(true)}
              >
                Select All
              </Button>
            )}
            {canDeselectAll && (
              <Button
                kind="tertiary"
                onClick={() => onSelectAll(false)}
              >
                Deselect All
              </Button>
            )}
          </div>
          <span className="mx-4 text-gray-300 dark:text-gray-600">|</span>
          <span className="text-sm text-gray-500">
            Viewing: {getViewingLabel()}
          </span>
        </div>

        {/* Page info */}
        {totalPages > 1 && (
          <span className="text-sm text-gray-500">
            {streams.length} streams
          </span>
        )}
      </div>

      {/* Grid - scrollable */}
      <div className="flex-1 overflow-auto px-6 pt-1 pb-4">
        <div
          data-testid="video-streams-grid"
          ref={gridRef}
          className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4"
        >
          {paginatedStreams.map((stream) => (
            <StreamCard
              key={stream.streamId}
              stream={stream}
              isSelected={selectedStreams.has(stream.streamId)}
              vstApiUrl={vstApiUrl}
              onSelectionChange={onSelectionChange}
              getEndTimeForStream={getEndTimeForStream}
              onPlay={onPlayStream}
              isLoadingPlay={loadingStreamId === stream.streamId}
            />
          ))}
        </div>
      </div>

      {/* Pagination controls */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 px-6 py-4 border-t border-gray-200 dark:border-gray-700">
          {/* Previous button */}
          <Button
            kind="tertiary"
            onClick={handlePrevPage}
            disabled={currentPage === 1}
          >
            Previous
          </Button>

          {/* Page numbers */}
          <div className="flex items-center gap-1">
            {getPageNumbers().map((page, index) =>
              page === 'ellipsis' ? (
                <span
                  key={`ellipsis-${index}`}
                  className="px-2 text-gray-400 dark:text-gray-500"
                >
                  ...
                </span>
              ) : (
                <Button
                  key={page}
                  kind="tertiary"
                  onClick={() => handlePageClick(page)}
                >
                  {page}
                </Button>
              )
            )}
          </div>

          {/* Next button */}
          <Button
            kind="tertiary"
            onClick={handleNextPage}
            disabled={currentPage === totalPages}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
};

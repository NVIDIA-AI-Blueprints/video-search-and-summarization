// SPDX-License-Identifier: MIT
import React from 'react';
import { Button } from '@nvidia/foundations-react-core';
import type { UploadProgress } from '../types';

interface UploadProgressPanelProps {
  uploads: UploadProgress[];
  onClose: () => void;
  onCancel: () => void;
}

export const UploadProgressPanel: React.FC<UploadProgressPanelProps> = ({
  uploads,
  onClose,
  onCancel,
}) => {
  if (uploads.length === 0) return null;

  const completedCount = uploads.filter((u) => u.status === 'success').length;
  const errorCount = uploads.filter((u) => u.status === 'error').length;
  const cancelledCount = uploads.filter((u) => u.status === 'cancelled').length;
  const inProgressCount = uploads.filter((u) => u.status === 'uploading').length;
  const processingCount = uploads.filter((u) => u.status === 'processing').length;
  const pendingCount = uploads.filter((u) => u.status === 'pending').length;

  const allDone = inProgressCount === 0 && pendingCount === 0 && processingCount === 0;
  const hasActiveUploads = inProgressCount > 0 || pendingCount > 0 || processingCount > 0;

  return (
    <div
      data-testid="upload-progress-panel"
      className="absolute inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Upload progress"
    >
      <div className="flex w-full max-h-[85vh] max-w-xl flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-lg dark:border-gray-600 dark:bg-black z-50">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-600">
          <div className="flex items-center gap-2">
            {!allDone ? (
            <svg
              className="animate-spin w-4 h-4 text-green-500"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
              <path d="M12 2a10 10 0 0 1 10 10" strokeOpacity="1" />
            </svg>
          ) : completedCount !== uploads.length && (
            <svg
              className="w-4 h-4 text-orange-500"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          )}
          <span data-testid="upload-progress-panel-title" className="font-medium text-sm text-gray-800 dark:text-gray-200">
            {allDone
              ? completedCount === uploads.length
                ? `Upload Complete (${completedCount}/${uploads.length})`
                : `Upload Finished (${completedCount}/${uploads.length} succeeded)`
              : processingCount > 0 && inProgressCount === 0 && pendingCount === 0
                ? `Processing ${processingCount} file${processingCount > 1 ? 's' : ''}...`
                : `Uploading ${completedCount + inProgressCount + processingCount}/${uploads.length} files...`}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {hasActiveUploads && (
            <Button
              kind="secondary"
              onClick={onCancel}
            >
              Cancel All
            </Button>
          )}
          {allDone && (
            <button
              onClick={onClose}
              className="p-1.5 rounded transition-colors text-gray-400 hover:text-white hover:bg-neutral-700 dark:text-gray-400 dark:hover:text-white dark:hover:bg-neutral-700"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Summary */}
      {allDone && (completedCount > 0 || errorCount > 0 || cancelledCount > 0) && (
        <div data-testid="upload-progress-panel-summary" className="px-4 py-2 text-xs text-gray-500 dark:text-gray-400">
          {completedCount > 0 && (
            <span className="text-green-500 mr-3">{completedCount} succeeded</span>
          )}
          {errorCount > 0 && <span className="text-red-500 mr-3">{errorCount} failed</span>}
          {cancelledCount > 0 && (
            <span className="text-gray-400 dark:text-gray-500">{cancelledCount} cancelled</span>
          )}
        </div>
      )}

      {/* File list - max height of 200px for ~5 items */}
      <div className="max-h-[200px] overflow-y-auto">
        {uploads.map((upload) => (
            <div
              key={upload.id}
              className="px-4 py-2 border-b last:border-b-0 border-gray-100 dark:border-gray-700"
            >
              <div className="flex items-center justify-between mb-1">
                <span
                  className="text-sm truncate max-w-[240px] text-gray-700 dark:text-gray-300"
                  title={upload.fileName}
                >
                  {upload.fileName}
                </span>
                <span className="flex items-center gap-1">
                  {upload.status === 'pending' && (
                    <span className="text-xs text-gray-400 dark:text-gray-500">
                      Pending
                    </span>
                  )}
                  {upload.status === 'uploading' && (
                    <span className="text-xs text-green-500">{upload.progress}%</span>
                  )}
                  {upload.status === 'processing' && (
                    <span className="text-xs text-amber-500">Processing...</span>
                  )}
                  {upload.status === 'success' && (
                    <svg
                      className="w-4 h-4 text-green-500"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                  {upload.status === 'error' && (
                    <svg
                      className="w-4 h-4 text-red-500"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <circle cx="12" cy="12" r="10" />
                      <line x1="15" y1="9" x2="9" y2="15" />
                      <line x1="9" y1="9" x2="15" y2="15" />
                    </svg>
                  )}
                  {upload.status === 'cancelled' && (
                    <span className="text-xs text-gray-400 dark:text-gray-500">
                      Cancelled
                    </span>
                  )}
                </span>
              </div>


              {/* Progress bar for uploading files */}
              {/* Note: Inline style required for dynamic width - Tailwind can't handle runtime-computed values */}
              {upload.status === 'uploading' && (
                <div className="h-1.5 rounded-full overflow-hidden bg-gray-200 dark:bg-gray-700">
                  <div
                    className="h-full rounded-full transition-all duration-300 bg-green-500"
                    style={{
                      width: `${upload.progress}%`,
                      minWidth: upload.progress > 0 ? '8px' : '0'
                    }}
                  />
                </div>
              )}

              {/* Indeterminate progress bar for post-upload processing (embeddings) */}
              {upload.status === 'processing' && (
                <div className="h-1.5 rounded-full overflow-hidden bg-gray-200 dark:bg-gray-700">
                  <div className="h-full rounded-full bg-amber-500 animate-pulse" style={{ width: '100%' }} />
                </div>
              )}

              {/* Error message */}
              {upload.status === 'error' && upload.error && (
                <div className="text-xs text-red-500 mt-1 max-h-16 overflow-auto rounded p-2 break-words whitespace-pre-wrap bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800" title={upload.error}>
                  {upload.error}
                </div>
              )}
            </div>
        ))}
      </div>
      </div>
    </div>
  );
};

import React from 'react';
import { IconCheck, IconX } from '@tabler/icons-react';

export type UploadFileStatus = 'pending' | 'uploading' | 'success' | 'error' | 'cancelled';

export interface UploadProgressFileItem {
  id: string;
  displayName: string;
  uploadProgress?: number;
  uploadStatus?: UploadFileStatus;
  uploadError?: string;
}

const POPUP_OVERLAY_CLASS = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
const POPUP_CONTAINER_CLASS =
  'mx-4 w-full max-w-xl rounded-lg border border-gray-200 bg-white p-6 shadow-xl dark:border-neutral-700 dark:bg-neutral-900 dark:shadow-2xl';

const UPLOAD_STATUS_STYLE: Record<UploadFileStatus, { progressBarClass: string; textClass: string }> = {
  pending: { progressBarClass: 'bg-gray-300', textClass: 'text-gray-400' },
  uploading: { progressBarClass: 'bg-[#76b900]', textClass: 'text-[#76b900]' },
  success: { progressBarClass: 'bg-green-500', textClass: 'text-green-500' },
  error: { progressBarClass: 'bg-red-500', textClass: 'text-red-500' },
  cancelled: { progressBarClass: 'bg-orange-500', textClass: 'text-orange-500' },
};

function getUploadStatusLabel(status: UploadFileStatus, progress?: number): string {
  switch (status) {
    case 'uploading': return `${progress ?? 0}%`;
    case 'success': return 'Done';
    case 'error': return 'Failed';
    case 'cancelled': return 'Cancelled';
    default: return 'Pending';
  }
}

function getUploadStatusIcon(status: UploadFileStatus, textClass: string) {
  switch (status) {
    case 'uploading':
      return <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-[#76b900]" />;
    case 'success':
      return <IconCheck size={16} className="flex-shrink-0 text-green-500" />;
    case 'error':
    case 'cancelled':
      return <IconX size={16} className={`flex-shrink-0 ${textClass}`} />;
    default:
      return <div className="h-4 w-4 rounded-full border-2 border-gray-300" />;
  }
}

export interface UploadProgressPopupProps {
  files: UploadProgressFileItem[];
  onCancelAll: () => void;
  onCancelSingle?: (fileId: string) => void;
}

export function UploadProgressPopup({
  files,
  onCancelAll,
  onCancelSingle,
}: Readonly<UploadProgressPopupProps>) {
  const hasActive = files.some(f => f.uploadStatus === 'pending' || f.uploadStatus === 'uploading');
  return (
    <div className={POPUP_OVERLAY_CLASS}>
      <div className={POPUP_CONTAINER_CLASS}>
        <h3 className="mb-4 text-center text-lg font-semibold text-gray-900 dark:text-white">
          Uploading Files...
        </h3>
        {hasActive && (
          <div className="mb-4 flex justify-center">
            <button
              type="button"
              onClick={onCancelAll}
              className="flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-100 dark:border-red-700 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40"
            >
              <IconX size={16} />
              Cancel All
            </button>
          </div>
        )}
        <div className="max-h-96 space-y-3 overflow-y-auto">
          {files.map((fileItem) => {
            const status = fileItem.uploadStatus ?? 'pending';
            const style = UPLOAD_STATUS_STYLE[status];
            const label = getUploadStatusLabel(status, fileItem.uploadProgress);
            return (
              <div key={fileItem.id} className="rounded-lg border border-gray-200 p-3 dark:border-gray-600">
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex items-center gap-2 overflow-hidden">
                    {getUploadStatusIcon(status, style.textClass)}
                    <span className="truncate text-sm text-gray-700 dark:text-gray-300">{fileItem.displayName}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-medium ${style.textClass}`}>{label}</span>
                    {onCancelSingle && (status === 'uploading' || status === 'pending') && (
                      <button
                        type="button"
                        onClick={() => onCancelSingle(fileItem.id)}
                        className="flex-shrink-0 rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-red-500 dark:hover:bg-gray-700"
                        title="Cancel upload"
                      >
                        <IconX size={14} />
                      </button>
                    )}
                  </div>
                </div>
                <div className="h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-700">
                  <div
                    className={`h-1.5 rounded-full transition-all duration-300 ${style.progressBarClass}`}
                    style={{ width: `${fileItem.uploadProgress ?? 0}%` }}
                  />
                </div>
                {fileItem.uploadError && <p className="mt-1 text-xs text-red-500">{fileItem.uploadError}</p>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

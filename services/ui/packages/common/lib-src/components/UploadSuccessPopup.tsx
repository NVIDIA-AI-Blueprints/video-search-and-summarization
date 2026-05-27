import React, { useState, useCallback } from 'react';
import { IconCheck, IconChevronDown, IconCopy, IconX } from '@tabler/icons-react';
import { copyToClipboard } from '../utils/clipboard';

export interface UploadResultItem {
  filename: string;
  result?: Record<string, unknown>;
  error?: string;
  cancelled?: boolean;
}

const POPUP_OVERLAY_CLASS = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
const POPUP_CONTAINER_CLASS =
  'mx-4 w-full max-w-xl rounded-lg border border-gray-200 bg-white p-6 shadow-xl dark:border-neutral-700 dark:bg-neutral-900 dark:shadow-2xl';

function getUploadStatusBanner(allSuccess: boolean, allFailed: boolean, allCancelled: boolean) {
  if (allSuccess) return { bg: 'bg-green-100 dark:bg-green-900', icon: <IconCheck size={24} className="text-green-600 dark:text-green-400" />, title: 'Upload Complete!', titleClass: 'text-green-700 dark:text-green-400' };
  if (allFailed) return { bg: 'bg-red-100 dark:bg-red-900', icon: <IconX size={24} className="text-red-600 dark:text-red-400" />, title: 'Upload Failed', titleClass: 'text-red-700 dark:text-red-400' };
  if (allCancelled) return { bg: 'bg-orange-100 dark:bg-orange-900', icon: <IconX size={24} className="text-orange-600 dark:text-orange-400" />, title: 'Upload Cancelled', titleClass: 'text-orange-700 dark:text-orange-400' };
  return { bg: 'bg-orange-100 dark:bg-orange-900', icon: <IconCheck size={24} className="text-orange-600 dark:text-orange-400" />, title: 'Upload Partially Complete', titleClass: 'text-gray-900 dark:text-white' };
}

function getResultItemStyle(item: UploadResultItem) {
  if (item.result) {
    return {
      borderClass: 'border-green-300 dark:border-green-700',
      bgClass: 'bg-green-50 hover:bg-green-100 dark:bg-green-900/20 dark:hover:bg-green-900/30',
      icon: <IconCheck size={16} className="flex-shrink-0 text-green-500" />,
      textClass: 'text-green-500',
      label: 'Success',
      content: JSON.stringify(item.result, null, 2),
    };
  }
  if (item.cancelled) {
    return {
      borderClass: 'border-orange-300 dark:border-orange-700',
      bgClass: 'bg-orange-50 hover:bg-orange-100 dark:bg-orange-900/20 dark:hover:bg-orange-900/30',
      icon: <IconX size={16} className="flex-shrink-0 text-orange-500" />,
      textClass: 'text-orange-500',
      label: 'Cancelled',
      content: 'Upload was cancelled',
    };
  }
  return {
    borderClass: 'border-red-300 dark:border-red-700',
    bgClass: 'bg-red-50 hover:bg-red-100 dark:bg-red-900/20 dark:hover:bg-red-900/30',
    icon: <IconX size={16} className="flex-shrink-0 text-red-500" />,
    textClass: 'text-red-500',
    label: 'Failed',
    content: `Error: ${item.error}`,
  };
}

export interface UploadSuccessPopupProps {
  results: UploadResultItem[];
  onClose: () => void;
}

export function UploadSuccessPopup({
  results,
  onClose,
}: Readonly<UploadSuccessPopupProps>) {
  const [expandedResults, setExpandedResults] = useState<Set<number>>(new Set());
  const [copiedResultIndex, setCopiedResultIndex] = useState<number | null>(null);

  const toggleResultExpanded = useCallback((index: number) => {
    setExpandedResults(prev => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const handleCopyJson = useCallback(async (text?: string, index?: number) => {
    const content = text ?? (results.length > 0 ? JSON.stringify(results, null, 2) : '');
    if (content) {
      const success = await copyToClipboard(content);
      if (success && index !== undefined) {
        setCopiedResultIndex(index);
        setTimeout(() => setCopiedResultIndex(null), 2000);
      }
    }
  }, [results]);

  const successCount = results.filter(r => r.result).length;
  const cancelledCount = results.filter(r => r.cancelled).length;
  const failedCount = results.length - successCount - cancelledCount;
  const totalCount = results.length;

  const statusConfig = getUploadStatusBanner(
    successCount === totalCount,
    failedCount === totalCount,
    cancelledCount === totalCount,
  );

  return (
    <div className={POPUP_OVERLAY_CLASS}>
      <div className={POPUP_CONTAINER_CLASS}>
        <div className="mb-4 flex justify-center">
          <div className={`flex h-12 w-12 items-center justify-center rounded-full ${statusConfig.bg}`}>
            {statusConfig.icon}
          </div>
        </div>
        <h3 className={`mb-2 text-center text-lg font-semibold ${statusConfig.titleClass}`}>
          {statusConfig.title}
        </h3>
        <p className="mb-4 text-center text-sm text-gray-600 dark:text-gray-400">
          {successCount} / {totalCount} files uploaded successfully
          {cancelledCount > 0 && <span className="ml-1 text-orange-500">({cancelledCount} cancelled)</span>}
          {failedCount > 0 && <span className="ml-1 text-red-500">({failedCount} failed)</span>}
        </p>
        <div className="mb-4 max-h-96 space-y-2 overflow-y-auto">
          {results.map((item, index) => {
            const rs = getResultItemStyle(item);
            return (
              <div
                key={`${item.filename}-${index}`}
                className={`overflow-hidden rounded-lg border ${rs.borderClass}`}
              >
                <button
                  type="button"
                  onClick={() => toggleResultExpanded(index)}
                  className={`flex w-full items-center justify-between p-3 text-left transition-colors ${rs.bgClass}`}
                >
                  <div className="flex items-center gap-2 overflow-hidden">
                    <IconChevronDown size={14} className={`flex-shrink-0 text-gray-400 transition-transform duration-200 ${expandedResults.has(index) ? 'rotate-180' : ''}`} />
                    {rs.icon}
                    <span className="truncate text-sm font-medium text-gray-700 dark:text-gray-300">{item.filename}</span>
                  </div>
                  <span className={`text-xs font-medium ${rs.textClass}`}>{rs.label}</span>
                </button>
                {expandedResults.has(index) && (
                  <div className="border-t border-gray-200 bg-gray-50 p-2 dark:border-gray-600 dark:bg-[#1e1e28]">
                    <div className="relative">
                      <button
                        type="button"
                        onClick={() => handleCopyJson(rs.content, index)}
                        className={`absolute right-1 top-1 rounded p-1 transition-colors ${copiedResultIndex === index ? 'text-green-500' : 'text-gray-400 hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300'}`}
                        title={copiedResultIndex === index ? 'Copied!' : 'Copy JSON'}
                      >
                        {copiedResultIndex === index ? <IconCheck size={14} /> : <IconCopy size={14} />}
                      </button>
                      <pre className="max-h-40 overflow-auto rounded bg-gray-100 p-2 pr-8 text-xs text-gray-800 dark:bg-[#0d0d12] dark:text-gray-300">
                        {rs.content}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <button
          data-testid="upload-close-button"
          type="button"
          onClick={onClose}
          className="w-full rounded-lg bg-[#76b900] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#5a8f00]"
        >
          Close
        </button>
      </div>
    </div>
  );
}

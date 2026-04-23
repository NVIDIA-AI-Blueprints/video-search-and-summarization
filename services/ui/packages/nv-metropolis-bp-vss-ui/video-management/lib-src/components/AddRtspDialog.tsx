// SPDX-License-Identifier: MIT
import React, { useState } from 'react';
import { parseApiError } from '../utils';
import { addRtspStream } from '../rtspStream';

interface AddRtspDialogProps {
  isOpen: boolean;
  agentApiUrl?: string | null;
  onClose: () => void;
  onSuccess?: () => void;
}

export const AddRtspDialog: React.FC<AddRtspDialogProps> = ({
  isOpen,
  agentApiUrl,
  onClose,
  onSuccess,
}) => {
  const [rtspUrl, setRtspUrl] = useState('');
  const [sensorName, setSensorName] = useState('');
  const [userEditedName, setUserEditedName] = useState(false); // Track if user manually edited the name
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Extract the last part of the RTSP URL path as the sensor name
  const extractNameFromUrl = (url: string): string => {
    try {
      // Remove query params and get the path
      const urlWithoutQuery = url.split('?')[0];
      const parts = urlWithoutQuery.split('/');
      // Get the last non-empty part
      const lastPart = parts.filter((p) => p.trim()).pop() || '';
      return lastPart;
    } catch {
      return '';
    }
  };

  const handleRtspUrlChange = (value: string) => {
    setRtspUrl(value);
    if (error) setError(null);
    
    // Auto-fill sensor name if user hasn't manually edited it and URL is valid
    if (!userEditedName && value.trim().startsWith('rtsp://')) {
      const extractedName = extractNameFromUrl(value.trim());
      setSensorName(extractedName);
    }
  };

  const handleSensorNameChange = (value: string) => {
    setSensorName(value);
    setUserEditedName(true); // User has manually edited the name
  };

  const handleClose = () => {
    setRtspUrl('');
    setSensorName('');
    setUserEditedName(false);
    setError(null);
    setIsSubmitting(false);
    onClose();
  };

  const handleSubmit = async () => {
    const trimmed = rtspUrl.trim();

    if (!trimmed) {
      setError('RTSP URL is required.');
      return;
    }
    if (!trimmed.startsWith('rtsp://')) {
      setError('RTSP URL must start with "rtsp://".');
      return;
    }
    if (!agentApiUrl) {
      setError('Agent API URL not configured.');
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      // Single API call to agent - backend handles VST and RTVI services
      await addRtspStream(agentApiUrl, {
        sensorUrl: trimmed,
        ...(sensorName.trim() ? { name: sensorName.trim() } : {}),
      });

      handleClose();
      onSuccess?.();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Error adding RTSP sensor via agent API:', err);
      const friendlyMessage = parseApiError(
        err instanceof Error ? err.message : '',
        'Failed to add RTSP. Please check the URL and try again.'
      );
      setError(friendlyMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/85" onClick={handleClose} />

      {/* Dialog panel */}
      <div
        className="relative z-50 rounded-lg shadow-lg border bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-600 w-[720px] max-w-[calc(100vw-32px)]"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-600">
          <div className="flex items-center gap-3">
            {/* Camera/monitor icon */}
            <svg
              className="text-gray-600 dark:text-gray-300"
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
              <line x1="8" y1="21" x2="16" y2="21" />
              <line x1="12" y1="17" x2="12" y2="21" />
            </svg>
            <span className="text-sm font-medium uppercase tracking-wide text-gray-800 dark:text-gray-200">
              ADD RTSP
            </span>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="text-sm px-3 py-1 rounded text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-5">
          {/* RTSP URL (required) */}
          <div>
            <label className="block text-sm mb-3 text-gray-700 dark:text-gray-300">
              RTSP URL <span className="text-red-500">*</span>
            </label>
            <div className="relative">
              <input
                type="text"
                value={rtspUrl}
                onChange={(e) => handleRtspUrlChange(e.target.value)}
                placeholder="rtsp://cam-warehouse.example.com:554/warehouse/cam01"
                className="w-full rounded px-4 py-3 pr-12 text-sm focus:outline-none focus:ring-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:ring-green-500 focus:border-green-500"
              />
              {/* Info icon */}
              <div className="absolute right-4 top-1/2 -translate-y-1/2">
                <svg
                  className="text-gray-400 dark:text-gray-500"
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="16" x2="12" y2="12" />
                  <line x1="12" y1="8" x2="12.01" y2="8" />
                </svg>
              </div>
            </div>
            <p
              className="text-xs flex items-center gap-2 mt-3 text-gray-500"
            >
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-gray-500 flex-shrink-0" />
              e.g. rtsp://192.168.1.10:554/stream1
            </p>
          </div>

          {/* Sensor Name (optional) */}
          <div>
            <label className="block text-sm mb-3 text-gray-700 dark:text-gray-300">
              Sensor Name <span className="text-xs text-gray-400 dark:text-gray-500">(optional)</span>
            </label>
            <input
              type="text"
              value={sensorName}
              onChange={(e) => handleSensorNameChange(e.target.value)}
              placeholder="e.g. Warehouse Camera 01"
              className="w-full rounded px-4 py-3 text-sm focus:outline-none focus:ring-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:ring-green-500 focus:border-green-500"
            />
          </div>

          {error && (
            <div className="max-h-24 overflow-auto rounded p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
              <p className="text-sm text-red-600 dark:text-red-400 break-words whitespace-pre-wrap">{error}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-600">
          <button
            type="button"
            onClick={handleClose}
            className="px-4 py-2 text-sm font-medium text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={isSubmitting}
            className={`px-4 py-2 text-sm font-medium rounded border ${
              !isSubmitting
                ? 'border-green-500 text-green-500 hover:bg-green-500 hover:text-white'
                : 'border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 cursor-not-allowed'
            }`}
          >
            {isSubmitting ? 'Adding...' : 'Add RTSP'}
          </button>
        </div>
      </div>
    </div>
  );
};

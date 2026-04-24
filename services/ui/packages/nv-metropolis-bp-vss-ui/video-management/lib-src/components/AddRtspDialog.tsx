// SPDX-License-Identifier: MIT
import React, { useState } from 'react';
import { Button, TextInput } from '@nvidia/foundations-react-core';
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

  const extractNameFromUrl = (url: string): string =>
    url.split('?')[0].split('/').filter((p) => p.trim()).pop() ?? '';

  const handleRtspUrlChange = (value: string) => {
    setRtspUrl(value);
    if (error) setError(null);
    // Auto-fill sensor name if user hasn't manually edited it and URL is valid
    if (!userEditedName && value.trim().startsWith('rtsp://')) {
      setSensorName(extractNameFromUrl(value.trim()));
    }
  };

  const handleSensorNameChange = (value: string) => {
    setSensorName(value);
    setUserEditedName(true);
    if (error) setError(null);
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
    const trimmedName = sensorName.trim();
    const validationError =
      !trimmed
        ? 'RTSP URL is required.'
        : !trimmed.startsWith('rtsp://')
          ? 'RTSP URL must start with "rtsp://".'
          : !trimmedName
            ? 'Sensor Name is required.'
            : !agentApiUrl
              ? 'Agent API URL not configured.'
              : null;
    if (validationError) {
      setError(validationError);
      return;
    }

    setError(null);
    setIsSubmitting(true);
    try {
      await addRtspStream(agentApiUrl!, { sensorUrl: trimmed, name: trimmedName });
      handleClose();
      onSuccess?.();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Error adding RTSP sensor via agent API:', err);
      setError(
        parseApiError(
          err instanceof Error ? err.message : '',
          'Failed to add RTSP. Please check the URL and try again.'
        )
      );
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
        data-testid="add-rtsp-dialog"
        className="relative z-50 rounded-lg shadow-lg border bg-white dark:bg-black border-gray-200 dark:border-gray-600 w-[720px] max-w-[calc(100vw-32px)]"
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
            onClick={handleClose}
            aria-label="Close"
            className="p-1.5 rounded transition-colors text-gray-400 hover:text-white hover:bg-neutral-700 dark:text-gray-400 dark:hover:text-white dark:hover:bg-neutral-700"
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
              <TextInput
                value={rtspUrl}
                onValueChange={(val: string) => handleRtspUrlChange(val)}
                placeholder="rtsp://cam-warehouse.example.com:554/warehouse/cam01"
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

          {/* Sensor Name (required) */}
          <div>
            <label className="block text-sm mb-3 text-gray-700 dark:text-gray-300" htmlFor="add-rtsp-sensor-name">
              Sensor Name <span className="text-red-500" aria-hidden="true">*</span>
            </label>
            <TextInput
              id="add-rtsp-sensor-name"
              value={sensorName}
              onValueChange={(val: string) => handleSensorNameChange(val)}
              placeholder="e.g. Warehouse Camera 01"
              required
              aria-required="true"
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
          <Button
            kind="secondary"
            onClick={handleClose}
          >
            Cancel
          </Button>
          <Button
            kind="primary"
            onClick={handleSubmit}
            disabled={isSubmitting}
          >
            {isSubmitting ? 'Adding...' : 'Add RTSP'}
          </Button>
        </div>
      </div>
    </div>
  );
};

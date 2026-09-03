// SPDX-License-Identifier: MIT
import React, { useEffect, useRef, useState } from 'react';
import { Button, TextInput } from '@nvidia/foundations-react-core';
import { parseApiError } from '../utils';
import { addRtspStream } from '../rtspStream';

const POPUP_OVERLAY_VIEWPORT =
  'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
/** Covers only the parent `relative` region (e.g. Video Management main pane), not the whole browser window */
const POPUP_OVERLAY_CONTAINED =
  'absolute inset-0 z-40 flex items-center justify-center bg-black/50';

interface AddRtspDialogProps {
  isOpen: boolean;
  vstApiUrl?: string | null;
  onClose: () => void;
  onSuccess?: () => void;
  /**
   * Polls VST until it lists the sensor the add call returned. The dialog stays
   * open until this resolves, so it never closes on a stream the grid has yet
   * to receive.
   */
  onAwaitStream?: (sensorId: string) => Promise<{ found: boolean }>;
  /** `contained` = overlay only the nearest positioned ancestor (Video Management pane). Default `viewport` = full window. */
  overlay?: 'viewport' | 'contained';
}

type SubmitPhase = 'idle' | 'adding' | 'confirming';

/** A sensor VST took, keyed by the RTSP URL it was created from. */
interface AcceptedSensor {
  sensorId: string;
  sensorUrl: string;
}

/** The message to show for bad input, or `null` when it is good. */
function validateRtspInput(url: string, name: string): string | null {
  if (!url) return 'RTSP URL is required.';
  if (!url.startsWith('rtsp://')) return 'RTSP URL must start with "rtsp://".';
  if (!name) return 'Sensor Name is required.';
  return null;
}

function getSubmitLabel(phase: SubmitPhase, canResumeWait: boolean): string {
  switch (phase) {
    case 'adding':
      return 'Adding...';
    case 'confirming':
      return 'Waiting for VST...';
    default:
      return canResumeWait ? 'Retry' : 'Add RTSP';
  }
}

export const AddRtspDialog: React.FC<AddRtspDialogProps> = ({
  isOpen,
  vstApiUrl,
  onClose,
  onSuccess,
  onAwaitStream,
  overlay = 'viewport',
}) => {
  const [rtspUrl, setRtspUrl] = useState('');
  const [sensorName, setSensorName] = useState('');
  const [userEditedName, setUserEditedName] = useState(false); // Track if user manually edited the name
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<SubmitPhase>('idle');
  // Sensor VST accepted but has not listed yet. A retry must only resume the
  // wait: re-sending the add would be rejected as a duplicate URL, which reads
  // as a failure for a sensor that in fact exists.
  const [accepted, setAccepted] = useState<AcceptedSensor | null>(null);
  // Bumped on close and on each submit, so an attempt whose wait outlives the
  // dialog — or a superseded one — stops writing to it.
  const attemptRef = useRef(0);

  const isSubmitting = phase !== 'idle';

  const trimmedUrl = rtspUrl.trim();
  const trimmedName = sensorName.trim();

  // VST keys sensors on the RTSP URL. A listing timeout leaves the fields
  // editable, but a name-only edit is still the same sensor — re-POSTing the
  // URL would come back as a duplicate. An edited URL is a different stream
  // and has to be added, not confirmed against the previous sensorId.
  const acceptedSensorId =
    accepted && accepted.sensorUrl === trimmedUrl ? accepted.sensorId : null;

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
    attemptRef.current += 1;
    setRtspUrl('');
    setSensorName('');
    setUserEditedName(false);
    setError(null);
    setPhase('idle');
    setAccepted(null);
    onClose();
  };

  // Closing mid-wait is safe — VST already holds the sensor and the poll keeps
  // refreshing the grid — but closing mid-add would drop the sensorId the wait
  // needs, leaving an added stream with nothing watching for it.
  const canClose = phase !== 'adding';
  const handleDismiss = () => {
    if (canClose) handleClose();
  };

  useEffect(() => {
    if (!isOpen || !canClose) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
    // handleClose is rebuilt every render; listing it would resubscribe the
    // listener on each one, and all it touches is state and the onClose prop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, canClose]);

  const handleSubmit = async () => {
    const validationError = validateRtspInput(trimmedUrl, trimmedName);
    if (validationError) {
      setError(validationError);
      return;
    }
    if (!vstApiUrl) {
      setError('VST API URL not configured.');
      return;
    }

    attemptRef.current += 1;
    const attempt = attemptRef.current;
    const isCurrentAttempt = () => attemptRef.current === attempt;

    setError(null);
    setPhase(acceptedSensorId ? 'confirming' : 'adding');
    try {
      let sensorId = acceptedSensorId;
      if (!sensorId) {
        const result = await addRtspStream(vstApiUrl, { sensorUrl: trimmedUrl, name: trimmedName });
        if (!isCurrentAttempt()) return;
        sensorId = result.sensorId;
        setAccepted({ sensorId, sensorUrl: trimmedUrl });
        setPhase('confirming');
      }

      // VST returns the sensorId before the stream shows up in its listing.
      // Closing here is what left the new camera out of the grid until the user
      // refreshed or switched tabs.
      const { found } = (await onAwaitStream?.(sensorId)) ?? { found: true };
      if (!isCurrentAttempt()) return;

      if (!found) {
        setError(
          'VST accepted the stream but has not listed it yet. Retry to check again.'
        );
        return;
      }

      handleClose();
      onSuccess?.();
    } catch (err) {
      if (!isCurrentAttempt()) return;
      // eslint-disable-next-line no-console
      console.error('Error adding RTSP sensor via VST API:', err);
      setError(
        parseApiError(
          err instanceof Error ? err.message : '',
          'Failed to add RTSP. Please check the URL and try again.'
        )
      );
    } finally {
      if (isCurrentAttempt()) setPhase('idle');
    }
  };

  if (!isOpen) return null;

  const overlayClass =
    overlay === 'contained' ? POPUP_OVERLAY_CONTAINED : POPUP_OVERLAY_VIEWPORT;

  const submitLabel = getSubmitLabel(phase, acceptedSensorId !== null);

  return (
    // Backdrop: a click target only, with Escape wired up above for the keyboard
    <div className={overlayClass} role="presentation" onClick={handleDismiss}>
      <div
        data-testid="add-rtsp-dialog"
        role="dialog"
        aria-modal="true"
        className="relative z-50 mx-4 w-full max-w-[720px] rounded-lg border border-gray-200 bg-white shadow-lg dark:border-gray-600 dark:bg-black"
        onClick={(e) => e.stopPropagation()}
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
            onClick={handleDismiss}
            disabled={!canClose}
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
                disabled={isSubmitting}
              />
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
              disabled={isSubmitting}
            />
          </div>

          {phase === 'confirming' && (
            <p
              data-testid="add-rtsp-confirming"
              className="text-sm text-gray-500 dark:text-gray-400"
            >
              Stream added. Waiting for VST to list it…
            </p>
          )}

          {error && (
            <div
              role="alert"
              data-testid="add-rtsp-error"
              className="max-h-24 overflow-auto rounded p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800"
            >
              <p className="text-sm text-red-600 dark:text-red-400 break-words whitespace-pre-wrap">{error}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-600">
          <Button
            kind="secondary"
            onClick={handleDismiss}
            disabled={!canClose}
          >
            Cancel
          </Button>
          <Button
            kind="primary"
            onClick={handleSubmit}
            disabled={isSubmitting}
          >
            {submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
};

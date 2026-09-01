// SPDX-License-Identifier: MIT
/**
 * Human-in-the-loop prompt.
 *
 * The toolkit drove this from a WebSocket `system_interaction_message`. The BYO
 * contract is HTTP/SSE only, so it is driven by an `interaction_data:` frame
 * instead (see `sse.ts`). No adapter emits one today; the surface exists so a
 * harness that needs a mid-turn answer or an OAuth consent has somewhere to put
 * it, rather than the capability disappearing with the WebSocket transport.
 */
import { IconExternalLink, IconX } from '@tabler/icons-react';
import React, { useState } from 'react';

import type { InteractionRequest } from './sse';

export interface InteractionModalProps {
  request: InteractionRequest | null;
  onClose: () => void;
  onSubmit: (response: string) => void;
  /** Mirrors NEXT_PUBLIC_INTERACTION_MODAL_CANCEL_ENABLED. */
  showCancelButton?: boolean;
}

export const InteractionModal: React.FC<InteractionModalProps> = ({
  request,
  onClose,
  onSubmit,
  showCancelButton = true,
}) => {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');

  if (!request) return null;

  const submit = (response: string) => {
    if (!response.trim()) {
      setError('A response is required.');
      return;
    }
    setError('');
    onSubmit(response);
    onClose();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Agent request"
      className="fixed inset-0 z-[150] flex items-center justify-center bg-black/60 p-4"
    >
      <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-4 shadow-xl dark:border-gray-700 dark:bg-black">
        <div className="mb-3 flex items-start justify-between gap-4">
          <p className="text-sm text-gray-800 dark:text-gray-100">{request.prompt}</p>
          {showCancelButton && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Dismiss"
              className="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-neutral-800"
            >
              <IconX size={16} />
            </button>
          )}
        </div>

        {request.inputType === 'oauth' && request.url ? (
          <a
            href={request.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => {
              onSubmit('consented');
              onClose();
            }}
            className="inline-flex items-center gap-2 rounded-md bg-[#76b900] px-3 py-2 text-sm font-medium text-black"
          >
            <IconExternalLink size={16} /> Continue to sign in
          </a>
        ) : request.inputType === 'binary' || request.options?.length ? (
          <div className="flex flex-wrap gap-2">
            {(request.options ?? ['Yes', 'No']).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => submit(option)}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm hover:bg-[#76b900] hover:text-black dark:border-gray-600"
              >
                {option}
              </button>
            ))}
          </div>
        ) : (
          <>
            <textarea
              className="w-full rounded-md border border-gray-300 bg-transparent p-2 text-sm outline-none focus:ring-1 focus:ring-[#76b900] dark:border-gray-600"
              rows={3}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              aria-label="Your response"
            />
            <div className="mt-3 flex justify-end gap-2">
              {showCancelButton && (
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-600"
                >
                  Cancel
                </button>
              )}
              <button
                type="button"
                onClick={() => submit(value)}
                className="rounded-md bg-[#76b900] px-3 py-1.5 text-sm font-medium text-black"
              >
                Send
              </button>
            </div>
          </>
        )}

        {error ? <p className="mt-2 text-xs text-red-500">{error}</p> : null}
      </div>
    </div>
  );
};

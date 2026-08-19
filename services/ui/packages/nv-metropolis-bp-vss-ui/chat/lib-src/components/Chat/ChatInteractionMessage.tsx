'use client';
// SPDX-License-Identifier: MIT
/**
 * Human-in-the-loop prompt.
 *
 * An agent pauses mid-run when it needs a decision — a confirmation before a
 * destructive action, OAuth consent, a choice between candidates. The run is
 * blocked until this resolves, so the modal is not dismissible by clicking
 * away: losing the prompt would strand the agent with no way to reopen it.
 */
import React, { useState } from 'react';
import { IconInfoCircle, IconX } from '@tabler/icons-react';
import ReactMarkdown from 'react-markdown';
import { toast } from 'react-hot-toast';

export interface InteractionOption {
  id: string;
  label: string;
  value: string;
  description?: string;
}

export interface InteractionContent {
  input_type?: 'text' | 'binary_choice' | 'radio' | 'notification' | string;
  text?: string;
  placeholder?: string;
  required?: boolean;
  options?: InteractionOption[];
  /** Seconds before the prompt expires; the agent stops waiting. */
  timeout?: number | null;
  error?: string;
  [key: string]: unknown;
}

export interface InteractionMessage {
  content?: InteractionContent;
  [key: string]: unknown;
}

export interface InteractionSubmission {
  interactionMessage: InteractionMessage;
  userResponse: string;
}

export interface InteractionModalProps {
  isOpen: boolean;
  interactionMessage: InteractionMessage | null | undefined;
  onClose: () => void;
  onSubmit: (submission: InteractionSubmission) => void;
  /** Hidden where the agent offers no way to decline. */
  showCancelButton?: boolean;
}

const SUBMIT_CLASS = 'px-4 py-2 bg-[#76b900] text-white rounded hover:bg-[#5a8c00]';
const CANCEL_CLASS =
  'px-4 py-2 bg-neutral-200 dark:bg-neutral-800 text-gray-800 dark:text-gray-100 rounded hover:bg-neutral-300 dark:hover:bg-neutral-700';

export const InteractionModal: React.FC<InteractionModalProps> = ({
  isOpen,
  interactionMessage,
  onClose,
  onSubmit,
  showCancelButton = true,
}) => {
  const [userInput, setUserInput] = useState('');
  const [error, setError] = useState('');

  if (!isOpen || !interactionMessage) return null;

  const content: InteractionContent = interactionMessage.content ?? {};

  const submit = (response: string) => {
    setError('');
    onSubmit({ interactionMessage, userResponse: response });
    onClose();
  };

  const handleTextSubmit = () => {
    if (content.required && !userInput.trim()) {
      setError('This field is required.');
      return;
    }
    submit(userInput);
  };

  const handleChoiceSubmit = (option = '') => {
    if (content.required && !option) {
      setError('Please select an option.');
      return;
    }
    submit(option);
  };

  const handleRadioSubmit = () => {
    if (content.required && !userInput) {
      setError('Please select an option.');
      return;
    }
    submit(userInput);
  };

  // Notifications carry no decision, so they surface as a toast rather than
  // interrupting whatever the user is reading. Pinned open (duration Infinity)
  // with a fixed id, so a repeat replaces rather than stacks.
  if (content.input_type === 'notification') {
    toast.custom(
      (t: any) => (
        <div
          className={`flex gap-2 items-center justify-evenly bg-white text-gray-800 dark:bg-black dark:text-gray-100 dark:border dark:border-neutral-800 px-4 py-2 rounded-lg shadow-md ${
            t.visible ? 'animate-fade-in' : 'animate-fade-out'
          }`}
        >
          <IconInfoCircle size={16} className="text-[#76b900]" />
          <span>{content.text || 'No content found for this notification'}</span>
          <button
            onClick={() => toast.dismiss(t.id)}
            className="text-gray-800 dark:text-gray-100 ml-3 hover:bg-neutral-200 dark:hover:bg-neutral-800 rounded-full p-1"
          >
            <IconX size={12} />
          </button>
        </div>
      ),
      { position: 'top-right', duration: Infinity, id: 'notification-toast' },
    );

    return null;
  }

  const errorMessage = error ? (
    <p className="text-red-500 text-sm mt-2">{error}</p>
  ) : null;

  const cancelButton = showCancelButton ? (
    <button data-testid="hitl-modal-cancel" className={CANCEL_CLASS} onClick={onClose}>
      Cancel
    </button>
  ) : null;

  return (
    // No backdrop onClick: the agent is blocked on this answer.
    <div
      data-testid="hitl-modal"
      className="fixed inset-0 flex items-center justify-center bg-black bg-opacity-50 z-50"
    >
      <div className="bg-white dark:bg-black dark:border dark:border-neutral-800 p-6 rounded-lg shadow-lg sm:w-[75%] h-auto">
        <div
          data-testid="hitl-modal-prompt"
          className="mb-4 text-gray-800 dark:text-white prose prose-base dark:prose-invert max-w-none prose-headings:font-semibold prose-p:my-1 max-h-[60vh] overflow-y-auto"
        >
          {/* Agents format prompts as markdown — lists of candidates, code. */}
          <ReactMarkdown>{content.text || ''}</ReactMarkdown>
        </div>

        {content.input_type === 'text' && (
          <div>
            <textarea
              data-testid="hitl-modal-textarea"
              className="w-full border border-gray-300 dark:border-neutral-700 p-2 rounded text-black dark:text-white bg-white dark:bg-neutral-900 placeholder-gray-500 dark:placeholder-neutral-500 focus:outline-none focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]"
              placeholder={content.placeholder}
              value={userInput}
              onChange={(event) => setUserInput(event.target.value)}
            />
            {errorMessage}
            <div className="flex justify-end mt-4 space-x-2">
              {cancelButton}
              <button
                data-testid="hitl-modal-submit"
                className={SUBMIT_CLASS}
                onClick={handleTextSubmit}
              >
                Submit
              </button>
            </div>
          </div>
        )}

        {content.input_type === 'binary_choice' && (
          <div>
            <div className="flex justify-end mt-4 space-x-2">
              {(content.options ?? []).map((option) => (
                <button
                  key={option.id}
                  // The affirmative option is styled as primary; which one that
                  // is comes from the agent's value, not the option order.
                  className={`px-4 py-2 rounded text-white ${
                    option?.value?.includes('continue')
                      ? 'bg-[#76b900] hover:bg-[#5a8c00]'
                      : 'bg-neutral-700 hover:bg-neutral-600 dark:bg-neutral-800 dark:hover:bg-neutral-700'
                  }`}
                  onClick={() => handleChoiceSubmit(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            {errorMessage}
          </div>
        )}

        {content.input_type === 'radio' && (
          <div>
            <div className="space-y-3">
              {(content.options ?? []).map((option) => (
                <div key={option.id} className="flex items-center">
                  <input
                    type="radio"
                    id={option.id}
                    name="notification-method"
                    value={option.value}
                    checked={userInput === option.value}
                    onChange={() => setUserInput(option.value)}
                    className="mr-2 text-[#76b900] focus:ring-[#76b900]"
                  />
                  <label htmlFor={option.id} className="flex flex-col">
                    <span className="text-gray-800 dark:text-white">{option.label}</span>
                  </label>
                </div>
              ))}
            </div>
            {errorMessage}
            <div className="flex justify-end mt-4 space-x-2">
              {cancelButton}
              <button
                data-testid="hitl-modal-submit"
                className={SUBMIT_CLASS}
                onClick={handleRadioSubmit}
              >
                Submit
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

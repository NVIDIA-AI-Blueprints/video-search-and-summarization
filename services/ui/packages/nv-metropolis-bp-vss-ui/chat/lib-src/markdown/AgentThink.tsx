// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
/**
 * `<agent-think>` / `<agent-think-step>` renderers for agent markdown.
 *
 * The backend emits reasoning traces as these two custom tags. Formatting
 * rules the backend must follow (unchanged from the toolkit, because the
 * prompts that produce them are unchanged):
 *
 *   - blank line before `<agent-think>` and after `</agent-think>`, so the
 *     markdown parser treats them as block-level rather than wrapping them in
 *     a `<p>`;
 *   - no blank line straight after the opening tag, or the content becomes a
 *     sibling instead of a child;
 *   - `<agent-think-step>` tags on their own lines.
 *
 * `data-streaming="true"` on the tag marks the trace still being written.
 */
import { IconChevronDown, IconChevronUp, IconLoader2 } from '@tabler/icons-react';
import React, { useEffect, useState } from 'react';

/** How long a finished trace stays open before collapsing itself. */
const AUTO_COLLAPSE_MS = 3000;

interface ThinkProps {
  children: React.ReactNode;
  title?: string;
  'data-streaming'?: string;
  messageIsStreaming?: boolean;
}

export const AgentThink: React.FC<ThinkProps> = ({
  children,
  title,
  messageIsStreaming,
  ...props
}) => {
  const isStreaming = props['data-streaming'] === 'true' && !!messageIsStreaming;
  const [isOpen, setIsOpen] = useState(false);
  const [wasStreaming, setWasStreaming] = useState(false);

  // Open while the trace is being written, then collapse a beat after it
  // finishes so a long trace does not bury the answer under it.
  useEffect(() => {
    if (isStreaming) {
      setIsOpen(true);
      setWasStreaming(true);
      return;
    }
    if (wasStreaming) {
      const timer = setTimeout(() => {
        setIsOpen(false);
        setWasStreaming(false);
      }, AUTO_COLLAPSE_MS);
      return () => clearTimeout(timer);
    }
    return;
  }, [isStreaming, wasStreaming]);

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-neutral-300 bg-neutral-100 shadow-sm dark:border-neutral-700 dark:bg-neutral-900">
      <button
        type="button"
        aria-expanded={isOpen}
        disabled={isStreaming}
        onClick={() => !isStreaming && setIsOpen((prev) => !prev)}
        className={`flex w-full items-center justify-between p-3 text-left transition-colors ${
          isStreaming ? 'cursor-default' : 'hover:bg-neutral-200 dark:hover:bg-neutral-800'
        }`}
      >
        <span className="flex items-center gap-2">
          {isStreaming && (
            <IconLoader2 size={20} className="flex-shrink-0 animate-spin text-[#76b900]" />
          )}
          <span className="font-medium text-gray-700 dark:text-gray-200">
            <strong>Reasoning Trace</strong>
            {title ? ` - ${title}` : ''}
          </span>
        </span>
        {!isStreaming &&
          (isOpen ? (
            <IconChevronUp size={20} className="text-gray-600 dark:text-gray-300" />
          ) : (
            <IconChevronDown size={20} className="text-gray-600 dark:text-gray-300" />
          ))}
      </button>

      {isOpen && (
        <div className="border-t border-neutral-300 px-4 pb-4 pt-2 text-gray-700 dark:border-zinc-600 dark:text-gray-300">
          <div className="whitespace-pre-wrap break-words">{children}</div>
        </div>
      )}
    </div>
  );
};

export const AgentThinkStep: React.FC<ThinkProps> = ({
  children,
  title,
  messageIsStreaming,
  ...props
}) => {
  const isStreaming = props['data-streaming'] === 'true' && !!messageIsStreaming;
  // Steps stay open once written — unlike the parent trace, a finished step is
  // the part a user goes back to read.
  const [isOpen, setIsOpen] = useState(true);

  useEffect(() => {
    if (isStreaming) setIsOpen(true);
  }, [isStreaming]);

  return (
    <div className="relative my-2 pl-6">
      {/* Storyline rail: a head circle at the step, a line down to the next. */}
      <div className="absolute bottom-0 left-0 top-0 flex flex-col items-center">
        <div className="mt-2 h-3 w-3 flex-shrink-0 rounded-full bg-gray-500 dark:bg-gray-400" />
        <div className="w-1 flex-1 bg-gray-400 dark:bg-gray-500" />
      </div>

      <div className="overflow-hidden rounded-md bg-gray-100/50 shadow-sm dark:bg-neutral-800/50">
        <button
          type="button"
          aria-expanded={isOpen}
          disabled={isStreaming}
          onClick={() => !isStreaming && setIsOpen((prev) => !prev)}
          className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left transition-colors ${
            isStreaming ? 'cursor-default' : 'hover:bg-gray-200/50 dark:hover:bg-neutral-700/50'
          }`}
        >
          <span className="flex items-center gap-2">
            {isStreaming && (
              <IconLoader2 size={16} className="flex-shrink-0 animate-spin text-[#76b900]" />
            )}
            <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
              <strong>Step</strong>
              {title ? ` - ${title}` : ''}
            </span>
          </span>
          {!isStreaming &&
            (isOpen ? (
              <IconChevronUp size={16} className="text-gray-600 dark:text-gray-300" />
            ) : (
              <IconChevronDown size={16} className="text-gray-600 dark:text-gray-300" />
            ))}
        </button>

        {isOpen && (
          <div className="border-t border-gray-300 px-3 pb-2 pt-1 text-sm text-gray-700 dark:border-zinc-500 dark:text-gray-300">
            <div className="whitespace-pre-wrap break-words">{children}</div>
          </div>
        )}
      </div>
    </div>
  );
};

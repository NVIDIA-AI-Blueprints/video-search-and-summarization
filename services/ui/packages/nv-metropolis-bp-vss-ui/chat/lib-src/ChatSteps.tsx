// SPDX-License-Identifier: MIT
/**
 * Intermediate-step disclosure.
 *
 * The toolkit built this by serialising steps into a `<details>` cascade,
 * embedding it in the assistant message's markdown, and re-parsing it — which
 * means a half-written step is a half-written HTML tag, and the repairs in
 * `markdown/streaming.ts` exist mostly to paper over that. Here the tree stays
 * structured all the way to render, so a step that is still arriving is just a
 * node with `status: 'in_progress'`.
 */
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react';
import React, { useState } from 'react';

import { buildStepTree } from './sse';
import type { ChatStep } from './types';

const STATUS_DOT: Record<ChatStep['status'], string> = {
  in_progress: 'bg-[#76b900] animate-pulse',
  complete: 'bg-gray-400 dark:bg-gray-500',
  error: 'bg-red-500',
};

const StepNode: React.FC<{ step: ChatStep; defaultOpen: boolean }> = ({ step, defaultOpen }) => {
  const [manual, setManual] = useState<boolean | null>(null);
  const hasDetail = !!step.payload || !!step.children?.length;
  const open = manual ?? defaultOpen;

  return (
    <li className="relative">
      <div className="flex items-start gap-2">
        <span className={`mt-[7px] h-2 w-2 flex-shrink-0 rounded-full ${STATUS_DOT[step.status]}`} />
        {hasDetail ? (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setManual(!open)}
            className="flex items-center gap-1 text-left text-sm text-gray-700 hover:text-[#76b900] dark:text-gray-300"
          >
            {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
            <span>{step.name}</span>
          </button>
        ) : (
          <span className="pl-[18px] text-sm text-gray-700 dark:text-gray-300">{step.name}</span>
        )}
      </div>

      {open && (
        <div className="ml-[5px] border-l border-gray-300 pl-3 dark:border-gray-600">
          {step.payload ? (
            <pre className="my-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-gray-100 p-2 text-xs text-gray-800 dark:bg-neutral-800 dark:text-gray-200">
              {step.payload}
            </pre>
          ) : null}
          {step.children?.length ? (
            <ul className="flex flex-col gap-1">
              {step.children.map((child) => (
                <StepNode key={child.id} step={child} defaultOpen={defaultOpen} />
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </li>
  );
};

export interface ChatStepsProps {
  steps: ChatStep[];
  /** True while the owning message is still streaming. */
  streaming?: boolean;
  /** Keep the list open once the answer lands (NEXT_PUBLIC_ENABLE… default). */
  expandByDefault?: boolean;
}

export const ChatSteps: React.FC<ChatStepsProps> = ({ steps, streaming, expandByDefault }) => {
  // Open while the agent is working so progress is visible the way the toolkit
  // showed it; collapses once the answer lands so finished steps do not bury
  // it. An explicit click wins over that default either way.
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? (!!streaming || !!expandByDefault);
  if (!steps.length) return null;

  const tree = buildStepTree(steps);

  return (
    <div className="mb-2 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 dark:border-neutral-700 dark:bg-neutral-900/60">
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left text-sm text-gray-600 dark:text-gray-300"
        onClick={() => setManual(!open)}
        aria-expanded={open}
      >
        {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
        <span>
          Intermediate steps ({steps.length}){streaming ? ' — running' : ''}
        </span>
      </button>
      {open && (
        <ul className="mt-2 flex flex-col gap-1">
          {tree.map((step) => (
            <StepNode key={step.id} step={step} defaultOpen={!!streaming} />
          ))}
        </ul>
      )}
    </div>
  );
};

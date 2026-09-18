// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Intermediate-step disclosure.
 *
 * The tree stays structured all the way to render, so a step that is still
 * arriving is just a node with `status: 'in_progress'`. Serialising steps into
 * markdown HTML and re-parsing them would turn a half-written step into a
 * half-written HTML tag.
 */
import { IconChevronDown, IconChevronRight, IconLoader } from '@tabler/icons-react';
import React, { useState } from 'react';

import { buildDisplayStepTree, countDisplaySteps } from './sse';
import type { ChatStep } from './types';

const STATUS_DOT: Record<ChatStep['status'], string> = {
  in_progress: 'bg-[#76b900] animate-pulse',
  complete: 'bg-gray-400 dark:bg-gray-500',
  error: 'bg-red-500',
};

const Spinner: React.FC<{ size: number; label: string }> = ({ size, label }) => (
  <IconLoader
    size={size}
    role="status"
    aria-label={label}
    className="animate-spin text-[#76b900]"
  />
);

/**
 * Whether a step is the one the agent is working on right now.
 *
 * Status alone does not answer this. Backends settle a step by sending a
 * closing frame for its id, and several never do — the agent API's
 * `Reasoning` step, for instance, is emitted `in_progress` on every delta and
 * gets no completion of its own. Spinning on status alone leaves those
 * animating for the rest of the turn, long after work moved on.
 *
 * So take the stream's trailing edge as the answer, the way the pre-#2001 UI
 * did with `isLast = isLastInArray && isParentLast`: a step is running while
 * it is the newest among its siblings and nothing below it has finished the
 * subtree off. A step with a younger sibling has been superseded, and a step
 * whose children have all settled is no longer waiting on them.
 */
function isStepRunning(step: ChatStep, isLastSibling: boolean): boolean {
  if (!isLastSibling || step.status !== 'in_progress') return false;
  const children = step.children ?? [];
  if (!children.length) return true;
  return children.some((child, i) => isStepRunning(child, i === children.length - 1));
}

const StepNode: React.FC<{
  step: ChatStep;
  defaultOpen: boolean;
  /** False once an ancestor is no longer on the trailing edge. */
  running: boolean;
}> = ({ step, defaultOpen, running }) => {
  const [manual, setManual] = useState<boolean | null>(null);
  const hasDetail = !!step.payload || !!step.children?.length;
  const open = manual ?? defaultOpen;

  return (
    <li className="relative" data-status={step.status}>
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
        {running ? (
          <span className="ml-auto mt-[3px] flex-shrink-0">
            <Spinner size={16} label={`${step.name} running`} />
          </span>
        ) : null}
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
              {step.children.map((child, i) => (
                <StepNode
                  key={child.id}
                  step={child}
                  defaultOpen={defaultOpen}
                  running={running && isStepRunning(child, i === step.children!.length - 1)}
                />
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
  // Open while the agent is working so progress is visible; collapse once the
  // answer lands so finished steps do not bury it. An explicit click wins.
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? (!!streaming || !!expandByDefault);
  if (!steps.length) return null;

  const tree = buildDisplayStepTree(steps);
  if (!tree.length) return null;
  const displayedStepCount = countDisplaySteps(tree);

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
          Intermediate steps ({displayedStepCount}){streaming ? ' — running' : ''}
        </span>
        {/* Collapsed hides every per-step spinner, so the header carries one too. */}
        {streaming ? (
          <span className="ml-auto flex-shrink-0">
            <Spinner size={16} label="Intermediate steps running" />
          </span>
        ) : null}
      </button>
      {open && (
        <ul className="mt-2 flex flex-col gap-1">
          {tree.map((step, i) => (
            <StepNode
              key={step.id}
              step={step}
              defaultOpen={false}
              // A step only runs while the turn does: a cancelled stream
              // leaves its last step `in_progress` for good.
              running={!!streaming && isStepRunning(step, i === tree.length - 1)}
            />
          ))}
        </ul>
      )}
    </div>
  );
};

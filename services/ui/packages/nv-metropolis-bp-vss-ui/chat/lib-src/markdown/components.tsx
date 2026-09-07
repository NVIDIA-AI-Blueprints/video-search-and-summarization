// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
/**
 * react-markdown component overrides for agent answers.
 *
 * Custom element names (`chart`, `incidents`, `agent-think`, …) reach this map
 * because the renderer runs `rehype-raw`, which keeps raw HTML in the tree
 * instead of escaping it. Without rehype-raw an answer containing `<video>`
 * shows up as literal text.
 *
 * Everything is memoised on `children`: an assistant message re-renders once
 * per streamed token, and re-mounting an `<img>` or a highlighted code block
 * that often is what makes a long answer crawl.
 */
import isEqual from 'lodash/isEqual';
import React, { memo } from 'react';

import { AgentThink, AgentThinkStep } from './AgentThink';
import Chart from './Chart';
import { CodeBlock } from './CodeBlock';
import { CustomIncidents } from './Incidents';
import { MarkdownImage, MarkdownVideo } from './Media';

const sameChildren = (prev: any, next: any) => isEqual(prev.children, next.children);

/**
 * Large `src` values (base64 frames) make a full compare expensive, so match
 * on length plus both ends — enough to distinguish two different frames.
 */
function sameSrc(prev: any, next: any) {
  const a: string = prev.src ?? '';
  const b: string = next.src ?? '';
  if (a.length > 1000 || b.length > 1000) {
    return (
      a.length === b.length && a.slice(0, 100) === b.slice(0, 100) && a.slice(-100) === b.slice(-100)
    );
  }
  return a === b && prev.alt === next.alt;
}

/** Custom tags carry their payload as a JSON string child. */
function parseJsonChild(children: unknown): any | null {
  let raw: unknown = children;
  if (Array.isArray(children)) raw = children[0];
  if (raw && typeof raw === 'object') return raw;
  if (typeof raw !== 'string') return null;
  const cleaned = raw.replaceAll('\n', '').trim();
  if (!cleaned.startsWith('{')) return null;
  try {
    return JSON.parse(cleaned);
  } catch {
    return null;
  }
}

export interface MarkdownComponentOptions {
  /** True while the message these components belong to is still streaming. */
  messageIsStreaming?: boolean;
  onDownloadError?: (message: string) => void;
}

export function getMarkdownComponents({
  messageIsStreaming = false,
  onDownloadError,
}: MarkdownComponentOptions = {}) {
  return {
    code: memo(({ className, children, ...props }: any) => {
      const match = /language-(\w+)/.exec(className || '');
      const value = String(children).replace(/\n$/, '');
      // Inline spans have no language class and no newline; rendering them as a
      // full code block would break a sentence into a card.
      if (!match && !value.includes('\n')) {
        return (
          <code
            className="rounded bg-gray-200 px-1 py-0.5 font-mono text-[0.9em] dark:bg-neutral-800"
            {...props}
          >
            {children}
          </code>
        );
      }
      return <CodeBlock language={match?.[1] ?? ''} value={value} />;
    }, sameChildren),

    chart: memo(({ children }: any) => {
      const payload = parseJsonChild(children);
      return payload ? <Chart payload={payload} /> : null;
    }, sameChildren),

    incidents: memo(({ children }: any) => {
      const payload = parseJsonChild(children);
      if (!payload || !Array.isArray(payload.incidents)) return null;
      return <CustomIncidents payload={payload} />;
    }, sameChildren),

    table: memo(
      ({ children }: any) => (
        <div className="w-full overflow-x-auto">
          <table className="border-collapse border border-black px-3 py-1 dark:border-white">
            {children}
          </table>
        </div>
      ),
      sameChildren,
    ),

    th: memo(
      ({ children }: any) => (
        <th className="break-words border border-black bg-gray-500 px-3 py-1 text-white dark:border-white dark:bg-neutral-800">
          {children}
        </th>
      ),
      sameChildren,
    ),

    td: memo(
      ({ children }: any) => (
        <td className="break-words border border-black px-3 py-1 dark:border-white">{children}</td>
      ),
      sameChildren,
    ),

    a: memo(({ href, children, ...props }: any) => {
      const isPdf = typeof href === 'string' && href.toLowerCase().endsWith('.pdf');
      return (
        <a
          href={href}
          className="text-[#76b900] no-underline hover:underline"
          // Agent answers link out to VST and report files; without noopener the
          // opened page gets a handle on this window.
          target="_blank"
          rel="noopener noreferrer"
          {...(isPdf ? { 'data-testid': 'pdf-report-link' } : {})}
          {...props}
        >
          {children}
        </a>
      );
    }, sameChildren),

    li: memo(
      ({ children, ordered: _ordered, ...props }: any) => (
        <li className="mb-1 list-disc leading-[1.35rem]" {...props}>
          {children}
        </li>
      ),
      sameChildren,
    ),

    sup: memo(({ children, ...props }: any) => {
      // Citation markers arrive as `<sup>1</sup>` but also as stray commas
      // between them; rendering those produces floating punctuation.
      const text = Array.isArray(children)
        ? children.filter((c) => typeof c === 'string' && c.trim() && c.trim() !== ',').join('')
        : typeof children === 'string' && children.trim() && children.trim() !== ','
          ? children
          : null;
      if (!text) return null;
      return (
        <sup
          className="ml-0.5 rounded-md border border-[#e7ece0] bg-gray-100 px-1 py-0.5 text-[0.7rem] font-bold text-[#76b900] shadow-sm"
          {...props}
        >
          {text}
        </sup>
      );
    }, sameChildren),

    img: memo(
      (props: any) => (
        <MarkdownImage {...props} showDownload onDownloadError={onDownloadError} />
      ),
      sameSrc,
    ),

    video: memo((props: any) => <MarkdownVideo {...props} />, sameSrc),

    details: memo(
      ({ children, ...props }: any) => (
        <details
          className="my-2 rounded-md border border-neutral-300 bg-neutral-50 px-3 py-2 dark:border-neutral-700 dark:bg-neutral-900"
          {...props}
        >
          {children}
        </details>
      ),
      sameChildren,
    ),

    summary: memo(
      ({ children, ...props }: any) => (
        <summary className="cursor-pointer font-medium text-gray-700 dark:text-gray-200" {...props}>
          {children}
        </summary>
      ),
      sameChildren,
    ),

    // The agent wraps answers in <workflow> metadata that is not for the user.
    workflow: memo(() => null, () => true),

    'agent-think': memo(
      ({ children, ...props }: any) => (
        <AgentThink messageIsStreaming={messageIsStreaming} {...props}>
          {children}
        </AgentThink>
      ),
      (prev, next) =>
        sameChildren(prev, next) && prev['data-streaming'] === next['data-streaming'],
    ),

    'agent-think-step': memo(
      ({ children, ...props }: any) => (
        <AgentThinkStep messageIsStreaming={messageIsStreaming} {...props}>
          {children}
        </AgentThinkStep>
      ),
      (prev, next) =>
        sameChildren(prev, next) && prev['data-streaming'] === next['data-streaming'],
    ),
  };
}

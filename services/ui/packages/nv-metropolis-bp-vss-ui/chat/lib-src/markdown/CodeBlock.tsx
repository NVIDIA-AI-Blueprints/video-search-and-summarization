// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { IconCheck, IconClipboard, IconDownload } from '@tabler/icons-react';
import React, { Suspense, lazy, memo, useEffect, useRef, useState } from 'react';
import { copyToClipboard as copyToClipboardUtil } from 'common';

import { shouldHighlightCode } from './highlight';

/**
 * Syntax highlighting, loaded on demand.
 *
 * react-syntax-highlighter pulls in refractor and the whole Prism grammar set —
 * several hundred kilobytes for a feature that only fires when an answer
 * contains a fenced block that has stopped streaming and is under
 * `CODE_HIGHLIGHT_MAX_CHARS`. Larger JSON is rendered as plain text (see
 * `shouldHighlightCode`): Prism would otherwise emit a span per token and stall
 * the sidebar. Loading the highlighter lazily keeps it out of the chat chunk
 * for the common case, and the plain-text renderer is the Suspense fallback, so
 * nothing flashes empty while it arrives.
 */
const LazyHighlighter = lazy(async () => {
  const [{ Prism }, styles] = await Promise.all([
    import('react-syntax-highlighter'),
    import('react-syntax-highlighter/dist/esm/styles/prism/one-dark'),
  ]);
  const oneDark = (styles as any).default ?? styles;
  return {
    default: ({ language, children }: { language: string; children: string }) => (
      <Prism language={language} style={oneDark} customStyle={{ margin: 0 }}>
        {children}
      </Prism>
    ),
  };
});

/** Extension used when a block is downloaded. Keys match markdown info strings. */
const EXTENSIONS: Record<string, string> = {
  javascript: '.js',
  typescript: '.ts',
  python: '.py',
  java: '.java',
  c: '.c',
  cpp: '.cpp',
  'c++': '.cpp',
  'c#': '.cs',
  ruby: '.rb',
  php: '.php',
  go: '.go',
  rust: '.rs',
  kotlin: '.kt',
  swift: '.swift',
  scala: '.scala',
  haskell: '.hs',
  lua: '.lua',
  perl: '.pl',
  shell: '.sh',
  bash: '.sh',
  sql: '.sql',
  html: '.html',
  css: '.css',
  json: '.json',
  yaml: '.yaml',
  xml: '.xml',
};

// Re-running highlight on every token during a stream stutters the chat.
const CONTENT_STABLE_DELAY_MS = 500;

export interface CodeBlockProps {
  language: string;
  value: string;
}

export const CodeBlock: React.FC<CodeBlockProps> = memo(({ language, value }) => {
  const [isCopied, setIsCopied] = useState(false);
  // Derived from the content itself rather than a streaming prop: the parent is
  // memoised, so its `isStreaming` can be stale exactly when it matters.
  const [contentStable, setContentStable] = useState(false);
  const lastValueRef = useRef(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Do not rewrite fenced answer code here. Tool Input/Output is already
  // pretty-printed in parseStepPayload before it reaches CodeBlock; converting
  // again would turn answer Python (`True`/`None`) into JSON on copy/download.
  const displayLanguage = language || 'text';
  const canHighlight = shouldHighlightCode(value);

  useEffect(() => {
    if (!canHighlight) return;
    if (lastValueRef.current !== value) {
      lastValueRef.current = value;
      setContentStable(false);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setContentStable(true), CONTENT_STABLE_DELAY_MS);
    }
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [value, canHighlight]);

  // Mark a block that never changes (restored from history) stable immediately.
  // Large payloads skip this: colouring them is more expensive than it is worth.
  useEffect(() => {
    if (!canHighlight) return;
    const initial = setTimeout(() => setContentStable(true), CONTENT_STABLE_DELAY_MS);
    return () => clearTimeout(initial);
  }, [canHighlight]);

  const usePlainText = !canHighlight || !contentStable;

  const handleCopy = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (await copyToClipboardUtil(value)) {
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    }
  };

  const handleDownload = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const extension = EXTENSIONS[displayLanguage] || '.txt';
    const blob = new Blob([value], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `agent-output-${Date.now()}${extension}`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const plain = (
    <pre className="m-0 overflow-x-auto p-4 text-[#abb2bf]">
      <code>{value}</code>
    </pre>
  );

  return (
    <div
      className="codeblock relative my-2 rounded-md bg-[#282c34] font-mono text-sm"
      data-highlighted={usePlainText ? 'false' : 'true'}
    >
      <div className="flex items-center justify-between px-4 py-1.5 text-xs text-gray-300">
        <span className="lowercase">{displayLanguage}</span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="flex items-center gap-1 rounded bg-none p-1 text-xs text-white hover:bg-white/10"
            onClick={handleCopy}
            aria-label="Copy code"
          >
            {isCopied ? <IconCheck size={16} /> : <IconClipboard size={16} />}
            {isCopied ? 'Copied!' : 'Copy code'}
          </button>
          <button
            type="button"
            className="rounded bg-none p-1 text-white hover:bg-white/10"
            onClick={handleDownload}
            aria-label="Download code"
          >
            <IconDownload size={16} />
          </button>
        </div>
      </div>

      {usePlainText ? (
        plain
      ) : (
        <Suspense fallback={plain}>
          <LazyHighlighter language={displayLanguage}>{value}</LazyHighlighter>
        </Suspense>
      )}
    </div>
  );
});
CodeBlock.displayName = 'CodeBlock';

// SPDX-License-Identifier: MIT
import { IconCheck, IconClipboard, IconDownload } from '@tabler/icons-react';
import React, { Suspense, lazy, memo, useEffect, useMemo, useRef, useState } from 'react';
import { copyToClipboard as copyToClipboardUtil } from 'common';

/**
 * Syntax highlighting, loaded on demand.
 *
 * react-syntax-highlighter pulls in refractor and the whole Prism grammar set —
 * several hundred kilobytes for a feature that only fires when an answer
 * contains a fenced code block, and never during streaming (see the stability
 * gate below). Loading it lazily keeps it out of the chat chunk entirely for
 * the common case, and the plain-text renderer is the Suspense fallback, so
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

// Highlighting a large block costs more than it is worth, and re-running it on
// every token during a stream is what makes the toolkit's chat stutter.
const VERY_LARGE_CONTENT_THRESHOLD = 50_000;
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

  const formattedValue = useMemo(() => {
    // Agents commonly emit near-JSON with single quotes; pretty-print when we
    // can and fall back to the raw text when we cannot.
    const candidate = language === 'json' ? value.replaceAll("'", '"') : value;
    try {
      return JSON.stringify(JSON.parse(candidate), null, 2);
    } catch {
      return value;
    }
  }, [language, value]);

  useEffect(() => {
    if (lastValueRef.current !== value) {
      lastValueRef.current = value;
      setContentStable(false);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setContentStable(true), CONTENT_STABLE_DELAY_MS);
    }
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [value]);

  // Mark a block that never changes (restored from history) stable immediately.
  useEffect(() => {
    const initial = setTimeout(() => setContentStable(true), CONTENT_STABLE_DELAY_MS);
    return () => clearTimeout(initial);
  }, []);

  const usePlainText = formattedValue.length > VERY_LARGE_CONTENT_THRESHOLD || !contentStable;

  const handleCopy = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (await copyToClipboardUtil(formattedValue)) {
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    }
  };

  const handleDownload = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const extension = EXTENSIONS[language] || '.txt';
    const blob = new Blob([formattedValue], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `agent-output-${Date.now()}${extension}`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const plain = (
    <pre className="m-0 overflow-x-auto p-4 text-[#abb2bf]">
      <code>{formattedValue}</code>
    </pre>
  );

  return (
    <div className="codeblock relative my-2 rounded-md bg-[#282c34] font-mono text-sm">
      <div className="flex items-center justify-between px-4 py-1.5 text-xs text-gray-300">
        <span className="lowercase">{language}</span>
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
          <LazyHighlighter language={language || 'text'}>{formattedValue}</LazyHighlighter>
        </Suspense>
      )}
    </div>
  );
});
CodeBlock.displayName = 'CodeBlock';

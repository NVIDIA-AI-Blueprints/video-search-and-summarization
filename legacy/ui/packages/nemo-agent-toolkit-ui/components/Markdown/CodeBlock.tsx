import { IconCheck, IconClipboard, IconDownload } from '@tabler/icons-react';
import { FC, memo, useState, useMemo, useEffect, useRef } from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/cjs/styles/prism';

import { useTranslation } from 'next-i18next';

import {
  generateRandomString,
  programmingLanguages,
} from '@/utils/app/codeblock';
import { copyToClipboard as copyToClipboardUtil } from '@aiqtoolkit-ui/common';

interface Props {
  language: string;
  value: string;
  isStreaming?: boolean; // Hint that streaming is active (may not update due to parent memo)
}

// For very large content, use plain text instead of syntax highlighting
const VERY_LARGE_CONTENT_THRESHOLD = 50000;
// Time to wait after content stops changing before applying syntax highlighting
const CONTENT_STABLE_DELAY_MS = 500;

export const CodeBlock: FC<Props> = memo(({ language, value, isStreaming = false }) => {
  const { t } = useTranslation('markdown');
  const [isCopied, setIsCopied] = useState<boolean>(false);
  
  // Track whether content has stabilized (not changing for CONTENT_STABLE_DELAY_MS)
  // This is more reliable than the isStreaming prop which may not update due to parent memoization
  const [contentStable, setContentStable] = useState<boolean>(false);
  const lastValueRef = useRef<string>(value);
  const stabilityTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Ensure value is a valid JSON string
  if (language === 'json') {
    try {
      value = value.replaceAll("'", '"');
    } catch (error) {
      console.log(error);
    }
  }

  const formattedValue = useMemo(() => {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value; // Return the original value if parsing fails
    }
  }, [value]);

  // Detect when content has stopped changing (streaming complete)
  useEffect(() => {
    // Content changed - reset stability
    if (lastValueRef.current !== value) {
      lastValueRef.current = value;
      setContentStable(false);
      
      // Clear existing timer
      if (stabilityTimerRef.current) {
        clearTimeout(stabilityTimerRef.current);
      }
      
      // Start new timer - if content doesn't change for CONTENT_STABLE_DELAY_MS, mark as stable
      stabilityTimerRef.current = setTimeout(() => {
        setContentStable(true);
      }, CONTENT_STABLE_DELAY_MS);
    }
    
    return () => {
      if (stabilityTimerRef.current) {
        clearTimeout(stabilityTimerRef.current);
      }
    };
  }, [value]);

  // For very large content OR while content is still changing, use plain text rendering
  // Syntax highlighting is expensive and causes lag during streaming
  const isVeryLarge = formattedValue.length > VERY_LARGE_CONTENT_THRESHOLD;
  const usePlainText = isVeryLarge || !contentStable;

  const copyToClipboard = async (e: React.MouseEvent) => {
    e?.preventDefault();
    e?.stopPropagation();
    const success = await copyToClipboardUtil(formattedValue);
    if (success) {
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    }
  };

  const downloadAsFile = (e: React.MouseEvent) => {
    e?.preventDefault();
    e?.stopPropagation();
    const fileExtension = programmingLanguages[language] || '.file';
    const suggestedFileName = `file-${generateRandomString(
      3,
      true,
    )}${fileExtension}`;

    if (!suggestedFileName) {
      return;
    }

    const blob = new Blob([formattedValue], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.download = suggestedFileName;
    link.href = url;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="codeblock relative font-sans text-[16px] w-full">
      <div className="flex items-center justify-between py-1.5 px-4 bg-neutral-800 text-white">
        <span className="text-xs lowercase">
          {language}
          {isVeryLarge && (
            <span className="ml-2 text-yellow-400 text-xs">
              (Plain text mode - large content)
            </span>
          )}
        </span>

        <div className="flex items-center gap-1">
          <button
            className="flex gap-1.5 items-center rounded bg-none p-1 text-xs text-white hover:bg-neutral-800"
            onClick={(e) => copyToClipboard(e)}
          >
            {isCopied ? <IconCheck size={18} /> : <IconClipboard size={18} />}
            {isCopied ? t('Copied!') : t('Copy code')}
          </button>
          <button
            className="flex items-center rounded bg-none p-1 text-xs text-white hover:bg-neutral-800"
            onClick={(e) => downloadAsFile(e)}
          >
            <IconDownload size={18} />
          </button>
        </div>
      </div>
      <div 
        className="overflow-hidden"
        style={{
          maxHeight: '50vh',
          overflowY: 'auto',
        }}
      >
        {usePlainText ? (
          // For very large content, use plain text for performance
          <pre
            style={{
              margin: 0,
              padding: '16px',
              background: '#262626',
              fontSize: '14px',
              lineHeight: '1.5',
              fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
              color: '#abb2bf',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              overflowWrap: 'break-word',
            }}
          >
            {formattedValue}
          </pre>
        ) : (
          // For normal content, use syntax highlighting
          <SyntaxHighlighter
            language={language || 'text'}
            style={oneDark}
            customStyle={{
              margin: 0,
              padding: '16px',
              background: '#262626',
              fontSize: '14px',
              lineHeight: '1.5',
              fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
              width: '100%',
              maxWidth: '100%',
              minWidth: 0,
              wordBreak: 'break-word',
              overflowWrap: 'break-word',
              boxSizing: 'border-box',
              border: 'none',
              borderRadius: 0,
            }}
            codeTagProps={{
              style: {
                fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
                fontSize: '14px',
              }
            }}
            wrapLines={true}
            wrapLongLines={true}
          >
            {formattedValue}
          </SyntaxHighlighter>
        )}
      </div>
    </div>
  );
});
CodeBlock.displayName = 'CodeBlock';

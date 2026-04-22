'use client';

/**
 * AgentThink and AgentThinkStep Components
 * 
 * These components handle custom <agent-think> and <agent-think-step> markdown tags
 * sent from the backend as collapsible UI elements.
 * 
 * ============================================================================
 * CRITICAL: Backend Formatting Rules (Required for Proper Nesting)
 * ============================================================================
 * 
 * The backend MUST format content with these rules:
 * 
 * 1. ✅ Blank line (\n\n) BEFORE <agent-think>
 *    WHY: Prevents markdown parser from wrapping it in a <p> tag
 * 
 * 2. ✅ Blank line (\n\n) AFTER </agent-think>
 *    WHY: Clean separation from following content
 * 
 * 3. ✅ NO blank lines immediately after <agent-think> opening tag
 *    WHY: Keeps content inside the tag, not as a sibling
 * 
 * 4. ✅ <agent-think-step> tags on their own lines (no blank lines around them)
 *    WHY: Ensures proper parent-child nesting
 * 
 * Example (correct format):
 * ```
 * Regular content here.
 * 
 * <agent-think title="Title">
 * Content line 1
 * <agent-think-step title="Step1">
 * Step content
 * </agent-think-step>
 * Final line
 * </agent-think>
 * 
 * More content here.
 * ```
 * 
 * ============================================================================
 * Why Can't We Fix This During Parsing?
 * ============================================================================
 * 
 * The issue occurs in the markdown-to-HTML parsing phase (react-markdown + rehype-raw):
 * 
 * 1. Markdown parsers treat consecutive text as paragraph content
 * 2. When they see: "text<agent-think>", they create: <p>text<agent-think></p>
 * 3. HTML spec: Block elements (like our <div> renders) CANNOT be inside <p> tags
 * 4. Browser auto-closes <p> when it encounters a block element, breaking nesting
 * 
 * Could we write a custom parser plugin?
 * - Yes, but it's complex, error-prone, and has performance costs
 * - Would need to pre-process content before markdown parsing
 * - Simpler solution: Format input correctly at the source
 * 
 * The blank lines signal to the markdown parser: "treat this as a block-level element,
 * not inline content" - which is the correct semantic meaning anyway.
 * 
 * ============================================================================
 * 
 * Features:
 * - Collapsible sections with smooth animations
 * - Optional title attribute for both components
 * - Nested step support within agent-think
 * - Vertical storyline bar with circle indicator for steps
 * - Dark mode support
 * - Neutral gray/white styling
 */

import { IconChevronDown, IconChevronUp, IconLoader2 } from '@tabler/icons-react';
import { useState, useEffect } from 'react';

interface AgentThinkProps {
  children: React.ReactNode;
  title?: string;
  'data-streaming'?: string;
  messageIsStreaming?: boolean;
  [key: string]: any;
}

export const AgentThink = ({ children, title, ...props }: AgentThinkProps) => {
  // Check if this component is currently being streamed
  const isStreaming = props['data-streaming'] === 'true' && props.messageIsStreaming;
  
  const [isOpen, setIsOpen] = useState(false);
  const [wasStreaming, setWasStreaming] = useState(false);

  // Auto-open when streaming starts, auto-close when streaming completes
  useEffect(() => {
    if (isStreaming) {
      setIsOpen(true);
      setWasStreaming(true);
      return; // No cleanup needed for this case
    } else if (wasStreaming && !isStreaming) {
      // Auto-close after a short pause when streaming completes (data-streaming removed)
      const closeTimeout = setTimeout(() => {
        setIsOpen(false);
        setWasStreaming(false);
      }, 3000); // 3 second pause before closing
      
      return () => clearTimeout(closeTimeout);
    }
    return; // No cleanup needed for other cases
  }, [isStreaming, wasStreaming]);

  const handleToggle = () => {
    // Prevent manual toggle while streaming
    if (!isStreaming) {
      setIsOpen((prev) => !prev);
    }
  };

  return (
    <div
      className="my-3 bg-neutral-100 dark:bg-neutral-900 border border-neutral-300 dark:border-neutral-700 rounded-lg shadow-sm overflow-hidden"
      {...props}
    >
      {/* Header/Summary */}
      <div
        className={`flex items-center justify-between p-3 ${
          isStreaming 
            ? 'cursor-default' 
            : 'cursor-pointer hover:bg-neutral-200 dark:hover:bg-neutral-800'
        } transition-colors`}
        onClick={handleToggle}
      >
        <div className="flex items-center gap-2">
          {/* Show spinner while streaming */}
          {isStreaming && (
            <IconLoader2 
              size={20} 
              className="text-[#76b900] animate-spin flex-shrink-0" 
            />
          )}
          <span className="font-medium text-gray-700 dark:text-gray-200">
            <strong>Reasoning Trace</strong>{title ? ` - ${title}` : ''}
          </span>
        </div>
        {!isStreaming && (
          <>
            {isOpen ? (
              <IconChevronUp
                size={20}
                className="text-gray-600 dark:text-gray-300 transition-transform"
              />
            ) : (
              <IconChevronDown
                size={20}
                className="text-gray-600 dark:text-gray-300 transition-transform"
              />
            )}
          </>
        )}
      </div>

      {/* Content */}
      <div 
        className={`px-4 pb-4 pt-2 border-t border-neutral-300 dark:border-zinc-600 text-gray-700 dark:text-gray-300 transition-all duration-200 ${
          isOpen ? 'block animate-fadeIn' : 'hidden'
        }`}
      >
        <div className="whitespace-pre-wrap break-words">{children}</div>
      </div>
    </div>
  );
};

interface AgentThinkStepProps {
  children: React.ReactNode;
  title?: string;
  'data-streaming'?: string;
  messageIsStreaming?: boolean;
  [key: string]: any;
}

export const AgentThinkStep = ({ children, title, ...props }: AgentThinkStepProps) => {
  // Check if this step is currently being streamed
  const isStreaming = props['data-streaming'] === 'true' && props.messageIsStreaming;
  
  const [isOpen, setIsOpen] = useState(true);

  // Auto-open when streaming starts, stay open after streaming completes
  useEffect(() => {
    if (isStreaming) {
      setIsOpen(true);
    }
  }, [isStreaming]);

  const handleToggle = () => {
    // Prevent manual toggle while streaming
    if (!isStreaming) {
      setIsOpen((prev) => !prev);
    }
  };

  return (
    <div
      className="my-2 pl-6 relative"
      {...props}
    >
      {/* Vertical storyline bar with start circle head */}
      <div className="absolute left-0 top-0 bottom-0 flex flex-col items-center">
        {/* Start head - solid thick circle */}
        <div className="w-3 h-3 rounded-full bg-gray-500 dark:bg-gray-400 flex-shrink-0 mt-2" />
        
        {/* Vertical line - plain line to the end */}
        <div className="w-1 flex-1 bg-gray-400 dark:bg-gray-500" />
      </div>
      
      {/* Content container - no border */}
      <div className="bg-gray-100/50 dark:bg-neutral-800/50 rounded-md shadow-sm overflow-hidden">
        {/* Header/Summary */}
        <div
          className={`flex items-center justify-between px-3 py-2 ${
            isStreaming 
              ? 'cursor-default' 
              : 'cursor-pointer hover:bg-gray-200/50 dark:hover:bg-neutral-700/50'
          } transition-colors rounded-md`}
          onClick={handleToggle}
        >
          <div className="flex items-center gap-2">
            {/* Show spinner while streaming */}
            {isStreaming && (
              <IconLoader2 
                size={16} 
                className="text-[#76b900] animate-spin flex-shrink-0" 
              />
            )}
            <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
              <strong>Step</strong>{title ? ` - ${title}` : ''}
            </span>
          </div>
          {!isStreaming && (
            <>
              {isOpen ? (
                <IconChevronUp
                  size={16}
                  className="text-gray-600 dark:text-gray-300 transition-transform"
                />
              ) : (
                <IconChevronDown
                  size={16}
                  className="text-gray-600 dark:text-gray-300 transition-transform"
                />
              )}
            </>
          )}
        </div>

        {/* Content */}
        <div 
          className={`px-3 pb-2 pt-1 border-t border-gray-300 dark:border-zinc-500 text-sm text-gray-700 dark:text-gray-300 transition-all duration-200 ${
            isOpen ? 'block animate-fadeIn' : 'hidden'
          }`}
        >
          <div className="whitespace-pre-wrap break-words">{children}</div>
        </div>
      </div>
    </div>
  );
};


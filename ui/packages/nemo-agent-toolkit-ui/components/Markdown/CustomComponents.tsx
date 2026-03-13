import { memo } from 'react';

import Chart from '@/components/Markdown/Chart';
import { CodeBlock } from '@/components/Markdown/CodeBlock';
import { CustomDetails } from '@/components/Markdown/CustomDetails';
import { CustomSummary } from '@/components/Markdown/CustomSummary';
import { CustomIncidents } from '@/components/Markdown/CustomIncidents';
import { Image } from '@/components/Markdown/Image';
import { Video } from '@/components/Markdown/Video';
import { AgentThink, AgentThinkStep } from '@/components/Markdown/AgentThink';

import { isEqual } from 'lodash';

export const getReactMarkDownCustomComponents = (
  messageIndex = 0,
  messageId = '',
  messageIsStreaming = false,
) => {
  return {
      code: memo(
        ({
          node,
          inline,
          className,
          children,
          ...props
        }: {
          children: React.ReactNode;
          [key: string]: any;
        }) => {
          // if (children?.length) {
          //   if (children[0] === '▍') {
          //     return <span className="animate-pulse cursor-default mt-1">▍</span>;
          //   }
          //   children[0] = children.length > 0 ? (children[0] as string)?.replace("`▍`", "▍") : '';
          // }

          const match = /language-(\w+)/.exec(className || '');
          const childString = String(children).replace(/\n$/, '');

          return (
            <CodeBlock
              language={(match && match.length > 1 && match[1]) || ''}
              value={childString}
              isStreaming={messageIsStreaming}
              {...props}
            />
          );
        },
        // Note: We intentionally don't compare messageIsStreaming here
        // The CodeBlock will re-render with syntax highlighting when streaming ends
        // because the parent MemoizedChatMessage re-renders when isStreaming changes
        (prevProps, nextProps) => {
          return isEqual(prevProps.children, nextProps.children);
        },
      ),

      chart: memo(
        ({ children }) => {
          try {
            const payload = JSON.parse(children[0].replaceAll('\n', ''));
            return payload ? <Chart payload={payload} /> : null;
          } catch (error) {
            console.error(error);
            return null;
          }
        },
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children),
      ),

      table: memo(
        ({ children }) => (
          <table className="border-collapse border border-black px-3 py-1 dark:border-white">
            {children}
          </table>
        ),
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children),
      ),

      th: memo(
        ({ children }) => (
          <th className="break-words border border-black bg-gray-500 px-3 py-1 text-white dark:border-white">
            {children}
          </th>
        ),
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children),
      ),

      td: memo(
        ({ children }) => (
          <td className="break-words border border-black px-3 py-1 dark:border-white">
            {children}
          </td>
        ),
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children),
      ),

      a: memo(
        ({ href, children, ...props }) => (
          <a
            href={href}
            className="text-[#76b900] no-underline hover:underline"
            {...props}
          >
            {children}
          </a>
        ),
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children),
      ),

      li: memo(
        ({ children, ordered, ...props }) => (
          <li className="leading-[1.35rem] mb-1 list-disc" {...props}>
            {children}
          </li>
        ),
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children),
      ),

      sup: memo(
        ({ children, ...props }) => {
          const validContent = Array.isArray(children)
            ? children
                .filter(
                  (child) =>
                    typeof child === 'string' &&
                    child.trim() &&
                    child.trim() !== ',',
                )
                .join('')
            : typeof children === 'string' &&
              children.trim() &&
              children.trim() !== ','
            ? children
            : null;

          return validContent ? (
            <sup
              className="text-xs bg-gray-100 text-[#76b900] border border-[#e7ece0] px-1 py-0.5 rounded-md shadow-sm"
              style={{
                fontWeight: 'bold',
                marginLeft: '2px',
                transform: 'translateY(-3px)',
                fontSize: '0.7rem',
              }}
              {...props}
            >
              {validContent}
            </sup>
          ) : null;
        },
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children),
      ),

      p: memo(
        ({
          children,
          ...props
        }: {
          children: React.ReactNode;
          [key: string]: any;
        }) => {
          return <p {...props}>{children}</p>;
        },
        (prevProps, nextProps) => {
          return isEqual(prevProps.children, nextProps.children);
        },
      ),
      img: memo(
        (props: any) => <Image {...props} />,
        (prevProps, nextProps) => {
          // For images, compare src and alt
          // Use optimized comparison for large base64 strings
          if (prevProps.src?.length > 1000 || nextProps.src?.length > 1000) {
            // Length check first (fast)
            if (prevProps.src?.length !== nextProps.src?.length) {
              return false;
            }
            // Compare start and end for large strings
            const prevStart = prevProps.src?.substring(0, 100) || '';
            const prevEnd = prevProps.src?.substring(prevProps.src.length - 100) || '';
            const nextStart = nextProps.src?.substring(0, 100) || '';
            const nextEnd = nextProps.src?.substring(nextProps.src.length - 100) || '';
            return prevStart === nextStart && prevEnd === nextEnd && prevProps.alt === nextProps.alt;
          }
          return prevProps.src === nextProps.src && prevProps.alt === nextProps.alt;
        },
      ),
      video: memo(
        (props) => <Video {...props} />,
        (prevProps, nextProps) => {
          // Optimize comparison for video src (could be large data URL)
          if (prevProps.src?.length > 1000 || nextProps.src?.length > 1000) {
            // Length check first (fast)
            if (prevProps.src?.length !== nextProps.src?.length) {
              return false;
            }
            // Compare start and end for large strings
            const prevStart = prevProps.src?.substring(0, 100) || '';
            const prevEnd = prevProps.src?.substring(prevProps.src.length - 100) || '';
            const nextStart = nextProps.src?.substring(0, 100) || '';
            const nextEnd = nextProps.src?.substring(nextProps.src.length - 100) || '';
            return prevStart === nextStart && prevEnd === nextEnd;
          }
          return prevProps.src === nextProps.src;
        },
      ),
      details: memo(
        (props) => <CustomDetails messageIndex={messageIndex} {...props} />,
        (prevProps, nextProps) => isEqual(prevProps, nextProps),
      ),
      summary: memo(
        (props) => <CustomSummary messageIndex={messageIndex} {...props} />,
        (prevProps, nextProps) => isEqual(prevProps, nextProps),
      ),
      workflow: memo(
        () => null,
        () => true,
      ),
      incidents: memo(
        ({ children, data, ...props }) => {
          try {
            if (!children) {
              return null;
            }
            
            let rawContent: any;
            
            // Handle different children formats:
            // 1. children is a string directly (the JSON content)
            // 2. children is an array with string/object as first element
            // 3. children is an object directly
            if (typeof children === 'string') {
              rawContent = children;
            } else if (Array.isArray(children) && children[0]) {
              rawContent = children[0];
            } else if (typeof children === 'object') {
              rawContent = children;
            } else {
              return null;
            }
            
            let payload: any;
            
            // Check if content is already a parsed object
            if (typeof rawContent === 'object' && rawContent !== null) {
              payload = rawContent;
            } else if (typeof rawContent === 'string') {
              // Parse string content as JSON
              const cleanedContent = rawContent.replaceAll('\n', '');
              
              // Check if content looks like JSON
              if (!cleanedContent.trim().startsWith('{')) {
                return null;
              }
              
              payload = JSON.parse(cleanedContent);
            } else {
              return null;
            }
            
            if (!payload || !payload.incidents || !Array.isArray(payload.incidents)) {
              return null;
            }
            
            return <CustomIncidents payload={payload} />;
          } catch (error) {
            return null;
          }
        },
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children),
      ),
      'agent-think': memo(
        ({ children, ...props }) => {
          return <AgentThink messageIsStreaming={messageIsStreaming} {...props}>{children}</AgentThink>;
        },
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children) &&
          prevProps['data-streaming'] === nextProps['data-streaming'],
      ),
      'agent-think-step': memo(
        ({ children, ...props }) => {
          return <AgentThinkStep messageIsStreaming={messageIsStreaming} {...props}>{children}</AgentThinkStep>;
        },
        (prevProps, nextProps) =>
          isEqual(prevProps.children, nextProps.children) &&
          prevProps['data-streaming'] === nextProps['data-streaming'],
      ),
  };
};

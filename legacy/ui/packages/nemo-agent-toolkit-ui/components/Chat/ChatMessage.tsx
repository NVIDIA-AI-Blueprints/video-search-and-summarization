'use client';

import {
  IconCheck,
  IconCopy,
  IconEdit,
  IconPlayerPause,
  IconTrash,
  IconUser,
  IconVolume2,
} from '@tabler/icons-react';
import { FC, memo, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';

import { useTranslation } from 'next-i18next';

import {
  fixMalformedHtml,
  generateContentIntermediate,
} from '@/utils/app/helper';

import { Message } from '@/types/chat';

import { BotAvatar } from '@/components/Avatar/BotAvatar';

import { getReactMarkDownCustomComponents } from '../Markdown/CustomComponents';
import { MemoizedReactMarkdown } from '../Markdown/MemoizedReactMarkdown';

import DOMPurify from 'isomorphic-dompurify';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

export interface Props {
  message: Message;
  messageIndex: number;
  onEdit?: (editedMessage: Message, deleteCount?: number) => void;
  onDelete?: (messageIndex: number) => void; // Callback to delete message at index
  totalMessageCount?: number; // Total messages for calculating deleteCount
  isStreaming?: boolean; // Whether this specific message is currently streaming
  showMessageEdit?: boolean;
  showMessageSpeaker?: boolean;
  showMessageCopy?: boolean;
}

export const ChatMessage: FC<Props> = memo(
  ({ message, messageIndex, onEdit, onDelete, totalMessageCount = 0, isStreaming = false, showMessageEdit = true, showMessageSpeaker = true, showMessageCopy = true }) => {
    const { t } = useTranslation('chat');

    const [isEditing, setIsEditing] = useState<boolean>(false);
    const [isTyping, setIsTyping] = useState<boolean>(false);
    const [messageContent, setMessageContent] = useState(message.content);
    const [messagedCopied, setMessageCopied] = useState(false);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const speechSynthesisRef = useRef<SpeechSynthesisUtterance | null>(null);

    // Memoize the markdown components - DO NOT include isStreaming in deps
    // Including isStreaming causes the entire markdown tree to be recreated when streaming ends,
    // which unmounts/remounts all elements (images, code blocks, etc.) causing massive lag
    // Instead, isStreaming is passed but components that need it should handle updates internally
    const markdownComponents = useMemo(() => {
      return getReactMarkDownCustomComponents(messageIndex, message?.id, isStreaming);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [messageIndex, message?.id]); // Intentionally excluding isStreaming

    const safeHtml = useMemo(
      () => DOMPurify.sanitize(message.callerInfo || ''),
      [message.callerInfo],
    );

    // return if the there is nothing to show
    // no message and no intermediate steps
    if (message?.content === '' && message?.intermediateSteps?.length === 0) {
      return null;
    }

    const toggleEditing = () => {
      setIsEditing(!isEditing);
    };

    const handleInputChange = (
      event: React.ChangeEvent<HTMLTextAreaElement>,
    ) => {
      setMessageContent(event.target.value);
      if (textareaRef.current) {
        textareaRef.current.style.height = 'inherit';
        textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
      }
    };

    const handleEditMessage = () => {
      if (message.content != messageContent) {
        if (onEdit) {
          const deleteCount = totalMessageCount - messageIndex;
          onEdit({ ...message, content: messageContent }, deleteCount);
        }
      }
      setIsEditing(false);
    };

    const handleDeleteMessage = () => {
      if (onDelete) {
        onDelete(messageIndex);
      }
    };

    const handlePressEnter = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !isTyping && !e.shiftKey) {
        e.preventDefault();
        handleEditMessage();
      }
    };

    const copyOnClick = () => {
      if (!navigator.clipboard) return;

      navigator.clipboard.writeText(message.content).then(() => {
        setMessageCopied(true);
        setTimeout(() => {
          setMessageCopied(false);
        }, 2000);
      });
    };

    useEffect(() => {
      setMessageContent(message.content);
    }, [message.content]);

    useEffect(() => {
      if (textareaRef.current) {
        textareaRef.current.style.height = 'inherit';
        textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
      }
    }, [isEditing]);

    const removeLinks = (text: string) => {
      // This regex matches http/https URLs
      const urlRegex = /(https?:\/\/[^\s]+)/g;
      return text.replace(urlRegex, '');
    };

    const handleTextToSpeech = () => {
      if ('speechSynthesis' in window) {
        if (isPlaying) {
          window.speechSynthesis.cancel();
          setIsPlaying(false);
        } else {
          const textWithoutLinks = removeLinks(message?.content);
          const utterance = new SpeechSynthesisUtterance(textWithoutLinks);
          utterance.onend = () => setIsPlaying(false);
          utterance.onerror = () => setIsPlaying(false);
          speechSynthesisRef.current = utterance;
          setIsPlaying(true);
          window.speechSynthesis.speak(utterance);
        }
      } else {
        console.log('Text-to-speech is not supported in your browser.');
      }
    };

    useEffect(() => {
      return () => {
        if (speechSynthesisRef.current) {
          window.speechSynthesis.cancel();
        }
      };
    }, []);

    const prepareContent = ({
      message = {} as Message,
      responseContent = true,
      intermediateStepsContent = false,
      role = 'assistant',
    } = {}) => {
      const { content = '', intermediateSteps = [] } = message;

      if (role === 'user') return content.trim();

      let result = '';
      if (intermediateStepsContent) {
        result += generateContentIntermediate(intermediateSteps);
      }

      if (responseContent) {
        result += result ? `\n\n${content}` : content;
      }

      // fixing malformed html and removing extra spaces to avoid markdown issues
      return fixMalformedHtml(result)?.trim()?.replace(/\n\s+/, '\n ');
    };

    return (
      <div
        data-testid={message.role === 'assistant' ? 'chat-message-assistant' : 'chat-message-user'}
        className={`group md:px-4 ${
          message.role === 'assistant'
            ? 'border-b border-black/10 bg-gray-50 text-gray-800 dark:border-gray-900/50 dark:bg-black dark:text-gray-100'
            : 'border-b border-black/10 bg-white text-gray-800 dark:border-gray-900/50 dark:bg-black dark:text-gray-100'
        }`}
        style={{ overflowWrap: 'anywhere' }}
      >
        <div className="relative m-auto flex text-base w-full max-w-[95%] md:gap-6 sm:p-2 md:py-6 lg:px-0">
          <div className="min-w-[40px] text-right font-bold">
            {message.role === 'assistant' ? (
              <BotAvatar src={'nvidia.jpg'} />
            ) : (
              <IconUser size={30} />
            )}
          </div>

          <div className="w-full min-w-0 dark:prose-invert overflow-hidden">
            {message.role === 'user' ? (
              <div className="flex w-full">
                {isEditing ? (
                  <div className="flex w-full flex-col">
                    <textarea
                      ref={textareaRef}
                      className="w-full resize-none whitespace-pre-wrap border-none dark:bg-black"
                      value={messageContent}
                      onChange={handleInputChange}
                      onKeyDown={handlePressEnter}
                      onCompositionStart={() => setIsTyping(true)}
                      onCompositionEnd={() => setIsTyping(false)}
                      style={{
                        fontFamily: 'inherit',
                        fontSize: 'inherit',
                        lineHeight: 'inherit',
                        padding: '0',
                        margin: '0',
                        overflow: 'hidden',
                      }}
                    />

                    <div className="mt-10 flex justify-center space-x-4">
                      <button
                        className="h-[40px] rounded-md border border-neutral-300 px-4 py-1 text-sm font-medium text-neutral-700 enabled:hover:bg-[#76b900] enabled:hover:text-white disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300"
                        onClick={handleEditMessage}
                        disabled={messageContent.trim().length <= 0}
                      >
                        {t('Save & Submit')}
                      </button>
                      <button
                        className="h-[40px] rounded-md border border-neutral-300 px-4 py-1 text-sm font-medium text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                        onClick={() => {
                          setMessageContent(message.content);
                          setIsEditing(false);
                        }}
                      >
                        {t('Cancel')}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="prose whitespace-pre-wrap dark:prose-invert flex-1 w-full overflow-x-auto flex-grow max-w-full whitespace-normal">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkMath]}
                      rehypePlugins={[rehypeRaw] as any}
                      components={markdownComponents}
                    >
                      {prepareContent({ message, role: 'user' })}
                    </ReactMarkdown>
                  </div>
                )}

                {!isEditing && (
                  <div className="absolute right-2 flex flex-col md:flex-row gap-1 items-center md:items-start justify-end md:justify-start">
                    {showMessageEdit && (
                      <button
                        className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
                        onClick={toggleEditing}
                      >
                        <IconEdit size={20} />
                      </button>
                    )}
                    <button
                      className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
                      onClick={handleDeleteMessage}
                    >
                      <IconTrash size={20} />
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="min-w-0 w-full max-w-full">
                <div className="flex flex-col w-[90%]">
                  <div className="flex flex-col gap-2">
                    {/* for intermediate steps content  */}
                    <div className="w-full overflow-x-hidden overflow-y-auto prose dark:prose-invert max-w-none break-words" style={{ fontSize: '18px' }}>
                      <MemoizedReactMarkdown
                        rehypePlugins={[rehypeRaw] as any}
                        remarkPlugins={[
                          remarkGfm,
                          [
                            remarkMath,
                            {
                              singleDollarTextMath: false,
                            },
                          ],
                        ]}
                        components={markdownComponents}
                      >
                        {prepareContent({
                          message,
                          role: 'assistant',
                          intermediateStepsContent: true,
                          responseContent: false,
                        })}
                      </MemoizedReactMarkdown>
                    </div>
                    {/* for response content */}
                    <div className="overflow-x-auto prose dark:prose-invert flex-1 w-full flex-grow max-w-full whitespace-normal" style={{ fontSize: '18px' }}>
                      <MemoizedReactMarkdown
                        rehypePlugins={[rehypeRaw] as any}
                        remarkPlugins={[
                          remarkGfm,
                          [
                            remarkMath,
                            {
                              singleDollarTextMath: false,
                            },
                          ],
                        ]}
                        components={markdownComponents}
                      >
                        {prepareContent({
                          message,
                          role: 'assistant',
                          intermediateStepsContent: false,
                          responseContent: true,
                        })}
                      </MemoizedReactMarkdown>
                    </div>
                    {message.callerInfo && (
                      <div className="mt-2 rounded-md border border-black/10 bg-neutral-100 px-4 py-2.5 text-sm text-neutral-800 dark:border-white/10 dark:bg-transparent dark:text-neutral-200 sm:px-5">
                        <div
                          className="caller-info-html [&>div]:text-neutral-900 dark:[&>div]:text-neutral-100 [&_ul]:mt-2 [&_ul]:mb-0 [&_ul]:list-disc [&_ul]:space-y-0.5 [&_ul]:pl-5 [&_ul]:marker:text-neutral-600 dark:[&_ul]:marker:text-neutral-400"
                          dangerouslySetInnerHTML={{ __html: safeHtml }}
                        />
                      </div>
                    )}
                    {(showMessageCopy || showMessageSpeaker) && (
                      <div className="mt-1 flex gap-1">
                        {!isStreaming && (
                          <>
                            {showMessageCopy && (messagedCopied ? (
                              <IconCheck
                                size={20}
                                className="text-[#76b900] dark:text-[#76b900]"
                                id={message?.id}
                              />
                            ) : (
                              <button
                                className="text-[#76b900] hover:text-gray-700 dark:text-[#76b900] dark:hover:round-gray-300"
                                onClick={copyOnClick}
                                title="Copy to clipboard"
                                id={message?.id}
                              >
                                <IconCopy size={20} />
                              </button>
                            ))}
                            {showMessageSpeaker && (
                              <button
                                className="text-[#76b900] hover:text-gray-700 dark:text-[#76b900] dark:hover:text-gray-300"
                                onClick={handleTextToSpeech}
                                aria-label={
                                  isPlaying ? 'Stop speaking' : 'Start speaking'
                                }
                              >
                                {isPlaying ? (
                                  <IconPlayerPause
                                    size={20}
                                    className="animate-pulse text-red-400"
                                  />
                                ) : (
                                  <IconVolume2 size={20} />
                                )}
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  },
);
ChatMessage.displayName = 'ChatMessage';

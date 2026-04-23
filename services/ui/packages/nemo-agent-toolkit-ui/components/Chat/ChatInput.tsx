import {
  IconArrowDown,
  IconBolt,
  IconPaperclip,
  IconPhoto,
  IconPlayerStop,
  IconRepeat,
  IconSend,
  IconTrash,
  IconMicrophone,
  IconPlayerStopFilled,
  IconMicrophone2,
  IconUpload,
  IconBrain,
  IconX,
} from '@tabler/icons-react';
import {
  KeyboardEvent,
  MutableRefObject,
  Ref,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import toast from 'react-hot-toast';

import { useTranslation } from 'next-i18next';

import { useWorkflowName } from '@/contexts/RuntimeConfigContext';
import { appConfig } from '@/utils/app/const';
import { compressImage } from '@/utils/app/helper';

import { Message, QueryDataContext } from '@/types/chat';

import HomeContext from '@/pages/api/home/home.context';
import { ChatFileUpload } from './ChatFileUpload';
import {
  CustomAgentParams,
  CustomAgentParamsValues,
  ParamField,
  useInitialParamFields,
  fieldsToParams,
} from './CustomAgentParams';

interface Props {
  onSend: (message: Message, customParams?: CustomAgentParamsValues) => void;
  onRegenerate: () => void;
  onScrollDownClick: () => void;
  textareaRef: MutableRefObject<HTMLTextAreaElement | null>;
  showScrollDownButton: boolean;
  controller: Ref<AbortController>;
  onStopConversation: () => void;
  queryContextItems?: QueryDataContext[];
  onRemoveQueryContext?: (itemId: string) => void;
}

export const ChatInput = ({
  onSend,
  onRegenerate,
  onScrollDownClick,
  textareaRef,
  showScrollDownButton,
  controller,
  onStopConversation,
  queryContextItems = [],
  onRemoveQueryContext,
}: Props) => {
  const { t } = useTranslation('chat');

  const {
    state: { selectedConversation, messageIsStreaming, loading, webSocketMode, customAgentParamsJson, chatUploadFileEnabled, chatInputMicEnabled },
    dispatch: homeDispatch,
  } = useContext(HomeContext);

  const workflow = useWorkflowName();

  // Create audio only when the file is present
  const [recordingStartSound, setRecordingStartSound] = useState<Audio | null>(null);

  useEffect(() => {
    const checkAudioFile = async () => {
      try {
        const response = await fetch('audio/recording.wav', { method: 'HEAD' });
        if (response.ok) {
          setRecordingStartSound(new Audio('audio/recording.wav'));
        }
      } catch (error) {
        console.log('Recording audio file not found, proceeding without sound');
      }
    };
    
    checkAudioFile();
  }, []);

  const [content, setContent] = useState<string>('');
  const [isTyping, setIsTyping] = useState<boolean>(false);
  const fileInputRef = useRef(null);
  const [inputFile, setInputFile] = useState(null);
  const [inputFileExtension, setInputFileExtension] = useState('');
  const [inputFileContent, setInputFileContent] = useState('');
  const [inputFileContentCompressed, setInputFileContentCompressed] =
    useState('');
  const [isRecording, setIsRecording] = useState(false);
  const recognitionRef = useRef(null);
  const [showCustomParams, setShowCustomParams] = useState(false);
  const [paramFields, setParamFields] = useInitialParamFields(customAgentParamsJson);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);

  const triggerFileUpload = () => {
    fileInputRef?.current.click();
  };

  const handleInputFileDelete = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputFile(null);
    setInputFileExtension('');
    setInputFileContent('');
    setInputFileContentCompressed('');
  };

  const handleFileChange = (e: { target: { files: any[]; value: null } }) => {
    const file = e.target.files[0];
    if (file) {
      // Reset the input value so the same file can be selected again if needed
      e.target.value = null;
      const reader = new FileReader();
      reader.onload = (loadEvent) => {
        const fullBase64String = loadEvent.target?.result;
        processFile({ fullBase64String, file });
      };
      reader.readAsDataURL(file);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;

    setContent(value);
  };

  const handleSend = () => {
    if (messageIsStreaming) {
      return;
    }

    // stop recognition if it's running
    if (isRecording) {
      recognitionRef.current.stop();
      setIsRecording(false);
    }

    if (!content.trim() && !inputFile && !inputFileContent && queryContextItems.length === 0) {
      toast.error(t('Please enter a message'));
      return;
    }

    if (inputFile || inputFileContent) {
      onSend({
        role: 'user',
        content: content,
        attachments: [
          {
            content: inputFileContent,
            type: 'image',
          },
        ],
      }, fieldsToParams(paramFields));
      setContent('');
      setInputFile(null);
      setInputFileExtension('');
      setInputFileContent('');
      setInputFileContentCompressed('');
    } else {
      onSend({ role: 'user', content }, fieldsToParams(paramFields));
      setContent('');
      setInputFile(null);
      setInputFileExtension('');
      setInputFileContent('');
      setInputFileContentCompressed('');
    }

    if (window.innerWidth < 640 && textareaRef && textareaRef.current) {
      textareaRef.current.blur();
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !isTyping && !isMobile() && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    } else if (e.key === '/' && e.metaKey) {
      e.preventDefault();
    }
  };

      // Use the passed callback for stop conversation
  const handleStopConversation = onStopConversation;

  const isMobile = () => {
    const userAgent =
      typeof window.navigator === 'undefined' ? '' : navigator.userAgent;
    const mobileRegex =
      /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile|mobile|CriOS/i;
    return mobileRegex.test(userAgent);
  };

  const processFile = ({
    fullBase64String,
    file,
  }: {
    fullBase64String: string;
    file: File;
  }) => {
    const [fileType] = file && file.type.split('/');
    if (!['image'].includes(fileType)) {
      alert(`Only supported file types are : ${['image'].join(', ')}`);
      return;
    }

    if (file && file.size > 2 * 1024 * 1024) {
      alert(`File size should not exceed : 2 MB`);
      return;
    }

    const base64WithoutPrefix = fullBase64String.replace(
      /^data:image\/[a-z]+;base64,/,
      '',
    );
    const sizeInKB = (base64WithoutPrefix.length * 3) / 4 / 1024;
    // Compress image only if it larger than 200KB
    const shouldCompress = sizeInKB > 200;

    if (shouldCompress) {
      compressImage(
        fullBase64String,
        file.type,
        true,
        (compressedBase64: string) => {
          setInputFileContentCompressed(compressedBase64);
          setInputFileContent(fullBase64String);
          setInputFile(file?.name);
          const extension = file.name.split('.').pop() ?? 'jpg';
          setInputFileExtension(extension.toLowerCase());
        },
      );
    } else {
      // If no compression is needed, use the original image data
      setInputFileContent(fullBase64String);
      setInputFileContentCompressed(fullBase64String);
      setInputFile(file.name);
      const extension = file.name.split('.').pop() ?? 'jpg';
      setInputFileExtension(extension.toLowerCase());
    }
  };

  const handleInitModal = () => {
    const selectedPrompt = filteredPrompts[activePromptIndex];
    if (selectedPrompt) {
      setContent((prevContent) => {
        const newContent = prevContent?.replace(
          /\/\w*$/,
          selectedPrompt.content,
        );
        return newContent;
      });
      handlePromptSelect(selectedPrompt);
    }
    setShowPromptList(false);
  };

  const parseVariables = (content: string) => {
    const regex = /{{(.*?)}}/g;
    const foundVariables = [];
    let match;

    while ((match = regex.exec(content)) !== null) {
      foundVariables.push(match[1]);
    }

    return foundVariables;
  };

  const handleSubmit = (updatedVariables: string[]) => {
    const newContent = content?.replace(/{{(.*?)}}/g, (match, variable) => {
      const index = variables.indexOf(variable);
      return updatedVariables[index];
    });

    setContent(newContent);

    if (textareaRef && textareaRef.current) {
      textareaRef.current.focus();
    }
  };

  // Additional handlers for drag and drop
  const handleDragOver = (e: React.DragEvent<HTMLTextAreaElement>) => {
    e.preventDefault(); // Necessary to allow the drop event
  };

  const handleDrop = (e: React.DragEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      const reader = new FileReader();
      reader.onload = (loadEvent) => {
        const fullBase64String = loadEvent.target?.result;
        processFile({ fullBase64String, file });
      };
      reader.readAsDataURL(file);
    }
  };

  const handlePaste = (event: {
    clipboardData: any;
    originalEvent: { clipboardData: any };
  }) => {
    const clipboardData =
      event.clipboardData || event.originalEvent.clipboardData;
    let items = clipboardData.items;
    let isImagePasted = false;

    if (items) {
      for (const item of items) {
        if (item.type.indexOf('image') === 0) {
          isImagePasted = true;
          const file = item.getAsFile();
          // Reading the image as Data URL (base64)
          const reader = new FileReader();
          reader.onload = (loadEvent) => {
            const fullBase64String = loadEvent.target?.result;
            processFile({ fullBase64String, file });
          };
          reader.readAsDataURL(file);
          break; // Stop checking after finding image, preventing any text setting
        }
      }
    }

    // Handle text only if no image was pasted
    if (!isImagePasted) {
      let text = clipboardData.getData('text/plain');
      if (text) {
        // setContent(text); // Set text content only if text is pasted
      }
    }
  };

  useEffect(() => {
    if (textareaRef && textareaRef.current) {
      textareaRef.current.style.height = 'inherit';
      textareaRef.current.style.height = `${textareaRef.current?.scrollHeight}px`;
      textareaRef.current.style.overflow = `${
        textareaRef?.current?.scrollHeight > 400 ? 'auto' : 'hidden'
      }`;
    }
  }, [content, textareaRef]);

  const handleSpeechToText = useCallback(() => {
    if (!recognitionRef.current) {
      const SpeechRecognition =
        window?.SpeechRecognition || window?.webkitSpeechRecognition;

      recognitionRef.current = new SpeechRecognition();
      recognitionRef.current.lang = 'en-US';
      recognitionRef.current.interimResults = true;
      recognitionRef.current.continuous = true;

      recognitionRef.current.onresult = (event) => {
        let currentTranscript = '';
        for (let i = 0; i < event.results.length; i++) {
          currentTranscript += event.results[i][0].transcript;
        }
        setContent(currentTranscript);
      };

      recognitionRef.current.onend = () => {
        if (isRecording) {
          recognitionRef.current.start();
        }
      };
    }

    if (!isRecording) {
      // Play sound when recording starts (only if audio file is available)
      if (recordingStartSound) {
        recordingStartSound.play().catch(error => {
          console.log('Could not play recording sound:', error);
        });
      }
      recognitionRef.current.start();
      setIsRecording(true);
    } else {
      recognitionRef.current.stop();
      setIsRecording(false);
    }
  }, [isRecording]);

  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
    };
  }, []);

  const leftButtonCount = (chatInputMicEnabled ? 1 : 0) + (chatUploadFileEnabled ? 1 : 0);
  const hasLeftButtons = leftButtonCount > 0;
  // Padding so 8px gap between last icon and text: 0 = minimal, 1 = pl-11 (44px), 2 = pl-[76px]
  const leftPaddingClass =
    leftButtonCount === 0 ? 'pl-3 sm:pl-4' : leftButtonCount === 2 ? 'pl-[76px]' : 'pl-11';

  return (
    <div
      className={`absolute bottom-0 left-0 w-full border-transparent bg-gradient-to-b from-transparent via-white to-white pt-6 dark:border-white/20 dark:via-black dark:to-black pointer-events-none ${
        isMobile() ? 'pb-14' : 'pb-4'
      }`}
    >
      <div className="stretch mx-auto mt-4 flex flex-row gap-3 last:mb-2 md:mt-[52px] w-full max-w-[95%] pointer-events-auto">
        {messageIsStreaming && (
          <button
            className="absolute top-0 left-0 right-0 mx-auto mb-3 flex w-fit items-center gap-3 rounded border border-neutral-200 bg-white py-2 px-4 text-black hover:opacity-50 dark:border-neutral-600 dark:bg-black dark:text-white md:mb-0 md:mt-2"
            onClick={handleStopConversation}
          >
            <IconPlayerStop size={16} /> {t('Stop Generating')}
          </button>
        )}

        {!messageIsStreaming &&
          selectedConversation &&
          selectedConversation.messages.length > 1 && (
            // selectedConversation.messages[selectedConversation.messages.length - 1].role === 'assistant' &&
            <button
              className="absolute top-0 left-0 right-0 mx-auto mb-3 flex w-fit items-center gap-3 rounded border border-neutral-200 bg-white py-2 px-4 text-black hover:opacity-50 dark:border-neutral-600 dark:bg-black dark:text-white md:mb-0 md:mt-2"
              onClick={onRegenerate}
            >
              <IconRepeat size={16} /> {t('Regenerate response')}
            </button>
          )}

        <div className="relative mx-2 flex w-full flex-grow flex-col rounded-md border border-black/10 bg-white shadow-[0_0_10px_rgba(0,0,0,0.10)] dark:border-neutral-700 dark:bg-black dark:text-white dark:shadow-[0_0_15px_rgba(0,0,0,0.10)] sm:mx-4">
          {!content && !isRecording && queryContextItems.length === 0 && (
            <div
              className={`pointer-events-none absolute inset-0 flex items-center py-2 text-gray-500 dark:text-gray-400 md:py-3 ${leftPaddingClass} ${paramFields.length > 0 ? 'pr-20' : 'pr-12'}`}
              aria-hidden
            >
              <span className="min-w-0 truncate">
                Unlock {workflow} knowledge and expertise
              </span>
            </div>
          )}
          {queryContextItems.length > 0 && (
            <div className={`flex flex-wrap gap-1.5 pt-2 pr-12 ${
              chatUploadFileEnabled
                ? 'pl-12 sm:pl-18 md:pl-20'
                : 'pl-10 sm:pl-12 md:pl-14'
            }`}>
              {queryContextItems.map((item) => (
                <span
                  key={item.id}
                  className="inline-flex items-center gap-1 rounded-md bg-gray-100 dark:bg-gray-600 text-xs text-gray-700 dark:text-gray-200 pl-2 pr-1 py-1 max-w-[200px]"
                  title={`${item.label} (${item.type})`}
                >
                  <span className="truncate">{item.label}</span>
                  <button
                    type="button"
                    onClick={() => onRemoveQueryContext?.(item.id)}
                    className="flex-shrink-0 rounded hover:bg-gray-300 dark:hover:bg-gray-500 p-0.5"
                    aria-label={`Remove ${item.label}`}
                  >
                    <IconX size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <textarea
            data-testid="chat-textarea"
            ref={textareaRef}
            className={`m-0 w-full resize-none border-0 bg-transparent p-0 py-2 text-black dark:bg-transparent dark:text-white md:py-3 outline-none ${leftPaddingClass} ${paramFields.length > 0 ? 'pr-20' : 'pr-12'}`}
            style={{
              resize: 'none',
              bottom: `${textareaRef?.current?.scrollHeight}px`,
              minHeight: '44px',
              maxHeight: '400px',
              overflow: `${
                textareaRef.current && textareaRef.current.scrollHeight > 400
                  ? 'auto'
                  : 'hidden'
              }`,
            }}
            placeholder={isRecording ? 'Listening...' : ''}
            aria-label={isRecording ? 'Listening...' : `Unlock ${workflow} knowledge and expertise`}
            value={content}
            rows={1}
            onCompositionStart={() => setIsTyping(true)}
            onCompositionEnd={() => setIsTyping(false)}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            {...(appConfig?.fileUploadEnabled && {
              onDragOver: handleDragOver,
              onDrop: handleDrop,
              onPaste: handlePaste,
            })}
          />
          {inputFile && inputFileContent && (
            <div>
              <div className="relative right-0 top-0 p-1 bg-[#91c438] dark:bg-green-700 text-black dark:text-white flex items-center justify-start gap-2 rounded-small">
                <IconPhoto className="ml-8" size={16} />
                <span>{inputFile}</span>
                <IconTrash
                  className="hover:text-[#ff1717e9] cursor-pointer"
                  size={16}
                  onClick={handleInputFileDelete}
                />
              </div>
            </div>
          )}
          {appConfig?.fileUploadEnabled && !inputFile && (
            <>
              <button
                className="absolute right-10 top-2 rounded-sm p-1 text-neutral-800 opacity-60 hover:text-[#76b900] dark:bg-opacity-50 dark:text-neutral-100 dark:hover:text-neutral-200"
                onClick={triggerFileUpload}
              >
                {messageIsStreaming ? (
                  <></>
                ) : (
                  <>
                    <IconPaperclip size={18} />
                  </>
                )}
              </button>
              <input
                type="file"
                ref={fileInputRef}
                style={{ display: 'none' }}
                onChange={handleFileChange}
              />
            </>
          )}
          {hasLeftButtons && (
          <div className="absolute left-2 top-2 flex gap-1">
            {chatInputMicEnabled && (
              <button
                onClick={handleSpeechToText}
                className={`rounded-sm p-[5px] text-neutral-800 opacity-60 dark:bg-opacity-50 dark:text-neutral-100 ${
                  messageIsStreaming
                    ? 'text-neutral-400' // Disable hover and change color when streaming
                    : 'hover:text-[#76b900] dark:hover:text-neutral-200' // Normal hover effect
                }`}
                disabled={messageIsStreaming}
              >
                {isRecording ? (
                  <IconPlayerStopFilled
                    size={18}
                    className="text-red-500 animate-blink"
                  />
                ) : (
                  <IconMicrophone size={18} />
                )}
              </button>
            )}
            {chatUploadFileEnabled && (
              <ChatFileUpload
                disabled={messageIsStreaming}
                onSendHiddenMessage={(message) => {
                  onSend({ role: 'user', content: message, hidden: true }, fieldsToParams(paramFields));
                }}
              >
                {({ triggerUpload }) => (
                  <button
                    onClick={triggerUpload}
                    className={`rounded-sm p-[5px] text-neutral-800 opacity-60 dark:bg-opacity-50 dark:text-neutral-100 ${
                      messageIsStreaming
                        ? 'text-neutral-400'
                        : 'hover:text-[#76b900] dark:hover:text-neutral-200'
                    }`}
                    disabled={messageIsStreaming}
                  >
                    <IconUpload size={18} />
                  </button>
                )}
              </ChatFileUpload>
            )}
          </div>
          )}
          {/* Settings Button - only show when there are enabled params */}
          {paramFields.length > 0 && (
            <div className="absolute right-10 top-2">
              <button
                ref={settingsButtonRef}
                className={`rounded-sm p-1 text-neutral-800 opacity-60 hover:text-[#76b900] dark:bg-opacity-50 dark:text-neutral-100 dark:hover:text-neutral-200 transition-colors ${
                  showCustomParams ? 'text-[#76b900] dark:text-[#76b900]' : ''
                }`}
                onClick={() => setShowCustomParams(!showCustomParams)}
                title="Agent Parameters"
              >
                <IconBrain size={18} />
              </button>
              <CustomAgentParams
                isOpen={showCustomParams}
                onClose={() => setShowCustomParams(false)}
                fields={paramFields}
                onFieldsChange={setParamFields}
                anchorRef={settingsButtonRef}
              />
            </div>
          )}
          {/* Send Button */}
          <button
            className="absolute right-2 top-2 rounded-sm p-1 text-neutral-800 opacity-60 hover:bg-neutral-200 hover:text-neutral-900 dark:bg-opacity-50 dark:text-neutral-100 dark:hover:text-neutral-200"
            onClick={handleSend}
          >
            {messageIsStreaming ? (
              <div data-testid="chat-loading-spinner" className="h-4 w-4 animate-spin rounded-full border-t-2 border-neutral-800 opacity-60 dark:border-neutral-100"></div>
            ) : (
              <IconSend size={18} />
            )}
          </button>

          {showScrollDownButton && (
            <div className="absolute bottom-12 right-0 lg:bottom-2 lg:-right-10">
              <button
                className="flex h-7 w-7 items-center justify-center rounded-full bg-neutral-300 text-gray-800 shadow-md hover:shadow-lg focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-900 dark:text-neutral-200"
                onClick={onScrollDownClick}
              >
                <IconArrowDown size={18} />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

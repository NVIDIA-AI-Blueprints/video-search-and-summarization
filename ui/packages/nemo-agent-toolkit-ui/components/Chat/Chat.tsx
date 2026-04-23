
'use client';

import { ChatHeader } from './ChatHeader';
import { ChatInput } from './ChatInput';
import { ChatLoader } from './ChatLoader';
import { MemoizedChatMessage } from './MemoizedChatMessage';
import { CustomAgentParamsValues } from './CustomAgentParams';
import { InteractionModal } from '@/components/Chat/ChatInteractionMessage';
import HomeContext from '@/pages/api/home/home.context';
import {
  ChatBody,
  Conversation,
  Message,
  CustomAgentParams,
  QueryDataContext,
  type CallerInfo,
} from '@/types/chat';
import {
  WebSocketInbound,
  validateWebSocketMessage,
  validateWebSocketMessageWithConversationId,
  validateConversationId,
  isSystemResponseMessage,
  isSystemIntermediateMessage,
  isSystemInteractionMessage,
  isErrorMessage,
  isSystemResponseInProgress,
  isSystemResponseComplete,
  isOAuthConsentMessage,
  extractOAuthUrl,
  shouldAppendResponseContent,
} from '@/types/websocket';
import { getEndpoint } from '@/utils/app/api';
import { webSocketMessageTypes } from '@/utils/app/const';
import {
  saveConversation,
  saveConversations,
  updateConversation,
} from '@/utils/app/conversation';
import {
  fetchLastMessage,
  processIntermediateMessage,
  updateConversationTitle,
} from '@/utils/app/helper';
import {
  shouldAppendResponse,
  appendAssistantText,
  mergeIntermediateSteps,
  applyMessageUpdate,
  createAssistantMessage,
  updateAssistantMessage,
  shouldRenderAssistantMessage,
} from '@/utils/chatTransform';
import { throttle } from '@/utils/data/throttle';
import { useTranslation } from 'next-i18next';
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { v4 as uuidv4 } from 'uuid';

import { SESSION_COOKIE_NAME } from '@/constants/constants';




// Streaming utilities for handling SSE and NDJSON safely
function normalizeNewlines(s: string): string {
  // turn CRLF into LF so splitting is predictable
  return s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

function extractSsePayloads(buffer: string): {
  frames: string[];
  rest: string;
} {
  buffer = normalizeNewlines(buffer);

  // Split on blank line (event delimiter)
  const parts = buffer.split(/\n\n/);
  const rest = parts.pop() ?? '';

  const frames: string[] = [];

  for (const block of parts) {
    // Keep only lines that start with "data:" possibly followed by a space
    const dataLines = block
      .split('\n')
      .filter(line => /^data:\s*/.test(line))
      .map(line => line.replace(/^data:\s*/, '').trim())
      .filter(line => line.length > 0);

    if (dataLines.length === 0) continue;

    // Some servers send multi-line JSON; join them
    const payload = dataLines.join('\n');

    // Ignore sentinel frames
    if (payload === '[DONE]' || payload === 'DONE') continue;

    frames.push(payload);
  }

  return { frames, rest };
}

function splitNdjson(buffer: string): { lines: string[]; rest: string } {
  buffer = normalizeNewlines(buffer);
  const parts = buffer.split('\n');
  const rest = parts.pop() ?? '';
  // strip empty/whitespace lines
  const lines = parts.map(l => l.trim()).filter(Boolean);
  return { lines, rest };
}

function tryParseJson<T = any>(s: string): T | null {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

function parsePossiblyConcatenatedJson(payload: string): any[] {
  // Fast path
  const single = tryParseJson(payload);
  if (single !== null) return [single];

  // Slow path: try to split concatenated top-level objects
  const objs: any[] = [];
  let depth = 0,
    start = -1;
  for (let i = 0; i < payload.length; i++) {
    const ch = payload[i];
    if (ch === '{') {
      if (depth === 0) start = i;
      depth++;
    } else if (ch === '}') {
      depth--;
      if (depth === 0 && start !== -1) {
        const slice = payload.slice(start, i + 1);
        const parsed = tryParseJson(slice);
        if (parsed !== null) objs.push(parsed);
        start = -1;
      }
    }
  }
  return objs;
}

// Debug helper for streaming parse issues (commented out for production)
// const debugParse = (label: string, payload: string) => {
//   const preview = payload.length > 200 ? payload.slice(0, 200) + '…' : payload;
//   console.debug(`[stream][${label}] payload preview:`, preview);
// };

export const Chat = () => {
  const { t } = useTranslation('chat');
  const {
    state: {
      selectedConversation,
      conversations,
      messageIsStreaming,
      loading,
      chatHistory,
      webSocketConnected,
      webSocketMode,
      webSocketURL,
      webSocketSchema,
      chatCompletionURL,
      expandIntermediateSteps,
      intermediateStepOverride,
      enableIntermediateSteps,
      chatMessageEditEnabled,
      chatMessageSpeakerEnabled,
      chatMessageCopyEnabled,
      interactionModalCancelEnabled,
    },
    storageKeyPrefix,
    handleUpdateConversation,
    dispatch: homeDispatch,
    onAnswerComplete,
    onAnswerCompleteWithContent,
    onSubmitMessageReady,
    onMessageSubmitted,
    onAddQueryContextReady,
  } = useContext(HomeContext);

  const [queryContextItems, setQueryContextItems] = useState<QueryDataContext[]>([]);
  const queryContextRef = useRef<QueryDataContext[]>([]);
  useEffect(() => { queryContextRef.current = queryContextItems; }, [queryContextItems]);

  const handleAddQueryContext = useCallback((item: QueryDataContext) => {
    setQueryContextItems((prev) => {
      if (prev.some((c) => c.id === item.id)) return prev;
      return [...prev, item];
    });
  }, []);

  const handleRemoveQueryContext = useCallback((itemId: string) => {
    setQueryContextItems((prev) => prev.filter((c) => c.id !== itemId));
  }, []);

  const [currentMessage, setCurrentMessage] = useState<Message>();
  const [autoScrollEnabled, setAutoScrollEnabled] = useState<boolean>(true);
  const [showSettings, setShowSettings] = useState<boolean>(false);
  const [showScrollDownButton, setShowScrollDownButton] =
    useState<boolean>(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const controllerRef = useRef(new AbortController());
  const selectedConversationRef = useRef(selectedConversation);
  const conversationsRef = useRef(conversations);

  const [modalOpen, setModalOpen] = useState(false);
  const [interactionMessage, setInteractionMessage] = useState(null);
  const webSocketRef = useRef<WebSocket | null>(null);
  const webSocketConnectedRef = useRef(false);
  // Initialize with state value, will be properly set in useEffect after checking sessionStorage
  const webSocketModeRef = useRef<boolean | undefined>(webSocketMode);
  let websocketLoadingToastId: string | null = null;
  
  // Sync webSocketModeRef with sessionStorage and state changes
  // This runs on mount and whenever webSocketMode state changes
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const storedValue = sessionStorage.getItem('webSocketMode');
      if (storedValue !== null) {
        // User has a stored preference from toggling - respect it
        webSocketModeRef.current = storedValue === 'true';
      } else {
        // No stored preference - use the env-based default from state
        webSocketModeRef.current = webSocketMode ?? false;
      }
    }
  }, [webSocketMode]);
  const lastScrollTop = useRef(0); // Store last known scroll position

  // Add these variables near the top of your component
  const isUserInitiatedScroll = useRef(false);
  const scrollTimeout = useRef<NodeJS.Timeout | null>(null);

  // WebSocket message tracking for stop generating functionality
  const activeUserMessageId = useRef<string | null>(null);
  
  // WebSocket throttling for state updates - reduces render cycles while maintaining smooth streaming
  const WS_THROTTLE_MS = 32; // ~30fps update rate
  const wsLastDispatchTime = useRef<number>(0);
  const wsPendingUpdate = useRef<{
    conversationId: string;
    messages: Message[];
  } | null>(null);
  const wsFlushTimeout = useRef<NodeJS.Timeout | null>(null);
  
  // Store custom agent params for use in handleSend
  const customAgentParamsRef = useRef<CustomAgentParamsValues | null>(null);
  
  // Ref to store the latest handleSend function for stable callbacks
  // This prevents unnecessary re-renders of memoized chat messages
  const handleSendRef = useRef<(message: Message, deleteCount?: number, retry?: boolean) => Promise<void>>();

  /**
   * Handles stopping conversation generation for WebSocket mode
   * Marks the current active user message as stopped and resets UI state
   */
  const handleStopConversation = useCallback(() => {
    if (webSocketModeRef?.current) {
      console.log('Stopping generation for user message:', activeUserMessageId.current);

      // Set active user message ID to null to ignore subsequent messages
      activeUserMessageId.current = null;

      // Reset UI state
      homeDispatch({ field: 'loading', value: false });
      homeDispatch({ field: 'messageIsStreaming', value: false });
    } else {
      // HTTP mode - use the existing abort controller logic
      try {
        controllerRef?.current?.abort('aborted');
        setTimeout(() => {
          controllerRef.current = new AbortController(); // Reset the controller
        }, 100);
      } catch (error) {
        console.log('error aborting - ', error);
      }
    }
  }, [webSocketModeRef, homeDispatch]);

  const openModal = (data: any = {}) => {
    setInteractionMessage(data);
    setModalOpen(true);
    // Notify embedder
    onAnswerComplete?.();
  };

  const handleUserInteraction = ({
    interactionMessage = {},
    userResponse = '',
  }: any) => {
    // todo send user input to websocket server as user response to interaction message
    // console.log("User response:", userResponse);
    const wsMessage = {
      type: webSocketMessageTypes.userInteractionMessage,
      id: uuidv4(), //new id for every new message
      thread_id: interactionMessage?.thread_id, // same thread_id from interaction message received
      parent_id: interactionMessage?.parent_id, // same parent_id from interaction message received
      content: {
        messages: [
          {
            role: 'user',
            content: [
              {
                type: 'text',
                text: userResponse,
              },
            ],
          },
        ],
      },
      timestamp: new Date().toISOString(),
    };
    webSocketRef?.current?.send(JSON.stringify(wsMessage));
  };

  useEffect(() => {
    selectedConversationRef.current = selectedConversation;
  }, [selectedConversation]);

  // Keep conversationsRef in sync with state so that "Clear conversations" and other
  // state-driven changes are reflected. Skip syncing while streaming so we don't
  // overwrite the ref with stale state (streaming handlers update the ref directly).
  useEffect(() => {
    if (!messageIsStreaming) {
      conversationsRef.current = conversations;
    }
  }, [conversations, messageIsStreaming]);
  
  // Reset WebSocket state when conversation changes to prevent stale message display
  useEffect(() => {
    if (selectedConversation?.id) {
      // Clear any pending WebSocket message tracking
      activeUserMessageId.current = null;

      // Clear WebSocket throttling state
      wsLastDispatchTime.current = 0;
      wsPendingUpdate.current = null;
      if (wsFlushTimeout.current) {
        clearTimeout(wsFlushTimeout.current);
        wsFlushTimeout.current = null;
      }

      // Clear streaming states to ensure clean conversation switch
      homeDispatch({ field: 'messageIsStreaming', value: false });
      homeDispatch({ field: 'loading', value: false });
    }
  }, [selectedConversation?.id]);

  useEffect(() => {
    if (webSocketModeRef?.current && !webSocketConnectedRef.current) {
      connectWebSocket();
    }

    // todo cancel ongoing connection attempts
    else {
      if (websocketLoadingToastId) toast.dismiss(websocketLoadingToastId);
    }

    return () => {
      if (webSocketRef?.current) {
        webSocketRef?.current?.close();
        webSocketConnectedRef.current = false;
      }
    };
    // Use webSocketMode state instead of ref in dependencies - refs don't trigger re-renders
  }, [webSocketMode, webSocketURL]);

  const connectWebSocket = async (retryCount = 0) => {
    const maxRetries = 3;
    const retryDelay = 1000; // 1-second delay between retries

    if (!(sessionStorage.getItem('webSocketURL') || webSocketURL)) {
      toast.error('Please set a valid WebSocket server in settings');
      return false;
    }

    return new Promise(resolve => {
      // Universal cookie handling for both cross-origin and same-origin connections
      const getCookie = (name: string) => {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
        return null;
      };

      const sessionCookie = getCookie(SESSION_COOKIE_NAME);
      let wsUrl: string =
        sessionStorage.getItem('webSocketURL') ||
        webSocketURL ||
        'ws://127.0.0.1:8000/websocket';

      // Determine if this is a cross-origin connection
      const wsUrlObj = new URL(wsUrl);
      const isCrossOrigin = wsUrlObj.origin !== window.location.origin;

      // Always add session cookie as query parameter for reliability
      // This works for both cross-origin (required) and same-origin (redundant but harmless)
      if (sessionCookie) {
        const separator = wsUrl.includes('?') ? '&' : '?';
        wsUrl += `${separator}session=${encodeURIComponent(sessionCookie)}`;
      } else {
      }

      const ws = new WebSocket(wsUrl);

      websocketLoadingToastId = toast.loading(
        'WebSocket is not connected, trying to connect...',
        { id: 'websocketLoadingToastId' }
      );

      ws.onopen = () => {
        toast.success(
          'Connected to ' +
            (sessionStorage.getItem('webSocketURL') || webSocketURL),
          {
            id: 'websocketSuccessToastId',
          }
        );
        if (websocketLoadingToastId) toast.dismiss(websocketLoadingToastId);

        // using ref due to usecallback for handlesend which will be recreated during next render when dependency array changes
        // so values inside of are still one and be updated after next render
        // so we'll not see any changes to websocket (state variable) or webSocketConnected (context variable) changes while the function is executing
        webSocketConnectedRef.current = true;
        homeDispatch({ field: 'webSocketConnected', value: true });
        webSocketRef.current = ws;
        resolve(true); // Resolve true only when connected
      };

      ws.onmessage = event => {
        const message = JSON.parse(event.data);
        handleWebSocketMessage(message);
      };

      ws.onclose = async () => {
        if (retryCount < maxRetries) {
          retryCount++;

          // Retry and capture the result
          if (webSocketModeRef?.current) {
            // Wait for retry delay
            await new Promise(res => setTimeout(res, retryDelay));

            const success = await connectWebSocket(retryCount);
            resolve(success);
          }
        } else {
          // Only resolve(false) after all retries fail
          homeDispatch({ field: 'webSocketConnected', value: false });
          webSocketConnectedRef.current = false;
          homeDispatch({ field: 'loading', value: false });
          homeDispatch({ field: 'messageIsStreaming', value: false });
          if (websocketLoadingToastId) toast.dismiss(websocketLoadingToastId);
          toast.error('WebSocket connection failed.', {
            id: 'websocketErrorToastId',
          });
          resolve(false);
        }
      };

      ws.onerror = error => {
        homeDispatch({ field: 'webSocketConnected', value: false });
        webSocketConnectedRef.current = false;
        homeDispatch({ field: 'loading', value: false });
        homeDispatch({ field: 'messageIsStreaming', value: false });
        ws.close(); // Ensure the WebSocket is closed on error
      };
    });
  };

  // Re-attach the WebSocket handler when intermediateStepOverride changes because we need updated value from settings
  useEffect(() => {
    if (webSocketRef.current) {
      webSocketRef.current.onmessage = event => {
        const message = JSON.parse(event.data);
        handleWebSocketMessage(message);
      };
    }
  }, [intermediateStepOverride]);

  /**
   * Handles OAuth consent flow by opening popup window
   */
  const handleOAuthConsent = (message: WebSocketInbound) => {
    if (!isSystemInteractionMessage(message)) return false;

    if (message.content?.input_type === 'oauth_consent') {
      const oauthUrl = extractOAuthUrl(message);
      if (oauthUrl) {
        const popup = window.open(
          oauthUrl,
          'oauth-popup',
          'width=600,height=700,scrollbars=yes,resizable=yes'
        );
        const handleOAuthComplete = (event: MessageEvent) => {
          if (popup && !popup.closed) popup.close();
          window.removeEventListener('message', handleOAuthComplete);
        };
        window.addEventListener('message', handleOAuthComplete);
      }
      return true;
    }
    return false;
  };

  /**
   * Updates refs immediately before React dispatch to prevent stale reads
   */
  const updateRefsAndDispatch = (
    updatedConversations: Conversation[],
    updatedConversation: Conversation,
    currentSelectedConversation: Conversation | null | undefined
  ) => {
    // Write-through to refs before dispatch to avoid stale reads on next WS tick
    conversationsRef.current = updatedConversations;
    if (currentSelectedConversation?.id === updatedConversation.id) {
      selectedConversationRef.current = updatedConversation;
    }

    // Dispatch and persist
    homeDispatch({ field: 'conversations', value: updatedConversations });
    saveConversations(updatedConversations, storageKeyPrefix);

    if (currentSelectedConversation?.id === updatedConversation.id) {
      homeDispatch({
        field: 'selectedConversation',
        value: updatedConversation,
      });
      saveConversation(updatedConversation, storageKeyPrefix);
    }
  };

  /**
   * Processes system response messages for content updates
   * Only appends content for in_progress status with non-empty text
   */
  const processSystemResponseMessage = (
    message: WebSocketInbound,
    messages: Message[]
  ): Message[] => {
    if (!shouldAppendResponse(message)) {
      return messages;
    }

    const incomingText = isSystemResponseMessage(message)
      ? message.content?.text || ''
      : '';
    const lastMessage = messages.at(-1);
    const isLastAssistant = lastMessage?.role === 'assistant';

    if (isLastAssistant) {
      // Append to existing assistant message using pure helper
      const combinedContent = appendAssistantText(
        lastMessage.content || '',
        incomingText
      );
      return messages.map((m, idx) =>
        idx === messages.length - 1
          ? updateAssistantMessage(m, combinedContent)
          : m
      );
    } else {
      // Create new assistant message using pure helper
      return [
        ...messages,
        createAssistantMessage(message.id, message.parent_id, incomingText),
      ];
    }
  };

  /**
   * Processes intermediate step messages without modifying content
   */
  const processIntermediateStepMessage = (
    message: WebSocketInbound,
    messages: Message[]
  ): Message[] => {
    if (!isSystemIntermediateMessage(message)) return messages;

    const lastMessage = messages.at(-1);
    const isLastAssistant = lastMessage?.role === 'assistant';

    if (!isLastAssistant) {
      // Create new assistant message with empty content for intermediate steps
      const stepWithIndex = { ...message, index: 0 };
      return [
        ...messages,
        createAssistantMessage(message.id, message.parent_id, '', [
          stepWithIndex,
        ]),
      ];
    } else {
      // Update intermediate steps on existing assistant message using pure helper
      const lastIdx = messages.length - 1;
      const lastSteps = messages[lastIdx]?.intermediateSteps || [];
      const mergedSteps = mergeIntermediateSteps(
        lastSteps,
        message,
        sessionStorage.getItem('intermediateStepOverride') === 'false'
          ? false
          : Boolean(intermediateStepOverride)
      );

      return messages.map((m, idx) =>
        idx === lastIdx ? updateAssistantMessage(m, m.content, mergedSteps) : m
      );
    }
  };

  /**
   * Processes error messages by attaching them to assistant messages
   */
  const processErrorMessage = (
    message: WebSocketInbound,
    messages: Message[]
  ): Message[] => {
    if (!isErrorMessage(message)) return messages;

    const lastMessage = messages.at(-1);
    const isLastAssistant = lastMessage?.role === 'assistant';

    if (isLastAssistant) {
      // Attach error to existing assistant message
      return messages.map((m, idx) =>
        idx === messages.length - 1
          ? {
              ...m,
              errorMessages: [...(m.errorMessages || []), message],
              timestamp: Date.now(),
            }
          : m
      );
    } else {
      // Create new assistant message for error using pure helper
      return [
        ...messages,
        createAssistantMessage(
          message.id,
          message.parent_id,
          '',
          [],
          [],
          [message]
        ),
      ];
    }
  };

  /**
   * Flushes pending WebSocket updates to state
   */
  const flushWsPendingUpdate = () => {
    if (!wsPendingUpdate.current) return;

    const { conversationId, messages } = wsPendingUpdate.current;
    const currentConversations = conversationsRef.current;
    const targetConversation = currentConversations.find(
      c => c.id === conversationId
    );

    if (!targetConversation) {
      wsPendingUpdate.current = null;
      return;
    }

    // Update conversation with pending messages
    const updatedConversation = applyMessageUpdate(
      targetConversation,
      messages
    );

    // Update conversations array
    const updatedConversations = currentConversations.map(c =>
      c.id === updatedConversation.id ? updatedConversation : c
    );

    // Update state and persistence
    updateRefsAndDispatch(
      updatedConversations,
      updatedConversation,
      selectedConversationRef.current
    );

    wsPendingUpdate.current = null;
    wsLastDispatchTime.current = Date.now();
  };

  /**
   * Main WebSocket message handler
   * Processes different message types and updates conversation state
   * Uses throttling to reduce render cycles (~30fps)
   */
  const handleWebSocketMessage = (message: any) => {
    // Validate message structure AND conversation ID with detailed error reporting
    try {
      validateWebSocketMessageWithConversationId(message);
    } catch (error: any) {
      console.error('WebSocket message validation failed:', error.message);
      toast.error(`WebSocket Error: ${error.message}`);

      // Log additional debugging info
      console.error('Raw message data:', message);
      console.error(
        'Available conversations:',
        conversationsRef.current?.map(c => ({ id: c.id, name: c.name }))
      );

      return; // Don't process invalid messages
    }

    // Filter messages based on active conversation for stop generating functionality
    const messageConversationId = message.conversation_id;
    const currentConversationId = selectedConversationRef.current?.id;

    if (activeUserMessageId.current === null || messageConversationId !== currentConversationId) {
      return;
    }

    // End loading indicators as messages arrive
    homeDispatch({ field: 'loading', value: false });
    
    // Check if this is a completion message
    const isComplete = isSystemResponseComplete(message);
    
    if (isComplete) {
      // Flush any pending updates immediately before completing
      if (wsFlushTimeout.current) {
        clearTimeout(wsFlushTimeout.current);
        wsFlushTimeout.current = null;
      }
      flushWsPendingUpdate();
      
      setTimeout(() => {
        homeDispatch({ field: 'messageIsStreaming', value: false });
        // Clear active tracking when response is complete
        activeUserMessageId.current = null;
        const conv = selectedConversationRef.current;
        const lastMsg = conv?.messages?.slice(-1)[0];
        if (lastMsg?.role === 'assistant' && typeof lastMsg.content === 'string') {
          onAnswerComplete?.();
          handleCompletedAnswerWithContent(lastMsg.content);
        }
      }, 200);
      return;
    }

    // Handle human-in-the-loop interactions using type guard
    if (isSystemInteractionMessage(message)) {
      // Check for OAuth consent message and automatically open OAuth URL directly
      if (message?.content?.input_type === 'oauth_consent') {
        // Expect the OAuth URL to be directly in the message content
        const oauthUrl =
          message?.content?.oauth_url ||
          message?.content?.redirect_url ||
          message?.content?.text;
        if (oauthUrl) {
          // Open the OAuth URL directly in a new tab
          window.open(oauthUrl, '_blank');
        } else {
          console.error(
            'OAuth consent message received but no URL found in content:',
            message?.content
          );
          toast.error('OAuth URL not found in message content');
        }
        return; // Don't process further or show modal
      }
      openModal(message);
      return;
    }

    // Respect intermediate-steps toggle
    if (
      sessionStorage.getItem('enableIntermediateSteps') === 'false' &&
      isSystemIntermediateMessage(message)
    ) {
      return;
    }

    // Find target conversation with enhanced error reporting
    const currentConversations = conversationsRef.current;
    const targetConversation = currentConversations.find(
      c => c.id === message.conversation_id
    );

    if (!targetConversation) {
      return;
    }

    // Process message based on type using pure helpers
    // Use pending messages as base if available for same conversation, otherwise use target conversation
    const pending = wsPendingUpdate.current;
    let baseMessages = 
      (pending && pending.conversationId === message.conversation_id)
        ? pending.messages
        : targetConversation.messages;
    
    let updatedMessages = baseMessages;
    updatedMessages = processSystemResponseMessage(message, updatedMessages);
    updatedMessages = processIntermediateStepMessage(message, updatedMessages);
    updatedMessages = processErrorMessage(message, updatedMessages);

    // Force string materialization on the last message content to prevent race conditions
    const lastMsg = updatedMessages.at(-1);
    if (lastMsg?.content) {
      void lastMsg.content.length;
    }

    // Store pending update
    wsPendingUpdate.current = {
      conversationId: message.conversation_id,
      messages: updatedMessages,
    };

    // Determine if we should dispatch now (throttled updates)
    const now = Date.now();
    const timeSinceLastDispatch = now - wsLastDispatchTime.current;
    const isFirstMessage = wsLastDispatchTime.current === 0 || baseMessages === targetConversation.messages;

    if (isFirstMessage || timeSinceLastDispatch >= WS_THROTTLE_MS) {
      // Dispatch immediately
      if (wsFlushTimeout.current) {
        clearTimeout(wsFlushTimeout.current);
        wsFlushTimeout.current = null;
      }
      flushWsPendingUpdate();
    } else {
      // Schedule a flush if not already scheduled
      if (!wsFlushTimeout.current) {
        wsFlushTimeout.current = setTimeout(() => {
          wsFlushTimeout.current = null;
          flushWsPendingUpdate();
        }, WS_THROTTLE_MS - timeSinceLastDispatch);
      }
    }
  };

  // CallerInfo is what the parent app passes to the Chat sidebar.
  // It can be used to annotate the latest assistant message with the caller info.
  const annotateLatestAssistantMessageWithCallerInfo = useCallback(
    (callerInfo?: CallerInfo) => {
      if (!callerInfo) return;
      const conversation = selectedConversationRef.current;
      const allConversations = conversationsRef.current;
      if (!conversation) return;
      const assistantIdx = conversation.messages
        .map((msg) => msg.role)
        .lastIndexOf('assistant');
      if (assistantIdx < 0) return;

      const currentInfo = conversation.messages[assistantIdx].callerInfo;
      if (currentInfo === callerInfo) return;

      const updatedMessages = [...conversation.messages];
      updatedMessages[assistantIdx] = {
        ...updatedMessages[assistantIdx],
        callerInfo,
      };
      const updatedConversation = { ...conversation, messages: updatedMessages };
      const { single, all } = updateConversation(
        updatedConversation,
        allConversations || [],
        storageKeyPrefix,
      );
      selectedConversationRef.current = single;
      conversationsRef.current = all;
      homeDispatch({ field: 'selectedConversation', value: single });
      homeDispatch({ field: 'conversations', value: all });
    },
    [storageKeyPrefix, homeDispatch],
  );

  const handleCompletedAnswerWithContent = useCallback(
    (answer: string) => {
      const callerInfo = onAnswerCompleteWithContent?.(answer);
      annotateLatestAssistantMessageWithCallerInfo(
        typeof callerInfo === 'string' ? callerInfo : undefined,
      );
    },
    [
      annotateLatestAssistantMessageWithCallerInfo,
      onAnswerCompleteWithContent,
    ],
  );

  const handleSend = useCallback(
    async (message: Message, deleteCount = 0, retry = false) => {
      // DON'T mutate the original message - create a new one with a new ID
      const messageWithNewId = {
        ...message,
        id: uuidv4()
      };

      // Set the active user message ID for WebSocket message tracking
      activeUserMessageId.current = messageWithNewId.id;

      // Notify embedder that a message was submitted (e.g. so Search tab can disable content until response completes)
      onMessageSubmitted?.();

      // chat with bot
      if (selectedConversation) {
        let updatedConversation: Conversation;
        if (deleteCount) {
          const updatedMessages = [...selectedConversation.messages];
          for (let i = 0; i < deleteCount; i++) {
            updatedMessages.pop();
          }
          updatedConversation = {
            ...selectedConversation,
            messages: [...updatedMessages, messageWithNewId],
          };
        } else {
          // remove content from attachment since it could a large base64 encoded string which can cause session stroage overflow
          // Clone the message and update the attachment contentconst updateMessage = JSON.parse(JSON.stringify(message));
          const updateMessage = JSON.parse(JSON.stringify(messageWithNewId));
          if (updateMessage?.attachment) {
            updateMessage.attachment.content = '';
          }
          updatedConversation = {
            ...selectedConversation,
            messages: [...selectedConversation.messages, { ...updateMessage }],
            // Remove isHomepageConversation flag when first message is sent to make it visible in sidebar
            isHomepageConversation: undefined,
          };
        }
        homeDispatch({
          field: 'selectedConversation',
          value: updatedConversation,
        });

        homeDispatch({ field: 'loading', value: true });
        homeDispatch({ field: 'messageIsStreaming', value: true });

        // websocket connection chat request
        if (webSocketModeRef?.current) {
          if (!webSocketConnectedRef?.current) {
            const connected = await connectWebSocket();
            if (!connected) {
              homeDispatch({ field: 'loading', value: false });
              homeDispatch({ field: 'messageIsStreaming', value: false });
              toast.error('Failed to send message. WebSocket connection could not be established.');
              return;
            } else {
              handleSend(messageWithNewId, 1);
              return;
            }
          }
          toast.dismiss();

          saveConversation(updatedConversation, storageKeyPrefix);
          // Use conversationsRef.current to avoid stale closure that causes conversation wiping
          const currentConversations = conversationsRef.current || [];
          const conversationExists = currentConversations.some(
            c => c.id === selectedConversation.id
          );
          
          let updatedConversations: Conversation[];
          if (conversationExists) {
            // Update existing conversation
            updatedConversations = currentConversations.map(conversation => {
              if (conversation.id === selectedConversation.id) {
                return updatedConversation;
              }
              return conversation;
            });
          } else {
            // Add new conversation if it doesn't exist in the array
            updatedConversations = [...currentConversations, updatedConversation];
          }
          
          // Update the ref immediately to prevent race condition with incoming WebSocket messages
          conversationsRef.current = updatedConversations;
          
          homeDispatch({
            field: 'conversations',
            value: updatedConversations,
          });
          saveConversations(updatedConversations, storageKeyPrefix);

          let chatMessages;
          if (chatHistory) {
            chatMessages = updatedConversation?.messages?.map(
              (message: Message) => {
                return {
                  role: message.role,
                  content: [
                    {
                      type: 'text',
                      text: message?.content?.trim() || '',
                    },
                    ...(typeof message?.content === 'object' &&
                    message?.content &&
                    'attachments' in message.content &&
                    (message.content as any).attachments?.length > 0
                      ? (message.content as any).attachments?.map(
                          (attachment: any) => ({
                            type: 'image',
                            image_url: attachment?.content,
                          })
                        )
                      : []),
                  ],
                };
              }
            );
          }
          // else set only the user last message
          else {
            chatMessages = [
              updatedConversation?.messages[
                updatedConversation?.messages?.length - 1
              ],
            ].map(message => {
              return {
                role: message.role,
                content: [
                  {
                    type: 'text',
                    text: message?.content?.trim() || '',
                  },
                ],
              };
            });
          }

                              const wsMessage = {
            // Spread custom params first so fixed fields take precedence
            ...(customAgentParamsRef.current || {}),
            type: webSocketMessageTypes.userMessage,
            schema_type:
              sessionStorage.getItem('webSocketSchema') || webSocketSchema,
            id: messageWithNewId?.id,
            conversation_id: selectedConversation.id,
            content: {
              messages: chatMessages,
            },
            timestamp: new Date().toISOString(),
          };

          // console.log('Sent message via websocket', wsMessage)
          webSocketRef?.current?.send(JSON.stringify(wsMessage));
          return;
        }

        // cleaning up messages to fit the request payload
        const messagesCleaned = updatedConversation.messages.map(message => {
          return {
            role: message.role,
            content: (typeof message.content === 'string'
              ? message.content
              : ''
            ).trim(),
          };
        });

        const chatBody: ChatBody = {
          // Spread custom params first so fixed fields take precedence
          ...(customAgentParamsRef.current || {}),
          messages: chatHistory
            ? messagesCleaned
            : [{ role: 'user', content: message?.content }],
          chatCompletionURL:
            sessionStorage.getItem('chatCompletionURL') || chatCompletionURL,
          additionalProps: {
            enableIntermediateSteps: sessionStorage.getItem(
              'enableIntermediateSteps'
            )
              ? sessionStorage.getItem('enableIntermediateSteps') === 'true'
              : enableIntermediateSteps,
          },
        };

        const endpoint = getEndpoint({ service: 'chat' });
        let body;
        body = JSON.stringify({
          ...chatBody,
        });

        let response;
        try {
          response = await fetch(`${window.location.origin}/${endpoint}`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Conversation-Id': selectedConversation?.id || '',
              'User-Message-ID': messageWithNewId?.id || '',
            },
            signal: controllerRef.current.signal,
            body,
          });

          if (!response?.ok) {
            homeDispatch({ field: 'loading', value: false });
            homeDispatch({ field: 'messageIsStreaming', value: false });
            toast.error(response.statusText);
            return;
          }

          const data = response?.body;
          if (!data) {
            homeDispatch({ field: 'loading', value: false });
            homeDispatch({ field: 'messageIsStreaming', value: false });
            toast.error('Error: No data received from server');
            return;
          }
          if (!false) {
            if (updatedConversation.messages.length === 1) {
              const { content } = message;
              const customName =
                content.length > 30
                  ? content.substring(0, 30) + '...'
                  : content;
              updatedConversation = {
                ...updatedConversation,
                name: customName,
              };
            }
            homeDispatch({ field: 'loading', value: false });
            const reader = data.getReader();
            const decoder = new TextDecoder();
            let done = false;
            let isFirst = true;
            let text = '';
            let counter = 1;
            let partialIntermediateStep = ''; // Add this to store partial chunks

            // Throttling for state updates - reduces render cycles while maintaining smooth streaming
            const THROTTLE_MS = 32; // ~30fps update rate
            let lastDispatchTime = 0;
            let pendingIntermediateSteps: any[] = []; // Accumulate intermediate steps between dispatches
            let hasPendingUpdate = false;

            // Initialize streaming buffers
            const currentURL =
              sessionStorage.getItem('chatCompletionURL') ||
              chatCompletionURL ||
              '';
            const isGenerateStream = currentURL.includes('generate');
            let sseBuffer = '';
            let ndjsonBuffer = '';

            while (!done) {
              const { value, done: doneReading } = await reader.read();
              done = doneReading;
              if (!value) continue;

              let chunkValue = '';

              // Handle generate/stream endpoints safely
              if (isGenerateStream) {
                const chunkText = decoder.decode(value, { stream: true });

                // 1) Try SSE first
                sseBuffer += chunkText;
                let sseFrames: string[] = [];
                ({ frames: sseFrames, rest: sseBuffer } =
                  extractSsePayloads(sseBuffer));

                let extractedText = '';

                if (sseFrames.length > 0) {
                  for (const frame of sseFrames) {
                    const objs = parsePossiblyConcatenatedJson(frame);
                    for (const obj of objs) {
                      if (obj && typeof obj.value === 'string') {
                        extractedText += obj.value;
                      } else if (typeof obj === 'string') {
                        extractedText += obj; // some servers may send string payloads
                      }
                    }
                  }
                } else {
                  // 2) Fall back to NDJSON if it wasn't SSE
                  ndjsonBuffer += chunkText;
                  let lines: string[] = [];
                  ({ lines, rest: ndjsonBuffer } = splitNdjson(ndjsonBuffer));
                  for (const line of lines) {
                    const obj = tryParseJson<any>(line);
                    if (obj && typeof obj.value === 'string') {
                      extractedText += obj.value;
                    } else if (typeof obj === 'string') {
                      extractedText += obj;
                    }
                  }
                }

                // 3) If neither SSE nor NDJSON detected, treat as plain text
                chunkValue = extractedText || chunkText;
              } else {
                // Non-generate streaming path (existing logic)
                chunkValue = decoder.decode(value, { stream: true });
              }

              // Ensure chunkValue is always a string
              if (typeof chunkValue !== 'string') {
                chunkValue = String(chunkValue ?? '');
              }

              counter++;

              // First, handle any partial chunk from previous iteration
              if (partialIntermediateStep) {
                chunkValue = partialIntermediateStep + chunkValue;
                partialIntermediateStep = '';
              }

              // Check for incomplete tags
              const openingTagIndex =
                chunkValue.lastIndexOf('<intermediatestep>');
              const closingTagIndex = chunkValue.lastIndexOf(
                '</intermediatestep>'
              );

              // If we have an opening tag without a closing tag (or closing tag comes before opening)
              if (openingTagIndex > closingTagIndex) {
                // Store the partial chunk for the next iteration
                partialIntermediateStep = chunkValue.substring(openingTagIndex);
                // Remove the partial chunk from current processing
                chunkValue = chunkValue.substring(0, openingTagIndex);
              }

              // Process complete intermediate steps
              let rawIntermediateSteps: any[] = [];
              let stepMatches =
                chunkValue.match(
                  /<intermediatestep>([\s\S]*?)<\/intermediatestep>/g
                ) || [];
              for (const stepMatch of stepMatches) {
                try {
                  const jsonString = stepMatch
                    .replace('<intermediatestep>', '')
                    .replace('</intermediatestep>', '')
                    .trim();
                  let rawIntermediateMessage = tryParseJson<any>(jsonString);
                  // handle intermediate data
                  if (rawIntermediateMessage?.type === 'system_intermediate') {
                    rawIntermediateSteps.push(rawIntermediateMessage);
                  }
                } catch (error) {
                  // console.log('Stream response parse error:', error.message);
                }
              }

              // if the received chunk contains rawIntermediateSteps then remove them from the chunkValue
              if (stepMatches.length > 0) {
                chunkValue = chunkValue.replace(
                  /<intermediatestep>[\s\S]*?<\/intermediatestep>/g,
                  ''
                );
              }

              text = text + chunkValue;
              // Force string materialization to prevent race condition with state updates
              // This ensures the string is fully evaluated before being passed to dispatch
              void text.length;

              // Accumulate intermediate steps for batched dispatch
              pendingIntermediateSteps.push(...rawIntermediateSteps);
              hasPendingUpdate = true;

              // Determine if we should dispatch now (throttled updates)
              const now = Date.now();
              const shouldDispatch = isFirst || done || (now - lastDispatchTime >= THROTTLE_MS);

              if (shouldDispatch && hasPendingUpdate) {
                lastDispatchTime = now;
                hasPendingUpdate = false;

                homeDispatch({ field: 'loading', value: false });

                if (isFirst) {
                  isFirst = false;

                  // loop through pendingIntermediateSteps and add them to the processedIntermediateSteps
                  let processedIntermediateSteps: any[] = [];
                  pendingIntermediateSteps.forEach(step => {
                    processedIntermediateSteps = processIntermediateMessage(
                      processedIntermediateSteps,
                      step,
                      sessionStorage.getItem('intermediateStepOverride') ===
                        'false'
                        ? false
                        : intermediateStepOverride
                    );
                  });
                  pendingIntermediateSteps = []; // Clear after processing

                  // update the message
                  const updatedMessages: Message[] = [
                    ...updatedConversation.messages,
                    {
                      role: 'assistant',
                      content: text, // main response content without intermediate steps
                      intermediateSteps: [...processedIntermediateSteps], // intermediate steps
                    },
                  ];

                  updatedConversation = {
                    ...updatedConversation,
                    messages: updatedMessages,
                  };

                  homeDispatch({
                    field: 'selectedConversation',
                    value: updatedConversation,
                  });
                } else {
                  const updatedMessages: Message[] =
                    updatedConversation.messages.map((message, index) => {
                      if (index === updatedConversation.messages.length - 1) {
                        // process intermediate steps
                        // need to loop through pendingIntermediateSteps and add them to the updatedIntermediateSteps
                        let updatedIntermediateSteps = Array.isArray(
                          message?.intermediateSteps
                        )
                          ? [...message.intermediateSteps]
                          : [];
                        pendingIntermediateSteps.forEach(step => {
                          updatedIntermediateSteps = processIntermediateMessage(
                            updatedIntermediateSteps,
                            step,
                            sessionStorage.getItem('intermediateStepOverride') ===
                              'false'
                              ? false
                              : intermediateStepOverride
                          );
                        });

                        // update the message
                        const msg = {
                          ...message,
                          content: text, // main response content
                          intermediateSteps: updatedIntermediateSteps, // intermediate steps
                        };
                        return msg;
                      }
                      return message;
                    });
                  pendingIntermediateSteps = []; // Clear after processing

                  updatedConversation = {
                    ...updatedConversation,
                    messages: updatedMessages,
                  };
                  homeDispatch({
                    field: 'selectedConversation',
                    value: updatedConversation,
                  });
                }
              }
            }

            // Final dispatch if there's any pending update after loop ends
            if (hasPendingUpdate) {
              const updatedMessages: Message[] =
                updatedConversation.messages.map((message, index) => {
                  if (index === updatedConversation.messages.length - 1) {
                    let updatedIntermediateSteps = Array.isArray(
                      message?.intermediateSteps
                    )
                      ? [...message.intermediateSteps]
                      : [];
                    pendingIntermediateSteps.forEach(step => {
                      updatedIntermediateSteps = processIntermediateMessage(
                        updatedIntermediateSteps,
                        step,
                        sessionStorage.getItem('intermediateStepOverride') ===
                          'false'
                          ? false
                          : intermediateStepOverride
                      );
                    });
                    return {
                      ...message,
                      content: text,
                      intermediateSteps: updatedIntermediateSteps,
                    };
                  }
                  return message;
                });
              updatedConversation = {
                ...updatedConversation,
                messages: updatedMessages,
              };
              homeDispatch({
                field: 'selectedConversation',
                value: updatedConversation,
              });
            }

            console.log(`[STREAMING] HTTP response complete detected at: ${new Date().toISOString()} (${performance.now().toFixed(2)}ms)`);
            
            saveConversation(updatedConversation, storageKeyPrefix);
            const updatedConversations: Conversation[] = conversations.map(
              conversation => {
                if (conversation.id === selectedConversation.id) {
                  return updatedConversation;
                }
                return conversation;
              }
            );
            if (updatedConversations.length === 0) {
              updatedConversations.push(updatedConversation);
            }
            homeDispatch({
              field: 'conversations',
              value: updatedConversations,
            });
            saveConversations(updatedConversations, storageKeyPrefix);
            // to show the message on UI and scroll to the bottom after 200ms delay
            setTimeout(() => {
              homeDispatch({ field: 'messageIsStreaming', value: false });
              homeDispatch({ field: 'loading', value: false });
              onAnswerComplete?.();
              handleCompletedAnswerWithContent(text);
            }, 200);
          } else {
            const { answer } = await response?.json();
            const updatedMessages: Message[] = [
              ...updatedConversation.messages,
              { role: 'assistant', content: answer },
            ];
            updatedConversation = {
              ...updatedConversation,
              messages: updatedMessages,
            };
            homeDispatch({
              field: 'selectedConversation',
              value: updatedConversation,
            });
            saveConversation(updatedConversation, storageKeyPrefix);
            const updatedConversations: Conversation[] = conversations.map(
              conversation => {
                if (conversation.id === selectedConversation.id) {
                  return updatedConversation;
                }
                return conversation;
              }
            );
            if (updatedConversations.length === 0) {
              updatedConversations.push(updatedConversation);
            }
            homeDispatch({
              field: 'conversations',
              value: updatedConversations,
            });
            saveConversations(updatedConversations, storageKeyPrefix);
            homeDispatch({ field: 'loading', value: false });
            homeDispatch({ field: 'messageIsStreaming', value: false });
            onAnswerComplete?.();
            handleCompletedAnswerWithContent(answer);
          }
        } catch (error) {
          saveConversation(updatedConversation, storageKeyPrefix);
          homeDispatch({ field: 'loading', value: false });
          homeDispatch({ field: 'messageIsStreaming', value: false });
          if (error === 'aborted' || (error as any)?.name === 'AbortError') {
            return;
          } else {
            return;
          }
        }
      }
    },
    [
      conversations,
      selectedConversation,
      homeDispatch,
      chatHistory,
      webSocketConnected,
      webSocketSchema,
      chatCompletionURL,
      expandIntermediateSteps,
      intermediateStepOverride,
      enableIntermediateSteps,
      storageKeyPrefix,
      onAnswerComplete,
      handleCompletedAnswerWithContent,
      onMessageSubmitted,
    ]
  );

  // Keep handleSendRef updated with the latest handleSend function
  // This allows handleEditMessage to remain stable while still calling the latest version
  useEffect(() => {
    handleSendRef.current = handleSend;
  }, [handleSend]);

  // Expose programmatic submit to embedder (send a message to the agent without user typing)
  useEffect(() => {
    if (!onSubmitMessageReady || !selectedConversation) return;
    const submitMessage = (content: string) => {
      const message: Message = { role: 'user', content };
      handleSendRef.current?.(message, 0);
      onMessageSubmitted?.();
    };
    onSubmitMessageReady(submitMessage);
  }, [onSubmitMessageReady, selectedConversation?.id, onMessageSubmitted]);

  // Expose addQueryContext to embedder so external panels can add context items to the chat input
  useEffect(() => {
    if (!onAddQueryContextReady) return;
    onAddQueryContextReady(handleAddQueryContext);
  }, [onAddQueryContextReady, handleAddQueryContext]);

  // Create stable onEdit callback to prevent unnecessary re-renders of MemoizedChatMessage
  // Uses ref to access the latest handleSend without depending on it directly
  const handleEditMessage = useCallback((editedMessage: Message, deleteCount?: number) => {
    setCurrentMessage(editedMessage);
    handleSendRef.current?.(editedMessage, deleteCount || 0);
  }, []); // Empty deps - stable reference forever

  // Create stable onDelete callback - uses refs to access latest state
  const handleDeleteMessage = useCallback((messageIndex: number) => {
    const conversation = selectedConversationRef.current;
    const allConversations = conversationsRef.current;
    
    if (!conversation) return;

    const { messages } = conversation;
    if (messageIndex < 0 || messageIndex >= messages.length) return;

    // Create a copy of messages to avoid mutating state directly
    const updatedMessages = [...messages];
    
    // If next message is assistant response, delete both
    if (
      messageIndex < updatedMessages.length - 1 &&
      updatedMessages[messageIndex + 1].role === 'assistant'
    ) {
      updatedMessages.splice(messageIndex, 2);
    } else {
      updatedMessages.splice(messageIndex, 1);
    }

    const updatedConversation = {
      ...conversation,
      messages: updatedMessages,
    };

    const { single, all } = updateConversation(
      updatedConversation,
      allConversations || [],
      storageKeyPrefix,
    );
    
    // Update refs immediately to prevent stale state
    selectedConversationRef.current = single;
    conversationsRef.current = all;
    
    homeDispatch({ field: 'selectedConversation', value: single });
    homeDispatch({ field: 'conversations', value: all });
  }, [storageKeyPrefix, homeDispatch]); // Refs for latest state; prefix for correct storage

  // Track previous streaming state to detect completion and ensure embedder is notified
  const prevStreamingRef = useRef(messageIsStreaming);
  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    prevStreamingRef.current = messageIsStreaming;
    if (messageIsStreaming) {
      setAutoScrollEnabled(true);
      setShowScrollDownButton(false);
      homeDispatch({ field: 'autoScroll', value: true });
    } else {
      // When streaming stops, disable auto-scroll to prevent further automatic scrolling
      setAutoScrollEnabled(false);
      homeDispatch({ field: 'autoScroll', value: false });
      // Fallback: when streaming just ended, notify embedder so attention signal is not missed
      const shouldNotifyCompletion = onAnswerComplete || onAnswerCompleteWithContent;
      if (wasStreaming && shouldNotifyCompletion) {
        const conv = selectedConversationRef.current;
        const lastMsg = conv?.messages?.slice(-1)[0];
        if (lastMsg?.role === 'assistant' && typeof lastMsg.content === 'string') {
          onAnswerComplete?.();
          handleCompletedAnswerWithContent(lastMsg.content);
        }
      }
    }
  }, [
    messageIsStreaming,
    onAnswerComplete,
    onAnswerCompleteWithContent,
    handleCompletedAnswerWithContent,
  ]);

  // Add an effect to set up wheel and touchmove event listeners
  useEffect(() => {
    const container = chatContainerRef.current;
    if (!container) return;

    // Function to handle user input events (mouse wheel, touch)
    const handleUserInput = () => {
      // Mark this as user-initiated scrolling
      isUserInitiatedScroll.current = true;

      // Reset the flag after a longer delay to ensure scroll event is captured
      if (scrollTimeout.current) {
        clearTimeout(scrollTimeout.current);
      }
      scrollTimeout.current = setTimeout(() => {
        isUserInitiatedScroll.current = false;
      }, 500) as NodeJS.Timeout;
    };

    // Add event listeners for user interactions
    container.addEventListener('wheel', handleUserInput, { passive: true });
    container.addEventListener('touchmove', handleUserInput, { passive: true });

    return () => {
      // Clean up
      container.removeEventListener('wheel', handleUserInput);
      container.removeEventListener('touchmove', handleUserInput);
      if (scrollTimeout.current) {
        clearTimeout(scrollTimeout.current);
      }
    };
  }, [chatContainerRef.current]); // Only re-run if the container ref changes

  // Now modify your handleScroll function to use this flag
  const handleScroll = useCallback(() => {
    if (!chatContainerRef.current) return;

    const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
    const isScrollingUp = scrollTop < lastScrollTop.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 20;

    // Disable auto-scroll on any scroll-up during streaming (mouse wheel up or scrollbar drag up)
    if (isScrollingUp && autoScrollEnabled && messageIsStreaming) {
      setAutoScrollEnabled(false);
      homeDispatch({ field: 'autoScroll', value: false });
      setShowScrollDownButton(true);
    }

    // Re-enable auto-scroll if user scrolls to bottom
    if (isAtBottom && !autoScrollEnabled) {
      setAutoScrollEnabled(true);
      homeDispatch({ field: 'autoScroll', value: true });
      setShowScrollDownButton(false);
    }

    lastScrollTop.current = scrollTop;
  }, [autoScrollEnabled, messageIsStreaming]);

  const handleScrollDown = () => {
    chatContainerRef.current?.scrollTo({
      top: chatContainerRef.current.scrollHeight,
      behavior: 'smooth',
    });
    // Enable auto-scroll after user clicks scroll down, assuming the user wants to auto-scroll
    setAutoScrollEnabled(true);
    homeDispatch({ field: 'autoScroll', value: true });
  };

  const scrollDown = () => {
    if (autoScrollEnabled) {
      messagesEndRef.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'end',
      });
    }
  };

  const throttledScrollDown = throttle(scrollDown, 250);

  useEffect(() => {
    throttledScrollDown();
    selectedConversation &&
      setCurrentMessage(() => {
        const len = selectedConversation?.messages.length ?? 0;
        return len >= 2 ? selectedConversation.messages[len - 2] : undefined;
      });
  }, [selectedConversation]);

  useEffect(() => {
    // Only set up the observer if we're actually streaming
    if (!messageIsStreaming) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          textareaRef.current?.focus();
        }

        // Only auto-scroll if we're streaming and auto-scroll is enabled
        if (autoScrollEnabled && messageIsStreaming) {
          requestAnimationFrame(() => {
            messagesEndRef.current?.scrollIntoView({
              behavior: 'smooth',
              block: 'end',
            });
          });
        }
      },
      {
        root: null,
        threshold: 0.5,
      }
    );

    const messagesEndElement = messagesEndRef.current;
    if (messagesEndElement) {
      observer.observe(messagesEndElement);
    }
    return () => {
      if (messagesEndElement) {
        observer.unobserve(messagesEndElement);
      }
    };
  }, [autoScrollEnabled, messageIsStreaming]);

  /** Matches ChatHeader: welcome vs conversation layout */
  const hasConversationMessages =
    (selectedConversation?.messages?.length ?? 0) > 0;

  return (
    <div className="relative flex flex-1 min-h-0 min-w-0 flex-col overflow-hidden bg-white dark:bg-black transition-all duration-300 ease-in-out">
      <>
        <div
          className={
            hasConversationMessages
              ? 'w-full shrink-0'
              : 'flex min-h-0 flex-1 flex-col'
          }
        >
          <ChatHeader
            webSocketModeRef={webSocketModeRef}
            onSend={(message) => handleSend(message, 0)}
          />
        </div>
        <div
          className={
            hasConversationMessages
              ? 'min-h-0 flex-1 overflow-x-hidden overflow-y-auto'
              : 'h-0 shrink-0 overflow-hidden'
          }
          ref={chatContainerRef}
          onScroll={handleScroll}
        >
          {selectedConversation?.messages.map((message, index, arr) => {
            // Hide hidden messages (used for auto-generated prompts like video upload)
            if (message.hidden) {
              return null;
            }
            if (!shouldRenderAssistantMessage(message)) {
              return null; // Hide empty assistant messages
            }

            // Only pass isStreaming to the last assistant message when actively streaming
            const isLastMessage = index === arr.length - 1;
            const isStreamingMessage = messageIsStreaming && isLastMessage && message.role === 'assistant';

            return (
              <MemoizedChatMessage
                key={message.id ?? index}
                message={message}
                messageIndex={index}
                onEdit={handleEditMessage}
                onDelete={handleDeleteMessage}
                totalMessageCount={arr.length}
                isStreaming={isStreamingMessage}
                showMessageEdit={chatMessageEditEnabled !== false}
                showMessageSpeaker={chatMessageSpeakerEnabled !== false}
                showMessageCopy={chatMessageCopyEnabled !== false}
              />
            );
          })}
          {loading && <ChatLoader statusUpdateText={`Thinking...`} />}
          <div
            className={`bg-white dark:bg-black ${
              (selectedConversation?.messages?.length ?? 0) > 0 || loading
                ? 'h-[162px]'
                : 'h-0'
            }`}
            ref={messagesEndRef}
          ></div>
        </div>
        <ChatInput
          textareaRef={textareaRef}
          queryContextItems={queryContextItems}
          onRemoveQueryContext={handleRemoveQueryContext}
          onSend={(message, customParams) => {
            const items = queryContextRef.current;
            if (items.length > 0) {
              const contextJson = JSON.stringify(items.map(({ label, type, data }) => ({ label, type, ...data })));
              const prefix = `[Context: ${contextJson}]`;
              message = { ...message, content: message.content ? `${prefix}\n\n${message.content}` : prefix };
              setQueryContextItems([]);
            }
            setCurrentMessage(message);
            if (customParams) {
              customAgentParamsRef.current = customParams;
            }
            handleSend(message, 0);
          }}
          onScrollDownClick={handleScrollDown}
          onRegenerate={() => {
            if (currentMessage && currentMessage?.role === 'user') {
              handleSend(currentMessage, 0);
            } else {
              const lastUserMessage = fetchLastMessage({
                messages: selectedConversation?.messages || [],
                role: 'user',
              });
              lastUserMessage && handleSend(lastUserMessage, 1);
            }
          }}
          showScrollDownButton={showScrollDownButton}
          controller={controllerRef}
          onStopConversation={handleStopConversation}
        />
        <InteractionModal
          isOpen={modalOpen}
          interactionMessage={interactionMessage}
          onClose={() => setModalOpen(false)}
          onSubmit={handleUserInteraction}
          showCancelButton={interactionModalCancelEnabled}
        />
      </>
    </div>
  );
};
Chat.displayName = 'Chat';

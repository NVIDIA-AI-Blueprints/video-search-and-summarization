'use client';

import {
  IconArrowsSort,
  IconMobiledataOff,
  IconSun,
  IconMoonFilled,
  IconUserFilled,
  IconChevronLeft,
  IconChevronRight,
  IconUpload,
} from '@tabler/icons-react';
import React, { useContext, useState, useRef, useEffect } from 'react';

import { useWorkflowName, useRightMenuOpenDefault } from '@/contexts/RuntimeConfigContext';
import ChatFileUpload from '@/components/Chat/ChatFileUpload';

import HomeContext from '@/pages/api/home/home.context';
import { Message } from '@/types/chat';

interface ChatHeaderProps {
  webSocketModeRef?: React.MutableRefObject<boolean | undefined> | Record<string, never>;
  onSend?: (message: Message) => void;
}

export const ChatHeader = ({ webSocketModeRef = {}, onSend }: ChatHeaderProps) => {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const rightMenuOpenDefault = useRightMenuOpenDefault();
  const [isExpanded, setIsExpanded] = useState(rightMenuOpenDefault);
  const menuRef = useRef(null);

  const workflow = useWorkflowName();

  const {
    state: {
      chatHistory,
      webSocketMode,
      webSocketConnected,
      lightMode,
      selectedConversation,
      chatUploadFileEnabled,
      themeChangeButtonEnabled,
    },
    dispatch: homeDispatch,
  } = useContext(HomeContext);

  const handleLogin = () => {
    console.log('Login clicked');
    setIsMenuOpen(false);
  };

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setIsMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const hasMessages = selectedConversation?.messages?.length > 0;

  // Shared content for the header
  const renderHeaderContent = (uploadProps?: { 
    triggerFilePicker: () => void; 
    fileInputId: string;
    isUploading: boolean; 
    isDragging: boolean; 
    dragHandlers: any;
  }) => (
    <div
      className={hasMessages ? 'relative' : 'relative min-h-full'}
    >
      <div
      className={`top-0 z-10 flex justify-center items-center h-12 ${
        hasMessages
          ? 'bg-white border-b border-gray-200'
          : 'bg-none'
      }  py-2 px-4 text-sm text-black dark:border-none dark:bg-black dark:text-neutral-200`}
    >
      {hasMessages ? (
        <div
          className={`absolute top-6 left-1/2 transform -translate-x-1/2 -translate-y-1/2`}
        >
          <span className="text-lg font-semibold text-black dark:text-white">{workflow}</span>
        </div>
      ) : (
        /* Welcome screen */
        <div 
          className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 mx-auto flex flex-col items-center px-3 pt-5 md:pt-12 sm:max-w-[600px] text-center"
          {...(uploadProps?.dragHandlers || {})}
        >
          <div className="text-3xl font-semibold text-gray-800 dark:text-white mb-4">
            Hi, I'm {workflow}
          </div>
          <div className="text-lg text-gray-600 dark:text-gray-400 mb-8">
            How can I assist you today?
          </div>
          
          {/* File Upload Drop Zone - only show when upload is enabled  */}
          {chatUploadFileEnabled && uploadProps && (
            <label
              htmlFor={uploadProps.fileInputId}
              className={`
                w-full max-w-md cursor-pointer rounded-xl border-2 border-dashed p-8 block
                transition-all duration-300 ease-in-out
                ${uploadProps.isDragging 
                  ? 'border-[#76b900] bg-[#76b900]/10 scale-105 shadow-lg shadow-[#76b900]/20' 
                  : 'border-gray-300 dark:border-gray-600 hover:border-[#76b900] hover:bg-gray-50 dark:hover:bg-black/50'
                }
                ${uploadProps.isUploading ? 'opacity-50 pointer-events-none' : ''}
              `}
            >
              <div className="flex flex-col items-center gap-4">
                <div className={`
                  p-4 rounded-2xl transition-all duration-300
                  ${uploadProps.isDragging 
                    ? 'bg-[#76b900]/20 text-[#76b900]' 
                    : 'bg-[#76b900]/15 text-[#76b900]'
                  }
                `}>
                  <IconUpload size={48} stroke={1.5} />
                </div>
                
                <div data-testid="upload-drop-zone-text" className="text-center">
                  <p className={`text-base font-medium mb-1 transition-colors duration-300 ${
                    uploadProps.isDragging 
                      ? 'text-[#76b900]' 
                      : 'text-gray-700 dark:text-gray-300'
                  }`}>
                    {uploadProps.isDragging ? 'Drop files here' : 'Click or drop files here to upload'}
                  </p>
                </div>
                
                {/* File type hints */}
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Movie Files (mp4, mkv)
                </p>
              </div>
            </label>
          )}
        </div>
      )}

      {/* Collapsible Menu - opaque background so it hides the title when expanded */}
      <div
        className={`absolute right-0 top-0 h-12 pl-6 flex items-center transition-all duration-300 z-20 bg-white dark:bg-black`}
      >
        <button
          onClick={() => {
            setIsExpanded(!isExpanded);
          }}
          className="flex p-1 text-black dark:text-white transition-colors"
        >
          {isExpanded ? (
            <IconChevronRight size={20} />
          ) : (
            <IconChevronLeft size={20} />
          )}
        </button>

        <div
          className={`flex gap-1 sm:gap-1 md:gap-4 overflow-hidden transition-all duration-300 ${
            isExpanded ? 'w-auto opacity-100' : 'w-0 opacity-0'
          }`}
        >
          {/* Chat History Toggle */}
          <div className="flex items-center gap-2 whitespace-nowrap">
            <label className="flex items-center gap-2 cursor-pointer flex-shrink-0">
              <span className="text-sm font-medium text-black dark:text-white">
                Chat History
              </span>
              <div
                onClick={() => {
                  homeDispatch({
                    field: 'chatHistory',
                    value: !chatHistory,
                  });
                }}
                className={`relative inline-flex h-5 w-10 items-center cursor-pointer rounded-full transition-colors duration-300 ease-in-out ${
                  chatHistory ? 'bg-black dark:bg-[#76b900]' : 'bg-gray-200'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform duration-300 ease-in-out ${
                    chatHistory ? 'translate-x-6' : 'translate-x-0'
                  }`}
                />
              </div>
            </label>
          </div>

          {/* WebSocket Mode Toggle */}
          <div className="flex items-center gap-2 whitespace-nowrap">
            <label className="flex items-center gap-2 cursor-pointer flex-shrink-0">
              <span
                className={`flex items-center gap-1 justify-evenly text-sm font-medium text-black dark:text-white`}
              >
                WebSocket{' '}
                {webSocketModeRef?.current &&
                  (webSocketConnected ? (
                    <IconArrowsSort size={18} className="text-black dark:text-white" />
                  ) : (
                    <IconMobiledataOff size={18} className="text-black dark:text-white" />
                  ))}
              </span>
              <div
                onClick={() => {
                  const newWebSocketMode = !webSocketModeRef.current;
                  sessionStorage.setItem(
                    'webSocketMode',
                    String(newWebSocketMode),
                  );
                  webSocketModeRef.current = newWebSocketMode;
                  homeDispatch({
                    field: 'webSocketMode',
                    value: !webSocketMode,
                  });
                }}
                className={`relative inline-flex h-5 w-10 items-center cursor-pointer rounded-full transition-colors duration-300 ease-in-out ${
                  webSocketModeRef.current
                    ? 'bg-black dark:bg-[#76b900]'
                    : 'bg-gray-200'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform duration-300 ease-in-out ${
                    webSocketModeRef.current ? 'translate-x-6' : 'translate-x-0'
                  }`}
                />
              </div>
            </label>
          </div>

          {/* Theme Toggle Button  */}
          {themeChangeButtonEnabled && (
            <div className="flex items-center dark:text-white text-black transition-colors duration-300">
              <button
                onClick={() => {
                  const newMode = lightMode === 'dark' ? 'light' : 'dark';
                  homeDispatch({
                    field: 'lightMode',
                    value: newMode,
                  });
                }}
                className="rounded-full flex items-center justify-center bg-none dark:bg-gray-900 transition-colors duration-300 focus:outline-none"
              >
                {lightMode === 'dark' ? (
                  <IconSun className="w-6 h-6 text-yellow-500 transition-transform duration-300" />
                ) : (
                  <IconMoonFilled className="w-6 h-6 text-gray-800 transition-transform duration-300" />
                )}
              </button>
            </div>
          )}

          {/* User Icon with Dropdown Menu */}
          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setIsMenuOpen(!isMenuOpen)}
              className="flex items-center dark:text-white text-black cursor-pointer"
            >
              <IconUserFilled size={20} />
            </button>
            {isMenuOpen && (
              <div className="absolute right-0 mt-2 px-2 w-auto rounded-md shadow-lg bg-white dark:bg-black ring-1 ring-black ring-opacity-5">
                <div className="py-1">
                  <button
                    onClick={handleLogin}
                    className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-neutral-800"
                  >
                    Login
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
    </div>
  );

  // Conditionally wrap with ChatFileUpload when upload is enabled
  if (chatUploadFileEnabled) {
    return (
      <ChatFileUpload
        onSendHiddenMessage={onSend ? (message) => {
          onSend({ role: 'user', content: message, hidden: true });
        } : undefined}
      >
        {({ triggerFilePicker, fileInputId, isUploading, isDragging, dragHandlers }) => 
          renderHeaderContent({ triggerFilePicker, fileInputId, isUploading, isDragging, dragHandlers })
        }
      </ChatFileUpload>
    );
  }

  return renderHeaderContent();
};

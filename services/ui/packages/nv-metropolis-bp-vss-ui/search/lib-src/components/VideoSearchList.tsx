// SPDX-License-Identifier: MIT
import React from 'react';
import { IconInbox } from '@tabler/icons-react';
import { Whisper, Tooltip } from 'rsuite';
import { SearchData } from '../types';
import { formatTime, parseDateAsLocal } from '../utils/Formatter';

interface VideoSearchListProps {
  data: SearchData[];
  loading: boolean;
  error: string | null;
  isDark: boolean;
  onRefresh: () => void;
  onPlayVideo: (data: SearchData, showObjectsBbox: boolean) => void;
  showObjectsBbox?: boolean;
}

export const VideoSearchList: React.FC<VideoSearchListProps> = ({
    data,
    loading,
    error,
    isDark,
    onRefresh,
    onPlayVideo,
    showObjectsBbox = false
}) => {
    if (loading) {
        return (
          <div className="p-4">
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <IconInbox className={`w-12 h-12 mb-3 ${isDark ? 'text-gray-500' : 'text-gray-400'}`} stroke={1.5} />
              <p className={isDark ? 'text-gray-400' : 'text-gray-600'}>Results will update here</p>
            </div>
          </div>
        );
      }

      if (error) {
        return (
          <div className="flex items-center justify-center h-full p-4">
            <div className={`w-full max-w-2xl p-6 rounded-lg ${isDark ? 'bg-red-500/10 border border-red-500/20' : 'bg-red-50 border border-red-200'}`}>
              <div className="flex items-center gap-2 mb-3">
                <svg className={`w-5 h-5 flex-shrink-0 ${isDark ? 'text-red-400' : 'text-red-600'}`} fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
                <p className={`font-bold text-lg ${isDark ? 'text-red-400' : 'text-red-700'}`}>Error loading items</p>
              </div>
              <div 
                className={`text-sm mb-4 p-3 rounded max-h-48 overflow-y-auto ${isDark ? 'bg-gray-800/50 text-gray-300' : 'bg-white text-red-600 border border-red-100'}`}
                style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
              >
                {error}
              </div>
              <div className="flex justify-end">
                <button 
                  onClick={onRefresh}
                  className="px-5 py-2.5 rounded-md font-medium transition-colors bg-blue-600 hover:bg-blue-700 text-white"
                >
                  Retry
                </button>
              </div>
            </div>
          </div>
        );
      }
    return (
      <div className="p-4">
      {data.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
              <IconInbox className={`w-12 h-12 mb-3 ${isDark ? 'text-gray-500' : 'text-gray-400'}`} stroke={1.5} />
              <p className={isDark ? 'text-gray-400' : 'text-gray-600'}>Results will update here</p>
          </div>
      ) : (
          <div className="grid gap-4 grid-cols-[repeat(auto-fill,280px)] justify-start">
              {data.map((item, index) => (
                  <div 
                      key={`${item.video_name}-${index}`}
                      className={`rounded-2xl overflow-hidden bg-white border border-gray-200 rounded-lg shadow-sm dark:bg-gray-700 dark:border-gray-600 w-[280px] min-w-[280px] max-w-[280px] box-border`}
                  >
                      {/* Video Thumbnail Container */}
                      <div className="p-4 pb-0 space-y-3">
                        {/* Video Title Overlay */}
                        <div>
                            <Whisper
                              placement="top"
                              trigger="hover"
                              speaker={<Tooltip>{item.video_name}</Tooltip>}
                            >
                              <h3 className="font-medium text-sm truncate cursor-default">
                                  {item.video_name}
                              </h3>
                            </Whisper>
                        </div>
                        <div className="rounded-2xl relative aspect-video group cursor-pointer">
                            <div className="rounded-2xl absolute inset-0 bg-gradient-to-br from-gray-700 to-gray-900">
                                <img src={item.screenshot_url} alt={item.video_name} className="rounded-2xl w-full h-full object-cover" />
                            </div>
                            
                            {/* Play Button Overlay */}
                            <div className="absolute inset-0 flex items-center justify-center" onClick={() => onPlayVideo(item, showObjectsBbox)}>
                                <div className="w-12 h-12 sm:w-14 sm:h-14 rounded-2xl bg-[rgb(209_255_117_/_0.6)] flex items-center justify-center shadow-lg transition-transform hover:scale-110 border border-white/30">
                                    <svg className="w-6 h-6 sm:w-7 sm:h-7 text-white ml-0.5" fill="currentColor" viewBox="0 0 24 24">
                                        <path d="M8 5v14l11-7z" />
                                    </svg>
                                </div>
                            </div>
                            
                            {/* Time and Similarity Info Overlay */}
                            <div className="rounded-b-2xl absolute bottom-0 left-0 right-0 px-4 py-2 bg-gradient-to-t from-black/70 to-transparent flex items-end justify-between">
                                      <div className="text-white text-xs">
                                          <span className="font-medium">{formatTime(parseDateAsLocal(item.start_time))}</span>
                                          <span className="mx-1">/</span>
                                          <span className="font-medium">{formatTime(parseDateAsLocal(item.end_time))}</span>
                                      </div>
                                      {item.description && (
                                        <Whisper
                                          placement="top"
                                          trigger="hover"
                                          speaker={<Tooltip>{item.description}</Tooltip>}
                                        >
                                          <div className="flex items-center gap-1 bg-white/20 backdrop-blur-sm rounded-full px-2 py-1 cursor-default">
                                            <svg className="w-4 h-4 text-white" fill="currentColor" viewBox="0 0 20 20">
                                              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                                            </svg>
                                          </div>
                                        </Whisper>
                                      )}
                                  </div>
                        </div>
                      </div>

                      {/* Card Footer */}
                      <div className="p-4 pt-0 space-y-3 flex justify-between items-baseline">
                          <div className="flex items-center justify-between">
                          </div>
                          <div className="flex items-center justify-between text-xs">
                              <span className={isDark ? 'text-gray-400' : 'text-gray-600'}>
                                  Similarity:
                              </span>
                              <span className="bg-gray-200 dark:bg-gray-800 dark:text-white text-gray-900 font-semibold ml-1 px-3 py-1 rounded-md">
                                  {item.similarity.toFixed(2)}
                              </span>
                          </div>
                      </div>
                  </div>
              ))}
          </div>
      )}
  </div>
    )
}
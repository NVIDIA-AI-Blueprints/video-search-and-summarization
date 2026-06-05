/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once

 #include "libav_wrapper.h"
 #include <string>
 #include <functional>
 #include <mutex>
 #include <atomic>
 #include <chrono>
 
 namespace nv_vms {
 
 /**
  * @brief VideoSegmentExtractor using libav APIs for reliable video segment extraction
  * 
  * Supports H.264 and H.265 codecs in MP4 and MKV container formats.
  * Uses PTS (Presentation Time Stamps) for precise segment extraction.
  * Stream copy method for optimal performance.
  */
 class VideoSegmentExtractor {
 public:
     VideoSegmentExtractor();
     ~VideoSegmentExtractor();
 
     /**
      * @brief Extract video segment using stream copy method (fastest)
      * @param inputFile Path to input video file (MP4/MKV)
      * @param outputFile Path for output video file
      * @param startPts Start presentation timestamp in nanoseconds (AV_TIME_BASE for 1 second)
      * @param endPts End presentation timestamp in nanoseconds (AV_TIME_BASE for 1 second)
      * @return true if extraction successful, false otherwise
      */
     bool extractSegmentStreamCopy(const std::string& inputFile, 
                                   const std::string& outputFile,
                                   int64_t startPts, 
                                   int64_t endPts);
 
     /**
      * @brief Extract video segment with optional transcoding
      * @param inputFile Path to input video file
      * @param outputFile Path for output video file
      * @param startPts Start presentation timestamp in nanoseconds
      * @param endPts End presentation timestamp in nanoseconds
      * @param transcode Whether to transcode or use stream copy
      * @return true if extraction successful, false otherwise
      */
     bool extractSegment(const std::string& inputFile,
                        const std::string& outputFile,
                        int64_t startPts,
                        int64_t endPts,
                        bool transcode = false);
 
     /**
      * @brief Set progress callback for extraction operation
      * @param callback Function to call with progress updates (0.0 to 1.0)
      */
     void setProgressCallback(std::function<void(double progress)> callback);
 
     /**
      * @brief Cancel ongoing extraction
      */
     void cancel();
 
     /**
      * @brief Check if extraction is currently running
      * @return true if extraction is in progress
      */
     bool isExtracting() const;
 
     /**
      * @brief Get last error message
      * @return Error message string
      */
     std::string getLastError() const;

     /**
      * @brief Check if the extractor is available and usable
      * @return true if LibavWrapper is initialized and ready to use
      */
     bool isAvailable() const;
 
 private:
     struct ProfilingStats {
         std::chrono::time_point<std::chrono::high_resolution_clock> startTime;
         std::chrono::time_point<std::chrono::high_resolution_clock> endTime;
         int64_t totalFrames = 0;
         int64_t processedFrames = 0;
         size_t inputFileSize = 0;
         size_t outputFileSize = 0;
         double segmentDuration = 0.0;
         
         void printStats() const;
     };
 
     /**
      * @brief Initialize libav libraries
      */
     bool initializeLibav();
 
     /**
      * @brief Open input file and get format context
      */
     bool openInputFile(const std::string& inputFile);
 
     /**
      * @brief Open output file and setup format context
      */
     bool openOutputFile(const std::string& outputFile);
 
     /**
      * @brief Find video stream in input
      */
     int findVideoStream();
 
     /**
      * @brief Setup stream copy parameters
      */
     bool setupStreamCopy();
 
     /**
      * @brief Seek to start position
      */
     bool seekToStart(int64_t startPts);
 
     /**
      * @brief Process packets for stream copy
      */
     bool processPackets(int64_t endPts);
 
     /**
      * @brief Cleanup resources
      */
     void cleanup();
 
     // Libav contexts
     AVFormatContext* m_inputFormatCtx = nullptr;
     AVFormatContext* m_outputFormatCtx = nullptr;
     AVCodecContext* m_codecCtx = nullptr;
     AVStream* m_inputVideoStream = nullptr;
     AVStream* m_outputVideoStream = nullptr;
     
     // Stream info
     int m_videoStreamIndex = -1;
     AVRational m_timeBase;
     
     // Control
     std::atomic<bool> m_isExtracting{false};
     std::atomic<bool> m_cancelled{false};
     std::function<void(double)> m_progressCallback;
     std::mutex m_mutex;
     
     // Error handling
     std::string m_lastError;
     
     // Profiling
     ProfilingStats m_stats;
 };
 
 } // namespace nv_vms 
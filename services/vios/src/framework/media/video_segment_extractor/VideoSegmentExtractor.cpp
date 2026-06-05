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

#include "VideoSegmentExtractor.h"
 #include <iostream>
 #include <fstream>
 #include <filesystem>
 #include <iomanip>
 #include <chrono>
 
 // Enable detailed logging
 #define LOG_INFO(msg) std::cout << "[INFO] " << msg << std::endl
 #define LOG_WARNING(msg) std::cout << "[WARNING] " << msg << std::endl
 #define LOG_ERROR(msg) std::cerr << "[ERROR] " << msg << std::endl
 #define LOG_DEBUG(msg) std::cout << "[DEBUG] " << msg << std::endl
 
 namespace nv_vms {
 
 VideoSegmentExtractor::VideoSegmentExtractor() 
     : m_inputFormatCtx(nullptr)
     , m_outputFormatCtx(nullptr)
     , m_codecCtx(nullptr)
     , m_inputVideoStream(nullptr)
     , m_outputVideoStream(nullptr)
     , m_videoStreamIndex(-1)
     , m_isExtracting(false)
     , m_cancelled(false)
 {
     // Don't throw exceptions in constructor - handle LibavWrapper failures gracefully
     try {
         initializeLibav();
     } catch (const std::exception& e) {
         LOG_ERROR("LibavWrapper initialization failed in constructor: " << e.what());
         // Don't rethrow - allow object to be constructed but mark as unavailable
         m_lastError = "LibavWrapper initialization failed: " + std::string(e.what());
     }
 }
 
 VideoSegmentExtractor::~VideoSegmentExtractor() {
     // Ensure safe cleanup even if constructor failed
     try {
         cleanup();
     } catch (const std::exception& e) {
         LOG_ERROR("Exception during VideoSegmentExtractor destructor: " << e.what());
     } catch (...) {
         LOG_ERROR("Unknown exception during VideoSegmentExtractor destructor");
     }
 }
 
 bool VideoSegmentExtractor::initializeLibav() {
     LOG_DEBUG("Initializing libav libraries using dynamic loading");
     
     // Get LibavWrapper instance (this will dynamically load the libraries)
     try {
         LibavWrapper* wrapper = LibavWrapper::getInstance();
         if (!wrapper->isLibavAvailable()) {
             LOG_ERROR("LibavWrapper is not available");
             return false;
         }
         
         // Initialize libav (this is deprecated in newer versions but still works)
         if (wrapper->av_register_all) {
             wrapper->av_register_all();
         }
         
         LOG_DEBUG("Libav initialized successfully using dynamic loading");
         return true;
     } catch (const std::exception& e) {
         LOG_ERROR("Failed to initialize LibavWrapper: " << e.what());
         return false;
     }
 }
 
  bool VideoSegmentExtractor::extractSegmentStreamCopy(const std::string& inputFile, 
                                                      const std::string& outputFile,
                                                      int64_t startPts, 
                                                      int64_t endPts) {
     std::lock_guard<std::mutex> lock(m_mutex);
     
     LOG_INFO("Starting libav-based stream copy extraction");
     LOG_INFO("Input: " << inputFile);
     LOG_INFO("Output: " << outputFile);
     LOG_INFO("Start PTS: " << startPts / AV_TIME_BASE << "s");
     LOG_INFO("End PTS: " << endPts / AV_TIME_BASE << "s");
     LOG_INFO("Duration: " << (endPts - startPts) / AV_TIME_BASE << "s");
     
     if (m_isExtracting) {
         m_lastError = "Extraction already in progress";
         LOG_ERROR("Extraction already in progress");
         return false;
     }

     // Check if the extractor is available (LibavWrapper initialized)
     if (!isAvailable()) {
         m_lastError = "VideoSegmentExtractor not available - LibavWrapper initialization failed";
         LOG_ERROR("VideoSegmentExtractor not available - LibavWrapper initialization failed");
         return false;
     }

     m_isExtracting = true;
     m_cancelled = false;
     m_lastError.clear();
     
     // Scope guard to ensure m_isExtracting is always reset
     auto cleanup_guard = [this]() {
         cleanup();
         m_isExtracting = false;
     };
     
     bool success = false;
     try {
         // Initialize profiling
         m_stats = ProfilingStats();
         m_stats.startTime = std::chrono::high_resolution_clock::now();
         m_stats.segmentDuration = (endPts - startPts) / (double)AV_TIME_BASE;
         
         if (std::filesystem::exists(inputFile)) {
             m_stats.inputFileSize = std::filesystem::file_size(inputFile);
         }
         
         do {
             // Open input file
             LOG_INFO("Opening input file");
             if (!openInputFile(inputFile)) {
                 break;
             }

             // Find video stream
             LOG_INFO("Finding video stream");
             m_videoStreamIndex = findVideoStream();
             if (m_videoStreamIndex < 0) {
                 m_lastError = "No video stream found";
                 LOG_ERROR("No video stream found");
                 break;
             }
             
             m_inputVideoStream = m_inputFormatCtx->streams[m_videoStreamIndex];
             m_timeBase = m_inputVideoStream->time_base;
             
             LOG_INFO("Video stream found at index " << m_videoStreamIndex);
             LOG_DEBUG("Time base: " << m_timeBase.num << "/" << m_timeBase.den);

             // Open output file
             LOG_INFO("Opening output file");
             if (!openOutputFile(outputFile)) {
                 break;
             }

             // Setup stream copy
             LOG_INFO("Setting up stream copy");
             if (!setupStreamCopy()) {
                 break;
             }

             // Seek to start position
             LOG_INFO("Seeking to start position");
             if (!seekToStart(startPts)) {
                 break;
             }

             // Write header
             LOG_DEBUG("Writing output header");
             LibavWrapper* wrapper = LibavWrapper::getInstance();
             int ret = wrapper->avformat_write_header(m_outputFormatCtx, nullptr);
             if (ret < 0) {
                 char errbuf[AV_ERROR_MAX_STRING_SIZE];
                 wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
                 m_lastError = "Failed to write header: " + std::string(errbuf);
                 LOG_ERROR("Failed to write header: " << errbuf);
                 break;
             }

             // Process packets
             LOG_INFO("Processing packets for stream copy");
             if (!processPackets(endPts)) {
                 break;
             }

             // Write trailer
             LOG_DEBUG("Writing output trailer");
             ret = wrapper->av_write_trailer(m_outputFormatCtx);
             if (ret < 0) {
                 char errbuf[AV_ERROR_MAX_STRING_SIZE];
                 wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
                 LOG_WARNING("Warning writing trailer: " << errbuf);
             }

             success = true;
             LOG_INFO("Stream copy extraction completed successfully");

         } while (false);

         // Finalize profiling
         m_stats.endTime = std::chrono::high_resolution_clock::now();
         
         if (std::filesystem::exists(outputFile)) {
             m_stats.outputFileSize = std::filesystem::file_size(outputFile);
         }
         
         // Print comprehensive profiling stats
         m_stats.printStats();

         // Call cleanup guard before returning
         cleanup_guard();
         
         return success && !m_cancelled;
         
     } catch (const std::exception& e) {
         // Handle any exceptions and ensure proper cleanup
         LOG_ERROR("Exception during extraction: " << e.what());
         m_lastError = "Exception during extraction: " + std::string(e.what());
         
         // Call cleanup guard
         cleanup_guard();
         
         return false;
     } catch (...) {
         // Handle any other exceptions
         LOG_ERROR("Unknown exception during extraction");
         m_lastError = "Unknown exception during extraction";
         
         // Call cleanup guard
         cleanup_guard();
         
         return false;
     }
 }
 
 bool VideoSegmentExtractor::extractSegment(const std::string& inputFile,
                                           const std::string& outputFile,
                                           int64_t startPts,
                                           int64_t endPts,
                                           bool transcode) {
    // Suppress unused parameter warning - transcode functionality not yet implemented
    (void)transcode;
     // For now, always use stream copy for best performance
     return extractSegmentStreamCopy(inputFile, outputFile, startPts, endPts);
 }
 
 void VideoSegmentExtractor::setProgressCallback(std::function<void(double progress)> callback) {
     m_progressCallback = callback;
 }
 
 void VideoSegmentExtractor::cancel() {
     m_cancelled = true;
 }
 
 bool VideoSegmentExtractor::isExtracting() const {
     return m_isExtracting;
 }
 
 std::string VideoSegmentExtractor::getLastError() const {
     return m_lastError;
 }
 
 bool VideoSegmentExtractor::isAvailable() const {
     // Check if LibavWrapper is available and initialized
     try {
         LibavWrapper* wrapper = LibavWrapper::getInstance();
         return wrapper && wrapper->isLibavAvailable();
     } catch (const std::exception& e) {
         LOG_ERROR("Exception checking LibavWrapper availability: " << e.what());
         return false;
     }
 }
 
  bool VideoSegmentExtractor::openInputFile(const std::string& inputFile) {
     LibavWrapper* wrapper = LibavWrapper::getInstance();
     
     int ret = wrapper->avformat_open_input(&m_inputFormatCtx, inputFile.c_str(), nullptr, nullptr);
     if (ret < 0) {
         char errbuf[AV_ERROR_MAX_STRING_SIZE];
         wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
         m_lastError = "Failed to open input file: " + std::string(errbuf);
         LOG_ERROR("Failed to open input file: " << errbuf);
         return false;
     }

     ret = wrapper->avformat_find_stream_info(m_inputFormatCtx, nullptr);
     if (ret < 0) {
         char errbuf[AV_ERROR_MAX_STRING_SIZE];
         wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
         m_lastError = "Failed to find stream info: " + std::string(errbuf);
         LOG_ERROR("Failed to find stream info: " << errbuf);
         return false;
     }

     LOG_DEBUG("Input file opened successfully");
     return true;
 }
 
  bool VideoSegmentExtractor::openOutputFile(const std::string& outputFile) {
     LibavWrapper* wrapper = LibavWrapper::getInstance();
     
     int ret = wrapper->avformat_alloc_output_context2(&m_outputFormatCtx, nullptr, nullptr, outputFile.c_str());
     if (!m_outputFormatCtx) {
         m_lastError = "Failed to allocate output context";
         LOG_ERROR("Failed to allocate output context");
         return false;
     }

     ret = wrapper->avio_open(&m_outputFormatCtx->pb, outputFile.c_str(), AVIO_FLAG_WRITE);
     if (ret < 0) {
         char errbuf[AV_ERROR_MAX_STRING_SIZE];
         wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
         m_lastError = "Failed to open output file: " + std::string(errbuf);
         LOG_ERROR("Failed to open output file: " << errbuf);
         return false;
     }

     LOG_DEBUG("Output file opened successfully");
     return true;
 }
 
 int VideoSegmentExtractor::findVideoStream() {
     for (unsigned int i = 0; i < m_inputFormatCtx->nb_streams; i++) {
         if (m_inputFormatCtx->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) {
             return i;
         }
     }
     return -1;
 }
 
  bool VideoSegmentExtractor::setupStreamCopy() {
     LibavWrapper* wrapper = LibavWrapper::getInstance();
     
     // Create output video stream
     m_outputVideoStream = wrapper->avformat_new_stream(m_outputFormatCtx, nullptr);
     if (!m_outputVideoStream) {
         m_lastError = "Failed to create output video stream";
         LOG_ERROR("Failed to create output video stream");
         return false;
     }

     // Copy codec parameters for stream copy
     int ret = wrapper->avcodec_parameters_copy(m_outputVideoStream->codecpar, m_inputVideoStream->codecpar);
     if (ret < 0) {
         char errbuf[AV_ERROR_MAX_STRING_SIZE];
         wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
         m_lastError = "Failed to copy codec parameters: " + std::string(errbuf);
         LOG_ERROR("Failed to copy codec parameters: " << errbuf);
         return false;
     }

     m_outputVideoStream->codecpar->codec_tag = 0;
     m_outputVideoStream->time_base = m_inputVideoStream->time_base;

     LOG_DEBUG("Stream copy setup completed");
     return true;
 }
 
  bool VideoSegmentExtractor::seekToStart(int64_t startPts) {
     if (startPts <= 0) {
         LOG_DEBUG("Starting from beginning, no seek required");
         return true;
     }

     LibavWrapper* wrapper = LibavWrapper::getInstance();

     // Convert PTS to input stream time base
     int64_t seekTarget = wrapper->av_rescale_q(startPts, AV_TIME_BASE_Q, m_inputVideoStream->time_base);
     
     LOG_DEBUG("Seeking to timestamp: " << seekTarget << " (stream time base)");
     
     int ret = wrapper->av_seek_frame(m_inputFormatCtx, m_videoStreamIndex, seekTarget, AVSEEK_FLAG_BACKWARD);
     if (ret < 0) {
         char errbuf[AV_ERROR_MAX_STRING_SIZE];
         wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
         m_lastError = "Failed to seek: " + std::string(errbuf);
         LOG_ERROR("Failed to seek: " << errbuf);
         return false;
     }

     LOG_DEBUG("Seek completed successfully");
     return true;
 }
 
  bool VideoSegmentExtractor::processPackets(int64_t endPts) {
     LibavWrapper* wrapper = LibavWrapper::getInstance();
     
     AVPacket* packet = wrapper->av_packet_alloc();
     if (!packet) {
         m_lastError = "Failed to allocate packet";
         LOG_ERROR("Failed to allocate packet");
         return false;
     }
     
     int64_t endPtsStreamTime = wrapper->av_rescale_q(endPts, AV_TIME_BASE_Q, m_inputVideoStream->time_base);
     int packetCount = 0;
     
     LOG_DEBUG("Processing packets until PTS: " << endPtsStreamTime << " (stream time base)");

     while (!m_cancelled) {
         int ret = wrapper->av_read_frame(m_inputFormatCtx, packet);
         if (ret < 0) {
             if (ret == AVERROR_EOF) {
                 LOG_DEBUG("Reached end of file");
                 break;
             }
             char errbuf[AV_ERROR_MAX_STRING_SIZE];
             wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
             m_lastError = "Error reading frame: " + std::string(errbuf);
             LOG_ERROR("Error reading frame: " << errbuf);
             wrapper->av_packet_free(&packet);
             return false;
         }

         // Only process video packets
         if (packet->stream_index == m_videoStreamIndex) {
             // Check if we've reached the end time
             if (packet->pts != AV_NOPTS_VALUE && packet->pts >= endPtsStreamTime) {
                 LOG_DEBUG("Reached end time, stopping at PTS: " << packet->pts);
                 wrapper->av_packet_unref(packet);
                 break;
             }

             // Update packet stream index for output
             packet->stream_index = 0; // Output video stream index

             // Rescale timestamps
             wrapper->av_packet_rescale_ts(packet, m_inputVideoStream->time_base, m_outputVideoStream->time_base);

             // Write packet
             ret = wrapper->av_interleaved_write_frame(m_outputFormatCtx, packet);
             if (ret < 0) {
                 char errbuf[AV_ERROR_MAX_STRING_SIZE];
                 wrapper->av_strerror(ret, errbuf, sizeof(errbuf));
                 LOG_WARNING("Warning writing packet: " << errbuf);
             }

             packetCount++;
             m_stats.processedFrames = packetCount;

             // Progress callback
             if (m_progressCallback && packetCount % 100 == 0) {
                 double progress = 0.0;
                 if (packet->pts != AV_NOPTS_VALUE && endPtsStreamTime > 0) {
                     progress = (double)packet->pts / endPtsStreamTime;
                     progress = std::max(0.0, std::min(1.0, progress));
                 }
                 m_progressCallback(progress);
             }
         }

         wrapper->av_packet_unref(packet);
     }

     wrapper->av_packet_free(&packet);

     LOG_INFO("Processed " << packetCount << " video packets");
     m_stats.totalFrames = packetCount;
     if (packetCount == 0)
     {
         LOG_ERROR("No video packets processed");
         return false;
     }
     return true;
 }
 
  void VideoSegmentExtractor::cleanup() {
     LibavWrapper* wrapper = LibavWrapper::getInstance();
     
     if (m_inputFormatCtx) {
         wrapper->avformat_close_input(&m_inputFormatCtx);
         m_inputFormatCtx = nullptr;
     }

     if (m_outputFormatCtx) {
         if (m_outputFormatCtx->pb) {
             wrapper->avio_closep(&m_outputFormatCtx->pb);
         }
         wrapper->avformat_free_context(m_outputFormatCtx);
         m_outputFormatCtx = nullptr;
     }

     m_videoStreamIndex = -1;
     m_inputVideoStream = nullptr;
     m_outputVideoStream = nullptr;
 }
 
 void VideoSegmentExtractor::ProfilingStats::printStats() const {
     auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(endTime - startTime);
     double extractionTime = duration.count() / 1000.0;
     double speedRatio = segmentDuration > 0 ? extractionTime / segmentDuration : 0.0;
     
     std::cout << "\n=== LIBAV PROFILING STATS ===" << std::endl;
     std::cout << std::fixed << std::setprecision(3);
     
     // Timing breakdown
     std::cout << "Timing Breakdown:" << std::endl;
     std::cout << "  Extraction Time:     " << extractionTime << "s" << std::endl;
     std::cout << "  Segment Duration:    " << segmentDuration << "s" << std::endl;
     std::cout << "  Speed Ratio:         " << speedRatio << "x" << std::endl;
     
     // Performance rating
     std::cout << "  Performance Rating:  ";
     if (speedRatio < 0.1) std::cout << "EXCELLENT (>10x real-time)" << std::endl;
     else if (speedRatio < 0.5) std::cout << "VERY GOOD (>2x real-time)" << std::endl;
     else if (speedRatio < 1.0) std::cout << "GOOD (>1x real-time)" << std::endl;
     else std::cout << "SLOW (<1x real-time)" << std::endl;
     
     // File size analysis
     std::cout << "\nFile Size Analysis:" << std::endl;
     std::cout << "  Input File Size:     " << (inputFileSize / (1024*1024)) << " MB" << std::endl;
     std::cout << "  Output File Size:    " << (outputFileSize / (1024*1024)) << " MB" << std::endl;
     if (inputFileSize > 0) {
         double ratio = (double)outputFileSize / inputFileSize * 100.0;
         std::cout << "  Size Ratio:          " << std::setprecision(1) << ratio << "%" << std::endl;
     }
     
     // Frame processing
     std::cout << "\nFrame Processing:" << std::endl;
     std::cout << "  Total Frames:        " << totalFrames << std::endl;
     std::cout << "  Processed Frames:    " << processedFrames << std::endl;
     if (extractionTime > 0) {
         double fps = processedFrames / extractionTime;
         std::cout << "  Processing FPS:      " << fps << std::endl;
     }
     
     std::cout << "=============================" << std::endl;
 }
 
 } // namespace nv_vms

// C-style export functions for dynamic loading
extern "C" {
    
    // Create VideoSegmentExtractor instance
    nv_vms::VideoSegmentExtractor* createVideoSegmentExtractor() {
        try {
            return new nv_vms::VideoSegmentExtractor();
        } catch (const std::exception& e) {
            LOG_ERROR("Failed to create VideoSegmentExtractor: " << e.what());
            return nullptr;
        }
    }
    
    // Destroy VideoSegmentExtractor instance
    void destroyVideoSegmentExtractor(nv_vms::VideoSegmentExtractor* extractor) {
        if (extractor) {
            delete extractor;
        }
    }
    
    // Extract video segment using stream copy
    bool extractSegmentStreamCopy(nv_vms::VideoSegmentExtractor* extractor, 
                                  const std::string& inputFile, 
                                  const std::string& outputFile, 
                                  int64_t startPts, 
                                  int64_t endPts) {
        if (!extractor) {
            LOG_ERROR("VideoSegmentExtractor instance is null");
            return false;
        }
        
        try {
            return extractor->extractSegmentStreamCopy(inputFile, outputFile, startPts, endPts);
        } catch (const std::exception& e) {
            LOG_ERROR("Exception in extractSegmentStreamCopy: " << e.what());
            return false;
        }
    }
    
    // Get last error message
    std::string getLastError(nv_vms::VideoSegmentExtractor* extractor) {
        if (!extractor) {
            return "VideoSegmentExtractor instance is null";
        }
        
        try {
            return extractor->getLastError();
        } catch (const std::exception& e) {
            return "Exception getting error: " + std::string(e.what());
        }
    }
    
    // Check if extractor is available
    bool isExtractorAvailable(nv_vms::VideoSegmentExtractor* extractor) {
        if (!extractor) {
            return false;
        }
        
        try {
            return extractor->isAvailable();
        } catch (const std::exception& e) {
            LOG_ERROR("Exception checking extractor availability: " << e.what());
            return false;
        }
    }
} 
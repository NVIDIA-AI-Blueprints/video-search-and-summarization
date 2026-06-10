/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "../unified_storage_types.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <queue>
#include <deque>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace nv_vms
{

class UploadRateLimiter;
struct BufferedFrame
{
    std::vector<uint8_t> m_data;
    size_t m_size;
    int64_t m_pts;  // Original PTS value for timing integrity
    std::string m_media_type;
    std::string m_session_id;
    std::string m_stream_id;

    BufferedFrame(const void* frame_data, size_t frame_size, int64_t ts, int64_t pts, const std::string& type,
                  const std::string& session, const std::string& stream = "")
        : m_size(frame_size), m_pts(pts), m_media_type(type), m_session_id(session), m_stream_id(stream)
    {
        // Validate input parameters
        if (!frame_data || frame_size == 0)
        {
            throw std::invalid_argument("Invalid frame data or size");
        }

        try
        {
            m_data.resize(frame_size);

            if (m_data.size() != frame_size)
            {
                throw std::runtime_error("Failed to allocate memory for frame data");
            }
            
            // Use memmove for safe memory copy (overlap-safe)
            std::memmove(m_data.data(), frame_data, frame_size);
        }
        catch (const std::exception& e)
        {
            throw std::runtime_error(std::string("Failed to create BufferedFrame: ") + e.what());
        }
    }

    // Move constructor for better performance
    BufferedFrame(BufferedFrame&& other) noexcept
        : m_data(std::move(other.m_data))
        , m_size(other.m_size)
        , m_pts(other.m_pts)
        , m_media_type(std::move(other.m_media_type))
        , m_session_id(std::move(other.m_session_id))
        , m_stream_id(std::move(other.m_stream_id))
    {
        other.m_size = 0;
        other.m_pts = 0;
    }

    // Move assignment operator
    BufferedFrame& operator=(BufferedFrame&& other) noexcept
    {
        if (this != &other)
        {
            m_data = std::move(other.m_data);
            m_size = other.m_size;
            m_pts = other.m_pts;
            m_media_type = std::move(other.m_media_type);
            m_session_id = std::move(other.m_session_id);
            m_stream_id = std::move(other.m_stream_id);
            
            other.m_size = 0;
            other.m_pts = 0;
        }
        return *this;
    }

    // Delete copy constructor and copy assignment operator to prevent expensive copies
    BufferedFrame(const BufferedFrame&) = delete;
    BufferedFrame& operator=(const BufferedFrame&) = delete;

    // Clear method for buffer reuse
    void clear()
    {
        m_data.clear();
        m_data.shrink_to_fit(); // Release memory
        m_size = 0;
        m_pts = 0;
        m_media_type.clear();
        m_session_id.clear();
        m_stream_id.clear();
    }
};

class CloudStorageBuffer
{
public:
    /**
     * @brief Constructor for cloud storage buffer
     * @param config Storage configuration including buffering parameters
     * @param stream_id Unique identifier for the stream
     * @param session_id Unique identifier for the session
     */
    CloudStorageBuffer(const StorageConfig& config, const std::string& stream_id, const std::string& session_id);
    ~CloudStorageBuffer();

    // Buffer management
    bool bufferFrame(const void* data, size_t size, int64_t timestamp, const std::string& media_type,
                     const std::string& session_id, const std::string& stream_id = "");

    void start(std::function<bool(const BufferedFrame&)> upload_callback);
    void stop();
    void flush(); // Force upload all buffered frames

    // Status and monitoring
    size_t getBufferedFrameCount() const
    {
        return m_buffered_frames.load();
    }
    size_t getBufferSizeBytes() const
    {
        return m_buffer_size_bytes.load();
    }
    double getBufferUtilizationPercent() const;
    bool isBufferFull() const;
    bool isRunning() const
    {
        return m_running.load();
    }

    // Get identification info
    std::string getStreamId() const
    {
        return m_stream_id;
    }

    std::string getSessionId() const
    {
        return m_session_id;
    }
    
    void updateSessionId(const std::string& new_session_id)
    {
        std::lock_guard<std::mutex> lock(m_session_mutex);
        m_session_id = new_session_id;
    }
    
    const StorageConfig& getConfiguration() const
    {
        return m_config;
    }
    
    // Clear buffer contents
    void clearBuffer();

    // Statistics access
    struct BufferStats
    {
        uint64_t m_frames_buffered = 0;
        uint64_t m_frames_uploaded = 0;
        uint64_t m_frames_dropped = 0;
        uint64_t m_bytes_buffered = 0;
        uint64_t m_bytes_uploaded = 0;
        double m_avg_upload_rate_fps = 0.0;
        std::chrono::milliseconds m_avg_buffer_delay{0};
    };

    BufferStats getStats() const
    {
        std::lock_guard<std::mutex> lock(m_stats_mutex);
        return m_stats;
    }
    void resetStats();

private:
    // Internal clear buffer method (assumes m_queue_mutex is already locked)
    void clearBufferInternal();

    // Configuration
    const size_t m_max_buffer_size_bytes;
    const size_t m_max_frames;
    StorageConfig m_config; // Store the complete configuration

    // Identification
    const std::string m_stream_id;
    std::string m_session_id; // Made mutable to support session ID updates

    // Buffer state - using deque for efficient front insertion
    std::deque<std::unique_ptr<BufferedFrame>> m_frame_queue;
    std::atomic<size_t> m_buffered_frames{0};
    std::atomic<size_t> m_buffer_size_bytes{0};
    std::atomic<size_t> m_frames_being_processed{0};

    // Thread safety
    mutable std::mutex m_queue_mutex;
    mutable std::mutex m_stats_mutex;
    mutable std::mutex m_flush_mutex;
    mutable std::mutex m_session_mutex;
    std::condition_variable m_queue_cv;
    std::condition_variable m_flush_cv;

    // Thread management
    std::atomic<bool> m_running{false};
    std::atomic<bool> m_stop_requested{false};
    std::atomic<bool> m_flush_mode{false};
    std::atomic<bool> m_critical_buffer_logged{false};
    std::thread m_upload_thread;

    // Upload callback
    std::function<bool(const BufferedFrame&)> m_upload_callback;

    // Rate limiting
    std::unique_ptr<UploadRateLimiter> m_rate_limiter;

    // Statistics
    mutable BufferStats m_stats;
    std::chrono::steady_clock::time_point m_last_stats_update;
    
    // Baseline timing for accurate delay calculation
    std::chrono::steady_clock::time_point m_first_frame_wall_time;
    int64_t m_first_frame_pts{-1};
    std::atomic<bool> m_baseline_initialized{false};

    bool shouldDropFrame() const;
    void updateStats(bool upload_success, size_t frame_size, std::chrono::milliseconds buffer_delay);
    void uploadWorkerThread();
    void logBufferStatus() const;
};

// Frame rate limiter for cloud uploads
class UploadRateLimiter
{
public:
    UploadRateLimiter(double max_fps, const std::string& stream_id, const std::string& session_id);
    ~UploadRateLimiter() = default;

    bool canUpload();    // Returns true if we can upload now
    bool canUploadAdaptive(double buffer_utilization); // Adaptive rate limiting based on buffer utilization
    void recordUpload(); // Record that an upload happened
    void resetRate();    // Reset rate calculation and clear upload history
    double getCurrentRate() const
    {
        return m_current_rate.load();
    }

private:
    const double m_max_fps;
    const std::chrono::milliseconds m_min_interval;
    const std::string m_stream_id;
    const std::string m_session_id;

    std::atomic<double> m_current_rate{0.0};
    std::chrono::steady_clock::time_point m_last_upload;
    mutable std::mutex m_rate_mutex;

    // Rate calculation
    std::queue<std::chrono::steady_clock::time_point> m_upload_times;
    void updateCurrentRate();
};

} // namespace nv_vms
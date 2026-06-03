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

#include "cloud_storage_buffer.h"
#include "logger.h"
#include <algorithm>
#include <chrono>
#include <iomanip>

namespace nv_vms
{

CloudStorageBuffer::CloudStorageBuffer(const StorageConfig& config, const std::string& streamId, const std::string& session_id)
    : m_max_buffer_size_bytes(config.buffering.buffer_size_mb * 1024 * 1024),
      m_max_frames(config.buffering.max_frames),
      m_config(config), // Store the complete configuration
      m_stream_id(streamId),
      m_session_id(session_id),
      m_buffered_frames(0),
      m_buffer_size_bytes(0),
      m_running(false),
      m_stop_requested(false),
      m_upload_thread(),
      m_upload_callback(nullptr),
      m_last_stats_update(std::chrono::steady_clock::now()),
      m_first_frame_wall_time(std::chrono::steady_clock::now()),
      m_first_frame_pts(-1),
      m_baseline_initialized(false)
{
    // Validate configuration values to prevent runtime crashes
    if (m_max_buffer_size_bytes <= 0)
    {
        std::string error_msg = "Invalid buffer size configuration: " + std::to_string(config.buffering.buffer_size_mb) + 
                               "MB results in " + std::to_string(m_max_buffer_size_bytes) + " bytes. Must be positive.";
        LOG(error) << error_msg << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        throw std::invalid_argument(error_msg);
    }
    
    if (m_max_frames <= 0)
    {
        std::string error_msg = "Invalid max frames configuration: " + std::to_string(config.buffering.max_frames) + 
                               ". Must be positive and non-zero.";
        LOG(error) << error_msg << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        throw std::invalid_argument(error_msg);
    }
    
    // Validate max_upload_fps to prevent divide-by-zero in rate limiter
    if (config.buffering.max_upload_fps <= 0.0)
    {
        std::string error_msg = "Invalid max upload FPS configuration: " + std::to_string(config.buffering.max_upload_fps) + 
                               ". Must be positive and non-zero.";
        LOG(error) << error_msg << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        throw std::invalid_argument(error_msg);
    }
    
    LOG(info) << "CloudStorageBuffer initialized: "
              << " max_size=" << config.buffering.buffer_size_mb << "MB, "
              << " max_frames=" << config.buffering.max_frames 
              << ", max_upload_fps=" << config.buffering.max_upload_fps
              << " stream_id=" << m_stream_id << " session_id=" << m_session_id
              << std::endl;
              
    // Create rate limiter with the specified FPS from config
    m_rate_limiter = std::make_unique<UploadRateLimiter>(config.buffering.max_upload_fps, streamId, session_id);
}

CloudStorageBuffer::~CloudStorageBuffer()
{
    try {
        stop();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~CloudStorageBuffer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~CloudStorageBuffer" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

bool CloudStorageBuffer::bufferFrame(const void* data, size_t size, int64_t timestamp, const std::string& media_type,
                                     const std::string& session_id, const std::string& stream_id)
{
    if (!m_running.load())
    {
        LOG(info) << "Attempted to buffer frame when buffer is not running"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        return false;
    }

    // Validate input parameters
    if (!data || size == 0)
    {
        LOG(info) << "Invalid frame data: data=" << (data ? "valid" : "null") << ", size=" << size
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        return false;
    }

    if (media_type.empty() || session_id.empty())
    {
        LOG(info) << "Invalid frame metadata: media_type='" << media_type << "', session_id='" << session_id << "'"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        return false;
    }

    LOG(verbose) << "Buffering frame: " << size << " bytes, timestamp: " << timestamp 
                 << ", media_type: " << media_type << ", session_id: " << session_id 
                 << ", stream_id: " << stream_id << std::endl;

    // Check if buffer is full with atomic operations
    if (shouldDropFrame())
    {
        std::lock_guard<std::mutex> stats_lock(m_stats_mutex);
        m_stats.m_frames_dropped++;
        LOG(info) << "Buffer full, dropping frame. "
                  << "Buffered: " << m_buffered_frames.load() << " frames, "
                  << (m_buffer_size_bytes.load() / 1024 / 1024) << "MB"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        return false;
    }

    // Create buffered frame with exception handling
    std::unique_ptr<BufferedFrame> frame;
    try
    {
        frame = std::make_unique<BufferedFrame>(data, size, timestamp, timestamp, media_type, session_id, stream_id);

        // Validate the created frame
        if (!frame || frame->m_data.empty() || frame->m_size != size)
        {
            LOG(info) << "Failed to create valid buffered frame"
                      << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
            return false;
        }
    }
    catch (const std::exception& e)
    {
        LOG(info) << "Exception creating buffered frame: " << e.what() << " stream_id=" << m_stream_id
                  << " session_id=" << m_session_id << std::endl;
        return false;
    }
    catch (...)
    {
        LOG(info) << "Unknown exception creating buffered frame"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        return false;
    }

    // Add to queue with atomic operations for better thread safety
    bool buffer_full = false;
    {
        std::lock_guard<std::mutex> lock(m_queue_mutex);
        
        // Double-check buffer capacity after acquiring lock
        if (m_buffered_frames.load() >= m_max_frames || 
            m_buffer_size_bytes.load() + size > m_max_buffer_size_bytes)
        {
            buffer_full = true;
        }
        else
        {
            // Initialize baseline timing for the first frame (thread-safe)
            bool expected = false;
            if (m_baseline_initialized.compare_exchange_strong(expected, true))
            {
                m_first_frame_wall_time = std::chrono::steady_clock::now();
                m_first_frame_pts = timestamp;
                LOG(verbose) << "Initialized baseline timing: first_frame_pts=" << m_first_frame_pts 
                             << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
            }
            
            m_frame_queue.push_back(std::move(frame));
            m_buffered_frames++;
            m_buffer_size_bytes += size;
        }
    }

    // Handle buffer full case outside of queue mutex to avoid nested locks
    if (buffer_full)
    {
        std::lock_guard<std::mutex> stats_lock(m_stats_mutex);
        m_stats.m_frames_dropped++;
        LOG(error) << "Buffer became full while acquiring lock, dropping frame"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        return false;
    }

    // Update stats outside of queue mutex to avoid nested locks
    {
        std::lock_guard<std::mutex> stats_lock(m_stats_mutex);
        m_stats.m_frames_buffered++;
        m_stats.m_bytes_buffered += size;
    }

    // Notify upload thread
    m_queue_cv.notify_one();

    return true;
}

void CloudStorageBuffer::start(std::function<bool(const BufferedFrame&)> upload_callback)
{
    if (m_running.load())
    {
        LOG(info) << "CloudStorageBuffer already running"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        return;
    }

    // Validate upload callback
    if (!upload_callback)
    {
        LOG(info) << "Invalid upload callback provided, cannot start CloudStorageBuffer"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        return;
    }

    m_upload_callback = upload_callback;
    m_running = true;
    m_stop_requested = false;

    // Start upload worker thread
    try
    {
        m_upload_thread = std::thread(&CloudStorageBuffer::uploadWorkerThread, this);
        LOG(info) << "CloudStorageBuffer started"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
    }
    catch (const std::exception& e)
    {
        LOG(info) << "Failed to start upload worker thread: " << e.what() << " stream_id=" << m_stream_id
                  << " session_id=" << m_session_id << std::endl;
        m_running = false;
        m_stop_requested = false;
        throw;
    }
}

void CloudStorageBuffer::stop()
{
    if (!m_running.load())
    {
        return;
    }

    LOG(info) << "Stopping CloudStorageBuffer..."
              << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;

    // Set stop flag first
    m_stop_requested = true;

    // Notify all waiting threads
    m_queue_cv.notify_all();

    // Wait for upload thread to finish
    if (m_upload_thread.joinable())
    {
        try
        {
            m_upload_thread.join();
        }
        catch (const std::exception& e)
        {
            LOG(info) << "Exception joining upload thread: " << e.what() << " stream_id=" << m_stream_id
                      << " session_id=" << m_session_id << std::endl;
        }
    }

    // Clear the upload callback to prevent any further calls
    m_upload_callback = nullptr;
    m_running = false;

    // Clear any remaining frames in queue
    {
        std::lock_guard<std::mutex> lock(m_queue_mutex);
        while (!m_frame_queue.empty())
        {
            m_frame_queue.pop_front();
        }
        m_buffered_frames = 0;
        m_buffer_size_bytes = 0;
    }

    // Log final stats
    BufferStats final_stats = getStats();
    LOG(info) << "CloudStorageBuffer stopped. Final stats: "
              << "buffered=" << final_stats.m_frames_buffered << ", uploaded=" << final_stats.m_frames_uploaded
              << ", dropped=" << final_stats.m_frames_dropped << ", stream_id=" << m_stream_id
              << ", session_id=" << m_session_id << std::endl;
}

void CloudStorageBuffer::flush()
{
    if (!m_running.load())
    {
        return;
    }

    // Set flush mode to disable rate limiting during flush
    m_flush_mode = true;

    // Wait until queue is empty or timeout
    const auto timeout = std::chrono::seconds(m_config.buffering.flush_timeout_sec);
    const auto start_time = std::chrono::steady_clock::now();

    while (m_buffered_frames.load() > 0)
    {
        if (std::chrono::steady_clock::now() - start_time > timeout)
        {
            LOG(info) << "Flush timeout, " << m_buffered_frames.load() << " frames still buffered"
                      << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    // Wait for worker thread to finish processing any frames it has already dequeued
    // Use conditional wait instead of sleep for better synchronization
    {
        std::unique_lock<std::mutex> lock(m_flush_mutex);
        size_t initial_frames = m_frames_being_processed.load();
        if (initial_frames > 0)
        {
            LOG(info) << "Waiting for " << initial_frames << " frames to finish processing"
                      << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        }
        
        if (!m_flush_cv.wait_for(lock, std::chrono::seconds(m_config.buffering.flush_timeout_sec), 
                                [this] { return m_frames_being_processed.load() == 0; }))
        {
            LOG(info) << "Flush wait timeout, " << m_frames_being_processed.load() 
                      << " frames still being processed"
                      << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        }
        else
        {
            LOG(verbose) << "All frames finished processing successfully"
                      << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
        }
    }

    // Disable flush mode
    m_flush_mode = false;

    LOG(info) << "CloudStorageBuffer flush completed"
              << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
}

double CloudStorageBuffer::getBufferUtilizationPercent() const
{
    double size_util = (double)m_buffer_size_bytes.load() / m_max_buffer_size_bytes * 100.0;
    double frame_util = (double)m_buffered_frames.load() / m_max_frames * 100.0;
    return std::max(size_util, frame_util);
}

bool CloudStorageBuffer::isBufferFull() const
{
    return m_buffer_size_bytes.load() >= m_max_buffer_size_bytes || m_buffered_frames.load() >= m_max_frames;
}

void CloudStorageBuffer::resetStats()
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_stats = BufferStats{};
    m_last_stats_update = std::chrono::steady_clock::now();
    LOG(info) << "CloudStorageBuffer stats reset"
              << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
}

void CloudStorageBuffer::clearBuffer()
{
    // Wait for any frames being processed to complete
    {
        std::unique_lock<std::mutex> lock(m_flush_mutex);
        if (m_frames_being_processed.load() > 0)
        {
            LOG(info) << "Waiting for " << m_frames_being_processed.load() 
                      << " frames to finish processing before clearing buffer"
                      << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
            
            if (!m_flush_cv.wait_for(lock, std::chrono::seconds(m_config.buffering.flush_timeout_sec), 
                                    [this] { return m_frames_being_processed.load() == 0; }))
            {
                LOG(warning) << "Timeout waiting for frames to finish processing, clearing buffer anyway"
                          << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
            }
        }
    }
    
    std::lock_guard<std::mutex> lock(m_queue_mutex);
    clearBufferInternal();
}

void CloudStorageBuffer::clearBufferInternal()
{
    // This method assumes m_queue_mutex is already locked
    
    // Clear all frames in queue
    while (!m_frame_queue.empty())
    {
        m_frame_queue.pop_front();
    }
    
    // Reset counters
    m_buffered_frames = 0;
    m_buffer_size_bytes = 0;
    
    // Reset critical buffer flag
    m_critical_buffer_logged = false;
    
    // Reset rate limiter to clear stale upload history
    if (m_rate_limiter)
    {
        // Clear upload history to reset rate calculation
        m_rate_limiter->resetRate();
    }
    
    // Reset baseline timing for new session
    m_baseline_initialized = false;
    m_first_frame_pts = -1;
    m_first_frame_wall_time = std::chrono::steady_clock::now();
    
    LOG(verbose) << "Buffer cleared for new session: " << m_session_id 
              << " stream_id=" << m_stream_id << std::endl;
}

void CloudStorageBuffer::uploadWorkerThread()
{
    LOG(info) << "Upload worker thread started"
              << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;

    auto last_log_time = std::chrono::steady_clock::now();
    const auto log_interval = std::chrono::seconds(60);

    while (!m_stop_requested.load())
    {
        std::unique_ptr<BufferedFrame> frame;

        // Get frame from queue with improved error handling
        {
            std::unique_lock<std::mutex> lock(m_queue_mutex);

            if (m_queue_cv.wait_for(lock, std::chrono::milliseconds(1000),
                                    [this] { return !m_frame_queue.empty() || m_stop_requested.load(); }))
            {
                if (!m_frame_queue.empty())
                {
                    frame = std::move(m_frame_queue.front());
                    m_frame_queue.pop_front();
                    m_buffered_frames--;
                    m_buffer_size_bytes -= frame->m_size;
                }
            }
        }

        if (frame && !m_stop_requested.load())
        {
            // Increment frames being processed counter
            m_frames_being_processed++;
            
            // Validate frame data before processing
            if (!frame || frame->m_data.empty() || frame->m_size == 0 || frame->m_size > frame->m_data.size())
            {
                LOG(info) << "Invalid frame data detected, skipping upload "
                          << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
                m_frames_being_processed--;
                continue;
            }

            // Rate limiting - but be more aggressive when buffer is full
            // Skip rate limiting during flush mode for immediate upload
            double buffer_utilization = getBufferUtilizationPercent();
            
            // Adaptive rate limiting: use buffer utilization to adjust upload rate
            // TODO: 17.07.25: This is not working as expected. And we need to implemnet a rate limitor in such
            // a way that it can be adaptive as per the server responses/failures. If server is not able to upload the frame,
            // then we should limit the upload rate in success case we don't need to limit the upload rate.
            bool should_rate_limit = false; //!m_flush_mode.load(); && !m_rate_limiter->canUploadAdaptive(buffer_utilization);
            
            // Only apply rate limiting if buffer is not critically full and not in flush mode
            if (should_rate_limit && buffer_utilization < 90.0)
            {
                static int rate_limit_log_count = 0;
                if (++rate_limit_log_count % 100 == 0)
                {
                    LOG(info) << "Rate limiting active: buffer_utilization=" << buffer_utilization 
                                << "%, upload_rate=" << m_rate_limiter->getCurrentRate() << "fps"
                                << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
                }
                
                // Rate limit is active - put frame back at FRONT of queue to maintain order
                {
                    std::lock_guard<std::mutex> lock(m_queue_mutex);
                    size_t frame_size = frame->m_size; // Get size before moving
                    
                    // Efficiently put frame at front using deque
                    m_frame_queue.push_front(std::move(frame));
                    
                    m_buffered_frames++;
                    m_buffer_size_bytes += frame_size; // Use saved size
                }
                // Decrement frames being processed since we're putting it back in queue
                if (m_frames_being_processed.load() > 0)
                {
                    m_frames_being_processed--;
                    m_flush_cv.notify_all();
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }

            

            // Log critical buffer utilization
            if (buffer_utilization > 90.0 && !m_critical_buffer_logged.load())
            {
                LOG(warning) << "CRITICAL: Buffer utilization at " << buffer_utilization
                          << "%, uploads may be too slow "
                          << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
                m_critical_buffer_logged = true;
            }
            else
            {
                // Reset flag when buffer utilization drops below critical threshold
                if (m_critical_buffer_logged.load())
                {
                    LOG(info) << "Buffer utilization normalized (" << buffer_utilization
                              << "%), rate limiting restored "
                              << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
                    m_critical_buffer_logged = false;
                }
            }

            // Calculate buffer delay using baseline timing for accurate measurement
            std::chrono::milliseconds buffer_delay{0};
            
            if (m_baseline_initialized.load() && m_first_frame_pts >= 0)
            {
                // Calculate relative delay: how much time has passed since the frame's PTS
                // relative to when the first frame arrived
                auto now = std::chrono::steady_clock::now();
                auto wall_time_since_first = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - m_first_frame_wall_time).count();
                
                // Calculate media time since first frame (in milliseconds)
                auto media_time_since_first = frame->m_pts - m_first_frame_pts;
                
                // Buffer delay = wall time - media time (how much we're behind real-time)
                auto delay_ms = wall_time_since_first - media_time_since_first;
                buffer_delay = std::chrono::milliseconds(delay_ms);
                
                // Sanity check for unreasonable delays
                if (buffer_delay.count() > 60000 || buffer_delay.count() < -60000)
                { // More than 1 minute ahead or behind
                    LOG(verbose) << "Unusual buffer delay: " << buffer_delay.count() 
                                 << "ms, using default. wall_time_since_first=" << wall_time_since_first 
                                 << "ms, media_time_since_first=" << media_time_since_first 
                                 << "ms, frame_pts=" << frame->m_pts 
                                 << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
                    buffer_delay = std::chrono::milliseconds(100);
                }
            }
            else
            {
                // Baseline not initialized yet, use a reasonable default
                buffer_delay = std::chrono::milliseconds(100);
            }

            // Call upload callback with exception handling
            bool upload_success = false;
            try
            {
                if (m_upload_callback)
                {
                    upload_success = m_upload_callback(*frame);
                }
                else
                {
                    LOG(error) << "Upload callback is null, cannot upload frame"
                               << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
                }
            }
            catch (const std::exception& e)
            {
                LOG(error) << "Upload callback exception: " << e.what() << " stream_id=" << m_stream_id
                           << " session_id=" << m_session_id << std::endl;
                upload_success = false;
            }
            catch (...)
            {
                LOG(error) << "Upload callback unknown exception caught"
                           << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
                upload_success = false;
            }

            // Update statistics
            updateStats(upload_success, frame->m_size, buffer_delay);

            if (!upload_success)
            {
                LOG(info) << "Frame upload failed, session=" << frame->m_session_id << ", size=" << frame->m_size
                          << " bytes"
                          << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;

                // If upload failed and buffer is getting full, consider dropping some frames
                if (buffer_utilization > 90.0)
                {
                    LOG(info) << "Upload failed and buffer critically full, considering frame drops"
                              << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
                }
            }

            // Decrement frames being processed counter and notify flush waiters
            if (m_frames_being_processed.load() > 0)
            {
                m_frames_being_processed--;
                m_flush_cv.notify_all();
            }
            else
            {
                LOG(info) << "Warning: frames_being_processed counter was already 0"
                          << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
            }
        }

        // Periodic logging
        auto now = std::chrono::steady_clock::now();
        if (now - last_log_time >= log_interval)
        {
            logBufferStatus();
            last_log_time = now;
        }
    }

    LOG(info) << "Upload worker thread stopped"
              << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
}

bool CloudStorageBuffer::shouldDropFrame() const
{
    // Drop frames if buffer is at capacity
    if (isBufferFull())
    {
        return true;
    }

    // Drop frames if buffer utilization is very high
    if (getBufferUtilizationPercent() > 95.0)
    {
        return true;
    }

    // Drop frames if we have too many frames buffered
    if (m_buffered_frames.load() > (m_max_frames * 0.95))
    {
        return true;
    }

    return false;
}

void CloudStorageBuffer::updateStats(bool upload_success, size_t frame_size, std::chrono::milliseconds buffer_delay)
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);

    if (upload_success)
    {
        m_stats.m_frames_uploaded++;
        m_stats.m_bytes_uploaded += frame_size;
    }

    // Update average buffer delay (simple moving average)
    const double alpha = 0.1; // Smoothing factor
    m_stats.m_avg_buffer_delay = std::chrono::milliseconds(
        static_cast<long long>(alpha * buffer_delay.count() + (1.0 - alpha) * m_stats.m_avg_buffer_delay.count()));
}

void CloudStorageBuffer::logBufferStatus() const
{
    //BufferStats stats = getStats();
    double utilization = getBufferUtilizationPercent();

    // Calculate buffer size in MB with proper precision
    double buffer_size_mb = static_cast<double>(m_buffer_size_bytes.load()) / (1024.0 * 1024.0);

    // Get current upload rate from rate limiter
    // double current_upload_rate = m_rate_limiter ? m_rate_limiter->getCurrentRate() : 0.0;

    LOG(info) << "CloudStorageBuffer status: "
              << " "
              << "utilization=" << std::fixed << std::setprecision(1) << utilization << "%, "
              << "buffered=" << m_buffered_frames.load() << " frames, "
              << "size=" << std::setprecision(2) << buffer_size_mb << "MB, "
            //   << "upload_rate=" << std::setprecision(1) << current_upload_rate << "fps, "
            //   << "delay=" << stats.m_avg_buffer_delay.count() << "ms"
              << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;

    // Add debug info for troubleshooting
    if (m_buffered_frames.load() > 0 && buffer_size_mb < 0.01)
    {
        LOG(verbose) << "Warning: Buffer has " << m_buffered_frames.load() << " frames but size shows " << buffer_size_mb
                  << "MB - possible size calculation issue "
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
    }

    if (utilization > 85.0)
    {
        LOG(info) << "High buffer utilization detected: " << utilization << "%"
                  << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
    }
}

// UploadRateLimiter implementation

UploadRateLimiter::UploadRateLimiter(double max_fps, const std::string& stream_id, const std::string& session_id)
    : m_max_fps(max_fps > 0.0 ? max_fps : 30.0), // Default to 30 FPS if invalid
      m_min_interval(std::chrono::milliseconds(static_cast<long long>(1000.0 / (max_fps > 0.0 ? max_fps : 30.0)))),
      m_stream_id(stream_id),
      m_session_id(session_id),
      m_last_upload(std::chrono::steady_clock::now())
{
    // Validate max_fps parameter
    if (max_fps <= 0.0)
    {
        LOG(error) << "Invalid max_fps value: " << max_fps << " (must be > 0), using default 30.0 FPS"
                   << " stream_id=" << stream_id << " session_id=" << session_id << std::endl;
    }
    else
    {
        LOG(info) << "UploadRateLimiter initialized: max_fps=" << max_fps << " stream_id=" << stream_id << " session_id=" << session_id << std::endl;
    }
}

bool UploadRateLimiter::canUpload()
{
    std::lock_guard<std::mutex> lock(m_rate_mutex);

    auto now = std::chrono::steady_clock::now();
    auto elapsed = now - m_last_upload;

    bool can_upload = elapsed >= m_min_interval;
    return can_upload;
}

bool UploadRateLimiter::canUploadAdaptive(double buffer_utilization)
{
    std::lock_guard<std::mutex> lock(m_rate_mutex);

    auto now = std::chrono::steady_clock::now();
    auto elapsed = now - m_last_upload;

    // Adaptive rate limiting based on buffer utilization
    double adaptive_interval = m_min_interval.count();
    
    if (buffer_utilization > 85.0) {
        // When buffer is very full, reduce rate to 50% of normal
        adaptive_interval = m_min_interval.count() * 2.0; // Slower uploads
    } else if (buffer_utilization > 75.0) {
        // When buffer is moderately full, reduce rate to 75% of normal
        adaptive_interval = m_min_interval.count() * 1.33; // Slower uploads
    } else if (buffer_utilization < 25.0) {
        // When buffer is very low, slow down uploads to match input rate
        adaptive_interval = m_min_interval.count() * 1.2; // Slightly slower
    }

    // Convert elapsed time to milliseconds for comparison with adaptive_interval
    auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
    bool can_upload = elapsed_ms >= adaptive_interval;
    // LOG(info) << "canUploadAdaptive: buffer_utilization=" << buffer_utilization << " adaptive_interval=" << adaptive_interval <<
    // " elapsed_ms=" << elapsed_ms << " can_upload=" << can_upload << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
    return can_upload;
}

void UploadRateLimiter::recordUpload()
{
    std::lock_guard<std::mutex> lock(m_rate_mutex);

    auto now = std::chrono::steady_clock::now();
    m_last_upload = now;

    // Record upload time for rate calculation
    m_upload_times.push(now);

    // Keep only recent uploads (last 5 seconds for more responsive rate calculation)
    while (!m_upload_times.empty() && (now - m_upload_times.front()) > std::chrono::seconds(5))
    {
        m_upload_times.pop();
    }

    updateCurrentRate();
    
    // Debug: Log upload recording
    // LOG(info) << "UploadRateLimiter::recordUpload() called, upload_times.size=" << m_upload_times.size() 
    //           << ", current_rate=" << m_current_rate.load() << "fps"
    //           << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
}

void UploadRateLimiter::resetRate()
{
    std::lock_guard<std::mutex> lock(m_rate_mutex);
    
    // Clear upload history
    while (!m_upload_times.empty())
    {
        m_upload_times.pop();
    }
    
    // Reset current rate
    m_current_rate = 0.0;
    
    // Reset last upload time to now
    m_last_upload = std::chrono::steady_clock::now();
}

void UploadRateLimiter::updateCurrentRate()
{
    auto now = std::chrono::steady_clock::now();
    
    // If no uploads in the last 5 seconds, rate is 0
    if (m_upload_times.empty() || (now - m_upload_times.back()) > std::chrono::seconds(5))
    {
        m_current_rate = 0.0;
        return;
    }
    
    // Need at least 2 uploads to calculate a rate
    if (m_upload_times.size() < 2) {
        m_current_rate = 0.0;
        return;
    }

    auto duration = m_upload_times.back() - m_upload_times.front();
    auto duration_seconds = std::chrono::duration_cast<std::chrono::milliseconds>(duration).count() / 1000.0;

    if (duration_seconds > 0)
    {
        // FIXED: Calculate rate as (uploads - 1) / duration for correct interval-based rate
        // For n uploads, there are (n-1) intervals between them
        m_current_rate = static_cast<double>(m_upload_times.size() - 1) / duration_seconds;
    }
    else
    {
        m_current_rate = 0.0;
    }
    
    // Debug: Log rate calculation details
    // LOG(info) << "Rate calculation: uploads=" << m_upload_times.size() 
    //           << ", duration=" << std::fixed << std::setprecision(3) << duration_seconds 
    //           << "s, calculated_rate=" << std::setprecision(1) << m_current_rate.load() << "fps"
    //           << " stream_id=" << m_stream_id << " session_id=" << m_session_id << std::endl;
}

} // namespace nv_vms
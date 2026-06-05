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

#include "cloud_storage_writer.h"
#include "cloud_storage_buffer.h"
#include "logger.h"
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>

namespace nv_vms
{

// CloudStorageWriter implementation

CloudStorageWriter::CloudStorageWriter()
{
    LOG(info) << "CloudStorageWriter created" << std::endl;
}

CloudStorageWriter::~CloudStorageWriter()
{
    try {
        stopBuffering();
        LOG(info) << "CloudStorageWriter destroyed" << std::endl;
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~CloudStorageWriter: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~CloudStorageWriter" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

bool CloudStorageWriter::writeData(const std::string& session_id, const void* data, size_t size, int64_t pts,
                                            const std::string& media_type)
{
    if (m_buffer && m_config.buffering.enabled)
    {
        // Use buffered upload
        return handleBufferedWrite(session_id, data, size, pts, media_type);
    }
    else
    {
        // Direct upload
        return doWriteData(session_id, data, size, pts, media_type);
    }
}

std::string CloudStorageWriter::startSession(const std::string& remote_path, const std::string& stream_id,
                                                      size_t estimated_size)
{
    std::lock_guard<std::mutex> lock(m_session_mutex);

    // Generate session ID using standardized format
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(100000, 999999);

    // Get current timestamp
    auto now = std::chrono::system_clock::now();
    auto timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();

    // Create session ID with format: cloud_timestamp_random_streamid
    std::string session_id = "cloud_" + std::to_string(timestamp) + "_" + std::to_string(dis(gen)) + "_" + stream_id;

    // Store stream_id for this session
    m_active_sessions[session_id] = stream_id;

    // Initialize buffering if enabled
    if (m_config.buffering.enabled)
    {
        initializeBuffering(stream_id, session_id);
    }

    LOG(verbose) << "Started cloud recording session " << session_id << " with stream_id: " << stream_id << std::endl;
    return session_id;
}

StorageResult CloudStorageWriter::completeSession(const std::string& session_id, const std::string& stream_id)
{
    // Flush any buffered data for this session first
    if (m_buffer)
    {
        m_buffer->flush();
    }

    // Complete the recording session
    StorageResult result = doCompleteSession(session_id, stream_id);

    // Clean up session and clear buffer for next session
    {
        std::lock_guard<std::mutex> lock(m_session_mutex);
        m_active_sessions.erase(session_id);
        
        // Clear the buffer to prepare for the next session
        // This removes all buffered frames but keeps the buffer running
        if (m_buffer)
        {
            m_buffer->clearBuffer();
        }
        
        LOG(info) << "Session completed, buffer cleared and ready for reuse. Active sessions: " 
                  << m_active_sessions.size() << std::endl;
    }

    return result;
}

bool CloudStorageWriter::cancelSession(const std::string& session_id, const std::string& stream_id)
{
    // Cancel any buffered uploads for this session
    if (m_buffer)
    {
        LOG(info) << "Cancelling buffered uploads for session: " << session_id << std::endl;
        // TODO: Implement session-specific cancellation in buffer
    }

    // Cancel the recording session
    bool result = doCancelSession(session_id, stream_id);

    // Clean up session
    {
        std::lock_guard<std::mutex> lock(m_session_mutex);
        m_active_sessions.erase(session_id);
    }

    return result;
}

bool CloudStorageWriter::configure(const StorageConfig& config)
{
    m_config = config;
    // Buffer will be created when first session starts, not during configuration
    return true;
}

StorageStats CloudStorageWriter::getStats() const
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    StorageStats stats = m_stats;

    // Update buffering stats if buffer is active
    if (m_buffer)
    {
        auto buffer_stats = m_buffer->getStats();
        stats.buffering.frames_buffered = buffer_stats.m_frames_buffered;
        stats.buffering.frames_uploaded = buffer_stats.m_frames_uploaded;
        stats.buffering.frames_dropped = buffer_stats.m_frames_dropped;
        stats.buffering.buffer_utilization_percent = m_buffer->getBufferUtilizationPercent();
        stats.buffering.upload_rate_fps = buffer_stats.m_avg_upload_rate_fps;
        stats.buffering.buffer_delay = buffer_stats.m_avg_buffer_delay;
        stats.buffering.is_buffering_active = true;
    }

    return stats;
}

void CloudStorageWriter::resetStats()
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_stats = StorageStats{};

    if (m_buffer)
    {
        m_buffer->resetStats();
    }
}

void CloudStorageWriter::flushBuffers()
{
    if (m_buffer)
    {
        m_buffer->flush();
    }
}

void CloudStorageWriter::clearBuffers()
{
    if (m_buffer)
    {
        LOG(info) << "Clearing cloud storage buffer for next session" << std::endl;
        m_buffer->clearBuffer();
    }
}

void CloudStorageWriter::initializeBuffering(const std::string& stream_id, const std::string& session_id)
{
    // Reuse existing buffer if it exists and is for the same stream
    if (m_buffer)
    {
        // Check if buffer is for the same stream
        if (m_buffer->getStreamId() == stream_id)
        {
            // Reuse existing buffer by updating session ID
            m_buffer->updateSessionId(session_id);
            
            // Ensure buffer is cleared and ready for new session
            m_buffer->clearBuffer();
            
            if (!m_buffer->isRunning())
            {
                // Create upload callback
                auto upload_callback = [this](const BufferedFrame& frame) -> bool
                { return this->doWriteData(frame.m_session_id, frame.m_data.data(), frame.m_size, frame.m_pts, frame.m_media_type); };

                m_buffer->start(upload_callback);
                LOG(info) << "Reused existing buffer for stream: " << stream_id 
                          << " with new session: " << session_id << " (buffer cleared)" << std::endl;
            }
            else
            {
                LOG(verbose) << "Buffer already running, continuing with existing buffer for stream: " << stream_id 
                          << " session: " << session_id << " (buffer cleared)" << std::endl;
            }
            return;
        }
        else
        {
            // Different stream, need to stop and recreate
            LOG(info) << "Different stream detected, stopping existing buffer for: " << m_buffer->getStreamId() 
                      << " and creating new one for: " << stream_id << std::endl;
            stopBuffering();
        }
    }

    // Create new buffer
    m_buffer = std::make_unique<CloudStorageBuffer>(m_config, stream_id, session_id);

    // Create upload callback
    auto upload_callback = [this](const BufferedFrame& frame) -> bool
    { return this->doWriteData(frame.m_session_id, frame.m_data.data(), frame.m_size, frame.m_pts, frame.m_media_type); };

    m_buffer->start(upload_callback);

    LOG(info) << "Cloud storage buffering initialized with buffer size: " << m_config.buffering.buffer_size_mb
              << " and max frames: " << m_config.buffering.max_frames
              << ", max_upload_fps=" << m_config.buffering.max_upload_fps 
              << ", enabled=" << m_config.buffering.enabled             
              << " for stream: " << stream_id 
              << " session: " << session_id << std::endl;
}

void CloudStorageWriter::stopBuffering()
{
    if (m_buffer)
    {
        m_buffer->stop();
        m_buffer.reset();
        LOG(info) << "Cloud storage buffering stopped" << std::endl;
    }
}

bool CloudStorageWriter::handleBufferedWrite(const std::string& session_id, const void* data, size_t size, int64_t pts,
                                             const std::string& media_type)
{
    if (!m_buffer)
    {
        return false;
    }

    // Get device_id for this session to use as stream_id
    std::string stream_id = "";
    {
        std::lock_guard<std::mutex> lock(m_session_mutex);
        auto it = m_active_sessions.find(session_id);
        if (it != m_active_sessions.end())
        {
            stream_id = it->second;
        }
    }

    // Use provided pts if available, otherwise use current timestamp
    int64_t timestamp = (pts > 0) ? pts : 
        std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();

    LOG(verbose) << "Buffering frame with timestamp: " << timestamp 
                 << " (pts: " << pts << ")"
                 << ", media_type: " << media_type
                 << " for session: " << session_id 
                 << ", stream: " << stream_id << std::endl;

    return m_buffer->bufferFrame(data, size, timestamp, media_type, session_id, stream_id);
}

void CloudStorageWriter::updateBufferingStats()
{
    // This would update stats based on buffer performance
    // For now, this is a placeholder
}

} // namespace nv_vms
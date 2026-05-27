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

#include "unified_cloud_storage_writer.h"
#include "cloud_storage_writer_factory.h"
#include <filesystem>
#include <fstream>
#include <iostream>

using namespace std;
using namespace nv_vms;

// UnifiedCloudStorageWriter implementation
UnifiedCloudStorageWriter::UnifiedCloudStorageWriter() : UnifiedStorageWriter(StorageType::CLOUD)
{
    LOG(info) << "Created unified cloud storage writer" << endl;
}

UnifiedCloudStorageWriter::~UnifiedCloudStorageWriter()
{
  try
  {
    LOG(info) << "Destroying unified cloud storage writer for stream: " << (m_current_session_id.empty() ? "unknown" : m_current_session_id)
              << endl;

    // Thread-safe cleanup of session tracking maps
    std::vector<std::pair<std::string, std::string>> sessions_to_cancel;
    
    {
        std::lock_guard<std::mutex> session_lock(m_session_mutex);
        
        // First, collect all sessions that need to be cancelled
        // Always iterate over m_active_sessions to ensure no sessions are leaked
        if (!m_active_sessions.empty())
        {
            LOG(info) << "Stopping " << m_active_sessions.size() << " active sessions during destruction for stream: " << m_streamId << endl;

            // Collect all active sessions for cancellation (regardless of m_session_active flag)
            for (const auto& session_pair : m_active_sessions)
            {
                const std::string& session_id = session_pair.first;
                const std::string& stream_id = session_pair.second;
                sessions_to_cancel.emplace_back(session_id, stream_id);
            }
        }
        else
        {
            LOG(info) << "No active sessions to cancel during destruction for stream: " << m_streamId << endl;
        }

        // Clear session tracking maps
        m_active_sessions.clear();
        m_session_remote_paths.clear();
        LOG(info) << "Cleared session tracking maps" << endl;
    }
    
    // Now cancel sessions without holding the mutex (prevents deadlock)
    for (const auto& session_pair : sessions_to_cancel)
    {
        const std::string& session_id = session_pair.first;
        const std::string& stream_id = session_pair.second;

        std::shared_ptr<StorageWriter> cloud_writer;
        {
            std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
            cloud_writer = m_cloud_writer;
        }
        if (cloud_writer)
        {
            cloud_writer->cancelSession(session_id, stream_id);
            LOG(info) << "Cleaned up session during destruction: " << session_id << endl;
        }
    }

    // Reset session state variables
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        m_session_active = false;
        m_current_session_id.clear();
        m_current_remote_path.clear();
    }

    // Destroy the cloud writer (this will cleanup any remaining cloud sessions)
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        if (m_cloud_writer)
        {
            LOG(info) << "Destroying cloud writer" << endl;
            m_cloud_writer.reset();
        }
    }

    // Call base class destructor (which handles pipeline cleanup)
    // Note: The base class destructor will be called automatically after this destructor

    LOG(info) << "Unified cloud storage writer destruction completed" << endl;
  } catch (const std::exception& e) {
    try { LOG(error) << "Exception in ~UnifiedCloudStorageWriter: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
  } catch (...) {
    try { LOG(error) << "Unknown exception in ~UnifiedCloudStorageWriter" << endl; } catch (...) { (void)std::current_exception(); }
  }
}

bool UnifiedCloudStorageWriter::initializeStorage()
{
    // Get storage type from configuration (default to minio for cloud storage)
    std::string storage_type = m_config.getParameter(StorageConstants::CLOUD_TYPE_KEY, StorageConstants::MINIO_TYPE);

    if (storage_type != StorageConstants::MINIO_TYPE && storage_type != StorageConstants::AWS_S3_TYPE)
    {
        LOG(error) << "Unsupported cloud storage type: " << storage_type << " for stream: " << m_streamId 
                   << " (only 'minio' is currently implemented)" << endl;
        return false;
    }

    // Create MinIO storage writer using factory
    m_cloud_writer = CloudStorageWriterFactory::createWriter(storage_type, m_config);
    if (!m_cloud_writer)
    {
        LOG(error) << "Failed to create " << storage_type << " storage writer" << endl;
        return false;
    }

    LOG(info) << storage_type << " storage writer initialized successfully" << endl;
    return true;
}

GstElement* UnifiedCloudStorageWriter::createSinkElement()
{
    // Create appsink for cloud storage
    GstElement* appsink = gst_element_factory_make("appsink", nullptr);
    if (!appsink)
    {
        LOG(error) << "Failed to create appsink element" << endl;
        return nullptr;
    }

    // Configure appsink
    g_object_set(G_OBJECT(appsink), "emit-signals", TRUE, nullptr);
    g_object_set(G_OBJECT(appsink), "sync", FALSE, nullptr);

    // Connect signals
    g_signal_connect(appsink, "new-sample", G_CALLBACK(onNewSampleCloud), this);
    g_signal_connect(appsink, "eos", G_CALLBACK(onEOSCloud), this);

    LOG(info) << "Created appsink element for cloud storage for stream ID: "
              << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
    return appsink;
}

bool UnifiedCloudStorageWriter::configureSinkElement(GstElement* sink, const std::string& remote_path,
                                                     const std::string& session_id)
{
    if (bool isSinkValid = sink ? GST_IS_ELEMENT(sink) : false; !isSinkValid)
    {
        LOG(error) << "Invalid sink element for cloud storage" << endl;
        return false;
    }

    LOG(info) << "Configured appsink for cloud upload session: " << session_id << " -> " << remote_path << endl;
    return true;
}

StorageResult UnifiedCloudStorageWriter::finalizeSession(const std::string& session_id, const std::string& stream_id)
{   
    StorageResult result;
    result.success = false;

    // Thread-safe access to session remote paths
    std::string remote_path;
    {
        std::lock_guard<std::mutex> session_lock(m_session_mutex);
        
        // Get the remote path for this session
        auto path_it = m_session_remote_paths.find(session_id);
        if (path_it == m_session_remote_paths.end())
        {
            result.message = "No remote path found for session: " + session_id;
            LOG(error) << result.message << endl;
            return result;
        }

        remote_path = path_it->second;
    }

    // For cloud storage, ensure all buffered data is uploaded and buffer is cleared
    std::shared_ptr<StorageWriter> cloud_writer;
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        cloud_writer = m_cloud_writer;
    }
    if (cloud_writer)
    {
        // Complete the recording session using the cloud writer
        StorageResult cloud_result = cloud_writer->completeSession(session_id, stream_id);
        if (cloud_result.success)
        {
            result = cloud_result;
            result.storage_path = remote_path;
            // Set object_id to remote_path since that's the object ID for cloud storage
            result.object_id = remote_path;
            LOG(info) << "Cloud recording completed successfully: " << remote_path << " (" << result.bytes_written
                      << " bytes, object_id: " << result.object_id << ")" << endl;
            
            // Reset session flags to prevent further writes to the completed session
            {
                std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
                m_session_active = false;
                m_current_session_id.clear();
                m_current_remote_path.clear();
            }
        }
        else
        {
            result.message = "Failed to complete cloud recording session: " + cloud_result.message;
            LOG(error) << result.message << endl;
        }
    }
    else
    {
        result.message = "No cloud writer available";
        LOG(error) << result.message << endl;
    }

    // Clean up session tracking
    cleanupSessionTracking(session_id);

    return result;
}

void UnifiedCloudStorageWriter::cleanupSessionTracking(const std::string& session_id)
{
    std::lock_guard<std::mutex> session_lock(m_session_mutex);
    
    // Remove from active sessions
    m_active_sessions.erase(session_id);
    m_session_remote_paths.erase(session_id);
    
    LOG(info) << "Cleaned up session tracking for: " << session_id << endl;
}

bool UnifiedCloudStorageWriter::cleanupSession(const std::string& session_id)
{
    // Check if session has already been finalized (removed from remote paths) with proper mutex protection
    std::string stream_id;
    bool session_already_finalized = false;
    
    {
        std::lock_guard<std::mutex> session_lock(m_session_mutex);
        auto path_it = m_session_remote_paths.find(session_id);
        session_already_finalized = (path_it == m_session_remote_paths.end());

        if (!session_already_finalized)
        {
            // Get stream_id from active sessions while we have the lock
            auto session_it = m_active_sessions.find(session_id);
            if (session_it != m_active_sessions.end())
            {
                stream_id = session_it->second;
            }
        }
    }

    if (session_already_finalized)
    {
        LOG(info) << "Session already finalized, skipping cleanup for: " << session_id << endl;
        return true;
    }

    // Cancel the recording session using the cloud writer
    std::shared_ptr<StorageWriter> cloud_writer;
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        cloud_writer = m_cloud_writer;
    }
    if (cloud_writer)
    {
        // Get stream_id from active sessions (we need to track this)
        auto session_it = m_active_sessions.find(session_id);
        if (session_it != m_active_sessions.end())
        {
            std::string stream_id = session_it->second;
            cloud_writer->cancelSession(session_id, stream_id);
        }
    }

    // Remove from active sessions
    m_active_sessions.erase(session_id);
    
    // Remove from remote paths
    m_session_remote_paths.erase(session_id);

    LOG(info) << "Cleaned up cloud storage session: " << session_id << endl;
    return true;
}

// Override startWrite for optimized pipeline reuse
std::string UnifiedCloudStorageWriter::startWrite(const std::string& remote_path, const std::string& stream_id,
                                                  size_t estimated_size)
{
    LOG(info) << "Starting cloud recording session for stream: " << stream_id << " path: " << remote_path << endl;

    // Handle case where there's an active session - complete it first
    if (m_session_active.load())
    {
        LOG(info) << "Active session found, completing current session before starting new one" << endl;
        
        // Store the original stream ID for the current session before updating m_streamId
        std::string original_stream_id = m_streamId;
        
        // Complete the current session first using the original stream ID
        if (!m_current_session_id.empty())
        {
            StorageResult result = stopWrite(m_current_session_id, original_stream_id);
            if (!result.success)
            {
                LOG(warning) << "Failed to complete previous session: " << result.message << endl;
                // Continue anyway, but log the warning
            }
        }
        
        // Reset session state
        {
            std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
            m_session_active = false;
            m_current_session_id.clear();
            m_current_remote_path.clear();
        }
        
        LOG(info) << "Previous session completed, ready to start new session" << endl;
    }

    // Update stream ID after previous session is completed
    m_streamId = stream_id;

    // Initialize storage if not already done
    std::shared_ptr<StorageWriter> cloud_writer;
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        if (!m_cloud_writer)
        {
            if (!initializeStorage())
            {
                setLastError("Failed to initialize storage");
                LOG(error) << "Failed to initialize storage for stream: " << stream_id << endl;
                return "";
            }
        }
        else
        {
            LOG(info) << "Reusing existing cloud writer for stream: " << stream_id << endl;
        }
        cloud_writer = m_cloud_writer;
    }

    // Generate session ID early so it's available for pipeline reuse
    std::string session_id;
    if (cloud_writer)
    {
        session_id = cloud_writer->startSession(remote_path, stream_id, estimated_size);
        if (session_id.empty())
        {
            setLastError("Failed to start cloud recording session: " + cloud_writer->getLastError());
            LOG(error) << "Failed to start cloud recording session for stream: " << stream_id << endl;
            return "";
        }
        
        // Set session state (protected by cloud_writer_mutex)
        {
            std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
            m_current_session_id = session_id;
            m_current_remote_path = remote_path;
            m_session_active = true;
        }
        
        // Update session tracking (protected by session_mutex)
        {
            std::lock_guard<std::mutex> session_lock(m_session_mutex);
            m_active_sessions[session_id] = stream_id;
            m_session_remote_paths[session_id] = remote_path;
        }
    }

    // Now acquire pipeline mutex for pipeline operations
    std::lock_guard<std::mutex> lock(m_pipeline_mutex);

    // Create pipeline only if not already created
    if (!m_pipeline_ready.load())
    {
        if (!createPipelineInternal(m_videoCodec, m_audioSupported, stream_id))
        {
            setLastError("Failed to create pipeline");
            LOG(error) << "Failed to create pipeline for stream: " << stream_id << endl;
            return "";
        }
        m_pipeline_ready = true;
        LOG(info) << "Created new pipeline for stream: " << stream_id << endl;
    }
    else
    {
        // Check pipeline health before reuse
        GstState current_state, pending_state;
        GstClockTime timeout = 2 * GST_SECOND;
        GstStateChangeReturn ret = gst_element_get_state(m_pipeline, &current_state, &pending_state, timeout);

        if (ret == GST_STATE_CHANGE_FAILURE)
        {
            LOG(warning) << "Pipeline state check failed, recreating pipeline for stream: " << stream_id << endl;
            destroyPipelineInternal();
            m_pipeline_ready = false;

            if (!createPipelineInternal(m_videoCodec, m_audioSupported, stream_id))
            {
                setLastError("Failed to recreate pipeline");
                LOG(error) << "Failed to recreate pipeline for stream: " << stream_id << endl;
                return "";
            }
            m_pipeline_ready = true;
            LOG(info) << "Recreated pipeline for stream: " << stream_id << endl;
        }
        else
        {
            // Reuse existing pipeline for new file
            if (!reusePipelineForNewFile(remote_path))
            {
                setLastError("Failed to reuse pipeline for new file");
                LOG(error) << "Failed to reuse pipeline for new file: " << remote_path << endl;
                return "";
            }
            LOG(info) << "Reused existing pipeline for stream: " << stream_id << endl;
        }
    }

    // Return the session ID that was created earlier
    if (cloud_writer)
    {
        LOG(info) << "Cloud recording session started: " << session_id << " for stream: " << stream_id << endl;
        return session_id;
    }
    else
    {
        setLastError("No cloud writer available");
        LOG(error) << "No cloud writer available for stream: " << stream_id << endl;
        return "";
    }
}

// Cloud storage appsink callbacks
GstFlowReturn UnifiedCloudStorageWriter::onNewSampleCloud(GstAppSink* appsink, gpointer user_data)
{
    UnifiedCloudStorageWriter* writer = static_cast<UnifiedCloudStorageWriter*>(user_data);

    GstSample* sample = gst_app_sink_pull_sample(appsink);
    if (!sample)
    {
        LOG(error) << "Failed to pull sample from appsink" << endl;
        return GST_FLOW_ERROR;
    }

    GstBuffer* buffer = gst_sample_get_buffer(sample);
    if (!buffer)
    {
        LOG(error) << "Failed to get buffer from sample" << endl;
        gst_sample_unref(sample);
        return GST_FLOW_ERROR;
    }

    // Extract PTS and media type information
    int64_t pts = 0;
    std::string media_type = "video"; // Default to video
    
    // Get PTS from buffer
    GstClockTime buffer_pts = GST_BUFFER_PTS(buffer);
    if (GST_CLOCK_TIME_IS_VALID(buffer_pts))
    {
        pts = static_cast<int64_t>(buffer_pts / GST_MSECOND); // Convert to milliseconds
    }
    
    // Get media type from sample caps
    GstCaps* caps = gst_sample_get_caps(sample);
    if (caps)
    {
        GstStructure* structure = gst_caps_get_structure(caps, 0);
        if (structure)
        {
            const gchar* media_type_str = gst_structure_get_string(structure, "media");
            if (media_type_str)
            {
                media_type = std::string(media_type_str);
            }
            else
            {
                // Try to infer media type from mimetype
                const gchar* mimetype = gst_structure_get_string(structure, "mimetype");
                if (mimetype)
                {
                    if (g_str_has_prefix(mimetype, "audio/"))
                    {
                        media_type = "audio";
                    }
                    else if (g_str_has_prefix(mimetype, "video/"))
                    {
                        media_type = "video";
                    }
                }
            }
        }
    }

    GstMapInfo map;
    if (gst_buffer_map(buffer, &map, GST_MAP_READ))
    {
        // Write data to cloud storage using the cloud writer (with buffering handled internally)
        std::shared_ptr<StorageWriter> cloud_writer;
        std::string current_session_id;
        {
            std::lock_guard<std::mutex> lock(writer->m_cloud_writer_mutex);
            cloud_writer = writer->m_cloud_writer;
            current_session_id = writer->m_current_session_id; // Copy session ID while holding lock
        }
        if (!current_session_id.empty() && cloud_writer)
        {
            bool success = cloud_writer->writeData(current_session_id, map.data, map.size, pts, media_type);

            if (success)
            {
                writer->m_total_bytes_written.fetch_add(map.size, std::memory_order_relaxed);
                writer->m_frames_written.fetch_add(1, std::memory_order_relaxed);

                LOG(verbose) << "Wrote " << map.size
                             << " bytes to cloud storage for session: " << current_session_id
                             << " (pts: " << pts << "ms, media_type: " << media_type << ")" << endl;
            }
            else
            {
                LOG(error) << "Failed to write data to cloud storage: " << cloud_writer->getLastError()
                           << endl;
            }
        }

        gst_buffer_unmap(buffer, &map);
    }
    else
    {
        LOG(error) << "Failed to map buffer from sample" << endl;
    }

    gst_sample_unref(sample);
    return GST_FLOW_OK;
}

void UnifiedCloudStorageWriter::onEOSCloud(GstAppSink* appsink, gpointer user_data)
{
    UnifiedCloudStorageWriter* writer = static_cast<UnifiedCloudStorageWriter*>(user_data);

    LOG(info) << "Received EOS from cloud storage appsink" << endl;

    // Set EOS received flag
    {
        std::lock_guard<std::mutex> lock(writer->m_monitor_eos_mutex);
        writer->m_eosReceived = true;
        writer->m_monitor_eos_cv.notify_all();
    }
}

// Implement missing pure virtual functions for UnifiedCloudStorageWriter
StorageResult UnifiedCloudStorageWriter::uploadFile(const std::string& local_path, const std::string& remote_path)
{
    StorageResult result;
    result.success = false;

    std::shared_ptr<StorageWriter> cloud_writer;
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        cloud_writer = m_cloud_writer;
    }
    if (cloud_writer)
    {
        StorageResult cloud_result = cloud_writer->uploadFile(local_path, remote_path);
        result = cloud_result;
    }
    else
    {
        result.message = "No cloud writer available";
        LOG(error) << result.message << endl;
    }

    return result;
}

bool UnifiedCloudStorageWriter::deleteFile(const std::string& remote_path)
{
    std::shared_ptr<StorageWriter> cloud_writer;
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        cloud_writer = m_cloud_writer;
    }
    if (cloud_writer)
    {
        return cloud_writer->deleteFile(remote_path);
    }

    LOG(error) << "No cloud writer available for delete operation" << endl;
    return false;
}

bool UnifiedCloudStorageWriter::fileExists(const std::string& remote_path) const
{
    std::shared_ptr<StorageWriter> cloud_writer;
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        cloud_writer = m_cloud_writer;
    }
    if (cloud_writer)
    {
        return cloud_writer->fileExists(remote_path);
    }

    LOG(error) << "No cloud writer available for fileExists operation" << endl;
    return false;
}

size_t UnifiedCloudStorageWriter::getFileSize(const std::string& remote_path) const
{
    std::shared_ptr<StorageWriter> cloud_writer;
    {
        std::lock_guard<std::mutex> lock(m_cloud_writer_mutex);
        cloud_writer = m_cloud_writer;
    }
    if (cloud_writer)
    {
        return cloud_writer->getFileSize(remote_path);
    }

    LOG(error) << "No cloud writer available for getFileSize operation" << endl;
    return 0;
}
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

#include "unified_local_storage_writer.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <ctime>

using namespace std;
using namespace nv_vms;

// UnifiedLocalStorageWriter implementation
UnifiedLocalStorageWriter::UnifiedLocalStorageWriter() : UnifiedStorageWriter(StorageType::LOCAL) {}

UnifiedLocalStorageWriter::~UnifiedLocalStorageWriter()
{
    LOG(info) << "Destroying unified local storage writer for stream: " << (m_streamId.empty() ? "unknown" : m_streamId)
              << endl;

    // All pipeline cleanup (setting pipeline to NULL, unreffing elements,
    // quitting the GMainLoop, joining the GMainLoop thread, unreffing
    // GMainContext/GSource, and resetting session state) is handled by the
    // base class ~UnifiedStorageWriter() via destroyPipeline().
    //
    // Do NOT manually unref m_pipeline here. Setting m_pipeline to nullptr
    // before the base destructor runs causes destroyPipelineInternal() to
    // exit early, skipping the GMainLoop thread join and GSource/GMainContext
    // cleanup — permanently leaking a thread and its file descriptors on
    // every writer destruction.
}

bool UnifiedLocalStorageWriter::initializeStorage()
{
    // Local storage doesn't need special initialization
    return true;
}

GstElement* UnifiedLocalStorageWriter::createSinkElement()
{
    // Create filesink for local storage
    GstElement* filesink = gst_element_factory_make("filesink", nullptr);
    if (!filesink)
    {
        LOG(error) << "Failed to create filesink element" << endl;
        return nullptr;
    }

    LOG(info) << "Created filesink element for local storage for stream ID: "
              << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
    return filesink;
}

bool UnifiedLocalStorageWriter::configureSinkElement(GstElement* sink, const std::string& remote_path,
                                                     const std::string& session_id)
{
    if (bool isSinkValid = sink ? GST_IS_ELEMENT(sink) : false; !isSinkValid)
    {
        LOG(error) << "Invalid sink element for local storage" << endl;
        return false;
    }

    // Ensure the directory exists before configuring filesink
    std::filesystem::path file_path(remote_path);
    std::filesystem::path dir_path = file_path.parent_path();

    if (!dir_path.empty() && !std::filesystem::exists(dir_path))
    {
        if (!std::filesystem::create_directories(dir_path))
        {
            setLastError("Failed to create directory: " + dir_path.string());
            LOG(error) << "Failed to create directory: " << dir_path << endl;
            return false;
        }
    }

    // Check if the directory is writable
    if (!dir_path.empty())
    {
        std::filesystem::path test_file = dir_path / ".test_write";
        std::ofstream test_stream(test_file);
        if (!test_stream.is_open())
        {
            setLastError("Directory not writable: " + dir_path.string());
            LOG(error) << "Directory not writable: " << dir_path << endl;
            return false;
        }
        test_stream.close();
        std::filesystem::remove(test_file);
    }

    if (GET_CONFIG().recorder_low_latency)
    {
        g_object_set(G_OBJECT(sink), "sync", FALSE,
                                     "buffer-mode", 2,  // Unbuffered mode
                                     nullptr);
    }

    // Configure filesink with the local file path
    g_object_set(G_OBJECT(sink), "location", remote_path.c_str(), nullptr);

    // Verify the property was set correctly
    gchar* location = nullptr;
    g_object_get(G_OBJECT(sink), "location", &location, nullptr);
    if (location)
    {
        g_free(location);
    }
    else
    {
        LOG(error) << "Failed to set filesink location" << endl;
        return false;
    }

    LOG(info) << "Configured filesink for local path: " << remote_path
              << " for stream ID: " << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
    return true;
}

StorageResult UnifiedLocalStorageWriter::finalizeSession(const std::string& session_id, const std::string& stream_id)
{
    StorageResult result;
    result.success = false;

    // For local storage, the file is already written by filesink
    // We just need to verify the file exists and get its size
    if (!m_current_remote_path.empty())
    {
        std::ifstream file(m_current_remote_path, std::ios::binary | std::ios::ate);
        if (file.is_open())
        {
            result.success = true;
            result.storage_path = m_current_remote_path;
            result.bytes_written = file.tellg();
            result.message = "Local file written successfully";

            LOG(info) << "Local recording completed: " << result.storage_path << " (" << result.bytes_written
                      << " bytes)" << endl;
        }
        else
        {
            result.message = "Failed to verify local file: " + m_current_remote_path;
            LOG(error) << result.message << endl;
        }
    }
    else
    {
        result.message = "No remote path configured for session: " + session_id;
        LOG(error) << result.message << endl;
    }

    return result;
}

bool UnifiedLocalStorageWriter::cleanupSession(const std::string& session_id)
{
    // For local storage, no special cleanup needed
    // The filesink handles file closing automatically
    return true;
}

// Override startWrite for optimized pipeline reuse
std::string UnifiedLocalStorageWriter::startWrite(const std::string& remote_path, const std::string& stream_id,
                                                  size_t estimated_size)
{
    std::lock_guard<std::mutex> lock(m_pipeline_mutex);
    m_streamId = stream_id; // Store the stream ID

    LOG(info) << "Starting local recording session for stream: " << stream_id << " path: " << remote_path << endl;

    // If there's an active session, reuse the pipeline for new file instead of stopping
    if (m_session_active.load())
    {
        LOG(info) << "Active session found, reusing pipeline for new file: " << remote_path << endl;

        // Generate new session ID for the new file
        std::string session_id = generateSessionId(stream_id);

        // Update session state
        m_current_session_id = session_id;
        m_current_remote_path = remote_path;
        
        if (!reusePipelineForNewFile(remote_path))
        {
            setLastError("Failed to reuse pipeline for new file");
            LOG(error) << "Failed to reuse pipeline for new file: " << remote_path << endl;
            return "";
        }

        LOG(info) << "Local recording session continued with new file: " << session_id << " for stream: " << stream_id
                  << endl;
        return session_id;
    }

    // Initialize storage if not already done
    if (!initializeStorage())
    {
        setLastError("Failed to initialize storage");
        LOG(error) << "Failed to initialize storage for stream: " << stream_id << endl;
        return "";
    }

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

    // Generate session ID using base class method
    std::string session_id = generateSessionId(stream_id);

    m_session_active = true;
    m_current_session_id = session_id;
    m_current_remote_path = remote_path;

    LOG(info) << "Local recording session started: " << session_id << " for stream: " << stream_id << endl;
    return session_id;
}

// Implement missing pure virtual functions for UnifiedLocalStorageWriter
StorageResult UnifiedLocalStorageWriter::uploadFile(const std::string& local_path, const std::string& remote_path)
{
    // For local storage, this is a no-op since files are already local
    StorageResult result;
    result.success = true;
    result.message = "File already local: " + local_path;
    result.storage_path = local_path;
    return result;
}

bool UnifiedLocalStorageWriter::deleteFile(const std::string& remote_path)
{
    try
    {
        return std::filesystem::remove(remote_path);
    }
    catch (const std::exception& e)
    {
        setLastError("Failed to delete file: " + std::string(e.what()));
        return false;
    }
}

bool UnifiedLocalStorageWriter::fileExists(const std::string& remote_path) const
{
    try
    {
        return std::filesystem::exists(remote_path);
    }
    catch (const std::exception& e)
    {
        return false;
    }
}

size_t UnifiedLocalStorageWriter::getFileSize(const std::string& remote_path) const
{
    try
    {
        if (std::filesystem::exists(remote_path))
        {
            return std::filesystem::file_size(remote_path);
        }
        return 0;
    }
    catch (const std::exception& e)
    {
        return 0;
    }
}
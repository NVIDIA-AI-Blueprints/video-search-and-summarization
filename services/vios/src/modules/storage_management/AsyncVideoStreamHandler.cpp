/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "AsyncVideoStreamHandler.h"
#include "utils.h"
#include "fs_utils.h"
#include <chrono>
#include <thread>
#include <filesystem>
#include <array>

AsyncVideoStreamHandler::AsyncVideoStreamHandler(const std::string& filePath, const std::string& taskId)
{
    // Input validation for security - safe error handling without throws
    if (filePath.empty() || taskId.empty())
    {
        LOG(error) << "[STREAM] Invalid file path or task ID" << endl;
        return; // isValidHandler remains false
    }
    
    // Path traversal protection using filesystem canonicalization
    try
    {
        // Use weakly_canonical to handle files that may not exist yet
        std::filesystem::path canonicalPath = std::filesystem::weakly_canonical(filePath);
        std::string pathStr = canonicalPath.string();
        
        // Additional checks: no ".." components should remain in canonical path
        if (pathStr.find("..") != std::string::npos)
        {
            LOG(error) << "[STREAM] Path traversal detected in file path" << endl;
            return;
        }
    }
    catch (const std::filesystem::filesystem_error& e)
    {
        LOG(error) << "[STREAM] Invalid file path: " << e.what() << endl;
        return;
    }
    
    // Sanitize task ID to prevent injection
    std::string sanitizedTaskId = sanitizePrefix(taskId);
    if (sanitizedTaskId.empty())
    {
        LOG(error) << "[STREAM] Invalid characters in task ID: " << taskId << endl;
        return; // isValidHandler remains false
    }
    
    this->filePath = filePath;
    this->taskId = sanitizedTaskId;
    this->isValidHandler = true; // Mark as valid only after all checks pass
    
    LOG(info) << "[STREAM] Created handler for task: " << this->taskId << endl;
}

AsyncVideoStreamHandler::~AsyncVideoStreamHandler() noexcept
{
    closeFileIfOpen();
}

int AsyncVideoStreamHandler::handler(struct mg_connection* conn)
{
    // Early validation - check if handler was created successfully
    if (!isValid())
    {
        LOG(error) << "[STREAM] Handler not valid, cannot stream" << endl;
        mg_send_http_error(conn, 400, "Invalid handler configuration");
        return 400;
    }
    
    auto streamStartTime = std::chrono::steady_clock::now();
    LOG(info) << "[STREAM] Starting stream for task: " << taskId << endl;

    if (!openFile())
    {
        LOG(error) << "[STREAM] Failed to open file: " << filePath << endl;
        mg_send_http_error(conn, 404, "File not found or cannot open");
        return 404;
    }

    // Send headers with error checking
    if (!sendHeaders(conn))
    {
        LOG(error) << "[STREAM] Failed to send headers for task: " << taskId << endl;
        closeFileIfOpen();
        return HTTP_CLIENT_CLOSED_REQUEST;
    }

    std::array<char, CHUNK_SIZE> buffer;
    size_t totalBytesSent = 0;
    size_t chunkCount = 0;

    while (true)
    {
        // Attempt to read a full chunk
        file.read(buffer.data(), CHUNK_SIZE);
        std::streamsize bytesRead = file.gcount();

        if (bytesRead == 0)
        {
            // No data available - clear error flags and check task status
            if (!file)
            {
                file.clear();
            }

            VideoTaskStatus taskStatus = getTaskStatus();
            
            if (taskStatus == VideoTaskStatus::IN_PROGRESS || taskStatus == VideoTaskStatus::PENDING)
            {
                // Task still running - wait for more data
                std::this_thread::sleep_for(WAIT_FOR_DATA_TIME);
                continue;
            }
            
            // Task completed or failed - do final drain and exit
            LOG(info) << "[STREAM] Task finished (status: " << static_cast<int>(taskStatus) 
                     << "), performing final drain for task: " << taskId << endl;
            
            while (file.read(buffer.data(), CHUNK_SIZE) && file.gcount() > 0)
            {
                std::streamsize finalBytes = file.gcount();
                if (!sendHttpChunk(conn, buffer.data(), static_cast<size_t>(finalBytes)))
                {
                    LOG(warning) << "[STREAM] Client disconnected during final drain" << endl;
                    closeFileIfOpen();
                    return HTTP_CLIENT_CLOSED_REQUEST;
                }
                totalBytesSent += finalBytes;
                chunkCount++;
            }
            
            break; // Exit streaming
        }

        // Data available - send it immediately
        if (!sendHttpChunk(conn, buffer.data(), static_cast<size_t>(bytesRead)))
        {
            LOG(error) << "[STREAM] Client disconnected or network error" << endl;
            closeFileIfOpen();
            return HTTP_CLIENT_CLOSED_REQUEST;
        }
        totalBytesSent += bytesRead;
        chunkCount++;
    }

    auto streamEndTime = std::chrono::steady_clock::now();
    auto streamDuration = std::chrono::duration_cast<std::chrono::seconds>(streamEndTime - streamStartTime).count();
    
    LOG(info) << "[STREAM] Completed - task: " << taskId
             << ", bytes: " << totalBytesSent << ", chunks: " << chunkCount
             << ", duration: " << streamDuration << "s" << endl;
    // Send final chunk terminator
    if (mg_printf(conn, "0\r\n\r\n") <= 0)
    {
        LOG(warning) << "[STREAM] Failed to send terminator for task: " << taskId << endl;
        closeFileIfOpen();
        return HTTP_CLIENT_CLOSED_REQUEST;
    }
    
    // Ensure file is properly closed on successful completion
    closeFileIfOpen();
    return 200;
}

bool AsyncVideoStreamHandler::openFile()
{
    try
    {
        // Open in read-only mode with shared read access
        file.open(filePath, std::ios::in | std::ios::binary);
        if (!file.is_open())
        {
            LOG(error) << "[STREAM] Failed to open file: " << filePath << endl;
            return false;
        }
        LOG(info) << "[STREAM] Opened file successfully" << endl;
        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "[STREAM] Exception opening file: " << e.what() << endl;
        return false;
    }
}

bool AsyncVideoStreamHandler::sendHeaders(struct mg_connection* conn)
{
    // Use existing utility functions for content type detection
    const string fileExtension = getFileExtension(filePath);
    const string contentType = getMediaContentType(fileExtension);
    
    // Send headers with error checking
    int result = mg_printf(conn, "HTTP/1.1 200 OK\r\n"
                                "Content-Type: %s\r\n"
                                "Transfer-Encoding: chunked\r\n"
                                "Cache-Control: no-cache\r\n"
                                "\r\n", contentType.c_str());
    
    return result > 0;
}

VideoTaskStatus AsyncVideoStreamHandler::getTaskStatus()
{
    auto now = std::chrono::steady_clock::now();
    
    // Cache task status for performance (expensive operation)
    if ((now - lastStatusCheck) > TASK_STATUS_CACHE_DURATION) {
        VideoGeneratorTaskManager* taskManager = VideoGeneratorTaskManager::getInstance();
        if (taskManager != nullptr) {
            cachedTaskStatus = taskManager->getTaskStatus(taskId);
        } else {
            LOG(error) << "[STREAM] TaskManager not available" << endl;
            cachedTaskStatus = VideoTaskStatus::FAILED;
        }
        lastStatusCheck = now;
    }
    
    return cachedTaskStatus;
}

bool AsyncVideoStreamHandler::sendHttpChunk(struct mg_connection* conn, const void* data, size_t size)
{
    // Send chunk size in hex
    if (mg_printf(conn, "%zx\r\n", size) <= 0)
    {
        return false; // Client disconnected or network error
    }
    
    // Send chunk data
    if (mg_write(conn, data, size) != static_cast<int>(size))
    {
        return false; // Client disconnected or network error
    }
    
    // Send chunk terminator
    if (mg_write(conn, "\r\n", 2) != 2)
    {
        return false; // Client disconnected or network error
    }
    
    return true;
}

void AsyncVideoStreamHandler::closeFileIfOpen() noexcept
{
    try
    {
        if (file.is_open())
        {
            file.close();
        }
    }
    catch (...)
    {
        // File close should not throw in cleanup
    }
}


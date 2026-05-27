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

#include "VideoGeneratorTaskManager.h"
#include "error_code.h"
#include "logger.h"
#include "storage_management_utils.h"
#include "utils.h"
#include "config.h"
#include "fs_utils.h"
#include "vst_common.h"
#include "vstmodule.h"
#include <cctype>
#include <chrono>

VideoGeneratorTaskManager::VideoGeneratorTaskManager()
{
}

VideoGeneratorTaskManager::~VideoGeneratorTaskManager() noexcept
{
    waitForAllTasksAndCleanup();
}



// Creates temp directory if needed and returns the complete file path for video output
string VideoGeneratorTaskManager::ensureTempDirAndGenerateFilePath(const string& taskId, const string& container)
{
    const auto webRoot = VmsConfigManager::getInstance()->getWebRootPath();
    const auto tempFilesDir = webRoot + TEMP_STORAGE_DIR;
    
    // container extension
    const auto extension = container.empty() ? DEFAULT_VIDEO_CONTAINER : 
                           (container[0] == '.' ? container : "." + container);
    
    if (!isDirExist(tempFilesDir))
    {
        if (!createDir(tempFilesDir))
        {
            LOG(error) << "[ASYNC_MEDIA] Failed to create temp files directory: " << tempFilesDir << endl;
            return EMPTY_STRING; // Fallback to webRoot
        }
    }
    
    return tempFilesDir + "/" + taskId + extension;
}

string VideoGeneratorTaskManager::addTask(const VideoGenerationParam& params)
{
    if (m_isShuttingDown)
    {
        LOG(warning) << "[ASYNC_MEDIA] Cannot add new task during shutdown" << endl;
        return "";
    }
    
    if (params.streamId.empty() || params.startTime.empty() || params.endTime.empty())
    {
        LOG(error) << "[ASYNC_MEDIA] Invalid parameters for video generation task" << endl;
        return "";
    }
    
    // Generate unique task ID with sensor prefix
    string sensorPrefix;
    if (!params.deviceName.empty() && params.deviceName != "disconnected_device") 
    {
        const std::string candidate = sanitizePrefix(params.deviceName);
        if (!candidate.empty()) {
            sensorPrefix = candidate + "_";
        }
    }
    auto makeTaskId = [&]() -> string {
        return (sensorPrefix.empty() ? VIDEO_TASK_PREFIX : "") + sensorPrefix + 
                getUniqueIdFromUTCTime(params.startTime, "");
    };
    string taskId = makeTaskId();
    
    VideoGenerationParam taskParams = params;
    taskParams.taskId = taskId;
    taskParams.outputFilePath = VideoGeneratorTaskManager::ensureTempDirAndGenerateFilePath(taskId, params.container);
    if (taskParams.outputFilePath.empty())
    {
        LOG(error) << "[ASYNC_MEDIA] Failed to generate output file path" << endl;
        return EMPTY_STRING;
    }
    auto task = std::make_unique<VideoGenerationTask>();
    task->params = taskParams;
    
    // Start async video generation with safe status update
    auto regularTask = async::spawn([taskParams]() -> VmsErrorCode {
        VmsErrorCode result = VideoGeneratorTaskManager::executeVideoGeneration(taskParams);
        LOG(info) << "[ASYNC_MEDIA] Video generation completed for task " << taskParams.taskId 
                  << " with result: " << static_cast<int>(result) << endl;
        return result;
    }).then([this, taskId](VmsErrorCode result) -> VmsErrorCode {
        // Proactive cleanup: immediately remove task when async work completes
        {
            std::lock_guard<std::mutex> lock(m_tasksMutex);
            auto it = m_tasks.find(taskId);
            if (it != m_tasks.end())
            {
                VideoTaskStatus finalStatus = (result == VmsErrorCode::NoError) ? VideoTaskStatus::COMPLETED : VideoTaskStatus::FAILED;
                LOG(info) << "[ASYNC_MEDIA] Task " << taskId << " completed with result " << static_cast<int>(result) 
                          << ", status: " << static_cast<int>(finalStatus) << ", cleaning up" << endl;
                m_tasks.erase(it);
                LOG(info) << "[ASYNC_MEDIA] Task " << taskId << " cleaned up. Remaining: " << m_tasks.size() << endl;
            }
        }
        return result;
    });
    
    task->asyncTask = regularTask.share();
    task->status = VideoTaskStatus::IN_PROGRESS;
    
    std::lock_guard<std::mutex> lock(m_tasksMutex);
    m_tasks[taskId] = std::move(task);
    
    LOG(info) << "[ASYNC_MEDIA] Started video generation task: " << taskId 
              << " for stream: " << params.streamId << endl;
    
    return taskId;
}

VmsErrorCode VideoGeneratorTaskManager::waitForTask(const string& taskId, string& outputFilePath)
{
    LOG(info) << "[ASYNC_MEDIA] Waiting for task: " << taskId << endl;
    
    // Parse task ID
    string container = DEFAULT_VIDEO_CONTAINER;
    string actualTaskId = taskId;
    
    size_t dotPos = taskId.find_last_of('.');
    if (dotPos != string::npos)
    {
        container = taskId.substr(dotPos);
        actualTaskId = taskId.substr(0, dotPos);
    }
    
    string potentialFilePath = VideoGeneratorTaskManager::ensureTempDirAndGenerateFilePath(actualTaskId, container);
    if (potentialFilePath.empty())
    {
        LOG(error) << "[ASYNC_MEDIA] Failed to generate potential file path" << endl;
        return VmsErrorCode::VMSInternalError;
    }
    
    // Check if task is in progress first - don't return early just because file exists
    // (file might exist but still be written to progressively)
    bool taskInProgress = false;
    {
        std::lock_guard<std::mutex> lock(m_tasksMutex);
        auto it = m_tasks.find(actualTaskId);
        taskInProgress = (it != m_tasks.end());
    }
    
    // If task is NOT in progress and file exists, it's completed
    if (!taskInProgress && isFileExist(potentialFilePath))
    {
        LOG(info) << "[ASYNC_MEDIA] Task not in active list and file exists, assuming completed: " << potentialFilePath << endl;
        outputFilePath = potentialFilePath;
        return VmsErrorCode::NoError;
    }
    
    // If task is NOT in progress and file doesn't exist, it failed or never existed
    if (!taskInProgress)
    {
        LOG(error) << "[ASYNC_MEDIA] Task not found and file doesn't exist: " << actualTaskId << endl;
        return VmsErrorCode::InvalidParameterError;
    }
    
    // Task is in progress - need to wait for completion
    
    // Get the async task
    async::shared_task<VmsErrorCode> sharedTask;
    string expectedOutputPath;
    {
        std::lock_guard<std::mutex> lock(m_tasksMutex);
        auto it = m_tasks.find(actualTaskId);
        if (it == m_tasks.end())
        {
            LOG(error) << "[ASYNC_MEDIA] Task not found during wait: " << actualTaskId << endl;
            return VmsErrorCode::InvalidParameterError;
        }
        sharedTask = it->second->asyncTask;
        expectedOutputPath = it->second->params.outputFilePath;
    }
    
    try
    {
        if (!sharedTask.valid())
        {
            LOG(error) << "[ASYNC_MEDIA] Invalid task for: " << actualTaskId << endl;
            {
                std::lock_guard<std::mutex> lock(m_tasksMutex);
                auto it = m_tasks.find(actualTaskId);
                if (it != m_tasks.end())
                {
                    m_tasks.erase(it);
                }
            }
            return VmsErrorCode::VMSInternalError;
        }
        
        VmsErrorCode result = sharedTask.get();
        
        // Clean up task on completion
        {
            std::lock_guard<std::mutex> lock(m_tasksMutex);
            auto it = m_tasks.find(actualTaskId);
            if (it != m_tasks.end() && it->second->status == VideoTaskStatus::IN_PROGRESS)
            {
                VideoTaskStatus finalStatus = (result == VmsErrorCode::NoError) ? VideoTaskStatus::COMPLETED : VideoTaskStatus::FAILED;
                it->second->status = finalStatus;
                it->second->completedTime = std::chrono::steady_clock::now();
                LOG(info) << "[ASYNC_MEDIA] Task " << actualTaskId << " completed with status: " << static_cast<int>(finalStatus) << endl;
                m_tasks.erase(it);
                LOG(info) << "[ASYNC_MEDIA] Task " << actualTaskId << " removed. Remaining: " << m_tasks.size() << endl;
            }
            else if (it != m_tasks.end())
            {
                LOG(warning) << "[ASYNC_MEDIA] Task " << actualTaskId << " has unexpected status: " << static_cast<int>(it->second->status) << endl;
            }
            else
            {
                LOG(warning) << "[ASYNC_MEDIA] Task " << actualTaskId << " not found during cleanup" << endl;
            }
        }
        
        if (result == VmsErrorCode::NoError)
        {
            outputFilePath = expectedOutputPath;
            LOG(info) << "[ASYNC_MEDIA] Video generation completed: " << actualTaskId << endl;
        }
        else
        {
            LOG(error) << "[ASYNC_MEDIA] Video generation failed: " << actualTaskId << endl;
        }
        
        return result;
    }
    catch (const std::exception& e)
    {
        {
            std::lock_guard<std::mutex> lock(m_tasksMutex);
            auto it = m_tasks.find(actualTaskId);
            if (it != m_tasks.end())
            {
                m_tasks.erase(it);
            }
        }
        LOG(error) << "[ASYNC_MEDIA] Exception in video generation task " << actualTaskId << ": " << e.what() << endl;
        return VmsErrorCode::VMSInternalError;
    }
}

VideoTaskStatus VideoGeneratorTaskManager::getTaskStatus(const string& taskId) noexcept
{
    try
    {
        std::lock_guard<std::mutex> lock(m_tasksMutex);
        const auto it = m_tasks.find(taskId);
        if (it != m_tasks.end())
        {
            VideoTaskStatus currentStatus = it->second->status;
            
            LOG(info) << "[ASYNC_MEDIA] Task " << taskId << " status: " << static_cast<int>(currentStatus) << endl;
            return currentStatus;
        }
        
        // Task not found in active map - this could mean:
        // 1. Task was completed and cleaned up (race condition)
        // 2. Task actually failed
        // 3. Task never existed
        
        // Check if output file exists to distinguish between completed vs failed
        string expectedOutputPath = VideoGeneratorTaskManager::ensureTempDirAndGenerateFilePath(taskId, DEFAULT_VIDEO_CONTAINER);
        if (!expectedOutputPath.empty() && isFileExist(expectedOutputPath))
        {
            LOG(info) << "[ASYNC_MEDIA] Task " << taskId << " file exists, status: COMPLETED" << endl;
            return VideoTaskStatus::COMPLETED;
        }
        
        LOG(info) << "[ASYNC_MEDIA] Task " << taskId << " not found, status: FAILED" << endl;
        return VideoTaskStatus::FAILED;
    }
    catch (...)
    {
        LOG(error) << "[ASYNC_MEDIA] Exception in getTaskStatus for: " << taskId << endl;
        return VideoTaskStatus::FAILED;
    }
}

string VideoGeneratorTaskManager::getTaskError(const string& taskId) const noexcept
{
    try
    {
        std::lock_guard<std::mutex> lock(m_tasksMutex);
        const auto it = m_tasks.find(taskId);
        return (it != m_tasks.end()) ? it->second->errorMessage : "Task not found";
    }
    catch (...)
    {
        return "Exception during error retrieval";
    }
}

bool VideoGeneratorTaskManager::cleanupTask(const string& taskId) noexcept
{
    try
    {
        std::lock_guard<std::mutex> lock(m_tasksMutex);
        const auto it = m_tasks.find(taskId);
        if (it == m_tasks.end())
        {
            return false;
        }
        
        // Cannot cleanup active tasks to prevent race conditions
        LOG(warning) << "[ASYNC_MEDIA] Cannot cleanup active task: " << taskId << endl;
        return false;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "[ASYNC_MEDIA] Exception during task cleanup for " << taskId << ": " << e.what() << endl;
        return false;
    }
    catch (...)
    {
        LOG(error) << "[ASYNC_MEDIA] Unknown exception during task cleanup for " << taskId << endl;
        return false;
    }
}

void VideoGeneratorTaskManager::waitForAllTasksAndCleanup() noexcept
{
    try
    {
        m_isShuttingDown = true;
        
        std::vector<std::pair<string, async::shared_task<VmsErrorCode>>> runningTasks;
        
        {
            std::lock_guard<std::mutex> lock(m_tasksMutex);
            
            for (auto& [taskId, task] : m_tasks)
            {
                if (task && task->asyncTask.valid())
                {
                    if (!task->asyncTask.ready())
                    {
                        runningTasks.emplace_back(taskId, task->asyncTask);
                    }
                }
            }
            
            m_tasks.clear();
        }
        
        if (!runningTasks.empty())
        {
            LOG(info) << "[ASYNC_MEDIA] Waiting for " << runningTasks.size() << " running tasks to complete..." << endl;
            
            for (auto& [taskId, task] : runningTasks)
            {
                try
                {
                    LOG(info) << "[ASYNC_MEDIA] Waiting for task " << taskId << " to complete..." << endl;
                    task.get();
                    LOG(info) << "[ASYNC_MEDIA] Task " << taskId << " completed" << endl;
                }
                catch (const std::exception& e)
                {
                    LOG(warning) << "[ASYNC_MEDIA] Task " << taskId << " exception during shutdown: " << e.what() << endl;
                }
                catch (...)
                {
                    LOG(warning) << "[ASYNC_MEDIA] Task " << taskId << " unknown exception during shutdown" << endl;
                }
            }
        }
        
        LOG(info) << "[ASYNC_MEDIA] VideoGeneratorTaskManager shutdown complete" << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "[ASYNC_MEDIA] Exception during shutdown: " << e.what() << endl;
    }
    catch (...)
    {
        LOG(error) << "[ASYNC_MEDIA] Unknown exception during shutdown" << endl;
    }
}

VmsErrorCode VideoGeneratorTaskManager::executeVideoGeneration(const VideoGenerationParam& params)
{
    string video_codec;
    string outputFilePath = params.outputFilePath;
    
    OverlayBBoxParams overlayParamsCopy;
    OverlayBBoxParams* overlayPtr = nullptr;

    if (params.overlayParams.has_value()) 
    {
        overlayParamsCopy = params.overlayParams.value();
        overlayPtr = &overlayParamsCopy;
    }
    
    nv_vms::IMediaInterface* mediaInterface = ModuleLoader::getInstance()->getMediaInterface();
    VmsErrorCode result = makeVideoFile(
        params.startTime,
        params.endTime, 
        params.streamId,
        "",
        params.deviceName,
        outputFilePath,
        video_codec,
        "",
        params.sensorType,
        params.container,
        params.transcode,
        params.disableAudio,
        params.enableOverlay,
        overlayPtr,
        params.frameRate,
        mediaInterface,
        params.uselibav,
        params.isCloudStream
    );
    
    if (result != VmsErrorCode::NoError)
    {
        LOG(error) << "[ASYNC_MEDIA] makeVideoFile failed for task: " << params.taskId 
                   << " Error code: " << static_cast<int>(result) << endl;
    }
    
    return result;
} 
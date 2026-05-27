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

#pragma once

#include <string>
#include <unordered_map>
#include <memory>
#include <mutex>
#include <chrono>
#include <optional>
#include "libasync++/async++.h"
#include "error_code.h"
#include "elasticSearch.h"
#include <atomic>

enum class VideoTaskStatus {
    PENDING,
    IN_PROGRESS,
    COMPLETED,
    FAILED
};

struct VideoGenerationParam {
    string taskId;
    string streamId;
    string startTime;
    string endTime;
    string deviceName;
    string outputFilePath;
    string sensorType;
    string container;
    string disableAudio;
    string enableOverlay;
    string queryString;
    string fileName;
    string uselibav;
    string transcode;
    string frameRate;
    std::optional<OverlayBBoxParams> overlayParams;
    bool isCloudStream = false;
    VideoGenerationParam() = default;
};

struct VideoGenerationTask {
    VideoGenerationParam params;
    async::shared_task<VmsErrorCode> asyncTask;
    VideoTaskStatus status;
    string errorMessage;
    std::chrono::steady_clock::time_point createdTime;
    std::chrono::steady_clock::time_point completedTime;
    
    VideoGenerationTask() : status(VideoTaskStatus::PENDING) {
        createdTime = std::chrono::steady_clock::now();
    }
};

class VideoGeneratorTaskManager {
public:
    static inline const std::string VIDEO_TASK_PREFIX = "vid_";
    static inline const std::string DEFAULT_VIDEO_CONTAINER = ".mp4";
    
    static VideoGeneratorTaskManager* getInstance()
    {
        static VideoGeneratorTaskManager instance;
        return &instance;
    }
    
    string addTask(const VideoGenerationParam& params);
    VmsErrorCode waitForTask(const string& taskId, string& outputFilePath);
    VideoTaskStatus getTaskStatus(const string& taskId) noexcept;
    string getTaskError(const string& taskId) const noexcept;
    
    // Note: Active tasks cannot be cleaned up to prevent race conditions
    bool cleanupTask(const string& taskId) noexcept;
    
    void waitForAllTasksAndCleanup() noexcept;
    static string ensureTempDirAndGenerateFilePath(const string& taskId, const string& container);
    
private:
    VideoGeneratorTaskManager();
    ~VideoGeneratorTaskManager() noexcept;
    
    std::unordered_map<string, std::unique_ptr<VideoGenerationTask>> m_tasks;
    mutable std::mutex m_tasksMutex;
    std::atomic<bool> m_isShuttingDown{false};
    
    static VmsErrorCode executeVideoGeneration(const VideoGenerationParam& params);
}; 
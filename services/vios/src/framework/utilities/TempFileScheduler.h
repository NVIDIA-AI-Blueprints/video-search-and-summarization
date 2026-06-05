/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <mutex>
#include <unordered_map>
#include <atomic>
#include <memory>
#include <functional>
#include <cstdint>
#include <Scheduler.h>

class TempFileScheduler
{
public:
    using CleanupCallback = std::function<void(const std::string& taskId, const std::string& filePath)>;

    explicit TempFileScheduler(const std::string& fileType, CleanupCallback callback);
    ~TempFileScheduler();

    TempFileScheduler(const TempFileScheduler&) = delete;
    TempFileScheduler& operator=(const TempFileScheduler&) = delete;

    void shutdown();
    void schedule(const std::string& taskId, int64_t durationMs, const std::string& filePath);
    void initializeFromDatabase();

private:
    std::string m_fileType;
    CleanupCallback m_cleanupCallback;
    std::shared_ptr<std::atomic<bool>> m_shuttingDown;
    std::mutex m_schedulersMutex;
    std::unordered_map<std::string, std::unique_ptr<Bosma::Scheduler>> m_schedulers;
    std::shared_ptr<Bosma::Scheduler> m_cleanupScheduler;
};

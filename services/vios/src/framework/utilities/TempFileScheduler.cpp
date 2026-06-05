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

#include "TempFileScheduler.h"
#include <chrono>
#include "logger.h"
#include "database.h"
#include "database_schema.h"

using namespace std;

TempFileScheduler::TempFileScheduler(const std::string& fileType, CleanupCallback callback)
    : m_fileType(fileType),
      m_cleanupCallback(std::move(callback)),
      m_shuttingDown(std::make_shared<std::atomic<bool>>(false)),
      m_cleanupScheduler(std::make_shared<Bosma::Scheduler>(1))
{}

TempFileScheduler::~TempFileScheduler()
{
    shutdown();
}

void TempFileScheduler::shutdown()
{
    m_shuttingDown->store(true);
    {
        std::lock_guard<std::mutex> lock(m_schedulersMutex);
        m_schedulers.clear();
    }
    m_cleanupScheduler.reset();
}

void TempFileScheduler::schedule(const std::string& taskId, int64_t durationMs, const std::string& filePath)
{
    if (durationMs < 0) durationMs = 0;
    auto duration = std::chrono::milliseconds(durationMs);

    std::lock_guard<std::mutex> lock(m_schedulersMutex);
    m_schedulers.erase(taskId);

    auto scheduler = std::make_unique<Bosma::Scheduler>(1);
    auto cleanupGuard = m_cleanupScheduler;
    auto shuttingDown = m_shuttingDown;
    auto callback = m_cleanupCallback;

    scheduler->in(duration, [this, taskId, filePath, cleanupGuard, shuttingDown, callback]() {
        LOG(info) << "Temp file cleanup triggered for task: " << taskId << " file: " << filePath << endl;
        callback(taskId, filePath);

        cleanupGuard->in(std::chrono::milliseconds(100), [this, shuttingDown, taskId]() {
            if (shuttingDown->load()) return;
            std::lock_guard<std::mutex> lock(this->m_schedulersMutex);
            this->m_schedulers.erase(taskId);
        });
    });

    m_schedulers[taskId] = std::move(scheduler);
    LOG(info) << "Scheduled temp file cleanup for task: " << taskId
              << " file: " << filePath << " in " << durationMs << " ms" << endl;
}

void TempFileScheduler::initializeFromDatabase()
{
    auto dbHelper = GET_DB_INSTANCE();
    if (!dbHelper)
    {
        LOG(error) << "Database not available for " << m_fileType << " cleanup initialization" << endl;
        return;
    }

    auto now = std::chrono::system_clock::now();
    auto nowMs = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();

    std::vector<nv_vms::TempFilesDBColumns> allTempFiles = dbHelper->getAllTempFiles();

    int expiredCount = 0;
    int scheduledCount = 0;

    for (const auto& tempFile : allTempFiles)
    {
        if (tempFile.file_type_value != m_fileType)
        {
            continue;
        }

        const std::string& filePath = tempFile.file_path_value;
        if (filePath.empty())
        {
            continue;
        }
        std::string filename = filePath.substr(filePath.find_last_of("/\\") + 1);
        size_t lastDot = filename.find_last_of('.');
        std::string taskId = (lastDot != std::string::npos) ? filename.substr(0, lastDot) : filename;

        if (tempFile.expiry_timestamp_value <= nowMs)
        {
            LOG(info) << "Found expired " << m_fileType << " on startup, cleaning up: " << filePath << endl;
            m_cleanupCallback(taskId, filePath);
            expiredCount++;
        }
        else
        {
            int64_t durationMs = tempFile.expiry_timestamp_value - nowMs;
            schedule(taskId, durationMs, filePath);
            scheduledCount++;
        }
    }

    LOG(info) << m_fileType << " cleanup initialization complete - Scheduled: " << scheduledCount
              << ", Cleaned immediately: " << expiredCount << endl;
}

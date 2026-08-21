/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "dash_packager_consumer.h"
#include "device_manager.h"

#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <jsoncpp/json/json.h>
#include <string>
#include <vector>
#include <thread>
#include <unordered_map>

class CommonVideoSource;

struct DashStartResult
{
    bool success = false;
    std::string error;
    std::string viewerId;
    std::string streamId;
    std::string streamToken;
    std::string manifestRelativeUrl;
    DashPackagerState state = DashPackagerState::Stopped;
    bool audioAvailable = false;
};

struct DashAssetResult
{
    bool valid = false;
    bool starting = false;
    std::filesystem::path path;
    std::string mimeType;
};

class DashSessionManager
{
public:
    static DashSessionManager& instance();

    DashSessionManager(const DashSessionManager&) = delete;
    DashSessionManager& operator=(const DashSessionManager&) = delete;

    void setDeviceManager(std::shared_ptr<nv_vms::DeviceManager> deviceManager);
    DashStartResult start(const std::string& streamId);

    // Replay sessions are never shared.  Two viewers of the same recording can
    // sit at different points in it, so each one gets its own packager, its own
    // output directory and its own playhead.
    // An overlay has to be burned into pixels, so a session that asks for one
    // is decoded, drawn on and re-encoded; without it the recording's own
    // bitstream is republished untouched.
    DashStartResult startReplay(const std::string& streamId, const std::string& startTime,
                                const std::string& endTime,
                                const Json::Value& overlay = Json::Value());
    bool controlReplay(const std::string& viewerId, const std::string& action, const std::string& value);
    bool stopViewer(const std::string& viewerId);
    std::optional<DashStartResult> status(const std::string& viewerId);
    DashAssetResult resolveAsset(const std::string& streamToken, const std::string& fileName);
    void touch(const std::string& streamToken);
    void configure(std::chrono::seconds idleTimeout, unsigned targetDuration, unsigned playlistLength,
                   size_t maxSessions, std::filesystem::path outputRoot);
    void shutdown();

private:
    DashSessionManager();
    ~DashSessionManager();

    struct Session
    {
        std::string streamId;
        std::string streamToken;
        std::string mediaUrl;
        std::shared_ptr<DashPackagerConsumer> packager;
        // Replay only: the recorded source feeding the packager, and the window
        // it was opened for.  Live sessions are fed by StreamMonitor instead
        // and leave this null.
        bool replay = false;
        std::string startTime;
        std::string endTime;
        std::shared_ptr<CommonVideoSource> source;
        bool paused = false;
        std::set<std::string> viewerIds;
        std::chrono::steady_clock::time_point lastActivity;
        // Latches once the session has produced its preroll window.  Counting
        // the directory on every manifest request would cost a readdir per
        // stream per manifest refresh, forever, for a condition that can only
        // become true once.
        bool prerollComplete = false;
    };

    static std::string createStreamToken(const std::string& streamId);
    std::shared_ptr<nv_vms::StreamInfo> findStream(const std::string& streamId) const;
    void reaperLoop();
    void destroySession(std::shared_ptr<Session> session);

    mutable std::mutex m_mutex;
    std::condition_variable m_wakeup;
    std::weak_ptr<nv_vms::DeviceManager> m_deviceManager;
    // Live sessions are keyed by stream because every viewer of a camera shares
    // one packager.  Replay sessions cannot be, so they are owned by token.
    std::unordered_map<std::string, std::shared_ptr<Session>> m_sessionsByStream;
    std::unordered_map<std::string, std::shared_ptr<Session>> m_replaySessionsByToken;
    std::unordered_map<std::string, std::weak_ptr<Session>> m_sessionsByToken;
    std::unordered_map<std::string, std::weak_ptr<Session>> m_sessionsByViewer;
    std::thread m_reaperThread;
    bool m_shutdown = false;

    std::chrono::seconds m_idleTimeout{45};
    unsigned m_targetDuration = 1;
    unsigned m_playlistLength = 8;
    size_t m_maxSessions = 8;
    std::filesystem::path m_outputRoot{"webroot/dash"};
};

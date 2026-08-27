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

/* What a caller learns about a session that is already running, as opposed to
 * one it has just started.  Deliberately per viewer rather than per session:
 * several viewers can share one live stream, and a caller asking what is
 * running wants the same granularity the WebRTC query gives it. */
struct DashSessionInfo
{
    std::string viewerId;
    std::string streamId;
    std::string sensorId;
    std::string streamToken;
    std::string manifestRelativeUrl;
    DashPackagerState state = DashPackagerState::Stopped;
    bool audioAvailable = false;
    bool replay = false;
    bool paused = false;
    std::string startTime;
    std::string endTime;
    // Position on the published media timeline, negative when nothing has been
    // published yet, and how many frames put it there.
    int64_t positionMs = -1;
    /* Epoch milliseconds of the newest published frame, which is the form the
     * WebRTC query reports and the one a caller feeds back to the picture API.
     * Zero when nothing has been published. */
    int64_t frameEpochMs = 0;
    uint64_t framesPublished = 0;
    // How many viewers share this session, which is why one stream can appear
    // more than once in a query.
    unsigned viewerCount = 0;
};

/* One definition of the wire shape, shared by the live and the replay
 * endpoints.  They already keep separate copies of the start-response helper,
 * and those have drifted; a query answered differently depending on which URL
 * asked would be worse, because a caller polls both. */
Json::Value dashSessionInfoToJson(const DashSessionInfo& info);

struct DashAssetResult
{
    bool valid = false;
    bool starting = false;
    // A recording has no live edge the viewer is chasing, so a replay manifest
    // is published further behind its newest media than a live one: the extra
    // distance is buffer the player can absorb jitter from, and it costs only
    // latency nobody is watching for.
    bool replay = false;
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
    // Live without an overlay is fed straight from the stream monitor and
    // touches no codec.  An overlay has to be burned into pixels, so such a
    // session decodes, draws and re-encodes, and is not shared with viewers who
    // asked for something different.
    // A video wall composes several cameras into one picture, so it always owns
    // a private decode/composite/encode pipeline and is never shared with the
    // pass-through session for any single camera in it.
    DashStartResult start(const std::string& streamId, const Json::Value& overlay = Json::Value(),
                          const Json::Value& composite = Json::Value(),
                          const std::string& frameRate = std::string());

    // Replay sessions are never shared.  Two viewers of the same recording can
    // sit at different points in it, so each one gets its own packager, its own
    // output directory and its own playhead.
    // An overlay has to be burned into pixels, so a session that asks for one
    // is decoded, drawn on and re-encoded; without it the recording's own
    // bitstream is republished untouched.
    DashStartResult startReplay(const std::string& streamId, const std::string& startTime,
                                const std::string& endTime,
                                const Json::Value& overlay = Json::Value());
    // Replay seeking replaces only this viewer's private DASH session.  A new
    // token prevents a browser from mixing fMP4 fragments from before and
    // after the decoder flush.
    DashStartResult seekReplay(const std::string& viewerId, const std::string& startTime);
    bool controlReplay(const std::string& viewerId, const std::string& action, const std::string& value);
    bool stopViewer(const std::string& viewerId);
    std::optional<DashStartResult> status(const std::string& viewerId);
    /* Every running session, or just one viewer's when a viewerId is given.
     * `replayOnly` splits the answer the way the endpoints do: the live query
     * must not report recordings and the replay query must not report cameras. */
    std::vector<DashSessionInfo> query(const std::string& viewerId, bool replayOnly) const;
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
        // Kept alongside the stream so a query can name the camera without
        // going back to the device manager for every viewer it reports.
        std::string sensorId;
        std::string streamToken;
        std::string mediaUrl;
        std::shared_ptr<DashPackagerConsumer> packager;
        // True only for a recorded replay.  This controls the MPD shape and
        // replay-specific controls such as seek; a live overlay is still live
        // even though it owns a private source pipeline.
        bool replay = false;
        // Private-source sessions (recorded replay and live overlay) are kept
        // in the token-keyed collection and must tear down their own pipeline.
        bool ownsSource = false;
        std::string startTime;
        std::string endTime;
        Json::Value overlay;
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

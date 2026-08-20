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

#include "dash_session_manager.h"

#include "logger.h"
#include "stream_monitor.h"
#include "utils.h"

#include <algorithm>
#include <cctype>
#include <limits>
#include <vector>

namespace {

std::string compactCodec(std::string codec)
{
    codec.erase(std::remove_if(codec.begin(), codec.end(), [](unsigned char value) {
        return !std::isalnum(value);
    }), codec.end());
    std::transform(codec.begin(), codec.end(), codec.begin(), [](unsigned char value) {
        return static_cast<char>(std::tolower(value));
    });
    return codec;
}

std::string stateString(DashPackagerState state)
{
    switch (state)
    {
        case DashPackagerState::Stopped: return "stopped";
        case DashPackagerState::Starting: return "starting";
        case DashPackagerState::Running: return "running";
        case DashPackagerState::Failed: return "failed";
    }
    return "unknown";
}

unsigned parsePositive(const std::string& value, unsigned fallback)
{
    try
    {
        const unsigned long parsed = std::stoul(value);
        return parsed > 0 && parsed <= std::numeric_limits<unsigned>::max()
            ? static_cast<unsigned>(parsed) : fallback;
    }
    catch (const std::exception&)
    {
        return fallback;
    }
}

} // namespace

namespace
{
constexpr uint64_t kDashRetainedSegments = 60;

// A fresh session has no back catalogue, so a player that starts immediately
// sits on the live edge and stalls once per segment.  Withhold the manifest
// until this many seconds of media exist, expressed in seconds so it holds for
// any segment duration.
constexpr unsigned kDashPrerollSeconds = 10;
} // namespace

DashSessionManager& DashSessionManager::instance()
{
    static DashSessionManager manager;
    return manager;
}

DashSessionManager::DashSessionManager()
    : m_reaperThread(&DashSessionManager::reaperLoop, this)
{
}

DashSessionManager::~DashSessionManager()
{
    shutdown();
}

void DashSessionManager::setDeviceManager(std::shared_ptr<nv_vms::DeviceManager> deviceManager)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_deviceManager = std::move(deviceManager);
}

void DashSessionManager::configure(std::chrono::seconds idleTimeout, unsigned targetDuration,
                                   unsigned playlistLength, size_t maxSessions,
                                   std::filesystem::path outputRoot)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_idleTimeout = std::max(idleTimeout, std::chrono::seconds(5));
    m_targetDuration = std::max(targetDuration, 1U);
    m_playlistLength = std::max(playlistLength, 3U);
    m_maxSessions = std::max(maxSessions, size_t{1});
    m_outputRoot = std::move(outputRoot);
}

std::shared_ptr<nv_vms::StreamInfo> DashSessionManager::findStream(const std::string& streamId) const
{
    std::shared_ptr<nv_vms::DeviceManager> deviceManager;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        deviceManager = m_deviceManager.lock();
    }
    if (!deviceManager)
    {
        return nullptr;
    }
    for (const auto& stream : deviceManager->getStreamList(false))
    {
        if (stream && stream->id == streamId)
        {
            return stream;
        }
    }
    return nullptr;
}

std::string DashSessionManager::createStreamToken(const std::string& streamId)
{
    std::string prefix;
    prefix.reserve(std::min(streamId.size(), size_t{128}));
    for (const unsigned char value : streamId)
    {
        if (std::isalnum(value) || value == '-' || value == '_')
        {
            prefix.push_back(static_cast<char>(value));
        }
        if (prefix.size() == 128)
        {
            break;
        }
    }
    if (prefix.empty())
    {
        prefix = "stream";
    }
    return prefix + "-" + generate_uuid();
}

DashStartResult DashSessionManager::start(const std::string& streamId)
{
    DashStartResult result;
    result.streamId = streamId;
    if (streamId.empty())
    {
        result.error = "streamId is required";
        return result;
    }

    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto existing = m_sessionsByStream.find(streamId);
        if (existing != m_sessionsByStream.end())
        {
            const std::shared_ptr<Session>& session = existing->second;
            result.viewerId = generate_uuid();
            session->viewerIds.insert(result.viewerId);
            session->lastActivity = std::chrono::steady_clock::now();
            m_sessionsByViewer[result.viewerId] = session;
            result.success = true;
            result.streamToken = session->streamToken;
            result.manifestRelativeUrl = "/vst/dash/" + session->streamToken + "/"
                                         + session->streamToken + ".mpd";
            result.state = session->packager->state();
            result.audioAvailable = session->packager->audioEnabled();
            return result;
        }
        if (m_sessionsByStream.size() >= m_maxSessions)
        {
            result.error = "Maximum number of live DASH sessions reached";
            return result;
        }
    }

    const std::shared_ptr<nv_vms::StreamInfo> stream = findStream(streamId);
    if (!stream)
    {
        result.error = "Stream not found";
        return result;
    }
    const std::string videoCodec = compactCodec(stream->settings.encoderValues.encoding);
    if (videoCodec != "h264" && videoCodec != "avc")
    {
        result.error = "Live DASH v1 requires an H.264 source";
        return result;
    }
    const std::string mediaUrl = stream->live_proxy_url.empty() ? stream->live_url : stream->live_proxy_url;
    if (mediaUrl.rfind("rtsp://", 0) != 0 && mediaUrl.rfind("rtsps://", 0) != 0)
    {
        result.error = "Live DASH requires an RTSP or RTSPS source";
        return result;
    }

    const std::string audioCodec = compactCodec(stream->settings.audioEncoderValues.encoding);
    const bool enableAac = stream->settings.audioEncoderValues.enable
                           && (audioCodec == "aac" || audioCodec == "mpeg4generic");
    DashPackagerConfig packagerConfig;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        packagerConfig.streamToken = createStreamToken(streamId);
        packagerConfig.outputRoot = m_outputRoot;
        packagerConfig.targetDurationSeconds = m_targetDuration;
        packagerConfig.playlistLength = m_playlistLength;
        packagerConfig.enableAac = enableAac;
        packagerConfig.audioSampleRate = parsePositive(stream->settings.audioEncoderValues.sample_rate, 48000);
        packagerConfig.audioChannels = parsePositive(stream->settings.audioEncoderValues.channels, 2);
    }

    auto session = std::make_shared<Session>();
    session->streamId = streamId;
    session->streamToken = packagerConfig.streamToken;
    session->mediaUrl = mediaUrl;
    session->packager = std::make_shared<DashPackagerConsumer>(std::move(packagerConfig));
    session->lastActivity = std::chrono::steady_clock::now();

    if (!session->packager->start())
    {
        result.error = session->packager->lastError();
        return result;
    }
    std::string registrationUrl = mediaUrl;
    StreamMonitor::getInstance()->registerDataCallback(registrationUrl, session->packager);

    std::shared_ptr<Session> redundantSession;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        // A concurrent starter may have created the stream while this pipeline was initialized.
        const auto concurrent = m_sessionsByStream.find(streamId);
        if (concurrent != m_sessionsByStream.end())
        {
            redundantSession = session;
            session = concurrent->second;
        }
        else
        {
            m_sessionsByStream[streamId] = session;
            m_sessionsByToken[session->streamToken] = session;
        }
        result.viewerId = generate_uuid();
        session->viewerIds.insert(result.viewerId);
        m_sessionsByViewer[result.viewerId] = session;
    }
    if (redundantSession)
    {
        destroySession(redundantSession);
    }

    result.success = true;
    result.streamToken = session->streamToken;
    result.manifestRelativeUrl = "/vst/dash/" + session->streamToken + "/"
                                 + session->streamToken + ".mpd";
    result.state = session->packager->state();
    result.audioAvailable = session->packager->audioEnabled();
    LOG(info) << "Live DASH viewer started streamId=" << streamId
              << " state=" << stateString(result.state)
              << " audio=" << (result.audioAvailable ? "aac" : "none") << endl;
    return result;
}

bool DashSessionManager::stopViewer(const std::string& viewerId)
{
    std::shared_ptr<Session> sessionToDestroy;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto viewer = m_sessionsByViewer.find(viewerId);
        if (viewer == m_sessionsByViewer.end())
        {
            return false;
        }
        if (const std::shared_ptr<Session> session = viewer->second.lock())
        {
            session->viewerIds.erase(viewerId);
            session->lastActivity = std::chrono::steady_clock::now();
            if (session->viewerIds.empty())
            {
                m_sessionsByToken.erase(session->streamToken);
                m_sessionsByStream.erase(session->streamId);
                sessionToDestroy = session;
            }
        }
        m_sessionsByViewer.erase(viewer);
        m_wakeup.notify_all();
    }
    if (sessionToDestroy)
    {
        destroySession(sessionToDestroy);
    }
    return true;
}

std::optional<DashStartResult> DashSessionManager::status(const std::string& viewerId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto viewer = m_sessionsByViewer.find(viewerId);
    if (viewer == m_sessionsByViewer.end())
    {
        return std::nullopt;
    }
    const std::shared_ptr<Session> session = viewer->second.lock();
    if (!session)
    {
        return std::nullopt;
    }
    DashStartResult result;
    result.success = !session->packager->hasError();
    result.error = session->packager->lastError();
    result.viewerId = viewerId;
    result.streamId = session->streamId;
    result.streamToken = session->streamToken;
    result.manifestRelativeUrl = "/vst/dash/" + session->streamToken + "/"
                                 + session->streamToken + ".mpd";
    result.state = session->packager->state();
    result.audioAvailable = session->packager->audioEnabled();
    return result;
}

DashAssetResult DashSessionManager::resolveAsset(const std::string& streamToken, const std::string& fileName)
{
    DashAssetResult result;
    if (streamToken.empty() || fileName.empty() || fileName.find("..") != std::string::npos
        || fileName.find('/') != std::string::npos || fileName.find('\\') != std::string::npos)
    {
        return result;
    }
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto token = m_sessionsByToken.find(streamToken);
    if (token == m_sessionsByToken.end())
    {
        return result;
    }
    const std::shared_ptr<Session> session = token->second.lock();
    if (!session)
    {
        return result;
    }
    session->lastActivity = std::chrono::steady_clock::now();
    result.valid = true;
    result.path = session->packager->manifestPath().parent_path() / fileName;
    if (result.path.extension() == ".mpd")
    {
        result.mimeType = "application/dash+xml";
        result.starting = !std::filesystem::exists(result.path)
                          && session->packager->state() != DashPackagerState::Failed;
        if (!result.starting && !session->prerollComplete)
        {
            const unsigned required = std::max(1U, kDashPrerollSeconds / std::max(1U, m_targetDuration));
            unsigned produced = 0;
            std::error_code ec;
            for (const auto& entry : std::filesystem::directory_iterator(result.path.parent_path(), ec))
            {
                if (ec)
                {
                    break;
                }
                const std::string name = entry.path().filename().string();
                if (entry.path().extension() == ".mp4" && name.rfind("video_", 0) == 0 && ++produced >= required)
                {
                    break;
                }
            }
            session->prerollComplete = produced >= required;
            result.starting = !session->prerollComplete;
        }
    }
    else if (result.path.extension() == ".m4s")
    {
        result.mimeType = "video/iso.segment";
    }
    else if (result.path.extension() == ".mp4")
    {
        result.mimeType = "video/mp4";
    }
    else
    {
        result.valid = false;
    }
    return result;
}

void DashSessionManager::touch(const std::string& streamToken)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto token = m_sessionsByToken.find(streamToken);
    if (token != m_sessionsByToken.end())
    {
        if (const std::shared_ptr<Session> session = token->second.lock())
        {
            session->lastActivity = std::chrono::steady_clock::now();
        }
    }
}

void DashSessionManager::destroySession(std::shared_ptr<Session> session)
{
    if (!session)
    {
        return;
    }
    std::string registrationUrl = session->mediaUrl;
    StreamMonitor::getInstance()->deregisterDataCallback(session->packager, registrationUrl, false);
    session->packager->stop();
}

namespace
{
// dashsink never removes the segments it writes, so a long lived session grows
// its directory without bound.  Keep a window large enough for the manifest
// preroll and the player's live delay.  Segment 1 is always kept: the
// initialization segment served to players is derived from it.

void pruneSegments(const std::filesystem::path& directory)
{
    std::error_code ec;
    std::vector<std::pair<uint64_t, std::filesystem::path>> segments;
    for (const auto& entry : std::filesystem::directory_iterator(directory, ec))
    {
        if (ec)
        {
            return;
        }
        const std::string name = entry.path().filename().string();
        if (entry.path().extension() != ".mp4" || name.rfind("video_", 0) != 0)
        {
            continue;
        }
        const size_t underscore = name.rfind('_');
        const size_t dot = name.rfind(".mp4");
        if (underscore == std::string::npos || dot == std::string::npos || dot <= underscore + 1)
        {
            continue;
        }
        try
        {
            segments.emplace_back(std::stoull(name.substr(underscore + 1, dot - underscore - 1)), entry.path());
        }
        catch (const std::exception&)
        {
            continue;
        }
    }
    if (segments.size() <= kDashRetainedSegments)
    {
        return;
    }
    uint64_t newest = 0;
    for (const auto& segment : segments)
    {
        newest = std::max(newest, segment.first);
    }
    if (newest <= kDashRetainedSegments)
    {
        return;
    }
    const uint64_t oldestKept = newest - kDashRetainedSegments;
    for (const auto& [number, path] : segments)
    {
        if (number > 1 && number < oldestKept)
        {
            std::filesystem::remove(path, ec);
        }
    }
}
} // namespace

void DashSessionManager::reaperLoop()
{
    std::unique_lock<std::mutex> lock(m_mutex);
    while (!m_shutdown)
    {
        m_wakeup.wait_for(lock, std::chrono::seconds(1), [this] { return m_shutdown; });
        if (m_shutdown)
        {
            break;
        }
        const auto now = std::chrono::steady_clock::now();
        std::vector<std::shared_ptr<Session>> expired;
        for (auto iterator = m_sessionsByStream.begin(); iterator != m_sessionsByStream.end();)
        {
            const std::shared_ptr<Session>& session = iterator->second;
            // Reap on inactivity alone.  A viewer only leaves viewerIds when it
            // calls /dash/stop, which a closed tab, a lost network or a crashed
            // client never does, so requiring an empty viewer set kept the
            // pipeline and its output directory alive forever.  Every manifest
            // and segment request refreshes lastActivity, so a session nobody is
            // fetching from is dead regardless of who is still registered.
            if (now - session->lastActivity >= m_idleTimeout)
            {
                for (const std::string& staleViewer : session->viewerIds)
                {
                    m_sessionsByViewer.erase(staleViewer);
                }
                session->viewerIds.clear();
                m_sessionsByToken.erase(session->streamToken);
                expired.push_back(session);
                iterator = m_sessionsByStream.erase(iterator);
            }
            else
            {
                ++iterator;
            }
        }
        std::vector<std::filesystem::path> liveDirectories;
        for (const auto& [streamId, session] : m_sessionsByStream)
        {
            liveDirectories.push_back(session->packager->manifestPath().parent_path());
        }
        lock.unlock();
        for (const auto& session : expired)
        {
            destroySession(session);
        }
        for (const auto& directory : liveDirectories)
        {
            pruneSegments(directory);
        }
        lock.lock();
    }
}

void DashSessionManager::shutdown()
{
    std::vector<std::shared_ptr<Session>> sessions;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_shutdown)
        {
            return;
        }
        m_shutdown = true;
        for (const auto& entry : m_sessionsByStream)
        {
            sessions.push_back(entry.second);
        }
        m_sessionsByStream.clear();
        m_sessionsByToken.clear();
        m_sessionsByViewer.clear();
    }
    m_wakeup.notify_all();
    if (m_reaperThread.joinable())
    {
        m_reaperThread.join();
    }
    for (const auto& session : sessions)
    {
        destroySession(session);
    }
}

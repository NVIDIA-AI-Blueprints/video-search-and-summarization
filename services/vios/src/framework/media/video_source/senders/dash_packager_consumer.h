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

#include "media_consumer.h"

#include <atomic>
#include <chrono>
#include <filesystem>
#include <mutex>
#include <string>

struct DashPackagerConfig
{
    std::string streamToken;
    std::filesystem::path outputRoot;
    unsigned targetDurationSeconds = 1;
    unsigned playlistLength = 8;
    // The recorded pipeline hands the encoder a constant presentation time, so
    // a replay session cannot take a media timeline from its source and builds
    // one from the frame rate instead.  Live sessions carry real RTSP
    // timestamps and leave this off.
    bool synthesizeTimestamps = false;
    double sourceFrameRate = 30.0;
    // Live composition.  A wall is composed in real time but its frames reach
    // the packager without a timestamp, and counting them instead turns any
    // frame that goes missing on the way into a timeline that runs slow - the
    // live edge then falls behind real time for as long as the session lasts.
    // Stamping arrival keeps media time and wall time together whatever is
    // lost, so a gap costs a dropped frame rather than a growing deficit.
    bool useArrivalTimestamps = false;
    // Replay only.  Recordings are selected by whole file, so the first file
    // usually starts before the requested window; frames earlier than this are
    // dropped so playback begins where the caller asked.
    int64_t startEpochMs = 0;
    bool enableAac = false;
    unsigned audioSampleRate = 48000;
    unsigned audioChannels = 2;
};

enum class DashPackagerState
{
    Stopped,
    Starting,
    Running,
    Failed
};

class DashPackagerConsumer final : public IMediaDataConsumer
{
public:
    explicit DashPackagerConsumer(DashPackagerConfig config);
    ~DashPackagerConsumer() override;

    DashPackagerConsumer(const DashPackagerConsumer&) = delete;
    DashPackagerConsumer& operator=(const DashPackagerConsumer&) = delete;

    void onFrame(FrameParams& params) override;
    void onFrame(std::shared_ptr<RawFrameParams> frameData) override;

    [[nodiscard]] bool start() override;
    void stop() override;
    void sendEOS() override;
    [[nodiscard]] bool hasError() const override;

    [[nodiscard]] DashPackagerState state() const;
    [[nodiscard]] bool audioEnabled() const;
    [[nodiscard]] std::filesystem::path manifestPath() const;
    [[nodiscard]] std::string lastError() const;

    static bool isFmp4Available();

private:
    [[nodiscard]] bool createPipeline();
    void destroyPipeline();
    void cleanupOutput();
    // Per-branch state for turning producer timestamps into a monotonic media
    // timeline.  A producer that stamps every frame identically (the recorded
    // pipeline hands the encoder a constant pts) cannot drive a muxer, so the
    // branch synthesises a constant rate timeline instead.
    struct TimelineState
    {
        GstClockTime baseline = 0;
        bool baselineValid = false;
        std::chrono::steady_clock::time_point arrivalOrigin{};
        bool arrivalOriginValid = false;
        GstClockTime lastRaw = 0;
        bool lastRawValid = false;
        bool synthesize = false;
        uint64_t frameIndex = 0;
        // The timeline actually published, which is not always the one the
        // source offers: a hole in it cannot be handed to a player.
        GstClockTime lastOut = 0;
        bool lastOutValid = false;
        GstClockTime carried = 0;
        uint64_t jumps = 0;
        uint64_t framesSinceReport = 0;
        uint64_t framesPushed = 0;
        // Wall-clock arrival, which is what decides whether a segment is written
        // in time for a viewer sitting near the live edge.  The media timeline
        // can be perfectly continuous while the frames producing it arrive late.
        std::chrono::steady_clock::time_point lastArrival{};
        bool lastArrivalValid = false;
        uint64_t worstGapMs = 0;
        uint64_t lateArrivals = 0;
    };

    [[nodiscard]] bool pushFrame(GstElement* appsrc, const uint8_t* data, size_t size,
                                 GstClockTime rawPts, TimelineState& timeline);
    void reportDroppedFrame(const char* kind);
    void setFailure(const std::string& message);
    static GstBusSyncReply busSyncHandler(GstBus* bus, GstMessage* message, gpointer userData);

    DashPackagerConfig m_config;
    std::filesystem::path m_outputDirectory;
    std::filesystem::path m_manifestPath;

    GstElement* m_pipeline = nullptr;
    GstElement* m_videoAppsrc = nullptr;
    GstElement* m_videoParser = nullptr;
    GstElement* m_audioAppsrc = nullptr;
    GstElement* m_audioParser = nullptr;
    GstElement* m_dashSink = nullptr;

    std::atomic<DashPackagerState> m_state{DashPackagerState::Stopped};
    std::atomic<bool> m_hasError{false};
    mutable std::mutex m_mutex;
    mutable std::mutex m_errorMutex;
    std::string m_lastError;
    // Set once the source's own caps have been applied to the appsrc.
    bool m_sourceCapsSet = false;
    TimelineState m_videoTimeline;
    TimelineState m_audioTimeline;
    // Session accounting, reported once on stop rather than per frame.
    std::chrono::steady_clock::time_point m_startedAt{};
    std::atomic<bool> m_firstFrameLogged{false};
    std::atomic<uint64_t> m_droppedFrames{0};
};

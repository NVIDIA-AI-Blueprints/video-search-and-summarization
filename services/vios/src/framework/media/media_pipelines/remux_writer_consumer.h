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

#pragma once

#include <memory>
#include <string>
#include <atomic>
#include <limits>

#include "media_consumer.h"

// Forward declarations to avoid heavy headers in interface
typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;
typedef struct _GstPad GstPad;

struct RemuxWriterConfig
{
    std::string video_codec;           // "h264" or "h265"
    std::string container;             // "mp4", "mkv", "ts"
    std::string output_file;           // filesink location
    std::string log_id;                // request log identifier
    bool enable_audio = false;         // whether to create audio branch
    int64_t seek_start_ms = 0;         // for PTS offset filtering
    int64_t end_time_ms = std::numeric_limits<int64_t>::max(); // EOS guard
};

// Remux writer pipeline consumer (passthrough - no transcoding).
// This class derives from IMediaDataConsumer and accepts video frames.
// For audio, call getAudioConsumer() to obtain an IMediaDataConsumer that feeds audio appsrc.
// Pipeline: appsrc → parser → mux → filesink
class RemuxWriterConsumer : public IMediaDataConsumer
{
public:
    explicit RemuxWriterConsumer(const RemuxWriterConfig& cfg);
    virtual ~RemuxWriterConsumer();

    // IMediaDataConsumer (video path)
    void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;

    // Lifecycle
    bool start();
    void stop();
    bool isRunning() const { return mRunning.load(); }
    void* getPipeline() const { return mPipeline; }
    
    // Wait on this writer's bus for EOS or ERROR. Returns true on EOS/ERROR, false on timeout.
    bool waitForCompletion(int64_t timeout_secs);
    bool hasError() const { return mError.load(); }

    // Audio consumer to feed audio appsrc
    std::shared_ptr<IMediaDataConsumer> getAudioConsumer();
    
    // Send EOS to appsrc elements to signal end of input and flush pipeline
    void sendEOS();

    // Get actual first frame PTS (for filename correction in remux mode)
    int64_t getActualStartPtsMs() const override { return mActualStartPtsMs.load(); }

    // Get actual last frame PTS (appended to filename in remux mode).
    int64_t getActualEndPtsMs() const override { return mActualEndPtsMs.load(); }

private:
    bool buildPipeline();
    bool linkPipeline();
    void attachProbes();
    bool createContainerElements();
    void configureParserForMp4();
    void teardown();

private:
    RemuxWriterConfig mCfg;
    std::string mLogPrefix;
    std::atomic<bool> mRunning{false};
    std::atomic<bool> mError{false};

    // Pipeline elements
    // Video: appsrc → parser → [capsfilter] → mux → filesink
    GstElement* mPipeline = nullptr;
    GstElement* mVideoAppsrc = nullptr;
    GstElement* mVideoParser = nullptr;
    GstElement* mVideoCapsfilter = nullptr;  // optional (mp4 only)
    GstElement* mMux = nullptr;
    GstElement* mFilesink = nullptr;

    // Audio branch (optional) - passthrough, no transcoding
    // Audio: appsrc → queue → aacparse → mux (preserves original AAC codec)
    GstElement* mAudioAppsrc = nullptr;
    GstElement* mAudioQueue = nullptr;
    GstElement* mAudioParser = nullptr;     // parsebin for audio codec auto-detection
    GstPad* mMuxAudioSinkPad = nullptr;     // Pre-requested audio sink pad from mux

    // Helper inner consumer that feeds audio appsrc
    class AudioAppSrcConsumer : public IMediaDataConsumer
    {
    public:
        explicit AudioAppSrcConsumer(RemuxWriterConsumer* owner) 
            : IMediaDataConsumer("RemuxAudioConsumer"), mOwner(owner) {}
        void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
    private:
        RemuxWriterConsumer* mOwner;
    };

    std::shared_ptr<AudioAppSrcConsumer> mAudioConsumer;

    // Audio caps tracking
    bool mAudioCapsSet = false;

    // Video caps tracking. The appsrc is built with generic codec-only caps;
    // the actual stream-format (avc/byte-stream) is propagated from the first
    // incoming sample's caps so downstream h264parse/mp4mux do not
    // misinterpret the bitstream and drop PTS.
    bool mVideoCapsSet = false;

    // Track actual first frame PTS for filename correction
    std::atomic<int64_t> mActualStartPtsMs{-1};
    std::atomic<bool> mFirstFrameReceived{false};

    // Track actual last frame PTS (file-start-relative ms) for filename annotation.
    std::atomic<int64_t> mActualEndPtsMs{-1};
};


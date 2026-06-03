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
#include <functional>
#include <atomic>
#include <limits>

#include "media_consumer.h"

// Forward declarations to avoid heavy headers in interface
typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;
typedef struct _GstAppSrc GstAppSrc;
struct OverlayBBoxParams;
class NvLLOverlayInternal;

struct TranscodeWriterConfig
{
    std::string video_codec;           // "h264" or "h265"
    std::string container;             // "mp4", "mkv", "ts"
    std::string log_id;                // request log identifier
    int64_t file_start_time = 0;
    bool enable_overlay = false;
    bool enable_audio = false;         // whether to create audio branch
    GstElement* overlay_bin = nullptr; // optional pre-built overlay bin (can be null)
    // Overlay parameters for local construction (writer side only)
    std::string sensor_name;
    std::string stream_id;             // Stream ID for database lookups
    std::string user_start_time_iso;
    std::string user_end_time_iso;
    OverlayBBoxParams* overlay_params = nullptr; // optional
    bool is_software_encoder = false;  // true when not using NV v4l2 enc

    int64_t seek_start_ms = 0;         // for decoder/output filtering
    int64_t end_time_ms = std::numeric_limits<int64_t>::max(); // EOS guard

    std::string output_file;           // filesink location
    bool has_bframes = false;          // B-frame presence flag for decoder optimization
};

// Transcode writer pipeline consumer.
// This class derives from IMediaDataConsumer and accepts video frames.
// For audio, call getAudioConsumer() to obtain an IMediaDataConsumer that feeds audio appsrc.
class TranscodeWriterConsumer : public IMediaDataConsumer
{
public:
    explicit TranscodeWriterConsumer(const TranscodeWriterConfig& cfg);
    virtual ~TranscodeWriterConsumer();

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

private:
    bool buildPipeline();
    bool linkPipeline();
    void attachProbes();
    void detachProbes();
    GstElement* buildOverlayBinIfNeeded();
    void teardown();

private:
    TranscodeWriterConfig mCfg;
    std::string mLogPrefix;
    std::atomic<bool> mRunning{false};

    // pipeline elements
    GstElement* mPipeline = nullptr;
    GstElement* mVideoAppsrc = nullptr;
    GstElement* mTranscodebin = nullptr;      // decoder/conv/identity/overlay/encoder/capssetter
    GstElement* mVideoDecoder = nullptr;      // reference to decoder inside transcodebin (for probes)
    GstElement* mVideoEncoder = nullptr;      // reference to encoder inside transcodebin (for probes)
    GstElement* mParserAfterEncode = nullptr; // optional (mp4/mkv)
    GstElement* mPeCapsfilter = nullptr;     // optional (mp4/mkv)
    GstElement* mMux = nullptr;
    GstElement* mFilesink = nullptr;

    // audio branch (optional) - transcode: appsrc -> queue -> decodebin -> audioconvert -> audioresample -> avenc_aac -> mux
    GstElement* mAudioAppsrc = nullptr;
    GstElement* mAudioQueue = nullptr;
    GstElement* mAudioDecodebin = nullptr;
    GstElement* mAudioConvert = nullptr;
    GstElement* mAudioResample = nullptr;
    GstElement* mAudioEncoder = nullptr;     // avenc_aac (or fallback: voaacenc, fdkaacenc)

    std::atomic<bool> mError{false};

    // Helper inner consumer that feeds audio appsrc
    class AudioAppSrcConsumer : public IMediaDataConsumer
    {
    public:
        explicit AudioAppSrcConsumer(TranscodeWriterConsumer* owner) : IMediaDataConsumer("AudioAppSrcConsumer"), mOwner(owner) {}
        void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
    private:
        TranscodeWriterConsumer* mOwner;
    };

    std::shared_ptr<AudioAppSrcConsumer> mAudioConsumer;

    // Overlay instance (must stay alive for pipeline duration)
    std::unique_ptr<NvLLOverlayInternal> mOverlayInst;

    // Audio caps tracking
    bool mAudioCapsSet = false;

    // Video caps tracking. The appsrc is built with generic codec-only caps;
    // the actual stream-format (avc/hvc1 vs byte-stream) is propagated from
    // the first incoming sample's caps so the downstream decoder/parser
    // cannot misinterpret the bitstream (which would otherwise lead to PTS
    // drops and mp4mux failures).
    bool mVideoCapsSet = false;

    // Probe tracking (for safe removal)
    GstPad* mDecoderSinkPad = nullptr;
    gulong mDecoderSinkProbeId = 0;
    GstPad* mDecoderSrcPad = nullptr;
    gulong mDecoderSrcProbeId = 0;
    GstPad* mEncoderSrcPad = nullptr;
    gulong mEncoderSrcProbeId = 0;
    GstPad* mAudioSrcPad = nullptr;
    gulong mAudioSrcProbeId = 0;
    GstPad* mMuxSinkPad = nullptr;
    gulong mMuxSinkProbeId = 0;
};



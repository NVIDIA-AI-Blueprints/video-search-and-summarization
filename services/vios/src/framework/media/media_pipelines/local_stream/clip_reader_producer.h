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

#include <string>
#include <vector>
#include <map>
#include <memory>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <functional>
#include <limits>
#include <deque>

#include "media_producer.h"
#include "unified_storage/reader/unified_storage_reader.h"
#include "unified_storage/reader/unified_storage_reader_utils.h"
#include "syncobject.h"

// Forward declarations to avoid exposing EventLoop implementation details
class EventLoop;
struct EventLoopData;

// CLIP_READER_GIOSRC_EXTRA_DEBUG (compile-time, default 0): optional engineering diagnostics for
// giosrc + demuxer live reads (growing files). When enabled, installs pad probes and emits correlation
// logs (bytes at giosrc src vs demux video vs disk) around waiting-data / done-waiting-data. That helps
// distinguish "giosrc blocked at EOF", "demux not seeing data yet", and "recorder still writing" without
// affecting release builds. Product behavior uses signals + watchdog only; this path is not required
// for correct playback or shipping.
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
struct GiosrcDebugState;
#endif

// Forward declarations to avoid heavy headers in interface. These types
// are referenced via pointers only in this header.
typedef struct _GstElement GstElement;
typedef struct _GstAppSink GstAppSink;
typedef struct _GstSample  GstSample;
typedef struct _GstBus     GstBus;
typedef struct _GstMessage GstMessage;
typedef struct _GstCaps    GstCaps;
typedef struct _GstBuffer  GstBuffer;
typedef struct _GMainLoop  GMainLoop;
typedef struct _GMainContext GMainContext;
typedef struct _GSource    GSource;
typedef struct _GThread    GThread;
typedef int gboolean;
typedef unsigned int guint;

static constexpr int MAX_REF_FRAMES = 32;

struct ClipReaderConfig
{
    std::string stream_id;
    std::string log_id;
    std::vector<std::string> file_paths;
    std::string video_codec;
    bool enable_audio = false;
    int64_t seek_start_ms = 0;
    int64_t seek_end_ms = std::numeric_limits<int64_t>::max();
    std::string object_id;             // object ID
    int64_t file_start_epoch_ms = 0;
    bool is_image_capture = false;
    bool is_growing_file = false;
    bool bypass_giosrc_for_growing_file = false;
    bool has_bframes = false;
    
    // B-frame handling configuration
    int reorder_depth = 0;                     // Max reorder depth from SPS VUI (0 = unknown)
    double estimated_framerate = 30.0;        // Estimated framerate for seek calculations

#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    // When true, or when env VST_CLIP_READER_GIOSRC_DEBUG is set (non-empty, not "0"), install
    // extra pad probes (requires CLIP_READER_GIOSRC_EXTRA_DEBUG at compile time).
    bool enable_giosrc_debug_probes = false;
#endif

    // Called after creating splitmuxsrc (GstElement*) to allow caller to attach custom probes
    std::function<void(void* /*GstElement* reader_src*/)> attach_overlay_meta_fn;
};

// GStreamer reader producer that feeds frames to registered consumers.
// Distributes video frames to consumers registered with identifier "video"
// and audio frames to consumers registered with identifier "audio".
class ClipReaderProducer : public IMediaDataProducer
{
public:
    explicit ClipReaderProducer(const ClipReaderConfig& cfg);
    virtual ~ClipReaderProducer();

    void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                          const std::string& identifier = "") override;
    void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                          const std::string& identifier,
                          const std::string& media_type) override;
    void unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                           const std::string& identifier = "", bool doNotRemoveClient = false) override;
    bool start() override;
    void stop() override;
    bool isRunning() const override;
    eMediaType getProducerMediaType() const override;
    std::string getSourceIdentifier() const override;
    size_t getConsumerCount() const override;
    bool hasConsumers() const override;
    void onFinished(std::function<void()> cb) override;
    void onError(std::function<void(const std::string& /*errorMsg*/, int /*errorCode*/)> cb) override;

    // Expose internal handles for advanced debug if needed
    void* getGstPipeline() const { return mPipeline; }
    void* getReaderSrc() const { return mReaderSrc; }
    const std::string& logPrefix() const { return mLogPrefix; }

    // Used by appsink collect probe (post-seek buffer queue)
    bool seekDone() const { return mSeekDone.load(); }
    bool playingStateReached() const { return mPlayingStateReached.load(); }
    void pushPostSeekBuffer(GstBuffer* buffer, GstCaps* caps, int64_t pts_ms, size_t size);

protected:
    void distributeToConsumers(std::shared_ptr<RawFrameParams> frameData) override;
    void distributeToConsumers(FrameParams& frameParams) override;

private:
    bool buildPipeline();
    bool buildGiosrcPipeline();          // For growing files near live edge
    bool shouldUseGiosrcForGrowingFile() const;  // Check if giosrc pipeline should be used
    bool linkElements();
    bool applySeek();
    bool play();
    void teardown();
    void attachBusHandlers();
    bool startMainLoop();
    void stopMainLoop();

    static GstFlowReturn onVideoNewSample(GstAppSink* appsink, gpointer user_data);
    static GstFlowReturn onAudioNewSample(GstAppSink* appsink, gpointer user_data);
    static gboolean busWatch(GstBus* bus, GstMessage* msg, gpointer userData);  // Static bus watch callback
    static gpointer gmainLoopThread(gpointer data);
    GstFlowReturn handleVideoSample(GstSample* sample);
    GstFlowReturn handleVideoSampleSplitmux(GstSample* sample);
    GstFlowReturn handleVideoSampleGiosrc(GstSample* sample);
    GstFlowReturn maybeFlushGiosrcGopCacheAndSend(std::shared_ptr<RawFrameParams>& frame,
                                                  GstBuffer*& buffer,
                                                  bool* should_return);
    void handleAudioSample(GstSample* sample);
    void notifyFinishedOnce();
    void notifyErrorOnce(const std::string& msg, int code);

    // giosrc waiting-data / done-waiting-data (GStreamer 1.20+): detect when giosrc blocks at EOF
    static void onGiosrcWaitingData(GstElement* src, gpointer user_data);
    static void onGiosrcDoneWaitingData(GstElement* src, gpointer user_data);
    void onGiosrcWaitingTimeout();
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    void logGiosrcDebugWaitingDataCorrelation() const;
#endif

    // If recorder stops, giosrc with is-growing=true can block forever waiting for more bytes.
    // Watchdog forces completion after a short no-data period (to avoid higher-level timeouts).

    ClipReaderConfig mCfg;
    std::string mLogPrefix;
    std::map<std::string, std::shared_ptr<IMediaDataConsumer>, std::less<>> mConsumers;
    mutable std::mutex mConsumersMtx;
    mutable std::mutex mPipelineMtx;

    // Gst handles
    GstElement* mPipeline = nullptr;
    GstElement* mReaderSrc = nullptr;
    GstElement* mReaderVideoQueue = nullptr;
    GstElement* mReaderAudioQueue = nullptr;
    GstElement* mReaderParser = nullptr;
    GstElement* mReaderFilter = nullptr;
    GstElement* mReaderVideoSink = nullptr;
    GstElement* mReaderAudioSink = nullptr;

    // Flag to track which pipeline type is in use
    bool mUsingGiosrc = false;
    bool mGiosrcIsGrowingCurrent = false;

    // Completion/error callbacks should only fire once
    std::atomic<bool> mFinishedNotified{false};
    std::atomic<bool> mErrorNotified{false};

    // Runtime
    std::atomic<bool> mRunning{false};

    // EOS helpers
    std::atomic<int64_t> mPrevBufMs{0};
    std::atomic<int64_t> mFrameThresholdMs{0};
    std::atomic<bool> mEosSignalled{false};

    // Resume tracking for growing file retry
    std::atomic<int64_t> mLastSentPts{0};      // Last PTS successfully sent to consumers
    std::atomic<int64_t> mFirstVideoSentPts{0};
    // Last PTS from appsink after giosrc normalization (FIXED_TS_OFFSET + relative ms); -1 = none yet
    std::atomic<int64_t> mLastAppsinkPtsMs{-1};
    std::atomic<int64_t> mResumePts{0};        // PTS to resume from after retry (skip frames before this)
    std::atomic<int64_t> mOriginalSeekStartMs{0}; // Original seek_start_ms (before retries modify it)

    // For giosrc start alignment: cache the current GOP starting at the most recent keyframe,
    // and flush it once we reach the requested start time (so we start at the exact I/IDR).
    static constexpr size_t MAX_GOP_CACHE_FRAMES = 600;
    std::deque<std::shared_ptr<RawFrameParams>> mGiosrcVideoGopCache;

    // Retry logic for "Stream contains no data" errors (in-progress files)
    static constexpr int MAX_EMPTY_STREAM_RETRIES = 2;
    static constexpr int RETRY_DELAY_SECONDS = 1;
    std::atomic<int> mRetryCount{0};
    bool retryPipeline();  // Returns true if retry was initiated

    // Post retry to EventLoop (avoids deadlock when called from bus watch on main loop thread).
    // Returns true if retry was posted or already in progress.
    bool postRetryPipeline(bool isError, const std::string& errorMsg);

    static void process_retry_message(std::shared_ptr<EventLoopData> data, void* parent);

    // Cap retries for premature EOS gaps (separate from empty-stream retries)
    std::atomic<int> mPrematureEosRetryCount{0};

    // State tracking for proper seek timing (like CloudStreamProducer)
    std::atomic<bool> mPausedStateReached{false};  // Track when PAUSED is reached
    std::atomic<bool> mSeekDone{false};            // Track when seek is complete
    std::atomic<bool> mPlayingStateReached{false};  // Track when PLAYING is reached

    // Post-seek buffer queue: filled by appsink probe (when seek done and not yet playing),
    // drained in onVideoNewSample when we get a non-IDR frame so we process in order.
    struct PostSeekQueuedBuffer {
        GstBuffer* buffer = nullptr;
        GstCaps* caps = nullptr;
        int64_t pts = -1;
        size_t size = 0;
    };
    std::deque<PostSeekQueuedBuffer> mPostSeekBufferQueue;

    void clearPostSeekBufferQueue();
    GstClockTime calculateSeekMargin() const;

    // Per-pipeline GMainLoop for bus processing
    GMainContext* mMainContext = nullptr;
    GMainLoop* mMainLoop = nullptr;
    GThread* mGMainLoopThread = nullptr;
    GSource* mGSource = nullptr;
    std::mutex mMainLoopMtx;
    std::condition_variable mMainLoopCv;
    bool mMainLoopRunning = false;

    // Recovery EventLoop: runs retryPipeline off the bus watch thread to avoid deadlock
    std::unique_ptr<EventLoop> m_recoveryEventLoop;

    // Retry synchronization: allows stop() to wait for retry completion
    std::atomic<bool> mRetryInProgress{false};
    SyncObject mRetrySync;

    // giosrc waiting-data watchdog: timeout when giosrc blocks at EOF for too long
    static constexpr int GIOSRC_WAITING_TIMEOUT_SEC = 3;
    GSource* mGiosrcWaitingTimeoutSource = nullptr;
    std::mutex mGiosrcWaitingMtx;

#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    bool mGiosrcDebugProbes = false;
    std::unique_ptr<GiosrcDebugState> mGiosrcDebug;
#endif

    // callbacks
    std::function<void()> mFinishedCb;
    std::function<void(const std::string&, int)> mErrorCb;
    int64_t mPrevSysTimeMs = 0;
};



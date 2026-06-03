/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <atomic>
#include <condition_variable>
#include <queue>
#include <iomanip>
#include <gst/gstmessage.h>
#include "cloud_storage_writer.h"
#include "unified_storage_types.h"
#include "unified_storage_writer.h"
#include "VideoQueue.h"
#include "config.h"
#include "database.h"
#include "event_loop.h"
#include "fps_display.h"
#include "gst_utils.h"
#include "logger.h"
#include "mm_utils.h"
#include "storage_management.h"
#include "stream_monitor.h"
#include "utils.h"

typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;
typedef struct _GstClock GstClock;

/* record state flags */
typedef enum : int
{
    RECORDING = 1,
    STOPPED = 0,
    ERROR = -1
} RecordStates;

namespace nv_vms
{
class RecorderQos
{
   public:
    static RecorderQos* getInstance()
    {
        static RecorderQos _instance;
        return &_instance;
    }
    void write(const string& data);

   private:
    RecorderQos();
    ~RecorderQos();
    std::ofstream m_recordQosFile;
    std::mutex m_recordQosMutex;
};

// Forward declaration
class GstMux;

class GstMux : public IMediaDataConsumer
{
   public:
    GstMux(RecordState record_state)
        : IMediaDataConsumer("GstMux"),
          m_totalFileSize(0),
          m_isError(false),
          m_frameRate(30),
          m_frameNum(0),
          m_muxerProcessThread(nullptr),
          m_storageMode(false)
    {
        m_recordingState = record_state;
        m_originalRecordingState = record_state;
        m_eventType = EventTypeWaitForEvent;
        m_recordingStopped = false;
        m_fpsDisplay.reset(new FPSDisplay());
        m_checkStatusScheduler = make_unique<Bosma::Scheduler>(1);
        setConsumerMediaType(MediaTypeAudioVideo);
        m_videoQueue.setRecordingState(m_recordingState);
    }
    ~GstMux()
    {
    }

    int create(shared_ptr<StreamInfo> stream, GAsyncQueue* qErrorDeviceID, bool recreate_muxer = false);
    void destroy();
    void destroyPipeline();
    bool isCreated();
    /** Snapshot of the segment currently being written to local storage (path + start time), for DB list merge. */
    VideoFileInfo getActiveLocalRecordingSnapshot() const;
    bool getError()
    {
        return m_isError;
    }
    bool isRecordGap()
    {
        return m_isRecordGap;
    }
    void disableEOS()
    {
        m_disableEOS = true;
    }
    bool play();
    bool isPlaying();
    bool isAudioSupported();
    void checkStatus();
    virtual void onFrame(FrameParams& params);
    GstMux* getSelf()
    {
        return this;
    }

    int onEvent();
    int changeRecordStateTo(RecordState new_state);

    // Storage methods
    bool CreateUnifiedStorage(const std::map<string, media_info, std::less<>>& media_details);
    bool isUsingLocalStorage() const;
    std::string getStorageMode() const;

#ifdef UNIT_TEST
    std::vector<guint64> getTimestamps();
#endif
    int m_width = 0;
    int m_height = 0;
    int m_numerator = 0;
    int m_denominator = 0;
    char* m_format = nullptr;
    size_t m_totalFileSize;
    int64_t m_prevFrameTime = 0;
    int m_maxAllowedFrameDiff{50};
    std::string m_resolution;

   private:
    std::string m_deviceId;
    std::string m_camName;
    std::atomic<bool> m_stop{false};
    std::atomic<bool> m_isError{false};
    std::atomic<bool> m_durationUpdated{true};
    std::atomic<int> m_recordState{RECORDING};
    int m_frameRate;
    int m_frameNum;
    std::unique_ptr<Bosma::Scheduler> m_checkStatusScheduler;
    GAsyncQueue* m_qErrorDeviceID{nullptr};
    GThread* m_muxerProcessThread;
    std::mutex m_threadMutex;
    std::string m_prevFileName{""};
    std::time_t m_prevFileStartTime{0};
    mutable std::mutex m_activeLocalRecordingMutex;
    std::string m_activeLocalRecordingPath;
    uint64_t m_activeLocalRecordingStartMs{0};
    std::string m_activeLocalRecordingCodec;
    void clearActiveLocalRecordingCache();
    int64_t m_prevTS{0};
    std::string m_uri;
    std::mutex m_recordStateMutex;
    std::condition_variable m_recordStateCv;
    std::atomic<bool> m_isRecordGap{false};
    std::atomic<bool> m_disableEOS{false};
    int m_channel{1};
    int m_freq{8000};
    bool m_audioSupported{false};
    VideoQueue m_videoQueue;
    shared_ptr<StreamInfo> m_stream{nullptr};
    std::unique_ptr<FPSDisplay> m_fpsDisplay = nullptr;
    std::vector<double> m_fpsVector;
    RecordState m_recordingState;
    RecordState m_originalRecordingState;
    EventType m_eventType;
    struct timeval m_eventTime;
    std::mutex m_changeStateLock;
    std::condition_variable m_condVar;
    std::atomic<bool> m_recordingStopped;
    std::string m_parserVideoName;
    bool m_fileclosed = false;
    std::atomic<int> mCurrentPipelineBuffers{0};  // Track number of buffers in pipeline
    gulong m_probe_id{0};                         // Store pad probe ID
    uint64_t m_lastPrometheusUpdate{0};           // Track last Prometheus FPS update time

    // Unified storage writer for local and cloud storage
    std::unique_ptr<UnifiedStorageWriter> m_unifiedStorageWriter;
    std::string m_storageSession;
    bool m_storageMode;
    std::string m_remotePath;

    void resetFileParams();
    bool pushBuffer(FrameInfo frameinfo);
    void insertRowInDB(int64_t& ts, string& file_name, RecordState& record_state, string& codec);
    double calculateAvgFPS(bool clear);
    friend gboolean busWatchFunc(GstBus* bus, GstMessage* message, gpointer data);
    friend gpointer muxerProcessThread(gpointer data);
    friend gpointer gmainLoopthread(gpointer data);
    void updateVideoRecordInDb(std::string filename, std::string remote_path, int64_t cur_start_time, int64_t prev_start_time,
                               bool file_closed = false);
    void pipelineReset();
    void updateInconsistencyIfAny(FrameInfo& frameinfo);
    void sendEOSAndUpdateDuration();
    bool createNewFileAndSetPlayState(FrameInfo& frameinfo);
    bool updateRecordingState();
    void logStats(FrameInfo& frameinfo);
    void setError();
    void checkIfCodecChanged(FrameInfo& frameinfo);
    void updateRecordStateToDB(std::string& m_deviceId, RecordState& m_recordingState,
                               const RecordState& changeStateTo);
};
}  // namespace nv_vms

class VideoRecordUpdater
{
   public:
    static VideoRecordUpdater& getInstance()
    {
        static VideoRecordUpdater instance;
        return instance;
    }
    void addToQueue(VideoRecordDBColumns record);
    void addInsertToQueue(VideoRecordDBColumns record);
    void start();
    void stop();

   private:
    VideoRecordUpdater() : m_done(false)
    {
        start();
    }
    ~VideoRecordUpdater()
    {
        try {
            stop();
        } catch (const std::exception& e) {
            try { LOG(error) << "Exception in ~VideoRecordUpdater: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
        } catch (...) {
            try { LOG(error) << "Unknown exception in ~VideoRecordUpdater" << endl; } catch (...) { (void)std::current_exception(); }
        }
    }

    VideoRecordUpdater(const VideoRecordUpdater&) = delete;
    VideoRecordUpdater& operator=(const VideoRecordUpdater&) = delete;

    void InsertVideoRecordLoop();
    void UpdateVideoRecordLoop();
    void logCombinedQueueSizeWarning(size_t& lastReportedSize);

    std::queue<VideoRecordDBColumns> m_insertQueue;
    std::queue<VideoRecordDBColumns> m_updateQueue;
    std::mutex m_queueMutex;
    std::condition_variable m_insertCv;
    std::condition_variable m_updateCv;
    std::atomic<bool> m_done = false;
    std::thread m_insertThread;
    std::thread m_updateThread;
};
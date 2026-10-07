/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <cstdint>
#include <memory>
#include <vector>
#include <glib.h>
#include <mutex>
#include <condition_variable>
#include <atomic>

#include "nvgstvideowriter.h"
#include "device_manager.h"
#include "vstmodule.h"
#include "Scheduler.h"
#include "libasync++/async++.h"

#define CHECK_RECORDER_INSTANCE(recorder)                             \
    do                                                                \
    {                                                                 \
        if (recorder == nullptr)                                      \
        {                                                             \
            LOG(error) << "Recorder module instance is null" << std::endl; \
            return VmsErrorCode::InvalidParameterError;               \
        }                                                             \
    } while (0)

inline constexpr const char* RECORD_API = "/api/v1/record/*";
inline constexpr const char* RECORD_STREAM_API = "/api/v1/record/stream/*";

inline constexpr const char* DEVICE_RECORD_API = "/api/device/*";

namespace nv_vms
{
inline constexpr int MAX_SCHEDULES = 20;
inline constexpr const char* KILL_ERROR_WATCH_THREAD = "KILL_ERROR_WATCH_THREAD";
    class RecordScheduler;
    enum RecordScheduleStatus
    {
        RecordScheduleStarted = 0,
        RecordScheduleON,
        RecordScheduleOFF,
        RecordScheduleFailed
    };

    enum StopRecordStatus
    {
        StopRecordError = -1,
        StopRecordSuccess = 0,
        StopRecordIgnore = 1
    };

    class StreamRecorder : public IVstModule
    {
    public:
        void recorderApis();
        VmsErrorCode handleRecordAPIrequest(const Json::Value &, const Json::Value &in, Json::Value &out, struct mg_connection *conn);
        IVstModule *createStreamRecorderObject();
        void deleteStreamRecorderObject(IVstModule *object);

        StreamRecorder(const std::string video_root, const std::string deviceId);
        StreamRecorder(std::vector<std::shared_ptr<StreamInfo>>, std::map<std::string, std::string, std::less<>> &);
        ~StreamRecorder();

        std::string recordStatus(const std::string streamId);
        VmsErrorCode getAllRecordStatus(std::map<std::string, RecordingStatusDBColumns, std::less<>> &allStatus);
        VmsErrorCode recordStatus(Json::Value &response);
        VmsErrorCode streams(Json::Value &list);
        RecordScheduleStatus startRecord(const std::string streamId, RecordState record_state);
        StopRecordStatus stopRecord(const std::string streamId, int record_state = 0);
        VmsErrorCode onEvent(const std::string &stream_id, Json::Value &response);
        VmsErrorCode addStream(std::shared_ptr<StreamInfo> stream, std::map<std::string, std::string, std::less<>> = std::map<std::string, std::string, std::less<>>());
        VmsErrorCode addStream(const std::string &stream_id, const std::string &url, const std::string &codec = "");
        VmsErrorCode removeStream(const std::string &streamId);
        std::map<const std::string, std::shared_ptr<StreamInfo>, std::less<>> getStreams();
        VmsErrorCode createNewRecordSchedule(const std::string camera_id, const std::string start_time,
                                             const std::string end_time, bool storeInDb = true);
        VmsErrorCode getRecordSchedules(const std::string &stream_id, Json::Value &response);
        VmsErrorCode deleteStreamRecordSchedule(const std::string streamId, const std::string start_time,
                                                const std::string end_time);
        VmsErrorCode getStreamRecordFiles(const std::string streamId, const std::string startTime,
                                          const std::string endTime, Json::Value &response);
        VmsErrorCode getRecordTimelines(const std::string stream_id, const std::string start_time,
                                        const std::string end_time, Json::Value &response);
        VmsErrorCode getConfiguration(Json::Value &config);
        VmsErrorCode getVersion(std::string &version);
        VmsErrorCode setDeviceRecordSchedule(const std::string device_id, const Json::Value &value, Json::Value &response);
        VmsErrorCode deleteCameraRecordSchedule(const std::string device_id, const std::string query_string,
                                                const Json::Value& value, Json::Value &response);
        VmsErrorCode GetAllRecordTimelines(const Json::Value& req_info, Json::Value &out);

        /** Live local segment being written (mux cache); empty if not recording or cloud-only. */
        VideoFileInfo getActiveLocalRecording(const std::string& streamOrSensorId);

        static void errorWatchThread (StreamRecorder* streamRecorder);
        void updatePrometheusStatus (const std::string& device_id, int status, bool increment, std::string& stream_name);
        bool changeRecordStateTo(const std::string camera_id, RecordState new_state);
        void loadSchedulesfromDb(const std::string &camera_id);

        static std::atomic<size_t> m_requiured_capacity;
#ifdef UNIT_TEST
        std::vector<guint64> getTimestamps(const std::string camera_id);
        bool getError(const std::string camera_id);
        bool isPlaying(const std::string camera_id);
        bool isRecordGap(const std::string camera_id);
        void disableEOS(const std::string camera_id);
        uint32_t getStreamCount();
        bool isAudioSupported(const std::string camera_id);
#endif
    private:
        bool onEvent(const std::string streamId);
        void performCrashRecovery();
        bool shouldPerformCrashRecovery();

        std::map<const std::string, std::shared_ptr<StreamInfo>, std::less<>> m_streams;
        std::map<const std::string, std::shared_ptr<NvGstVideoRecorder>, std::less<>> m_recorderList;
        std::string m_videoRoot;
        std::unique_ptr<RecordScheduler> m_scheduler;
        std::mutex m_recorderMutex;
        GAsyncQueue *m_qErrorDeviceID{nullptr};
        std::thread m_errorWatchThread;
        std::string m_deviceId;

        async::task<void> m_crashRecoveryTask;
        std::mutex m_crashRecoveryMutex;
        std::condition_variable m_crashRecoveryCv;
        std::atomic<bool> m_crashRecoveryTerminate{false};
    };

    class RecordScheduler
    {
        struct schedule_
        {
            RecordScheduleStatus m_isRunning;
            std::unique_ptr<Bosma::Scheduler> m_scheduler;

            explicit schedule_(std::unique_ptr<Bosma::Scheduler> bosma_scheduler_ptr)
            {
                m_scheduler = std::move(bosma_scheduler_ptr);
                m_isRunning = RecordScheduleOFF;
            }
        } typedef schedule;
        enum
        {
            at,
            cron
        } typedef schedule_type;
        std::unique_ptr<Bosma::Scheduler> m_schedule_eraser;
        std::mutex m_recorderMutex;
        std::map<std::string, std::shared_ptr<schedule>, std::less<>> m_schedule_map;
        bool removeScheduleFromMap(StreamRecorder *recorder, const std::string &key);
        long compareTimePoints(std::chrono::system_clock::time_point start_tp,
                               std::chrono::system_clock::time_point end_tp);
        void stopRecordIfScheduled(StreamRecorder *recorder, const std::string device_id);

    public:
        RecordScheduler()
        {
            m_schedule_eraser = std::make_unique<Bosma::Scheduler>(MAX_SCHEDULES);
        }
        std::mutex m_scheduleMapMutex;
        bool createNewSchedule(StreamRecorder *recorder, const std::string camera_id,
                               const std::string start_time, const std::string end_time,
                               bool storeInDb = true);
        std::vector<Json::Value> getSchedules(const std::string &camera_id);
        bool deleteStreamSchedule(StreamRecorder *recorder, const std::string &streamId,
                                  const std::string &start_time, const std::string &end_time);
    };

    inline StreamRecorder *GET_RECORDER()
    {
        return static_cast<StreamRecorder *>(ModuleLoader::getInstance()->getRecorderInstance());
    }

    /**
     * If user end time (t2) extends past the last segment end from `list`, appends the live local file from the
     * recorder when it overlaps [t1, t2] and is not already listed. No-op when GET_RECORDER() is null.
     * Defined in vst_common.cpp; builds without RECORDER_MODULE use a stub.
     */
    void getActiveLocalRecording(const std::string &streamOrSensorId, int64_t t1, int64_t t2,
                                 std::vector<VideoFileInfo> &list);
} // nv_vms

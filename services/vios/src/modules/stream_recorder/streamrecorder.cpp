/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "streamrecorder.h"
#include "logger.h"
#include "database.h"
#include "fs_utils.h"
#include "utils.h"
#include "prometheus_client/prometheus_client.h"
#include <boost/algorithm/string/predicate.hpp>
#include "network_utils.h"
#include "storage_management.h"
#include "vst_common.h"
#include "modules_apis.h"
#include "stream_event_manager.h"
#include <cstdlib>
#include <chrono>
#include <algorithm>
#include <limits>

using namespace std;
using namespace nv_vms;

constexpr int MILLISECOND = 1000;
constexpr int HUNDRED_MB = 100 * 1024 * 1024;
constexpr int CRASH_RECOVERY_RETRY_DELAY_SECONDS = 5;

typedef std::map<const std::string, std::shared_ptr<nv_vms::NvGstVideoRecorder>, std::less<>> recorder_list;
typedef std::map<const string, shared_ptr<StreamInfo>, std::less<>> record_stream_list;
namespace
{

    void setStreamAlwaysRecordingState(const string &streamId, bool status)
    {
        string sensorId = GET_DB_INSTANCE()->readStreamProperty(streamId, DBColumns::sensor_id);
        if (sensorId.empty())
        {
            LOG(warning) << "Stream " << streamId << " not found in DB, skipping always-recording update" << endl;
            return;
        }

        SensorStreamsDBColumns row;
        row.sensor_id_value = sensorId;
        row.stream_id_value = streamId;
        row.isAlwaysRecording_value = status == true ? "true" : "false";
        LOG(info) << "Setting Always Recording to " << row.isAlwaysRecording_value << endl;

        int ret = GET_DB_INSTANCE()->insertRowStream(row);
        if (ret == -1)
        {
            LOG(error) << "Error updating Stream Recording details into DB" << endl;
        }
    }

    VmsErrorCode RecorderStreamStatusCallback(const string &url,
                                              const StreamStatus newStatus,
                                              StreamEncParam& details)
    {
        if (newStatus != StreamStatus::STREAM_STATUS_STREAMING
            && newStatus != StreamStatus::STREAM_STATUS_END_OF_STREAM
            && newStatus != StreamStatus::STREAM_STATUS_REMOVED)
        {
            return VmsErrorCode::NoError;
        }
        LOG(info) << "#### RecorderStreamStatusCallback: Stream status: " << translateStreamStatusToString(newStatus) << ", url: " << url << endl;

        std::shared_ptr<DeviceManager> deviceManager =
            ModuleLoader::getInstance()->getDeviceManagerObject();
        if (!deviceManager || !deviceManager->needRecording)
        {
            LOG(warning) << "DeviceManager or needRecording is not set, skipping callback" << endl;
            return VmsErrorCode::NoError;
        }

        std::vector<shared_ptr<StreamInfo>> streamList = deviceManager->getStreamList();
        for (auto const& stream : streamList)
        {
            if (stream->live_proxy_url == url && stream->isMainStream)
            {
                int ret = 0;
                if (newStatus == StreamStatus::STREAM_STATUS_STREAMING)
                {
                    ret = vst_recorder::addStream(stream->id, stream->live_proxy_url);
                    if (ret != 0)
                    {
                        LOG(error) << "RecorderStreamStatusListener: addStream failed for: "
                                << stream->name << ", id: " << stream->id << endl;
                        return VmsErrorCode::VMSInternalError;
                    }
                    else
                    {
                        LOG(info) << "RecorderStreamStatusListener: Added stream to recorder: "
                                << stream->id << endl;
                    }
                }
                else if (newStatus == StreamStatus::STREAM_STATUS_END_OF_STREAM
                         || newStatus == StreamStatus::STREAM_STATUS_REMOVED)
                {
                    ret = vst_recorder::removeStream(stream->id);
                    if (ret != 0)
                    {
                        LOG(error) << "RecorderStreamStatusListener: removeStream failed for: "
                                << stream->name << ", id: " << stream->id << endl;
                        return VmsErrorCode::VMSInternalError;
                    }
                    else
                    {
                        LOG(info) << "RecorderStreamStatusListener: Removed stream from recorder: "
                                << stream->id << endl;
                    }
                }
                break;
            }
        }
        return VmsErrorCode::NoError;
    }

} // unnamed namespace

std::atomic<size_t> StreamRecorder::m_requiured_capacity{0};

StreamRecorder::StreamRecorder(const std::string video_root, const string deviceId)
    : m_videoRoot(video_root), m_deviceId(deviceId)
{
    LOG(info) << "Creating StreamRecorder m_deviceId:" << m_deviceId << endl;

    static std::once_flag registerFlag;
    std::call_once(registerFlag, []() {
        StreamEventManager& eventManager = StreamEventManager::getInstance();
        eventManager.registerCallback(RecorderStreamStatusCallback);

        LOG(info) << "Registered StreamEventManager and recorder stream status callback" << endl;
    });

    if (shouldPerformCrashRecovery())
    {
        m_crashRecoveryTask = async::spawn([this]()
        {
            performCrashRecovery();
        });
    }

    m_scheduler = std::make_unique<RecordScheduler>();

    m_qErrorDeviceID = g_async_queue_new();
    m_errorWatchThread = std::thread(errorWatchThread, this);
    recorderApis();
}

StreamRecorder::~StreamRecorder()
{
    LOG(info) << __METHOD_NAME__ << endl;

    {
        std::lock_guard<std::mutex> lock(m_crashRecoveryMutex);
        m_crashRecoveryTerminate = true;
    }
    m_crashRecoveryCv.notify_all();

    // Wait for crash recovery task to complete if it was started
    if (m_crashRecoveryTask.valid())
    {
        try
        {
            m_crashRecoveryTask.get();
        }
        catch (const std::exception &e)
        {
            LOG(warning) << "Crash recovery task exception during shutdown: " << e.what() << endl;
        }
    }

    try
    {
        string killThread = KILL_ERROR_WATCH_THREAD;
        g_async_queue_push(m_qErrorDeviceID, &killThread);
        if (m_errorWatchThread.joinable())
        {
            m_errorWatchThread.join();
        }
        m_streams.clear();
    }
    catch (const std::exception &e)
    {
        LOG(warning) << "Exception during StreamRecorder destruction: " << e.what() << endl;
    }

    if (m_qErrorDeviceID)
    {
        g_async_queue_unref(m_qErrorDeviceID);
        m_qErrorDeviceID = nullptr;
    }
}

bool StreamRecorder::shouldPerformCrashRecovery()
{
#if defined(MONOLITH_MODULE)
    LOG(info) << "Monolithic mode detected - will perform crash recovery" << endl;
    return true;
#else
    // Modular/scaling mode - only RECORDER_MODULE is defined
    // Check env variable to determine if this instance should perform recovery
    const char* envValue = std::getenv("ENABLE_RECORDING_RECOVERY");
    if (envValue != nullptr)
    {
        std::string value(envValue);
        std::transform(value.begin(), value.end(), value.begin(), ::tolower);
        if (value == "true" || value == "1" || value == "yes" || value == "on")
        {
            LOG(info) << "ENABLE_RECORDING_RECOVERY=" << envValue << " - will perform recording recovery" << endl;
            return true;
        }
    }
    LOG(info) << "ENABLE_RECORDING_RECOVERY not set or disabled - skipping recording recovery" << endl;
    return false;
#endif
}

void StreamRecorder::performCrashRecovery()
{
    LOG(info) << "Starting async crash recovery task" << endl;

    while (!m_crashRecoveryTerminate)
    {
        std::vector<VideoRecordDBColumns> rows;
        int result = GET_DB_INSTANCE()->queryCrashedRecordings(rows);
        if (result >= 0)
        {
            // Collect all file paths for batch protection
            std::vector<string> filePaths;
            filePaths.reserve(rows.size());
            for (const auto &row : rows)
            {
                filePaths.push_back(row.filepath_value);
            }

            // Phase 1: Get durations in parallel using async++ tasks
            std::vector<async::task<void>> durationTasks;
            durationTasks.reserve(rows.size());
            LOG(warning) << "Gstreamer duration retrieval tasks starting for " << rows.size() << " files" << endl;
            for (size_t i = 0; i < rows.size(); ++i)
            {
                if (m_crashRecoveryTerminate)
                {
                    break;
                }
                durationTasks.push_back(async::spawn([&rows, i, this]()
                {
                    if (m_crashRecoveryTerminate)
                    {
                        return;
                    }
                    guint64 duration = getMediaFileDuration(rows[i].filepath_value);
                    rows[i].duration_value = duration;
                }));
            }

            // Wait for all duration retrieval tasks to complete
            for (auto &task : durationTasks)
            {
                task.wait();
            }
            LOG(warning) << "Gstreamer duration retrieval tasks completed" << endl;

            // Log warnings for rows where duration could not be retrieved
            for (const auto &row : rows)
            {
                if (row.duration_value == 0)
                {
                    LOG(warning) << "Could not get duration for: " << row.filepath_value << " (file may not exist or is corrupted)" << endl;
                }
            }

            // Phase 2: Batch update database for all rows with valid duration
            int updatedCount = GET_DB_INSTANCE()->updateVideoRecordDurationBatch(rows);
            if (updatedCount < 0)
            {
                LOG(error) << "Failed to batch update video record durations" << endl;
            }
            else
            {
                LOG(info) << "Crash recovery completed successfully. Fixed " << updatedCount << " of " << rows.size() << " record(s)" << endl;
            }
            return;
        }
        else if (result == -1)
        {
            // Database connection error - retry after delay
            LOG(warning) << "Crash recovery failed due to DB connection error. "
                        << "Retrying in " << CRASH_RECOVERY_RETRY_DELAY_SECONDS << " seconds..." << endl;

            std::unique_lock<std::mutex> lock(m_crashRecoveryMutex);
            if (m_crashRecoveryCv.wait_for(lock,
                    std::chrono::seconds(CRASH_RECOVERY_RETRY_DELAY_SECONDS),
                    [this] { return m_crashRecoveryTerminate.load(); }))
            {
                LOG(info) << "Crash recovery terminated during retry wait" << endl;
                return;
            }
        }
        else
        {
            LOG(error) << "Crash recovery failed with non-recoverable error. Giving up." << endl;
            return;
        }
    }

    LOG(info) << "Crash recovery terminated by shutdown signal" << endl;
}

extern "C" void *createStreamRecorderObject()
{
    return new StreamRecorder(GET_CONFIG().recorded_video_root, ModuleLoader::getInstance()->getDeviceId());
}

extern "C" void deleteStreamRecorderObject(StreamRecorder *object)
{
    delete object;
}

string StreamRecorder::recordStatus(const string streamId)
{
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    RecordState current_state = UnknownState;
    record_stream_list::iterator it = m_streams.find(streamId);
    if (it != m_streams.end())
    {
        recorder_list::iterator map_it = m_recorderList.find(streamId);
        if (map_it != m_recorderList.end())
        {
            shared_ptr<NvGstVideoRecorder> recorder = map_it->second;
            if (recorder)
            {
                current_state = recorder->getRecordStatus(streamId);
            }
            else
            {
                return recording_off;
            }
        }
        else
        {
            return recording_off;
        }
    }
    else
    {
        LOG(verbose) << "Stream not present in Recorder " << streamId << endl;
        return recording_off;
    }

    LOG(verbose2) << "Current State = " << translateRecordStateToString(current_state) << endl;
    return translateRecordStateToString(current_state);
}

VmsErrorCode StreamRecorder::getAllRecordStatus(std::map<std::string, RecordingStatusDBColumns, std::less<>> &allStatus)
{
    if (GET_DB_INSTANCE()->getRecordingStatus(allStatus, std::nullopt) == VmsErrorCode::NoError)
    {
        return VmsErrorCode::NoError;
    }
    return VmsErrorCode::VMSInternalError;
}

VmsErrorCode StreamRecorder::recordStatus(Json::Value &response)
{
    std::map<std::string, RecordingStatusDBColumns, std::less<>> allStatus;
    VmsErrorCode result = getAllRecordStatus(allStatus);

    if (result != VmsErrorCode::NoError)
    {
        LOG(error) << "Failed to get all record status" << endl;
        return VmsErrorCode::VMSInternalError;
    }

    vector<shared_ptr<SensorInfo>> sensors;
    vector<SensorDetailsDBColumns> allSensorStatus = GET_DB_INSTANCE()->readAllSensorSatus("");
    for (uint32_t cnt = 0; cnt < allSensorStatus.size(); cnt++)
    {
        string stream_id = allSensorStatus[cnt].sensor_id_value; // sensorId is same as main streamdId
        Json::Value resp;
        std::string current_state = recording_status_unknown;
        if (allSensorStatus[cnt].sensorStatus_value == SensorStatusOnline && allSensorStatus[cnt].httpStatus_value == 200)
        {
            auto status_it = allStatus.find(stream_id);
            if (status_it != allStatus.end())
            {
                RecordState current_state_in_db = static_cast<RecordState>(status_it->second.recordingStatus_value);
                current_state = translateRecordStateToString(current_state_in_db);
            }
            else
            {
                LOG(warning) << "Status not found for stream " << stream_id;
                current_state = recording_off;
            }
        }
        else
        {
            current_state = recording_status_unknown;
        }
        resp["id"] = stream_id;
        resp["recording_status"] = current_state;
        response[stream_id] = resp;
    }

    return VmsErrorCode::NoError;
}

void StreamRecorder::errorWatchThread(StreamRecorder *streamRecorder)
{
    while (true)
    {
        /* POP from queue */
        gpointer popedData = g_async_queue_pop(streamRecorder->m_qErrorDeviceID);
        std::string &popedString = *static_cast<std::string *>(popedData);
        /* Check if application wants to kill the thread */
        if (popedString == KILL_ERROR_WATCH_THREAD)
        {
            LOG(info) << "Stopping errorWatchThread" << endl;
            break;
        }
        LOG(error) << "Stop Record for Camera ID = " << popedString << endl;
        streamRecorder->stopRecord(popedString, ERROR);
    }
}

RecordScheduleStatus StreamRecorder::startRecord(const string streamId, RecordState record_state)
{
    LOG(info) << "Start record for stream id = " << streamId << " in "
              << translateRecordStateToString(record_state) << endl;
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    if (m_deviceId.empty())
    {
        m_deviceId = ModuleLoader::getInstance()->getDeviceId();
    }

    record_stream_list::iterator it = m_streams.find(streamId);
    RecordScheduleStatus ret = RecordScheduleON;
    if (it != m_streams.end())
    {
        /* Add stream into stream_monitor */
        StreamMonitor* streamMonitor = StreamMonitor::getInstance();
        if (streamMonitor)
        {
            streamMonitor->addStream(it->second);
        }

        recorder_list::iterator it_map = m_recorderList.find(streamId);
        if (it_map == m_recorderList.end())
        {
            shared_ptr<StreamInfo> stream = it->second;
            if (stream)
            {
                LOG(info) << "Creating RTSP stream recorder for url: " << secureUrlForLogging(stream->live_proxy_url) << endl;
                ret = RecordScheduleStarted;
                bool increment = true;
                RecordStates state = RECORDING;
                const string id = stream->id;
                const string url = stream->live_proxy_url;

                shared_ptr<NvGstVideoRecorder> recorder = NvGstVideoRecorder::Create(stream, m_qErrorDeviceID, record_state);
                recorder->updateRecordingStatus(streamId, record_state, stream->sensorId);
                m_recorderList[id] = recorder;
                string stream_name = stream->name;
                updatePrometheusStatus(streamId, state, increment, stream_name);
            }
            else
            {
                LOG(error) << "Couldn't find stream for " << streamId << endl;
                ret = RecordScheduleFailed;
            }
        }
        else if (it_map != m_recorderList.end())
        {
            std::shared_ptr<nv_vms::NvGstVideoRecorder> recorder = it_map->second;
            if (recorder)
            {
                RecordState current_state = recorder->getRecordStatus(streamId);
                if (current_state == User || current_state == AlwaysOn)
                {
                    LOG(warning) << "Current State is " << current_state << ", no need to start recording again" << endl;
                }
                else if (current_state == Error)
                {
                    LOG(warning) << "Current State is " << current_state << "cannot start recording" << endl;
                }
                else
                {
                    LOG(warning) << "Changing state from " << translateRecordStateToString(current_state)
                                 << " to " << translateRecordStateToString(record_state)
                                 << endl;
                    int result = recorder->changeRecordStateTo(record_state);
                    if (result == 0)
                    {
                        recorder->updateRecordingStatus(streamId, record_state);
                    }
                    ret = RecordScheduleStarted;
                }
            }
        }
    }
    else
    {
        LOG(error) << "Cannot start recording: Stream not present in recorder" << endl;
        ret = RecordScheduleFailed;
        return ret;
    }
    if (ret == RecordScheduleON)
    {
        LOG(info) << "Recording is already in progress for stream id = " << streamId << endl;
    }
    if (record_state == User || record_state == AlwaysOn)
    {
        setStreamAlwaysRecordingState(streamId, true);
    }
    return ret;
}

bool StreamRecorder::onEvent(const string streamId)
{
    LOG(info) << "Handle Event for stream id = " << streamId << endl;
    bool ret = true;
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    recorder_list::iterator it = m_recorderList.find(streamId);
    if (it != m_recorderList.end())
    {
        std::shared_ptr<nv_vms::NvGstVideoRecorder> recorder = it->second;
        if (recorder->onEvent() == -1)
        {
            LOG(warning) << "Ignoring handling event for " << streamId << endl;
            ret = false;
        }
    }
    else
    {
        LOG(error) << "Writer not found for " << streamId << endl;
        ret = false;
    }
    return ret;
}

VmsErrorCode StreamRecorder::onEvent(const string &stream_id, Json::Value &response)
{
    if (!GET_CONFIG().event_recording)
    {
        string error_message = string("Event Recoding config is disabled");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
        return VmsErrorCode::InvalidParameterError;
    }

    bool ret = onEvent(stream_id);
    if (ret == false)
    {
        LOG(error) << "Recorder onEvent failed" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Recorder onEvent failed")
        return VmsErrorCode::VMSInternalError;
    }
    response = ret;
    return VmsErrorCode::NoError;
}

StopRecordStatus StreamRecorder::stopRecord(const string streamId, int record_state /* 0 */)
{
    LOG(info) << "Stop record for stream id = " << streamId << endl;
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    recorder_list::iterator it = m_recorderList.find(streamId);
    if (it != m_recorderList.end())
    {
        std::shared_ptr<nv_vms::NvGstVideoRecorder> recorder = it->second;
        if (recorder)
        {
            if (recorder->getRecordStatus(streamId) == User || recorder->getRecordStatus(streamId) == AlwaysOn)
            {
                setStreamAlwaysRecordingState(streamId, false);
            }
            if (GET_CONFIG().event_recording == false || record_state == UnknownState)
            {
                recorder->updateRecordingStatus(streamId, OFF);
                m_recorderList.erase(it);
            }
            else
            {
                if (recorder->getRecordStatus(streamId) == Event)
                {
                    LOG(warning) << "No need to change state as previous and current are same" << endl;
                    return StopRecordIgnore;
                }
                LOG(warning) << "Stopping record, but keeping pipeline running on Event"
                             << " and Changing state from " << translateRecordStateToString(recorder->getRecordStatus(streamId))
                             << " to " << translateRecordStateToString(Event)
                             << endl;
                int result = recorder->changeRecordStateTo(Event);
                if (result == 0)
                {
                    recorder->updateRecordingStatus(streamId, Event);
                }
                return StopRecordSuccess;
            }
        }
        bool increment = false;
        record_stream_list::iterator it_map = m_streams.find(streamId);
        string stream_name;
        if (it_map != m_streams.end())
        {
            shared_ptr<StreamInfo> stream = it_map->second;
            stream_name = stream->name;
        }
        updatePrometheusStatus(streamId, record_state, increment, stream_name);
        LOG(info) << "Recording stopped for stream id = " << streamId << endl;
        return StopRecordSuccess;
    }
    else
    {
        LOG(error) << "Cannot stop recording: Stream not present in recorder" << endl;
    }
    LOG(info) << "Recording is already stopped for stream id = " << streamId << endl;
    return StopRecordError;
}

VmsErrorCode StreamRecorder::addStream(shared_ptr<StreamInfo> stream, std::map<std::string, std::string, std::less<>> opts)
{
    if (stream.get() == nullptr)
    {
        LOG(error) << "stream object is null" << endl;
        return VmsErrorCode::InvalidParameterError;
    }
    {
        std::lock_guard<std::mutex> guard(m_recorderMutex);
        if (m_streams.find(stream->id) != m_streams.end())
        {
            LOG(warning) << "Stream already present, skip adding: " << stream->id << endl;
            return VmsErrorCode::NoError;
        }
    }
    string url = stream->live_proxy_url;
    if (url.empty() == false)
    {
        StreamRecorder::m_requiured_capacity += HUNDRED_MB;

        // Check if minimum space is available
        if (vst_storage::checkStorageCapacity(m_requiured_capacity) == false)
        {
            StreamRecorder::m_requiured_capacity -= HUNDRED_MB;
            LOG(error) << "Cannot add stream in recorder, insufficient disk capacity" << endl;
            return VmsErrorCode::VMSInsufficientStorage;
        }
        {
            std::lock_guard<std::mutex> guard(m_recorderMutex);
            m_streams[stream->id] = stream;
        }
        loadSchedulesfromDb(stream->id);
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::addStream(const string &stream_id, const string &url, const string &codec)
{
    if (stream_id.empty() || url.empty())
    {
        LOG(error) << "Getting streamId/Url empty" << endl;
        return VmsErrorCode::InvalidParameterError;
    }

    bool found = false;
    shared_ptr<StreamInfo> stream_to_add(new StreamInfo);
    std::shared_ptr<DeviceManager> m_deviceMngr = ModuleLoader::getInstance()->getDeviceManagerObject();
    vector<shared_ptr<SensorInfo>> sensors = m_deviceMngr->getSensorList();
    for (uint32_t i = 0; i < sensors.size(); i++)
    {
        shared_ptr<SensorInfo> sensor = sensors[i];
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            std::shared_ptr<StreamInfo> stream = streams[j];
            if (stream->id == stream_id)
            {
                stream_to_add  = stream;
                stream->live_proxy_url = url;
                found = true;
                break;
            }
        }
    }
    if (found == false)
    {
        /* Create & get the stream details from database */
        shared_ptr<SensorInfo> sensor_to_add(new SensorInfo);

        stream_to_add->id = stream_to_add->sensorId = stream_id;
        stream_to_add->live_proxy_url = url;
        stream_to_add->name = stream_to_add->id;
        stream_to_add->isMainStream = true;
        SensorDetailsDBColumns row = GET_DB_INSTANCE()->readSensorDetails("", stream_id);
        GET_DB_INSTANCE()->getSensorInfoFromDB(sensor_to_add, row);
        SensorStreamsDBColumns stream_row = GET_DB_INSTANCE()->readSensorStreams(stream_id);
        /* If DB entry for that particular sensor is present */
        if (row.sensor_id_value.empty() == false)
        {
            stream_to_add->name = row.name_value;         
            stream_to_add->isMainStream = stream_row.isMainStream_value == "true" ? true : false;
            stream_to_add->duration = stringToInt(stream_row.duration_value, 0);

            SensorVideoEncoderSettingsValues &enc_values = stream_to_add->getvideoEncoderValues();
            enc_values.resolution = stream_row.resolution_value;
            enc_values.encoding = codec.empty() ? stream_row.encoding_value : codec;
            enc_values.encodingInterval = stream_row.encodingInterval_value;
            enc_values.frameRate = stream_row.frameRate_value;
            enc_values.bitrate = stream_row.bitrate_value;
            enc_values.isBframesPresent = (stream_row.isBframesPresent_value == 1);
        }
        /* If only Recorder as a standalone module is running */
        else
        {
            sensor_to_add->id = stream_to_add->id;
            sensor_to_add->name = stream_to_add->id;
            stream_to_add->isMainStream = true;
            stream_to_add->duration = -1;
            sensor_to_add->updateSensorStatus(SensorStatusStreaming);
            sensor_to_add->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
            sensor_to_add->type = SENSOR_TYPE_RTSP;
        }
        sensor_to_add->addStreams (stream_to_add);
        m_deviceMngr->addOrUpdateSensor(*sensor_to_add);
    }

    int ret = addStream(stream_to_add);
    if (ret != 0)
    {
        LOG(error) << "Failed to add stream in recorder" << endl;
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::removeStream(const string &streamId)
{
    /* as remove stream is called, we should stop all pipelines related to this streamID */
    stopRecord(streamId, UnknownState); // stop recording, if recording

    /* Remove stream from stream_monitor */
    StreamMonitor* streamMonitor = StreamMonitor::getInstance();
    streamMonitor->removeStream(streamId);

    // remove recording schedules entry
    std::vector<VideoRecordScheduleDBColumns> rowArray = GET_DB_INSTANCE()
                                                             ->readVideoRecordSchedules(streamId);
    for (uint32_t i = 0; i < rowArray.size(); i++)
    {
        VideoRecordScheduleDBColumns row = rowArray[i];
        deleteStreamRecordSchedule(row.stream_id_value, row.start_time_value,
                                   row.end_time_value);
    }

    {
        std::lock_guard<std::mutex> guard(m_recorderMutex);
        // Reduce m_requiured_capacity if MainStream and present in map
        if (m_streams.find(streamId) != m_streams.end())
        {
            StreamRecorder::m_requiured_capacity -= HUNDRED_MB;
        }
        // Erase stream from map
        m_streams.erase(streamId);
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::createNewRecordSchedule(const string streamId,
                                                     const string start_time,
                                                     const string end_time,
                                                     bool storeInDb /*=true*/)
{
    bool ret = m_scheduler->createNewSchedule(this, streamId, start_time, end_time, storeInDb);
    if (ret == false)
    {
        LOG(error) << "createNewRecordSchedule failed with ret error:" << ret << endl;
        return VmsErrorCode::VMSInternalError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::getRecordSchedules(const string &stream_id, Json::Value &response)
{
    std::vector<Json::Value> schedules_array = m_scheduler->getSchedules(stream_id);
    for (uint32_t i = 0; i < schedules_array.size(); i++)
    {
        Json::Value value = schedules_array[i];
        response.append(value);
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::deleteStreamRecordSchedule(const string streamId,
                                                        const string start_time,
                                                        const string end_time)
{
    bool ret = false;
    if (start_time.empty() && end_time.empty())
    {
        std::vector<VideoRecordScheduleDBColumns> rowArray = GET_DB_INSTANCE()->readVideoRecordSchedules(streamId);
        for (uint32_t i = 0; i < rowArray.size(); i++)
        {
            VideoRecordScheduleDBColumns row = rowArray[i];
            ret = m_scheduler->deleteStreamSchedule(this, row.stream_id_value, row.start_time_value,
                                                    row.end_time_value);
            return ret == true ? VmsErrorCode::NoError : VmsErrorCode::VMSInternalError;
        }
    }
    ret = m_scheduler->deleteStreamSchedule(this, streamId, start_time, end_time);
    return ret == true ? VmsErrorCode::NoError : VmsErrorCode::VMSInternalError;
}

void StreamRecorder::loadSchedulesfromDb(const string &streamId)
{
    LOG(info) << "Loading Video Record Schedules from DB for " << streamId << endl;

    // Check if Recording is supposed to be Always_ON.
    DeviceConfig config = GET_CONFIG();

    string db_isAlwaysRecording = GET_DB_INSTANCE()->readStreamProperty(streamId, SensorStreamsDBColumns::isAlwaysRecording);
    if (config.always_recording == true)
    {
        LOG(warning) << "Starting Recording in AlwaysOn" << endl;
        if (startRecord(streamId, AlwaysOn) != RecordScheduleStarted)
        {
            LOG(error) << "Error in starting recording for " << streamId << endl;
        }
    }
    else if (db_isAlwaysRecording == "true")
    {
        LOG(warning) << "Starting Recording in User" << endl;
        if (startRecord(streamId, User) != RecordScheduleStarted)
        {
            LOG(error) << "Error in starting recording for " << streamId << endl;
        }
    }
    else if (config.event_recording == true)
    {
        LOG(warning) << "Starting Recording in Event" << endl;
        if (startRecord(streamId, Event) != RecordScheduleStarted)
        {
            LOG(error) << "Error in starting recording for " << streamId << endl;
        }
    }
    // Load Recording Schedules.

    std::vector<VideoRecordScheduleDBColumns> rowArray = GET_DB_INSTANCE()->readVideoRecordSchedules(streamId);
    for (uint32_t i = 0; i < rowArray.size(); i++)
    {
        VideoRecordScheduleDBColumns row = rowArray[i];
        createNewRecordSchedule(row.stream_id_value, row.start_time_value, row.end_time_value, false);
    }
    LOG(verbose) << "Reading schedule from DB over" << endl;
}

void StreamRecorder::updatePrometheusStatus(const string &device_id, int record_state, bool increment, std::string &stream_name)
{
    if (increment)
    {
        GET_PROMETHEUS()->incrementRecordingStreams();
        GET_PROMETHEUS()->updateRecordingStatus(record_state, stream_name);
    }
    else
    {
        GET_PROMETHEUS()->decrementRecordingStreams();
        GET_PROMETHEUS()->updateRecordingStatus(record_state, stream_name);
        GET_PROMETHEUS()->updateRecorderFps(0.0, device_id);
    }
}

std::map<const string, shared_ptr<StreamInfo>, std::less<>> StreamRecorder::getStreams()
{
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    return m_streams;
}

VmsErrorCode StreamRecorder::streams(Json::Value &list)
{
    std::map<const string, shared_ptr<StreamInfo>, std::less<>> streams = getStreams();
    for (auto stream : streams)
    {
        shared_ptr<StreamInfo> stream_info = stream.second;
        Json::Value jstreams;
        Json::Value stream_data;
        Json::Value metadata;
        jstreams[stream_info->sensorId] = Json::arrayValue;
        stream_data["name"] = stream_info->name;
        stream_data["streamId"] = stream_info->id;
        stream_data["isMain"] = stream_info->isMainStream;
        stream_data["storageLocation"] = StreamStorageTypeToString(stream_info->storageLocation);
        stream_data["url"] = stream_info->live_proxy_url;
        metadata["resolution"] = stream_info->settings.encoderValues.resolution.getString();
        metadata["codec"] = stream_info->settings.encoderValues.encoding;
        metadata["bitrate"] = stream_info->settings.encoderValues.bitrate;
        metadata["framerate"] = stream_info->settings.encoderValues.frameRate;
        metadata["govlength"] = stream_info->settings.encoderValues.govLength;
        stream_data["metadata"] = metadata;
        jstreams[stream_info->sensorId].append(stream_data);
        list.append(jstreams);
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::GetAllRecordTimelines(const Json::Value& req_info, Json::Value &out)
{
    return vst_common::GetAllRecordTimelines(req_info, out);
}

VmsErrorCode StreamRecorder::getRecordTimelines(const string stream_id, const string start_time,
                                                const string end_time, Json::Value &response)
{
    return vst_common::getRecordTimelines(stream_id, start_time, end_time, response);
}

VmsErrorCode StreamRecorder::getStreamRecordFiles(const string streamId, const string startTime,
                                                  const string endTime, Json::Value &response)
{
    int64_t startTimestamp = 0;
    int64_t endTimeStamp = 0;
    try
    {
        if (!startTime.empty())
        {
            startTimestamp = isoToEpoch(startTime);
        }
        if (!endTime.empty())
        {
            endTimeStamp = isoToEpoch(endTime);
        }
    }
    catch (...)
    {
        LOG(error) << "string to time_t conversion failed" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    /* Assume endTimeStamp as max int
    ** so that all records will be fetched
    */
    if (endTimeStamp == 0)
    {
        endTimeStamp = std::numeric_limits<int64_t>::max();
    }

    std::vector<VideoRecordDBColumns> videoInfo = GET_DB_INSTANCE()->getVideoRecordFilePaths(streamId, startTimestamp, endTimeStamp);
    for (uint32_t i = 0; i < videoInfo.size(); i++)
    {
        Json::Value Info;
        Info["file_path"] = videoInfo[i].filepath_value;
        Info["start_time"] = static_cast<Json::Value::UInt64>(videoInfo[i].start_time_value);
        Info["file_duration"] = videoInfo[i].duration_value;
        response.append(Info);
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::getConfiguration(Json::Value &out)
{
    out["httpPort"] = GET_CONFIG().http_port;
    out["recordedVideoDirRoot"] = GET_CONFIG().recorded_video_root;
    out["vstDataPath"] = GET_CONFIG().vst_data_path;
    out["alwaysRecording"] = GET_CONFIG().always_recording;
    out["useHttps"] = GET_CONFIG().use_https;
    out["useHttpDigestAuthentication"] = GET_CONFIG().use_http_digest_authentication;
    out["useMultiUser"] = GET_CONFIG().use_multi_user;
    out["enableUserCleanup"] = GET_CONFIG().enable_user_cleanup;
    out["sessionMaxAgeSec"] = GET_CONFIG().session_max_age_sec;
    out["multiUserExtraOptions"] = vectorToString(GET_CONFIG().multi_user_extra_options);
    out["eventRecordLengthSecs"] = GET_CONFIG().event_record_length_secs;
    out["recordBufferLengthSecs"] = GET_CONFIG().record_buffer_length_secs;
    return VmsErrorCode::NoError;
}

VmsErrorCode StreamRecorder::getVersion(string &version)
{
    version = STREAM_RECORDER_VERSION;
    return VmsErrorCode::NoError;
}

#ifdef UNIT_TEST
bool StreamRecorder::getError(const string streamId)
{
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    recorder_list::iterator it = m_recorderList.find(streamId);
    if (it != m_recorderList.end())
    {
        shared_ptr<NvGstVideoRecorder> recorder = it->second;
        return recorder != nullptr ? recorder->getError() : true;
    }
    return true;
}

bool StreamRecorder::isPlaying(const string streamId)
{
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    recorder_list::iterator it = m_recorderList.find(streamId);
    if (it != m_recorderList.end())
    {
        shared_ptr<NvGstVideoRecorder> recorder = it->second;
        return recorder != nullptr ? recorder->isPlaying() : false;
    }
    return false;
}

bool StreamRecorder::isRecordGap(const string streamId)
{
    recorder_list::iterator it = m_recorderList.find(streamId);
    if (it != m_recorderList.end())
    {
        shared_ptr<NvGstVideoRecorder> recorder = it->second;
        return recorder != nullptr ? recorder->isRecordGap() : false;
    }
    return false;
}

void StreamRecorder::disableEOS(const string streamId)
{
    recorder_list::iterator it = m_recorderList.find(streamId);
    if (it != m_recorderList.end())
    {
        shared_ptr<NvGstVideoRecorder> recorder = it->second;
        if (recorder)
        {
            recorder->disableEOS();
        }
    }
}

uint32_t StreamRecorder::getStreamCount()
{
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    return m_streams.size();
}

bool StreamRecorder::isAudioSupported(const string streamId)
{
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    recorder_list::iterator it = m_recorderList.find(streamId);
    if (it != m_recorderList.end())
    {
        shared_ptr<NvGstVideoRecorder> recorder = it->second;
        return recorder != nullptr ? recorder->isAudioSupported() : false;
    }
    return false;
}

#endif

bool RecordScheduler::createNewSchedule(StreamRecorder *recorder,
                                        const string streamId,
                                        const string start_time_utc,
                                        const string end_time_utc,
                                        bool storeInDb /*=true*/)
{
    LOG(verbose) << "Record Scheduler called streamId: " << streamId
                 << "\nfor start time: " << start_time_utc << " end time: " << end_time_utc << endl;

    string start_time = getUTCtoLocalTime(start_time_utc);
    string end_time = getUTCtoLocalTime(end_time_utc);

    LOG(verbose) << "Record Scheduler called streamId: " << streamId
                 << "\nfor Local start time: " << start_time
                 << " Local end time: " << end_time << endl;

    schedule_type start_schedule_type, end_schedule_type;
    chrono::system_clock::time_point start_tp, end_tp;
    string unique_id = streamId + "|" + start_time + "|" + end_time;
    if (m_schedule_map.find(unique_id) != m_schedule_map.end())
    {
        LOG(error) << "Schedule already exists" << endl;
        return false;
    }

    m_schedule_map[unique_id] = std::make_shared<schedule>(std::make_unique<Bosma::Scheduler>(2));
    std::shared_ptr<schedule> current_schedule = m_schedule_map[unique_id];

    // Schedule start of recording
    try
    {
        start_tp = current_schedule->m_scheduler->cron(start_time, [=]()
                                                       {
            std::lock_guard<std::mutex> mlock(m_recorderMutex);
            current_schedule->m_isRunning = recorder->startRecord(streamId, Schedule); });
        LOG(verbose) << "Used CRON scheduler for start time" << endl;
        start_schedule_type = cron;
    }
    catch (Bosma::BadCronExpression &)
    {
        try
        {
            start_tp = current_schedule->m_scheduler->at(start_time, [=]()
                                                         {
                std::lock_guard<std::mutex> mlock(m_recorderMutex);
                current_schedule->m_isRunning = recorder->startRecord(streamId, Schedule); });
            LOG(verbose) << "Used AT scheduler for start time" << endl;
            start_schedule_type = at;
        }
        catch (std::runtime_error &err)
        {
            LOG(error) << err.what() << endl;
            removeScheduleFromMap(recorder, unique_id);
            return false;
        }
    }

    // Schedule end of recording
    try
    {
        if (start_schedule_type == at)
        {
            end_tp = current_schedule->m_scheduler->cron(end_time, [=]()
                                                         {
                std::lock_guard<std::mutex> mlock(m_recorderMutex);
                if (current_schedule->m_isRunning == RecordScheduleStarted)
                {
                    stopRecordIfScheduled(recorder, streamId);
                    current_schedule->m_isRunning = RecordScheduleOFF;
                }
                m_schedule_eraser->in(5s, [=]() {
                    LOG(info) << "Erasing schedule: " << unique_id <<endl;
                    deleteStreamSchedule(recorder, streamId, start_time_utc, end_time_utc);
                }); });
        }
        else
        {
            end_tp = current_schedule->m_scheduler->cron(end_time, [=]()
                                                         {
                std::lock_guard<std::mutex> mlock(m_recorderMutex);
                if (current_schedule->m_isRunning == RecordScheduleStarted)
                {
                    stopRecordIfScheduled(recorder, streamId);
                    current_schedule->m_isRunning = RecordScheduleOFF;
                } });
        }
        LOG(verbose) << "Used CRON scheduler for end time" << endl;
        end_schedule_type = cron;
    }
    catch (Bosma::BadCronExpression &)
    {
        try
        {
            end_tp = current_schedule->m_scheduler->at(end_time, [=]()
                                                       {
                std::lock_guard<std::mutex> mlock(m_recorderMutex);
                if (current_schedule->m_isRunning == RecordScheduleStarted)
                {
                    stopRecordIfScheduled(recorder, streamId);
                    current_schedule->m_isRunning = RecordScheduleOFF;
                }
                m_schedule_eraser->in(5s, [=]() {
                    LOG(info) << "Erasing schedule: " << unique_id <<endl;
                    deleteStreamSchedule(recorder, streamId, start_time_utc, end_time_utc);
                }); });
            LOG(verbose) << "Used AT scheduler for end time" << endl;
            end_schedule_type = at;
        }
        catch (std::runtime_error &err)
        {
            LOG(error) << err.what() << endl;
            removeScheduleFromMap(recorder, unique_id);
            return false;
        }
    }

    if (compareTimePoints(start_tp, end_tp) <= 0)
    {
        if (start_schedule_type == cron && end_schedule_type == cron)
        {
            // For cron schedules, when end_tp < start_tp, it would mean that
            // current time is between start and end of schedule, so start recording.
            // Example 1: start-> 0 14 * * 1;  end-> 0 13 * * 1;
            // current_time-> 14:00 hrs Tuesday
            // This schedule starts at 14 hrs on Monday and ends at 13 hours on next Monday.
            // Example 2: start-> 0 13 * * 1;  end-> 0 15 * * 1;
            // current_time-> 14:00 hrs Monday
            // In this case, only start time has passed. So, start recording.
            std::lock_guard<std::mutex> mlock(m_recorderMutex);
            current_schedule->m_isRunning = recorder->startRecord(streamId, Schedule);
        }
        else
        {
            LOG(error) << "End time of schedule is less than or equal to Start time" << endl;
            removeScheduleFromMap(recorder, unique_id);
            return false;
        }
    }

    if (storeInDb)
    {
        VideoRecordScheduleDBColumns row;
        row.sensor_id_value = streamId;
        row.stream_id_value = streamId;
        row.start_time_value = start_time_utc;
        row.end_time_value = end_time_utc;
        int ret = GET_DB_INSTANCE()->insertRowVideoRecordSchedule(row);
        if (ret == -1)
        {
            LOG(error) << "Error adding Schedule details into DB" << endl;
            removeScheduleFromMap(recorder, unique_id);
            return false;
        }
    }

    return true;
}

bool RecordScheduler::removeScheduleFromMap(StreamRecorder *recorder, const string &unique_id)
{
    std::lock_guard<std::mutex> mlock(m_scheduleMapMutex);
    auto itr = m_schedule_map.find(unique_id);
    if (itr == m_schedule_map.end())
    {
        LOG(error) << "Schedule with id: " << unique_id << " not found" << endl;
        return false;
    }
    std::shared_ptr<schedule> current_schedule = m_schedule_map[unique_id];
    if (current_schedule->m_isRunning == RecordScheduleStarted)
    {
        std::lock_guard<std::mutex> mlock(m_recorderMutex);
        string streamId = splitString(unique_id, "|")[0];
        stopRecordIfScheduled(recorder, streamId);
    }
    current_schedule->m_scheduler.reset();
    int ret = m_schedule_map.erase(unique_id);
    if (ret == 1)
    {
        LOG(verbose) << "Schedule with id: " << unique_id << " removed" << endl;
        return true;
    }
    else
    {
        LOG(error) << "Schedule with id: " << unique_id << " not found" << endl;
        return false;
    }
}

std::vector<Json::Value> RecordScheduler::getSchedules(const string &streamId)
{
    std::vector<Json::Value> all_schedules;

    std::vector<VideoRecordScheduleDBColumns> rowArray = GET_DB_INSTANCE()->readVideoRecordSchedules(streamId);
    for (uint32_t i = 0; i < rowArray.size(); i++)
    {
        Json::Value schedule;
        VideoRecordScheduleDBColumns row = rowArray[i];
        schedule["startTime"] = row.start_time_value;
        schedule["endTime"] = row.end_time_value;
        all_schedules.push_back(schedule);
    }

    return all_schedules;
}

bool RecordScheduler::deleteStreamSchedule(StreamRecorder *recorder,
                                           const string &streamId,
                                           const string &start_time_utc,
                                           const string &end_time_utc)
{
    string start_time = getUTCtoLocalTime(start_time_utc);
    string end_time = getUTCtoLocalTime(end_time_utc);
    string unique_id = streamId + '|' + start_time + '|' + end_time;
    LOG(verbose) << "Delete record Schedule called streamId: " << streamId
                 << "\nfor Local start time: " << start_time
                 << " Local end time: " << end_time << endl;

    bool ret = GET_DB_INSTANCE()->deleteVideoRecordSchedule(streamId, start_time_utc, end_time_utc);
    if (!ret)
    {
        LOG(error) << "Error while deleting schedule form DB" << endl;
        return false;
    }

    return removeScheduleFromMap(recorder, unique_id);
}

long RecordScheduler::compareTimePoints(chrono::system_clock::time_point start_tp,
                                        chrono::system_clock::time_point end_tp)
{
    auto duration = (end_tp - start_tp) / chrono::microseconds(1);
    LOG(verbose) << "difference between end and start time: " << duration << endl;
    return duration;
}

void RecordScheduler::stopRecordIfScheduled(StreamRecorder *recorder, const string streamId)
{
    string db_isAlwaysRecording = GET_DB_INSTANCE()->readStreamProperty(streamId, SensorStreamsDBColumns::isAlwaysRecording);
    if (db_isAlwaysRecording != "true")
    {
        recorder->stopRecord(streamId);
    }
    else
    {
        LOG(warning) << "Not stopping recording as Always ON is true, only changing recording config to Always ON" << endl;
        recorder->changeRecordStateTo(streamId, AlwaysOn);
    }
}

VideoFileInfo StreamRecorder::getActiveLocalRecording(const std::string& streamOrSensorId)
{
    VideoFileInfo empty;
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    auto it = m_recorderList.find(streamOrSensorId);
    if (it != m_recorderList.end() && it->second)
    {
        return it->second->getActiveLocalRecording();
    }
    for (const auto& entry : m_recorderList)
    {
        const std::shared_ptr<NvGstVideoRecorder>& rec = entry.second;
        if (!rec)
        {
            continue;
        }
        if (rec->getBoundStreamId() == streamOrSensorId || rec->getBoundSensorId() == streamOrSensorId)
        {
            return rec->getActiveLocalRecording();
        }
    }
    return empty;
}

bool StreamRecorder::changeRecordStateTo(const string streamId, RecordState new_state)
{
    LOG(info) << "Changing recording state for stream id = " << streamId << " to " << translateRecordStateToString(new_state) << endl;
    bool ret = true;
    std::lock_guard<std::mutex> guard(m_recorderMutex);
    recorder_list::iterator it = m_recorderList.find(streamId);
    if (it != m_recorderList.end())
    {
        /* Update recording state in nvgstvideorecorder */
        std::shared_ptr<nv_vms::NvGstVideoRecorder> recorder = it->second;
        if (recorder)
        {
            int result = recorder->changeRecordStateTo(new_state);
            if (result == 0)
            {
                recorder->updateRecordingStatus(streamId, new_state);
            }
        }

        /* Update recording state in PROMETHEUS */
        record_stream_list::iterator it_map = m_streams.find(streamId);
        string stream_name;
        if (it_map != m_streams.end())
        {
            shared_ptr<StreamInfo> stream = it_map->second;
            stream_name = stream->name;
            GET_PROMETHEUS()->updateRecordingStatus(new_state, stream_name);
        }
    }
    else
    {
        LOG(error) << "Writer not found for " << streamId << endl;
        ret = false;
    }
    return ret;
}
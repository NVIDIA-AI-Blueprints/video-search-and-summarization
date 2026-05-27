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

#include <errno.h>
#include <glib/gstdio.h>
#include <string.h>
#include <sys/time.h>
#include <chrono>
#include <cmath>
#include <algorithm>
#include <filesystem>
#include <iostream>
#include <limits>
#include <numeric>
#include "cloud_storage_buffer.h"
#include "mm_utils.h"
#include "prometheus_client/prometheus_client.h"
#include "sensor_info.h"
#include "unified_storage_types.h"
#include "unified_storage_writer_factory.h"
#if defined(LIVE_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
#include "webrtcstreamproducer.h"
#endif
#include "modules_apis.h"
#include "gstmux.h"
constexpr int ONE_SEC_DURATION = 1 * 1000;
constexpr auto SCHEDULER_INTERVAL = std::chrono::seconds(5);

namespace {
// Round measured average FPS to integer for DB: half values go down (e.g. 29.4 -> 29, 29.51 -> 30).
uint64_t roundAvgFpsForDb(double avg_fps)
{
    if (!(avg_fps > 0.0))
    {
        return 0;
    }
    return static_cast<uint64_t>(std::ceil(avg_fps - 0.5));
}
} // namespace

using namespace std;
using namespace nv_vms;

string getOutFileName(std::time_t* out_time, std::string deviceId, std::string resolution)
{
    std::time_t time;
    time = *out_time;
    std::time_t time_secs = time / 1000;
    string outFileName = "";
    string dir_path = GET_CONFIG().recorded_video_root;
    dir_path += "/" + deviceId;
    dir_path += "/" + resolution;
    std::tm ts{};
    gmtime_r(&time_secs, &ts);
    int year = 1900 + ts.tm_year;
    std::ostringstream month;
    month << std::setw(2) << std::setfill('0') << (1 + ts.tm_mon);
    std::ostringstream day;
    day << std::setw(2) << std::setfill('0') << ts.tm_mday;
    std::ostringstream hour;
    hour << (1 + ts.tm_hour);

    dir_path += "/" + std::to_string(year);
    dir_path += "/" + month.str();
    dir_path += "/" + day.str();
    dir_path += "/" + hour.str();
    outFileName = dir_path + "/" + std::to_string(time) + "." + "mkv";
    return outFileName;
}

RecorderQos::RecorderQos()
{
    LOG(info) << "RecorderQos::RecorderQos" << endl;
    string qos_absolute_file_path;
    string filename = string("recording_qos_") + getCurrentTime() + string(".csv");

    qos_absolute_file_path = GET_CONFIG().qos_logfile_path + filename;

    m_recordQosFile.open(qos_absolute_file_path, std::ofstream::out | std::ofstream::app);
    if (m_recordQosFile.fail())
    {
        LOG(error) << "Failed to open recording-qos file " << qos_absolute_file_path << endl;
    }
    else
    {
        string header =
            "camera_name,  prev_frame_time,  cur_frame_time,  frame_diff, current_clock_time, framets_clockts_diff, "
            "is_future_ts";
        m_recordQosFile << header << endl;
        LOG(info) << "Recording-qos started at:" << qos_absolute_file_path << endl;
    }
}

RecorderQos::~RecorderQos()
{
    LOG(info) << "::~RecorderQos" << endl;
    if (m_recordQosFile.is_open())
    {
        m_recordQosFile.close();
    }
}

void RecorderQos::write(const string& data)
{
    std::lock_guard<std::mutex> guard(m_recordQosMutex);
    if (m_recordQosFile.good())
    {
        m_recordQosFile << data << endl;
    }
}

void GstMux::updateRecordStateToDB(std::string& m_deviceId, RecordState& m_recordingState,
                                   const RecordState& changeStateTo)
{
    if (changeStateTo != m_recordingState)
    {
        GET_DB_INSTANCE()->setRecordingStatus(m_deviceId, changeStateTo, nullopt);
        m_recordingState = changeStateTo;
    }
}

void GstMux::updateVideoRecordInDb(std::string file_name, std::string remote_path, int64_t prev_file_end_time, int64_t prev_file_start_time,
                                   bool file_closed)
{
    VideoRecordDBColumns dbRow;
    int64_t duration = 0;
    size_t file_size = 0;
    uint64_t file_fps = 0;
    bool isCloudStorage = !isUsingLocalStorage();

    if (file_closed)
    {
        if (isCloudStorage && m_unifiedStorageWriter)
        {
            // For cloud storage, use remote_path to get the size of the object
            file_size = m_unifiedStorageWriter->getFileSize(remote_path);
            if (file_size == 0)
            {
                file_size = this->m_totalFileSize;
            }
        }
        else
        {
            file_size = getFileSizeInBytes(file_name);
        }
        file_fps = roundAvgFpsForDb(this->calculateAvgFPS(false));
        m_fileclosed = true;
    }
    else
    {
        file_size = this->m_totalFileSize;
        file_fps = roundAvgFpsForDb(this->calculateAvgFPS(false));
    }

    /* Incorrect case, calculate actual duration or delete video record and video file and no need to update DB */
    if (prev_file_end_time < prev_file_start_time)
    {
        if (prev_file_end_time == 0)
        {
            LOG(error) << "Enough frames are not written to " << (isCloudStorage ? "cloud storage" : "file")
                       << ", deleting record from DB = " << file_name << endl;
            // Unprotect file before deletion (file is protected during recording)
            vst_storage::addOrRemoveFileInProtectList(file_name, false);
            vst_storage::deleteMediaFile(file_name);
        }
        else
        {
            LOG(error) << "File End time " << prev_file_end_time << " is less than " << prev_file_start_time
                       << ", updating duration for " << (isCloudStorage ? "cloud file" : "file") << " = " << file_name << " with frame count " << m_fpsVector.size() << endl;
            // Calculate duration for cloud storage using frame count and actual FPS data (most accurate)
            if (isCloudStorage)
            {
                // Calculate duration using frame count from FPS vector size and actual FPS data (most accurate for video)
                if (!m_fpsVector.empty())
                {
                    // Use actual FPS data from the recording session
                    double sum_fps = std::accumulate(m_fpsVector.begin(), m_fpsVector.end(), 0.0);
                    double avg_fps = sum_fps / static_cast<double>(m_fpsVector.size());
                    int frame_count = m_fpsVector.size();  // Number of frames processed
                    
                    if (avg_fps > 0)
                    {
                        duration = frame_count / avg_fps;  // Duration in seconds using actual average FPS
                    }
                    else if (m_frameRate > 0 && frame_count > 0)
                    {
                        // Fallback to nominal frame rate if average FPS is invalid
                        duration = (m_frameRate > 0) ? (frame_count / m_frameRate) : 0;
                    }
                    else
                    {
                        duration = 0;
                    }

                    // duration convert in milliseconds
                    duration = duration * 1000;
                }
                // No frames written - set duration to 0
                else
                {
                    duration = 0;  // No frames were written to the file
                }
            }
            else
            {
                duration = getMediaFileDuration(file_name);
            }
            if (duration == 0)
            {
                file_fps = 0;
                file_size = 0;
            }
        }
    }
    else
    {
        duration = (prev_file_end_time - prev_file_start_time);
    }

    if (file_closed)
    {
        // Clear FPS vector after all calculations are complete
        calculateAvgFPS(true);
    }

    // Set common fields
    dbRow.sensor_id_value = m_deviceId;
    dbRow.stream_id_value = m_stream.get() ? m_stream->id : m_deviceId;
    dbRow.resolution_value = m_resolution;
    dbRow.start_time_value = prev_file_start_time;
    dbRow.filesize_value = file_size;
    dbRow.filefps_value = file_fps;
    dbRow.duration_value = duration;
    dbRow.sensor_name_value = m_camName;
    dbRow.record_config_value = translateRecordStateToString(m_recordingState);
    dbRow.codec_value = m_stream.get() ? m_stream->getvideoEncoderValues().encoding : "";
    dbRow.bucket_name_value = isUsingLocalStorage() ? "" : GET_CONFIG().cloud_storage_bucket;
    
    // Set file paths based on storage type
    if (isCloudStorage)
    {
        // For cloud storage: filepath = local path, object_id = remote path
        dbRow.filepath_value = file_name;  // Local path
    }
    else
    {
        // For local storage: filepath = local path, object_id = empty string
        dbRow.filepath_value = file_name;       // Local path
        dbRow.object_id_value = "";      // Remote path (empty for local storage)
    }

    if (file_closed)
    {
        dbRow.file_protection_value = to_string(false);
        
        // Set cloud storage flag when file is closed
        if (isCloudStorage)
        {
            dbRow.object_id_value = remote_path;   // Remote path (cloud storage path)
            LOG(info) << "File closed for cloud storage with remote path: " << remote_path << endl;
        }
    }
    else
    {
        dbRow.file_protection_value = to_string(true);
    }

    VideoRecordUpdater::getInstance().addToQueue(dbRow);

    if (file_closed)
    {
        std::string storageType = isCloudStorage ? "cloud storage" : "local file";
        LOG(info) << "Update Database with " << storageType << " - local_path: " << dbRow.filepath_value
                    << " remote_path: " << dbRow.object_id_value
                    << " duration=" << dbRow.duration_value << " FPS=" << dbRow.filefps_value
                    << " size=" << dbRow.filesize_value << " protection=" << dbRow.file_protection_value
                    << " timestamp=" << convertEpocToHumanTime(prev_file_start_time) << endl;
    }
    m_durationUpdated = true;
}

void GstMux::pipelineReset()
{
    static thread_local bool isResetting = false;
    if (isResetting)
    {
        LOG(warning) << "Recursive pipeline reset detected for camera ID = " << m_deviceId << endl;
        return;
    }
    isResetting = true;

    mCurrentPipelineBuffers.store(0);
    m_isError = false;
    m_fileclosed = false;

    // Clean up any active storage session
    if (!m_storageSession.empty() && m_unifiedStorageWriter)
    {
        LOG(info) << "Cleaning up active storage session during pipeline reset for camera ID = " << m_deviceId << endl;
        m_unifiedStorageWriter->stopWrite(m_storageSession, m_deviceId);
        m_storageSession.clear();
    }

    updateVideoRecordInDb(m_prevFileName, m_remotePath, m_prevTS, m_prevFileStartTime, true);

    LOG(info) << "Re-creating started, Gstreamer mux pipeline for camera ID = " << m_deviceId << endl;
    int ret = create(m_stream, m_qErrorDeviceID, true);
    if (ret != 0)
    {
        LOG(error) << "Failed to recreate pipeline for camera ID = " << m_deviceId << endl;
        setError();
        isResetting = false;
        return;
    }

    m_videoQueue.setError(m_isError);
    m_recordState = ERROR;

    LOG(info) << "Re-creation completed, Gstreamer mux pipeline for camera ID = " << m_deviceId << endl;
    isResetting = false;
}

double GstMux::calculateAvgFPS(bool clear)
{
    if (m_fpsVector.empty())
    {
        LOG(warning) << "FPS vector is empty for camera: " << m_deviceId << endl;
        return 0.0;
    }

    double sum = std::accumulate(m_fpsVector.begin(), m_fpsVector.end(), 0.0);
    double avg_fps = sum / static_cast<double>(m_fpsVector.size());
    if (clear)
    {
        m_fpsVector.clear();
        LOG(info) << "FPS vector cleared for camera: " << m_deviceId << endl;
    }
    return avg_fps;
}

void GstMux::resetFileParams()
{
    m_prevFileStartTime = 0;
}

void GstMux::updateInconsistencyIfAny(FrameInfo& frameinfo)
{
    /* If frame_diff is more than thresold then log into file */
    if (GET_CONFIG().enable_qos_monitoring)
    {
        string frame_incos;
        int64_t current_frame_ts = convertTimeValToEpochMs(frameinfo.presentationTime);
        int64_t frame_diff = current_frame_ts - m_prevTS;
        int64_t framets_clockts_diff = frameinfo.currentClockTime - current_frame_ts;
        if ((frame_diff > m_maxAllowedFrameDiff && m_prevTS != 0) || framets_clockts_diff > ONE_SEC_DURATION ||
            framets_clockts_diff < (m_maxAllowedFrameDiff * -1))
        {
            /* camera_name, current_sys_time, prev_frame_time, current_frame_time, frame_diff */
            frame_incos = m_camName + string(",  ") + to_string(m_prevTS) + string(",  ") +
                          to_string(current_frame_ts) + string(",  ") + to_string(frame_diff) + string(",  ") +
                          to_string(frameinfo.currentClockTime) + string(",  ") + to_string(framets_clockts_diff) +
                          string(",  ");
            frame_incos += framets_clockts_diff < 0 ? "true" : "false";
        }
        if (frame_incos.empty() == false)
        {
            RecorderQos::getInstance()->write(frame_incos);
        }
    }
}

void GstMux::sendEOSAndUpdateDuration()
{
    // Use unified storage writer for both local and cloud storage
    if (!m_storageSession.empty() && m_unifiedStorageWriter)
    {
        LOG(info) << "Completing recording session for camera ID = " << m_deviceId << " (session: " << m_storageSession
                  << ")" << endl;

        StorageResult result = m_unifiedStorageWriter->stopWrite(m_storageSession, m_deviceId);

        if (result.success)
        {
            LOG(info) << "Recording completed for camera ID = " << m_deviceId << " to "
                      << m_unifiedStorageWriter->getStorageMode() << " (" << result.bytes_written << " bytes)"
                      << " session: " << m_storageSession << " local_path: " << m_prevFileName << " remote_path: " << result.storage_path << endl;

            // Update database with storage path from successful result
            updateVideoRecordInDb(m_prevFileName, result.storage_path, m_prevTS, m_prevFileStartTime, true);
        }
        else
        {
            LOG(error) << "Recording failed for camera ID = " << m_deviceId << ": " << result.message << endl;

            // Even in failure case, update database with available information
            if (m_durationUpdated == false || m_fileclosed == false)
            {
                LOG(warning) << "Updating database with partial recording data due to failure for camera ID = " << m_deviceId << endl;
                updateVideoRecordInDb(m_prevFileName, "", m_prevTS, m_prevFileStartTime, true);
            }

            setError();
        }

        // Mark as completed (both success and failure cases)
        m_durationUpdated = true;
        m_fileclosed = true;

        m_storageSession.clear();
        m_remotePath.clear();
    }
    else
    {
        LOG(error) << "No active recording session for camera ID = " << m_deviceId << endl;
        setError();
    }
    clearActiveLocalRecordingCache();
}

bool GstMux::createNewFileAndSetPlayState(FrameInfo& frameinfo)
{
    m_totalFileSize = 0;
    int64_t ts = 0;
    ts = convertTimeValToEpochMs(frameinfo.presentationTime);

    m_prevFileStartTime = ts;

    // Use unified storage writer for both local and cloud storage
    if (!m_unifiedStorageWriter)
    {
        LOG(error) << "Unified storage writer not initialized for camera ID = " << m_deviceId << endl;
        return true;  // terminate
    }

    if (!m_unifiedStorageWriter->isAvailable())
    {
        LOG(error) << "Unified storage writer not available for camera ID = " << m_deviceId << endl;
        return true;  // terminate
    }

    // Local storage: use local file path
    std::string outputPath = getOutFileName(&ts, m_deviceId, m_resolution);
    m_prevFileName = outputPath;

    // Generate output path based on storage type
    if (!isUsingLocalStorage())
    {
        // Cloud storage: use cloud path - generate a path similar to local but for cloud
        std::time_t time_secs = ts / 1000;
        std::tm ts_tm{};
        gmtime_r(&time_secs, &ts_tm);
        int year = 1900 + ts_tm.tm_year;
        std::ostringstream month, day, hour;
        month << std::setw(2) << std::setfill('0') << (1 + ts_tm.tm_mon);
        day << std::setw(2) << std::setfill('0') << ts_tm.tm_mday;
        hour << (1 + ts_tm.tm_hour);

        outputPath = m_deviceId + "/" + m_resolution + "/" + std::to_string(year) + "/" + month.str() + "/" +
                     day.str() + "/" + hour.str() + "/" + std::to_string(ts) + ".mkv";
    }

    // Start recording session with unified storage writer (with retry)
    const int MAX_SESSION_RETRY = 3;
    for (int retry = 1; retry <= MAX_SESSION_RETRY; ++retry)
    {
        m_storageSession = m_unifiedStorageWriter->startWrite(outputPath, m_deviceId, 0 /* estimated_size */);

        if (!m_storageSession.empty())
        {
            if (retry > 1)
            {
                LOG(info) << "Recording session created successfully on retry " << retry
                          << " for camera ID = " << m_deviceId << endl;
            }
            break;
        }

        if (retry < MAX_SESSION_RETRY)
        {
            LOG(warning) << "Failed to start recording session (attempt " << retry << "/" << MAX_SESSION_RETRY
                         << ") for camera ID = " << m_deviceId << ", retrying..." << endl;
            std::this_thread::sleep_for(std::chrono::milliseconds(100 * retry));  // Exponential backoff
        }
        else
        {
            LOG(error) << "Failed to start recording session after " << MAX_SESSION_RETRY
                       << " attempts for camera ID = " << m_deviceId << endl;
            return true;  // terminate
        }
    }

    // Store file path for database
    m_remotePath = outputPath;

    // Publish active segment for getFileList merge before DB insert (insert is async / may lag).
    if (isUsingLocalStorage())
    {
        std::lock_guard<std::mutex> lk(m_activeLocalRecordingMutex);
        m_activeLocalRecordingPath = m_prevFileName;
        m_activeLocalRecordingStartMs = static_cast<uint64_t>(ts);
        m_activeLocalRecordingCodec = frameinfo.codec;
    }

    // Insert entry into DB
    insertRowInDB(ts, m_prevFileName, frameinfo.recordState, frameinfo.codec);

    LOG(info) << "Started " << (isUsingLocalStorage() ? StorageConstants::LOCAL_STORAGE : StorageConstants::CLOUD_STORAGE)
              << " recording for camera ID = " << m_deviceId << " local_path: " << m_prevFileName << " remote_path: " << m_remotePath
              << " session: " << m_storageSession << endl;

    m_durationUpdated = false;
    m_fileclosed = false;
    return false;  // success
}

bool GstMux::updateRecordingState()
{
    std::lock_guard<std::mutex> recordstateLock(m_recordStateMutex);
    /* This case is to handle file close if gap is detected */
    if (m_recordState != STOPPED)
    {
        m_recordState = STOPPED;

        if (m_recordingState == Event)
        {
            m_eventType = EventTypeWaitForEvent;
        }
        else
        {
            /* In case of not event recording only, we need to update the record state to OFF, If we make it OFF then it
            will not record the next event */
            updateRecordStateToDB(m_deviceId, m_recordingState, RecordState::OFF);
        }
        return false;
    }
    /* This case is to handle timeout occuring in videoqueue, but recording is already stopped. This is NO-OP case */
    else
    {
        resetFileParams();
        return true;
    }
}

void GstMux::setError()
{
    m_isError = true;
    m_videoQueue.setError(m_isError);
    updateRecordStateToDB(m_deviceId, m_recordingState, RecordState::Error);
}

void GstMux::logStats(FrameInfo& frameinfo)
{
    m_fpsVector.push_back(frameinfo.instFPS);
    /* If frame_diff is more than thresold then log into file */
    updateInconsistencyIfAny(frameinfo);
}

void GstMux::checkIfCodecChanged(FrameInfo& frameinfo)
{
    if (frameinfo.mediaType == "video")
    {
        // Check if we have a valid parser name to compare against
        if (m_parserVideoName.empty())
        {
            LOG(warning) << "Parser video name not set yet for camera ID = " << m_deviceId << endl;
            return;
        }

        bool same_codec = findStringIgnoreCase(m_parserVideoName, frameinfo.codec);

        if (VmsConfigManager::getInstance()->isVideoFormatSupported(frameinfo.codec))
        {
            if (!same_codec)
            {
                LOG(error) << "Frame codec: " << frameinfo.codec << " and parser: " << m_parserVideoName
                           << " mismatch, resetting pipeline for camera ID = " << m_deviceId << endl;
                /* Reset codec name to match parser */
                SensorVideoEncoderSettingsValues enc_values = m_stream->getvideoEncoderValues();
                enc_values.encoding = frameinfo.codec;
                m_stream->updateVideoEncoderValues(enc_values);
                pipelineReset();
                /* In case error was in pipeline it needs to be reset, pipeline was resetted  */
                m_isError = false;
            }
        }
        else
        {
            LOG(error) << "Format not supported, breaking muxer process thread for camera ID = " << m_deviceId << endl;
            m_stop = true;
            setError();
        }
    }
}

namespace nv_vms
{
gpointer muxerProcessThread(gpointer data)
{
    GstMux* mux = (GstMux*)data;
    int64_t currentTS = 0;
    bool result = false;

    while (!mux->m_stop)
    {
        FrameInfo frameinfo;

        mux->m_videoQueue.pull(frameinfo);
        mux->checkIfCodecChanged(frameinfo);
        if (frameinfo.frameStatus == ErrorFrame)
        {
            continue;
        }
        std::lock_guard<std::mutex> changestateLock(mux->m_changeStateLock);
        /* Handle frame gap case */
        if (frameinfo.frameStatus == LastFrame)
        {
            bool reset_file_params = mux->updateRecordingState();
            if (reset_file_params)
            {
                currentTS = 0;
                continue;
            }
        }
        /* handle drop frame or timedout frame */
        else if (frameinfo.frameStatus == DropFrame)
        {
            mux->resetFileParams();
            continue;
        }
        /* Handling EOS case */
        else if (frameinfo.size == 0)
        {
            LOG(info) << "Breaking the muxer process thread for camera ID = " << mux->m_deviceId << endl;
            result = true;
            break;
        }
        if (mux->m_eventType == EventTypeStopRecord)
        {
            frameinfo.frameStatus = LastFrame;
            mux->m_recordingStopped = true;
            mux->m_condVar.notify_all();
            /* changing event type immediately to avoid this thread stopping recording again*/
            mux->m_eventType = EventTypeWaitForEvent;
        }

        /* Update prevFrameTs only if Video, and if m_prevFileStartTime in non zero i.e.
        ** new file is created */
        if (frameinfo.mediaType == "video" && mux->m_prevFileStartTime)
        {
            mux->m_prevTS = currentTS;
            currentTS = convertTimeValToEpochMs(frameinfo.presentationTime);
        }

        if (GET_CONFIG().event_recording && mux->m_prevFileStartTime && mux->m_recordingState == Event)
        {
            if (frameinfo.frameStatus == LastFrame)
            {
                mux->m_eventType = EventTypeWaitForEvent;
                LOG(info) << "Stopping recording on event for " << mux->m_deviceId << endl;
            }
        }

        if (frameinfo.mediaType == "video")
        {
            mux->logStats(frameinfo);
        }

        if (frameinfo.frameStatus != IntermediateFrame)
        {
            /* Do pipeline operation or duration update operation only if it not first iteration*/
            if (mux->m_prevFileStartTime != 0)
            {
                mux->sendEOSAndUpdateDuration();
                if (mux->m_isError)
                {
                    LOG(error) << "SendEOS error received for camera ID = " << mux->m_deviceId << endl;
                }
            }
            if (frameinfo.frameStatus == FirstFrame && mux->m_isError == false)
            {
                result = mux->createNewFileAndSetPlayState(frameinfo);
                if (result)
                {
                    LOG(error) << "Failed to get sink element in gstreamer MUX pipeline for camera ID = "
                               << mux->m_deviceId << endl;
                    mux->setError();
                }
                else
                {
                    mux->m_isError = false;
                }
            }
        }
        if (mux->m_isError)
        {
            // Check stop condition before error recovery
            if (mux->m_stop)
            {
                break;
            }

            LOG(warning) << "Error Occured in Muxer Pipeline, retrying, for camera ID = " << mux->m_deviceId
                         << " totalBytesofFile = " << mux->m_totalFileSize << endl;

            // Reset the pipeline and clear the storage session
            if (!mux->m_storageSession.empty())
            {
                mux->pipelineReset();
                mux->m_storageSession.clear();
                mux->resetFileParams();
            }

            mux->m_recordState = ERROR;
            mux->updateRecordStateToDB(mux->m_deviceId, mux->m_recordingState, RecordState::Error);

            if (frameinfo.isIDR)
            {
                // Check stop condition before attempting recovery
                if (mux->m_stop)
                {
                    break;
                }

                result = mux->createNewFileAndSetPlayState(frameinfo);
                frameinfo.frameStatus = FirstFrame;  // To avoid updating periodic DB update in error case
                if (result)
                {
                    LOG(error) << "Failed to create new file/session for camera ID = " << mux->m_deviceId << endl;
                    mux->setError();
                    continue;
                }
                else
                {
                    mux->m_isError = false;
                    LOG(info) << "Successfully recovered from error for camera ID = " << mux->m_deviceId << endl;
                }
            }
            else
            {
                continue;
            }
        }
        if (frameinfo.frameStatus != LastFrame)
        {
            result = mux->pushBuffer(frameinfo);
            if (result)
            {
                LOG(error) << "Error Occured" << endl;
                mux->setError();
            }
        }
        else
        {
            currentTS = 0;
            mux->resetFileParams();
        }
    }

    /* When the thread self-exits (not via m_stop), destroy() won't call
     * g_thread_join(), so we must g_thread_unref() to release the GThread
     * handle and its internal resources (pipe/eventfd FDs). Without this,
     * every self-exit leaks FDs.
     * The lock prevents a race with destroy() where both g_thread_join()
     * and g_thread_unref() could run on the same GThread. */
    {
        std::lock_guard<std::mutex> lock(mux->m_threadMutex);
        if (!mux->m_stop && mux->m_muxerProcessThread)
        {
            g_thread_unref(mux->m_muxerProcessThread);
            mux->m_muxerProcessThread = nullptr;
        }
    }

    LOG(info) << "Exiting muxer process thread for camera ID = " << mux->m_deviceId << endl;

    // Complete any active recording session BEFORE destroying pipeline
    if (mux->m_durationUpdated == false || mux->m_fileclosed == false)
    {
        /* Complete the recording session and update database */
        mux->sendEOSAndUpdateDuration();
    }

    // Destroy pipeline after session completion
    mux->destroyPipeline();
    return nullptr;
}
}  // namespace nv_vms

bool GstMux::pushBuffer(FrameInfo frameinfo)
{
    int64_t bufferPTS = 0;
    bool terminate = false;

    // Check if we're shutting down
    if (m_stop)
    {
        LOG(verbose) << "Skipping frame push during shutdown for camera ID = " << m_deviceId << endl;
        return false;  // Don't terminate, just skip
    }

    // Check if unified storage writer is available
    if (!m_unifiedStorageWriter || !m_unifiedStorageWriter->isAvailable())
    {
        LOG(error) << "Unified storage writer not available for camera ID = " << m_deviceId << endl;
        setError();
        return true;  // terminate
    }

    // Check if we have an active storage session
    if (m_storageSession.empty())
    {
        // Only log this occasionally to avoid spam during normal operation
        static int skip_count = 0;
        if (++skip_count % 30 == 1)
        {  // Log every 30th occurrence
            LOG(verbose) << "No active storage session for camera ID = " << m_deviceId
                         << ", skipping frame (this is normal during session initialization)" << endl;
        }
        return false;  // Don't terminate, just skip this frame
    }

    // Write frame data to unified storage writer
    bufferPTS = convertTimeValToEpochMs(frameinfo.presentationTime);

    bool success = m_unifiedStorageWriter->onFrame(m_storageSession, frameinfo.content.data(), frameinfo.size,
                                                   bufferPTS, frameinfo.mediaType);

    if (!success)
    {
        LOG(error) << "Failed to write frame to unified storage writer for camera ID = " << m_deviceId << endl;
        setError();
        terminate = true;
        goto exit;
    }

    mCurrentPipelineBuffers++;

    m_totalFileSize += frameinfo.size;
exit:
    return terminate;
}

void GstMux::insertRowInDB(int64_t& time_stamp, string& file_name, RecordState& record_state, string& codec)
{
    VideoRecordDBColumns dbRow;
    dbRow.sensor_id_value = m_deviceId;
    dbRow.stream_id_value = m_stream.get() ? m_stream->id : m_deviceId;
    dbRow.resolution_value = m_resolution;
    dbRow.start_time_value = time_stamp;
    dbRow.duration_value = FILE_INIT_DURATION;
    dbRow.filepath_value = file_name.c_str();
    dbRow.filesize_value = 0;
    dbRow.filefps_value = 0;
    dbRow.sensor_name_value = m_camName;
    dbRow.record_config_value = translateRecordStateToString(record_state);
    dbRow.codec_value = codec;
    dbRow.file_protection_value = to_string(true);
    dbRow.storage_location_value = isUsingLocalStorage() ? StreamStorageTypeLocal : StreamStorageTypeCloud;
    dbRow.bucket_name_value = isUsingLocalStorage() ? "" : GET_CONFIG().cloud_storage_bucket;
    VideoRecordUpdater::getInstance().addInsertToQueue(dbRow);
}

void GstMux::onFrame(FrameParams& frame_params)
{
    FrameInfo frameinfo;
    bool isIDR = false;
    if (m_stop.load() == true)
    {
        LOG(info) << "Ignoring the frames in muxer, due to stop" << endl;
        return;
    }

    std::vector<uint8_t> content;
    if (frame_params.m_media == "video")
    {
        if (frame_params.m_needParsing)
        {
            content = parseAndCreateFrame(frame_params, &isIDR);
            if (content.size() == 0 || m_startConsuming == false)
            {
                return;
            }
        }
        else
        {
            uint8_t data = frame_params.m_buffer[10];
            NaluType nal_type = static_cast<NaluType>(data & 0x1F);
            if (nal_type == NaluType::kSps)
            {
                m_startConsuming = true;
                isIDR = true;
            }
            if (m_startConsuming == false)
            {
                return;
            }
            else
            {
                content.insert(content.end(), frame_params.m_buffer + 6,
                               frame_params.m_buffer + frame_params.m_size - 6);
            }
        }
        m_fpsDisplay->displayFPS(getCurrentUnixTimestampInMs(), "recording_" + m_deviceId);
        frameinfo.instFPS = m_fpsDisplay->getInstFPS();
    }
    else if (frame_params.m_media == "audio" && m_audioSupported && m_startConsuming)
    {
        content.insert(content.end(), frame_params.m_buffer, frame_params.m_buffer + frame_params.m_size);
    }
    else
    {
        LOG(verbose) << "Unknown Media Type or I frame not received yet, Ignoring" << endl;
        return;
    }

    frameinfo.codec = frame_params.m_codec;
    frameinfo.content = content;
    frameinfo.isIDR = isIDR;
    frameinfo.size = content.size();
    frameinfo.mediaType = frame_params.m_media;
    frameinfo.presentationTime = frame_params.m_presentationTime;
    frameinfo.currentClockTime = getCurrentUnixTimestampInMs();

    m_videoQueue.push(frameinfo);

    if (!m_isError)
    {
        std::unique_lock<std::mutex> lock(m_recordStateMutex);
        m_recordState = RECORDING;
        updateRecordStateToDB(m_deviceId, m_recordingState, m_originalRecordingState);
        m_recordStateCv.notify_all();
    }
    return;
}

int GstMux::changeRecordStateTo(RecordState new_state)
{
    std::unique_lock<std::mutex> lk(m_changeStateLock);

    /* check if current recording session needs to be stopped */
    if (new_state == Event)
    {
        LOG(info) << "Stopping current record session" << endl;
        m_eventType = EventTypeStopRecord;
        m_condVar.wait(lk, [this] { return m_recordingStopped == true; });
        m_recordingStopped = false;
        m_eventType = EventTypeWaitForEvent;
        LOG(info) << "Stopped current record session" << endl;
    }

    // If recording is being completely stopped (OFF), cleanup any active sessions
    if (new_state == OFF && m_recordingState != OFF)
    {
        LOG(info) << "Recording stopped, cleaning up any active sessions for camera ID = " << m_deviceId << endl;
        if (!m_storageSession.empty() && m_unifiedStorageWriter)
        {
            m_unifiedStorageWriter->pauseWrite(m_storageSession, m_deviceId);
            m_storageSession.clear();
        }
    }

    m_recordingState = new_state;
    m_videoQueue.setRecordingState(m_recordingState);
    m_originalRecordingState = new_state;
    return 0;
}

int GstMux::onEvent()
{
    std::lock_guard<std::mutex> lock(m_changeStateLock);
    int ret = 0;
    if (m_recordingState == Event)
    {
        if (m_eventType != EventTypeWaitForEvent)
        {
            LOG(error) << "Already handling event recived at " << convertTimeValToEpochMs(m_eventTime)
                       << " for camera ID = " << m_deviceId << " and event type = " << m_eventType << endl;
            return -1;
        }
        /* send Event to VideoQueue only if required */
        m_videoQueue.onEvent();
        LOG(info) << "Handling event for " << m_stream->sensorId << endl;
        m_eventType = EventTypeStartRecord;
        gettimeofday(&m_eventTime, nullptr);
    }
    else
    {
        LOG(warning) << "Ignoring event, current Recording state is " << m_recordingState << endl;
        ret = -1;
    }
    return ret;
}

int GstMux::create(shared_ptr<StreamInfo> stream, GAsyncQueue* qErrorDeviceID, bool recreate_muxer)
{
    std::map<string, media_info, std::less<>> media_details;
    m_stream = stream;
    /* reset the value in case of recreation */
    m_audioSupported = false;
    LOG(info) << "Creating Gstreamer mux pipeline for Sensor ID = " << stream->sensorId << endl;

#if defined(LIVE_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
    if (stream->live_proxy_url.find("webrtc") != std::string::npos)
    {
        media_details = WebrtcStreamProducer::getInstance()->getAudioInfo(stream->sensorId);
    }
    else
#endif
    {
        const SensorAudioEncoderSettingsValues& aenc = stream->getAudioEncoderValues();
        if (!aenc.encoding.empty())
        {
            string codec_for_pipeline = aenc.encoding;
            if (aenc.encoding.find("AAC") != string::npos
                || aenc.encoding.find("aac") != string::npos)
            {
                codec_for_pipeline = "mpeg4-generic";
            }

            media_info audio_info;
            audio_info.codec     = codec_for_pipeline;
            audio_info.channel   = stringToInt(aenc.channels, 0);
            audio_info.frequency = stringToInt(aenc.sample_rate, 0);
            audio_info.codecData = 0;  // Not used downstream for SDP-discovered streams.
            media_details["audio"] = audio_info;
        }
    }
    std::map<string, media_info, std::less<>>::iterator it;
    string audio_codec = "", video_codec;
    it = media_details.find("audio");
    if (it != media_details.end())
    {
        if (VmsConfigManager::getInstance()->isAudioFormatSupported((it->second.codec)) == false)
        {
            LOG(error) << "Audio encode format " << it->second.codec << " not supported" << endl;
            m_audioSupported = false;
        }
        else
        {
            LOG(info) << "Audio encode format supported = " << it->second.codec << endl;
            m_audioSupported = true;
            audio_codec = it->second.codec;
        }
    }
    m_frameRate = stringToInt(stream->getvideoEncoderValues().frameRate, 30);
    if (m_frameRate > 0)
    {
        m_maxAllowedFrameDiff = (int)((1000 / m_frameRate) * 1.5);
    }
    video_codec = stream->getvideoEncoderValues().encoding;

    if (gst_is_initialized() == false)
    {
        gst_init(nullptr, nullptr);
    }

    if (!recreate_muxer)
    {
        /* No need to populate data structures while recreating pipeline */
        m_uri = stream->live_proxy_url;
        m_deviceId = stream->sensorId;
        m_resolution = stream->settings.encoderValues.resolution.getString();
        ;
        m_qErrorDeviceID = qErrorDeviceID;
        m_camName = stream->name;
        m_videoQueue.setDeviceId(m_deviceId);
    }

    // Set streaming mode based on storage type (only MinIO type uses cloud storage)
    m_storageMode = GET_CONFIG().enable_cloud_storage && (GET_CONFIG().cloud_storage_type == "minio");
    LOG(info) << "Using " << (m_storageMode ? StorageConstants::CLOUD_STORAGE : StorageConstants::LOCAL_STORAGE) << " storage for camera ID = " << m_deviceId << endl;

    // Initialize unified storage writer
    if (!CreateUnifiedStorage(media_details))
    {
        LOG(error) << "Failed to initialize unified storage writer for camera ID = " << m_deviceId << endl;
        return -1;
    }

    string thread_name = "muxerProcessThread_" + m_deviceId;
    /* Create this thread only if required */
    if (m_muxerProcessThread == nullptr)
    {
        m_muxerProcessThread = g_thread_new(thread_name.c_str(), muxerProcessThread, (void*)this);
    }

#ifdef UNIFIED_STORAGE_WRITER_UNIT_TEST
    // Disable test mode by passing 0 (or use 30 for testing)
    m_unifiedStorageWriter->testPushBufferFailure(30);
#endif

    LOG(info) << "Gstreamer mux pipeline created for camera ID = " << m_deviceId << endl;
    return 0;
}

void GstMux::checkStatus()
{
    if (m_isError)
    {
        // TODO TBD when to push error in queue
        // g_async_queue_push (m_qErrorDeviceID, &m_deviceId);
        return;
    }
    GET_PROMETHEUS()->updateRecordingStatus(m_recordState, m_camName);
    if (m_recordState == RECORDING && GET_CONFIG().enable_mega_simulation == false)
    {
        m_recordState = ERROR;
    }
}

bool GstMux::play()
{
    LOG(info) << "GstMux::play for camera ID = " << m_deviceId << endl;
    bool ret = true;
    m_startConsuming = false;
#ifndef UNIT_TEST
    m_checkStatusScheduler->interval(SCHEDULER_INTERVAL, [=]() { checkStatus(); });
#endif
    return ret;
}

bool GstMux::isPlaying()
{
    {
        std::unique_lock<std::mutex> lk(m_recordStateMutex);
        if (m_recordState == ERROR)
        {
            // (2 frames * 1000 milliseconds) / frameRate
            // Waiting for max 2 frames
            int time_to_wait = (2000 / m_frameRate);
            auto until = std::chrono::system_clock::now() + chrono::milliseconds(time_to_wait);
            if (m_recordStateCv.wait_until(lk, until, [this] { return (m_recordState == RECORDING); }) == false)
            {
                return false;
            }
        }
    }
    if (m_recordState == RECORDING)
    {
        return true;
    }
    else
    {
        return false;
    }
}

bool GstMux::isAudioSupported()
{
    return m_audioSupported;
}

bool GstMux::isCreated()
{
    // Pipeline creation is now handled by UnifiedStorageWriter
    return (m_unifiedStorageWriter && m_unifiedStorageWriter->isAvailable());
}

void GstMux::clearActiveLocalRecordingCache()
{
    std::lock_guard<std::mutex> lk(m_activeLocalRecordingMutex);
    m_activeLocalRecordingPath.clear();
    m_activeLocalRecordingStartMs = 0;
    m_activeLocalRecordingCodec.clear();
}

VideoFileInfo GstMux::getActiveLocalRecordingSnapshot() const
{
    VideoFileInfo out;
    if (!isUsingLocalStorage())
    {
        return out;
    }
    std::lock_guard<std::mutex> lk(m_activeLocalRecordingMutex);
    if (m_activeLocalRecordingPath.empty())
    {
        return out;
    }
    out.m_filePath = m_activeLocalRecordingPath;
    out.m_startTime = m_activeLocalRecordingStartMs;
    out.m_duration = FILE_INIT_DURATION;
    out.m_codec = m_activeLocalRecordingCodec;
    return out;
}

void GstMux::destroyPipeline()
{
    if (m_unifiedStorageWriter)
    {
        LOG(info) << "Calling UnifiedStorageWriter destroyPipeline for camera id = " << m_deviceId << endl;
        m_unifiedStorageWriter->destroyPipeline();
    }
    else
    {
        LOG(warning) << "No storage integration available for pipeline destruction, camera id = " << m_deviceId << endl;
    }
}

void GstMux::destroy()
{
    LOG(warning) << "Terminating gstreamer mux pipeline for camera id = " << m_deviceId << endl;
    clearActiveLocalRecordingCache();
    m_stop = true;

    /* to avoid hang issue, the queue should be released as
    ** Video Queue is waiting infinitely */
    if (GET_CONFIG().enable_mega_simulation)
    {
        m_videoQueue.release();
    }
    GThread* threadToJoin = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_threadMutex);
        if (m_muxerProcessThread)
        {
            threadToJoin = m_muxerProcessThread;
            m_muxerProcessThread = nullptr;
        }
    }
    if (threadToJoin)
    {
        LOG(info) << "Joining Muxer Process Thread for camera id = " << m_deviceId << endl;
        g_thread_join(threadToJoin);
    }
    m_videoQueue.release();
    // destroyPipeline();
    LOG(warning) << "Terminated gstreamer mux pipeline for camera id = " << m_deviceId << endl;
}

std::string GstMux::getStorageMode() const
{
    if (m_unifiedStorageWriter)
    {
        return m_unifiedStorageWriter->getStorageMode();
    }
    return "None";
}

bool GstMux::isUsingLocalStorage() const
{
    if (m_unifiedStorageWriter)
    {
        std::string typeName = getStorageMode();
        return (typeName.find("local") != std::string::npos || typeName == "None");
    }
    return true;  // Default to local storage if no integration
}

void VideoRecordUpdater::logCombinedQueueSizeWarning(size_t& lastReportedSize)
{
    size_t currentSize = m_insertQueue.size() + m_updateQueue.size();
    if ((currentSize >= 100) && (currentSize / 100 > lastReportedSize / 100))
    {
        LOG(warning) << "VideoRecordUpdater combined queue size reached: " << (currentSize / 100) * 100 << endl;
        lastReportedSize = currentSize;
    }
}

void VideoRecordUpdater::InsertVideoRecordLoop()
{
    size_t lastReportedSize = 0;
    while (!m_done || !m_insertQueue.empty())
    {
        VideoRecordDBColumns record;
        bool hasRecord = false;

        {
            std::unique_lock<std::mutex> lock(m_queueMutex);
            logCombinedQueueSizeWarning(lastReportedSize);
            m_insertCv.wait_for(lock, std::chrono::seconds(10),
                                [this] { return !m_insertQueue.empty() || m_done; });
            if (!m_insertQueue.empty())
            {
                try
                {
                    record = m_insertQueue.front();
                    m_insertQueue.pop();
                    hasRecord = true;
                }
                catch (const std::exception& e)
                {
                    LOG(error) << "Queue operation failed (insert): " << e.what() << endl;
                }
            }
        }

        if (hasRecord)
        {
            try
            {
                int retries = 3;
                while (retries-- > 0)
                {
                    int result = GET_DB_INSTANCE()->insertRowVideoRecord(record);
                    if (result == 0)
                    {
                        LOG(info) << "Insert into Database for file " << record.filepath_value
                                  << " with timestamp : " << convertEpocToHumanTime(record.start_time_value) << endl;
                        break;
                    }
                    if (retries > 0)
                    {
                        std::this_thread::sleep_for(std::chrono::milliseconds(100));
                    }
                    else
                    {
                        LOG(error) << "Failed to insert record after retries: " << record.filepath_value << endl;
                    }
                }
            }
            catch (const std::exception& e)
            {
                LOG(error) << "Database insert failed: " << e.what() << endl;
            }
        }
    }
}

void VideoRecordUpdater::UpdateVideoRecordLoop()
{
    size_t lastReportedSize = 0;
    while (!m_done || !m_updateQueue.empty())
    {
        VideoRecordDBColumns record;
        bool hasRecord = false;

        {
            std::unique_lock<std::mutex> lock(m_queueMutex);
            logCombinedQueueSizeWarning(lastReportedSize);
            m_updateCv.wait_for(lock, std::chrono::seconds(10),
                                 [this] { return !m_updateQueue.empty() || m_done; });
            if (!m_updateQueue.empty())
            {
                try
                {
                    record = m_updateQueue.front();
                    m_updateQueue.pop();
                    hasRecord = true;
                }
                catch (const std::exception& e)
                {
                    LOG(error) << "Queue operation failed (update): " << e.what() << endl;
                }
            }
        }

        if (hasRecord)
        {
            try
            {
                int retries = 3;
                while (retries-- > 0)
                {
                    int result = GET_DB_INSTANCE()->updateVideoRecordInDb(record);
                    if (result == 0)
                    {
                        LOG(info) << "Updated DB: streamId=" << record.stream_id_value << " duration=" << record.duration_value
                                     << " FPS=" << record.filefps_value << " size=" << record.filesize_value
                                     << " file=" << record.filepath_value
                                     << " protection=" << record.file_protection_value << endl;
                        break;
                    }
                    if (retries > 0)
                    {
                        std::this_thread::sleep_for(std::chrono::milliseconds(100));
                    }
                    else
                    {
                        LOG(error) << "Failed to update record after retries: " << record.filepath_value << endl;
                    }
                }
            }
            catch (const std::exception& e)
            {
                LOG(error) << "Database update failed: " << e.what() << endl;
            }
        }
    }
}

void VideoRecordUpdater::start()
{
    m_insertThread = std::thread(&VideoRecordUpdater::InsertVideoRecordLoop, this);
    m_updateThread = std::thread(&VideoRecordUpdater::UpdateVideoRecordLoop, this);
}

void VideoRecordUpdater::stop()
{
    m_done = true;
    m_insertCv.notify_all();
    m_updateCv.notify_all();
    if (m_insertThread.joinable())
    {
        m_insertThread.join();
    }
    if (m_updateThread.joinable())
    {
        m_updateThread.join();
    }
    LOG(info) << "Exited VideoRecordUpdater (insert + update threads)" << endl;
}

void VideoRecordUpdater::addToQueue(VideoRecordDBColumns record)
{
    std::unique_lock<std::mutex> lock(m_queueMutex);
    m_updateQueue.push(record);
    m_updateCv.notify_one();
}

void VideoRecordUpdater::addInsertToQueue(VideoRecordDBColumns record)
{
    std::unique_lock<std::mutex> lock(m_queueMutex);
    m_insertQueue.push(record);
    m_insertCv.notify_one();
}

bool GstMux::CreateUnifiedStorage(const std::map<string, media_info, std::less<>>& media_details)
{
    // Clean up existing unified storage writer if it exists
    if (m_unifiedStorageWriter)
    {
        LOG(info) << "Cleaning up existing unified storage writer before creating new one for camera ID = "
                  << m_deviceId << endl;
        m_unifiedStorageWriter->destroyPipeline();
        m_unifiedStorageWriter.reset();  // Explicitly destroy the old object
    }

    // Create unified storage writer based on configuration
    // Only use cloud storage for MinIO type, all other types use local storage
    bool useCloudStorage = GET_CONFIG().enable_cloud_storage && (GET_CONFIG().cloud_storage_type == "minio");
    std::string storage_type = useCloudStorage ? StorageConstants::CLOUD_STORAGE : StorageConstants::LOCAL_STORAGE;

    m_unifiedStorageWriter = UnifiedStorageWriterFactory::createWriter(storage_type);
    if (!m_unifiedStorageWriter)
    {
        LOG(error) << "Failed to create unified storage writer for type: " << storage_type << endl;
        return false;
    }

    LOG(info) << "Created unified " << storage_type << " storage writer for camera ID = " << m_deviceId << endl;

    // Extract audio parameters from media_details (preferred) or fallback to encoder settings
    std::string audio_codec, audio_sample_rate, audio_channels;

    auto it = media_details.find("audio");
    if (it != media_details.end())
    {
        audio_codec = it->second.codec;
        audio_sample_rate = std::to_string(it->second.frequency);
        audio_channels = std::to_string(it->second.channel);
        LOG(info) << "Using audio parameters from media_details for camera ID = " << m_deviceId
                  << ": codec=" << audio_codec << ", sample_rate=" << audio_sample_rate
                  << ", channels=" << audio_channels << endl;
    }

    // Configure the storage writer
    StorageConfig config;
    // Common video and audio configuration for both storage types
    config.setParameter(StorageConstants::VIDEO_CODEC_KEY, m_stream.get() ? m_stream->getvideoEncoderValues().encoding : "h264");
    config.setParameter(StorageConstants::AUDIO_SUPPORTED_KEY, m_audioSupported ? "true" : "false");

    // Audio configuration (common for both storage types)
    if (m_audioSupported)
    {
        config.setParameter(StorageConstants::AUDIO_CODEC_KEY, audio_codec);
        config.setParameter(StorageConstants::AUDIO_SAMPLE_RATE_KEY, audio_sample_rate);
        config.setParameter(StorageConstants::AUDIO_CHANNELS_KEY, audio_channels);

        /* For AAC, build the MPEG-4 AudioSpecificConfig (codec_data) from
         * the actual sample rate and channel count so that the matroska
         * file we record carries the correct AAC config. Without this we
         * fall back to the storage writer's hard-coded default ("1410" =
         * AAC LC / 16 kHz / 2 ch), which produces an MKV that mediainfo /
         * mp4mux / VLC interpret as 16 kHz audio even when the underlying
         * frames were sampled at 48 kHz, producing silent or garbled
         * remux/download playback.
         *
         * AudioSpecificConfig layout (ISO/IEC 14496-3):
         *   bits  [15..11] : AudioObjectType (LC = 2)
         *   bits  [10..7]  : sampling_frequency_index (table below)
         *   bits  [6..3]   : channel_configuration  (table 1.18; NOT a
         *                    raw channel count for >6 channels)
         *   bits  [2..0]   : zero padding to 16 bits */
        bool isAac = audio_codec.find("AAC") != string::npos
                  || audio_codec.find("aac") != string::npos
                  || audio_codec.find("mpeg4") != string::npos
                  || audio_codec.find("MPEG4") != string::npos;
        if (isAac)
        {
            const std::map<int, int, std::less<>> kAacFreqIdx = {
                {96000, 0}, {88200, 1}, {64000, 2}, {48000, 3}, {44100, 4},
                {32000, 5}, {24000, 6}, {22050, 7}, {16000, 8}, {12000, 9},
                {11025, 10}, {8000, 11}, {7350, 12}
            };
            int sample_rate = stringToInt(audio_sample_rate, 16000);
            int channels    = stringToInt(audio_channels, 2);
            auto fit = kAacFreqIdx.find(sample_rate);
            int  freq_idx = (fit != kAacFreqIdx.end()) ? fit->second : 8 /* 16000 */;
            const int aot = 2; // AAC LC

            /* AAC channel_configuration mapping (ISO/IEC 14496-3, Table 1.18):
             *   1..6  -> channel count itself (mono..5.1)
             *   8 ch  -> channel_configuration = 7 (7.1 surround)
             *   7, 0  -> would require PCE in AOT-specific config; not
             *            supported here, fall back to stereo (2)
             *   >8    -> reserved; fall back to stereo (2)               */
            unsigned int chan_cfg;
            if (channels >= 1 && channels <= 6)
            {
                chan_cfg = (unsigned)channels;
            }
            else if (channels == 8)
            {
                chan_cfg = 7;
            }
            else
            {
                LOG(warning) << "Unsupported AAC channel count " << channels
                             << "; using channel_configuration=2 (stereo) for codec_data"
                             << " for camera ID = " << m_deviceId << endl;
                chan_cfg = 2;
            }

            unsigned int asc = ((unsigned)aot      << 11)
                             | ((unsigned)freq_idx << 7)
                             |  (chan_cfg          << 3);
            char hexbuf[8] = {0};
            snprintf(hexbuf, sizeof(hexbuf), "%04x", asc & 0xFFFFu);
            config.setParameter(StorageConstants::CODEC_DATA_KEY, hexbuf);
            LOG(info) << "Computed AAC codec_data = " << hexbuf
                      << " (sample_rate=" << sample_rate
                      << ", channels=" << channels
                      << ", channel_configuration=" << chan_cfg
                      << ", aot=LC) for camera ID = " << m_deviceId << endl;
        }
    }

    // Storage-specific configuration
    if (GET_CONFIG().enable_cloud_storage && GET_CONFIG().cloud_storage_type == "minio")
    {
        // Cloud storage configuration
        config.setParameter(StorageConstants::CLOUD_TYPE_KEY, GET_CONFIG().cloud_storage_type);
        config.setParameter(StorageConstants::ENDPOINT_KEY, GET_CONFIG().cloud_storage_endpoint);
        config.setParameter(StorageConstants::ACCESS_KEY_KEY, GET_CONFIG().cloud_storage_access_key);
        config.setParameter(StorageConstants::SECRET_KEY_KEY, GET_CONFIG().cloud_storage_secret_key);
        config.setParameter(StorageConstants::BUCKET_NAME_KEY, GET_CONFIG().cloud_storage_bucket);
        config.setParameter(StorageConstants::USE_SSL_KEY, GET_CONFIG().cloud_storage_use_ssl ? "true" : "false");

        // Buffering configuration for cloud storage
        // TODO: We need to set it as per the stream input rate, currently rate limitor and error recovery
        // is not implemneted for cloud storage properly, so we are using default values for now. Once it is implemented
        // we need to config proper values for cloud storage
        config.buffering.enabled = true;
        config.buffering.buffer_size_mb = 25;  // Optimized buffer size
        config.buffering.max_frames = 1500;     // Optimized frame count
        config.buffering.max_upload_fps = 30; // Match input rate to prevent buffer growth
        config.buffering.auto_adapt_rate = true;
        config.buffering.flush_timeout_sec = 30;
        config.buffering.min_part_size_mb = 5; // Configurable minimum part size for multipart uploads

        LOG(info) << "Configured cloud storage buffering: buffer_size=" << config.buffering.buffer_size_mb
                  << "MB, max_frames=" << config.buffering.max_frames
                  << ", max_upload_fps=" << config.buffering.max_upload_fps
                  << ", min_part_size=" << config.buffering.min_part_size_mb << "MB"
                  << " for camera ID = " << m_deviceId << endl;
    }
    else
    {
        // Local storage configuration
        config.setParameter(StorageConstants::BASE_PATH_KEY, GET_CONFIG().recorded_video_root);
        config.setParameter(StorageConstants::CREATE_DIRECTORIES_KEY, "true");
    }

    if (!m_unifiedStorageWriter->configureStorage(config))
    {
        LOG(error) << "Failed to configure unified storage writer for camera ID = " << m_deviceId << endl;
        return false;
    }

    // Store the video codec name for codec checking
    m_parserVideoName = m_stream.get() ? m_stream->getvideoEncoderValues().encoding : "h264";

    // Initialize the pipeline (this creates the GStreamer elements)
    if (!m_unifiedStorageWriter->createPipeline(m_parserVideoName, m_audioSupported, m_deviceId))
    {
        LOG(error) << "Failed to initialize pipeline for camera ID = " << m_deviceId << endl;
        return false;
    }

    LOG(info) << "Unified storage writer configured and pipeline initialized successfully for camera ID = "
              << m_deviceId << " with video codec: " << m_parserVideoName
              << " and audio support: " << (m_audioSupported ? "enabled" : "disabled") << endl;
    return true;
}
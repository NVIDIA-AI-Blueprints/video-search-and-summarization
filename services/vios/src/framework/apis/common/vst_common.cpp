/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "vst_common.h"
#include "storage_management.h"
#include "network_utils.h"
#include "streamrecorder.h"
#include "config.h"
#include "nvgstvideocapturer.h"
#include "rtspvideocapturer.h"
#include "udpclientpool.h"
#include "media/video_source/decoders/decoderpool.h"
#include "media/video_source/core/CommonVideoSource.h"
#include "database.h"
#include "utils.h"
#include "fs_utils.h"
#include "TempFileScheduler.h"
#include <chrono>
#include <cmath>
#include <filesystem>
#include <algorithm>
#include <limits>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <thread>

using namespace nv_vms;
using namespace std;

constexpr double ZERO_FLOAT = 0.0;
static constexpr int MIN_ALLOWED_EXPIRY_MINUTES = 1;

namespace vst_common
{
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
    // If the requested picture-API startTime falls in an inter-file recording
    // gap for the given stream, return an ISO8601 string clamped to the last
    // frame of the previous file. Otherwise the original isoStartTime is
    // returned unchanged.
    static std::string clampPictureStartTimeForGap(const std::string& streamId,
                                                   const std::string& isoStartTime)
    {
        if (streamId.empty() || isoStartTime.empty())
        {
            return isoStartTime;
        }
        const int64_t requestedEpochMs = getEpocTimeInMS(isoStartTime);
        if (requestedEpochMs <= 0)
        {
            return isoStartTime;
        }
        auto dbHelper = GET_DB_INSTANCE();
        if (!dbHelper)
        {
            return isoStartTime;
        }

        // Detect gap: ask for files that contain requestedEpochMs (t1==t2).
        std::vector<VideoFileInfo> filesAtT = dbHelper->getFileList(streamId, requestedEpochMs, requestedEpochMs);
        const bool inGap = filesAtT.empty()
                           || (static_cast<int64_t>(filesAtT.front().m_startTime) > requestedEpochMs);
        if (!inGap)
        {
            return isoStartTime;
        }

        // Look back 1s for the previous file ending just before the requested
        // time.
        constexpr int64_t LOOKBACK_MS = 1000;
        const int64_t lookbackStartMs = (requestedEpochMs > LOOKBACK_MS)
                                            ? (requestedEpochMs - LOOKBACK_MS) : 0;
        std::vector<VideoFileInfo> prevFiles = dbHelper->getFileList(streamId, lookbackStartMs, requestedEpochMs);
        if (prevFiles.empty())
        {
            return isoStartTime;
        }

        const VideoFileInfo& prev = prevFiles.back();
        const int64_t prevEndMs = static_cast<int64_t>(prev.m_startTime) + static_cast<int64_t>(prev.m_duration);
        if (prevEndMs <= 0 || prevEndMs > requestedEpochMs)
        {
            // The "previous" file actually contains requestedEpochMs (no gap),
            // or the DB row is malformed. Skip clamp.
            return isoStartTime;
        }

        // Only clamp when the requested time is at most one frame interval
        // past the previous file's last frame.
        uint64_t prevFileFps = prev.m_fileFPS;
        if (prevFileFps == 0)
        {
            prevFileFps = static_cast<uint64_t>(DEFAULT_VIDEO_FRAME_RATE);
        }
        const auto frameIntervalMs = static_cast<int64_t>(1000 / prevFileFps);
        const int64_t gapMs = requestedEpochMs - prevEndMs;
        if (gapMs > frameIntervalMs)
        {
            LOG(info) << "Picture API gap clamp skipped: streamId=" << streamId
                      << ", requested=" << isoStartTime
                      << " is " << gapMs << "ms past previous file end (" << prevEndMs
                      << "ms), exceeds one frame interval (" << frameIntervalMs
                      << "ms at " << prevFileFps << " fps); not an inter-file boundary" << endl;
            return isoStartTime;
        }

        const std::string clampedIso = convertEpocToISO8601_2(prevEndMs * 1000);
        LOG(info) << "Picture API inter-file gap clamp: streamId=" << streamId
                     << ", requested startTime=" << isoStartTime
                     << " (" << requestedEpochMs << "ms) falls in inter-file boundary;"
                     << " previous file=" << prev.m_filePath
                     << " last-frame epoch=" << prevEndMs << "ms (gap=" << gapMs
                     << "ms, frameInterval=" << frameIntervalMs << "ms);"
                     << " clamping startTime to " << clampedIso << endl;
        return clampedIso;
    }
#endif

    // Helper function to save temp file record to database for cleanup
    void saveTempFileToDatabase(const string& deviceId, const string& filePath,
                               const string& streamId, size_t fileSize,
                               int64_t expiryTimestamp, int64_t createdTimestamp,
                               int64_t startTimeMs, const string& fileType)
    {
        auto dbHelper = GET_DB_INSTANCE();
        if (dbHelper)
        {
            nv_vms::TempFilesDBColumns tempFileRecord;
            tempFileRecord.device_id_value = deviceId;
            tempFileRecord.file_path_value = filePath;
            tempFileRecord.expiry_timestamp_value = expiryTimestamp;
            tempFileRecord.created_timestamp_value = createdTimestamp;
            tempFileRecord.stream_id_value = streamId;
            tempFileRecord.file_size_value = fileSize;
            tempFileRecord.start_time_ms_value = startTimeMs;
            tempFileRecord.file_type_value = fileType;

            int result = dbHelper->insertTempFileRecord(tempFileRecord);
            if (result == 0)
            {
                LOG(info) << "Recorded temp image for cleanup: " << filePath << " expires at: " << expiryTimestamp << endl;
            }
            else
            {
                LOG(warning) << "Failed to record temp image in database: " << filePath << endl;
            }
        }
    }

    // Helper function to generate URL response with expiry information
    void generateUrlResponse(Json::Value& response, const string& baseUrl, const string& tempFileName,
                            const string& streamId, bool isReplay, const string& startTime,
                            int expiryMinutesInt, int64_t expiryTimestamp, const string& expiryISO)
    {
        string justFileName = getFileNameWithExtension(tempFileName);
        string imageUrl = baseUrl + TEMP_STORAGE_PATH + "/" + justFileName;
        string webRoot = VmsConfigManager::getInstance()->getWebRootPath();
        string absolutePath = getAbsolutePath(webRoot) + TEMP_STORAGE_PATH + "/" + justFileName;

        response["imageUrl"] = imageUrl;
        response["absolutePath"] = absolutePath;
        response["streamId"] = streamId;
        response["type"] = isReplay ? "replay" : "live";
        response["expiryISO"] = expiryISO;
        response["expiryMinutes"] = expiryMinutesInt;
    }

    // Helper function to calculate expiry times and return ISO string
    string calculateExpiryTime(int expiryMinutesInt, int64_t& expiryTimestamp, int64_t& currentTimestamp)
    {
        auto now = std::chrono::system_clock::now();
        currentTimestamp = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
        auto expiryTime = now + std::chrono::minutes(expiryMinutesInt);
        expiryTimestamp = std::chrono::duration_cast<std::chrono::milliseconds>(expiryTime.time_since_epoch()).count();

        // convertEpocToISO8601_2 expects microseconds, so convert milliseconds to microseconds
        return convertEpocToISO8601_2(expiryTimestamp * 1000);
    }

    // Helper function to generate temp file path with directory creation
    string generateTempFilePath(const string& deviceName, bool isReplay, const string& start_time, string& err_msg)
    {
        // Create temp_images directory
        const string webRoot = VmsConfigManager::getInstance()->getWebRootPath();
        string tempImageDir = webRoot + TEMP_STORAGE_DIR;

        if (!createDir(tempImageDir))
        {
            err_msg = "Failed to create temp images directory: " + tempImageDir;
            return "";
        }

        // Generate task ID using same format as video async tasks
        string sensorPrefix = "";
        if (!deviceName.empty() && deviceName != "disconnected_device")
        {
            const string candidate = sanitizePrefix(deviceName);
            if (!candidate.empty()) {
                sensorPrefix = candidate + "_";
            }
        }

        string timeForId = isReplay ? start_time : getCurrentUtcTime();
        string taskId = sensorPrefix + getUniqueIdFromUTCTime(timeForId, "");

        return tempImageDir + "/" + taskId + ".jpg";
    }

    // Helper function to process URL generation workflow
    VmsErrorCode processUrlGeneration(const string& buffer, const string& start_time,
                                     const string& expiryMinutesStr, shared_ptr<SensorInfo> sensor,
                                     shared_ptr<DeviceManager> deviceManager, Json::Value& response,
                                     string& err_msg)
    {
        // Determine if this is live or replay based on startTime presence
        bool isReplay = !start_time.empty();

        // Parse and validate expiry minutes - use config default if not provided or invalid
        int expiryMinutesInt = stringToInt(expiryMinutesStr, GET_CONFIG().default_file_expiry_minutes);

        // Validate expiry range
        if (expiryMinutesInt < MIN_ALLOWED_EXPIRY_MINUTES)
        {
            err_msg = "Invalid expiryMinutes: " + to_string(expiryMinutesInt) + ". Must be greater than " + to_string(MIN_ALLOWED_EXPIRY_MINUTES);
            return VmsErrorCode::InvalidParameterError;
        }

        // Generate base URL
        string baseUrl = getIngressBaseUrl();
        LOG(info) << "Ingress base URL: " << baseUrl << endl;

        // Generate temp file path
        string deviceName = sensor ? sensor->name : "disconnected_device";
        string tempFileName = generateTempFilePath(deviceName, isReplay, start_time, err_msg);

        if (tempFileName.empty())
        {
            return VmsErrorCode::VMSInternalError;
        }

        // Save binary JPEG data
        if (!writeBinaryFile(tempFileName, buffer))
        {
            err_msg = "Failed to save image file: " + tempFileName;
            return VmsErrorCode::VMSInternalError;
        }

        // Verify file was created
        const size_t fileSize = getFileSizeInBytes(tempFileName);
        if (fileSize == 0)
        {
            err_msg = "Generated image file is empty: " + tempFileName;
            return VmsErrorCode::VMSInternalError;
        }

        // Calculate expiry times
        int64_t currentTimestamp, expiryTimestamp;
        string expiryISO = calculateExpiryTime(expiryMinutesInt, expiryTimestamp, currentTimestamp);

        int64_t startTimeMs = parseTimeToEpochMs(start_time);

        // Save temp file to database for cleanup
        saveTempFileToDatabase(deviceManager->getDeviceId(), tempFileName,
                             sensor->id, fileSize, expiryTimestamp, currentTimestamp,
                             startTimeMs, nv_vms::TempFilesDBColumns::FILE_TYPE_IMAGE);

        // Generate URL response
        generateUrlResponse(response, baseUrl, tempFileName, sensor->id,
                          isReplay, start_time, expiryMinutesInt, expiryTimestamp, expiryISO);

        LOG(info) << (isReplay ? "Replay" : "Live") << " picture URL generated successfully - Size: " << fileSize
                  << " bytes, Expires: " << expiryISO << endl;

        return VmsErrorCode::NoError;
    }

    string sensorStatusEventToString(SensorStatusEvent event)
    {
        switch(event)
        {
            case nv_vms::SensorStatusOnline: return "camera_add";
            case nv_vms::SensorStatusStreaming: return "camera_streaming";
            case nv_vms::SensorStatusProxy: return "camera_proxy";
            case nv_vms::SensorStatusOffline:
            default: return "camera_remove";
        }
    }

    string toDomainName(const string& url, const string& id)
    {
        string domain_url;
        string token;
        if (GET_CONFIG().server_domain_name.empty() || url.empty())
        {
            return url;
        }

        string rtspUrlPrefix = vst_rtsp::rtspServerDomainPrefix(id); /* Need to check, how to avoid this call everytime */
        if(url.find("live") != std::string::npos)
        {
            token = "live/";
        }
        else if(url.find("vod") != std::string::npos)
        {
            token = "vod/";
        }
        else if(url.find("webrtc") != std::string::npos)
        {
            token = "webrtc/";
        }
        else if(url.find(NV_STREAMER) != std::string::npos)
        {
            token = string(NV_STREAMER) + "/";
        }
        else
        {
            return url;
        }

        string sensor_id = url.substr(url.find(token));
        domain_url = rtspUrlPrefix + sensor_id;
        return domain_url;
    }

    VmsErrorCode getSensorStreamList(shared_ptr<DeviceManager> deviceManager, const string sensor_id,
                                        const string& query_string, Json::Value &response)
    {
        if (deviceManager)
        {
            shared_ptr<SensorInfo> sensor = deviceManager->searchSensor(sensor_id);
            if(sensor.get() == nullptr)
            {
                string error_message = string("Invalid Sensor ID " + sensor_id);
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::CameraNotFoundError, response, error_message.c_str());
                return VmsErrorCode::CameraNotFoundError;
            }
            response = sensor->getStreamsJson(deviceManager->getDeviceType() == TYPE_STREAMER);
            return VmsErrorCode::NoError;
        }
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    bool ShouldRefreshSensorCache(const string& deviceId, size_t currentSensorCount)
    {
    #if defined(MONOLITH_MODULE)
        return false;
    #else
        if (currentSensorCount == 0)
        {
            LOG(info) << "No sensors in memory, should refresh from DB" << endl;
            return true;
        }

        int dbSensorCount = GET_DB_INSTANCE()->CountSensorDetails(deviceId);
        if (dbSensorCount < 0)
        {
            LOG(warning) << "Failed to get sensor count from DB, assuming refresh needed" << endl;
            return true;
        }

        if (dbSensorCount != static_cast<int>(currentSensorCount))
        {
            LOG(info) << "Sensor count mismatch - DB: " << dbSensorCount << ", memory: " << currentSensorCount << ", should refresh" << endl;
            return true;
        }
        return false;
    #endif
    }

    VmsErrorCode getSensorStreamList(shared_ptr<DeviceManager> deviceManager, const Json::Value& req_info, Json::Value &response)
    {
        if (deviceManager)
        {
            std::vector<shared_ptr<SensorInfo>> sensors = deviceManager->getSensorList();
            bool refreshCache = ShouldRefreshSensorCache(deviceManager->getDeviceId(), sensors.size());

            if (deviceManager->getDeviceType() != TYPE_STREAMER && refreshCache == false)
            {
                // Fetch all streams in a single DB query instead of one query per sensor
                vector<SensorStreamsDBColumns> allDbStreams = GET_DB_INSTANCE()->readAllStreams();

                for(shared_ptr<SensorInfo> sensor : sensors)
                {
                    std::vector<shared_ptr<StreamInfo>> streams =  sensor->getStreams();

                    // Filter streams for current sensor from the already fetched data
                    vector<SensorStreamsDBColumns> dbStreams;
                    for(const auto& stream : allDbStreams)
                    {
                        if (stream.sensor_id_value == sensor->id)
                        {
                            dbStreams.push_back(stream);
                        }
                    }

                    // Check if database has different number of streams than what's in memory
                    if (dbStreams.size() != streams.size())
                    {
                        refreshCache = true;
                        LOG(info) << "Database has more streams (" << dbStreams.size() << ") than device manager (" << streams.size() << "), fetching from DB" << endl;
                        break;
                    }

                    for(shared_ptr<StreamInfo> stream : streams)
                    {
                        if ((stream->live_proxy_url.empty() || stream->replay_url.empty())
                        && stream->isMainStream)
                        {
                            refreshCache = true;
                            LOG(info) << "Proxy is not set in device manager, so fetching from DB " << endl;
                            break;
                        }

                        // Check if framerate has changed in database
                        for(const auto& dbStream : dbStreams)
                        {
                            if (dbStream.stream_id_value == stream->id)
                            {
                                // Compare framerate from cache with database (as floats to avoid string format differences)
                                float cacheFrameRate = 0.0f, dbFrameRate = 0.0f;
                                try {
                                    if (!stream->settings.encoderValues.frameRate.empty())
                                        cacheFrameRate = std::stof(stream->settings.encoderValues.frameRate);
                                    if (!dbStream.frameRate_value.empty())
                                        dbFrameRate = std::stof(dbStream.frameRate_value);
                                } catch (...) {
                                    // If conversion fails, fall back to string comparison
                                    if (stream->settings.encoderValues.frameRate != dbStream.frameRate_value)
                                    {
                                        refreshCache = true;
                                        LOG(info) << "Frame rate changed in DB (cache: "
                                                  << stream->settings.encoderValues.frameRate
                                                  << ", DB: " << dbStream.frameRate_value
                                                  << "), fetching from DB" << endl;
                                        break;
                                    }
                                }
                                // Use tolerance for float comparison (0.001 fps difference is negligible)
                                if (std::fabs(cacheFrameRate - dbFrameRate) > 0.001f)
                                {
                                    refreshCache = true;
                                    LOG(info) << "Frame rate changed in DB (cache: "
                                              << stream->settings.encoderValues.frameRate
                                              << ", DB: " << dbStream.frameRate_value
                                              << "), fetching from DB" << endl;
                                    break;
                                }
                            }
                        }

                        if (refreshCache)
                        {
                            break;
                        }
                    }

                    if (refreshCache)
                    {
                        break;
                    }
                }
            }

            std::string username = req_info.get("username", EMPTY_STRING).asString();
            if (refreshCache)
            {
                sensors = deviceManager->getSensorList(refreshCache);
            }
            for(shared_ptr<SensorInfo> sensor : sensors)
            {
                std::vector<shared_ptr<StreamInfo>> streams =  sensor->getStreams();
                Json::Value jstreams;
                Json::Value& streamArray = jstreams[sensor->id];  // Create object with sensor ID as key
                streamArray = sensor->getStreamsJson(deviceManager->getDeviceType() == TYPE_STREAMER);

                if (deviceManager->getDeviceType() == TYPE_STREAMER)
                {
                    for (auto& stream : streamArray)
                    {
                        stream["url"] = toDomainName(stream["url"].asString(), sensor->id);
                    }
                }

                response.append(jstreams);
            }
            if (response == Json::nullValue)
            {
                response = Json::arrayValue;
            }
            return VmsErrorCode::NoError;
        }
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    VmsErrorCode getSensorStreamListFromDB(shared_ptr<DeviceManager> deviceManager, Json::Value &response, bool fetchFromDB)
    {
        if (!deviceManager)
        {
            SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
            return VmsErrorCode::VMSInternalError;
        }

        std::vector<shared_ptr<SensorInfo>> sensors = deviceManager->getSensorList(fetchFromDB);
        bool isStreamer = deviceManager->getDeviceType() == TYPE_STREAMER;

        for (const auto& sensor : sensors)
        {
            Json::Value jstreams;
            Json::Value& streamArray = jstreams[sensor->id];
            streamArray = sensor->getStreamsJson(isStreamer);

            if (isStreamer)
            {
                for (auto& stream : streamArray)
                {
                    stream["url"] = toDomainName(stream["url"].asString(), sensor->id);
                }
            }

            response.append(jstreams);
        }
        if (response == Json::nullValue)
        {
            response = Json::arrayValue;
        }
        return VmsErrorCode::NoError;
    }

    void updateSensorDetailsToDB(const string deviceId, shared_ptr<SensorInfo> sensor, bool force)
    {
        int ret = -1;
        if(sensor.get() != nullptr)
        {
            SensorDetailsDBColumns row =  GET_DB_INSTANCE()->readSensorDetails(deviceId, sensor->id);
            vector<shared_ptr<StreamInfo>> streams = sensor->streams;
            if (row.sensor_id_value.empty() || force)
            {
                row.name_value = sensor->name;
            }

            Json::Value position;
            Json::Value origin;
            Json::Value geoLocation;
            Json::Value coordinates;
            row.device_id_value = deviceId;
            row.sensor_id_value = sensor->id;
            row.sensor_hw_id_value = sensor->sensorId;
            row.ip_value = sensor->ip;
            row.hardware_value = sensor->hardware;
            row.manufacturer_value = sensor->manufacturer;
            row.firmware_version_value = sensor->firmware_version;
            row.serial_number_value = sensor->serial_number;
            row.hardware_id_value = sensor->hardware_id;
            row.location_value = sensor->location;
            row.tags_value = sensor->tags;
            row.username_value = sensor->user;
            row.password_value = sensor->password;
            row.url_value = sensor->url;
            row.type_value = sensor->type;
            position["direction"] = sensor->position.direction;
            position["depth"] = sensor->position.depth;
            position["field_of_view"] = sensor->position.fieldOfView;
            origin["latitude"] = sensor->position.origin.first;
            origin["longitude"] = sensor->position.origin.second;
            position["origin"] = origin;
            geoLocation["latitude"] = sensor->position.geoLocation.first;
            geoLocation["longitude"] = sensor->position.geoLocation.second;
            position["geo_location"] = geoLocation;
            coordinates["x"] = sensor->position.coordinates.first;
            coordinates["y"] = sensor->position.coordinates.second;
            position["coordinates"] = coordinates;
            row.position_value = jsonToString(position);
            row.users_value = sensor->getUsersString();
            row.isRemoteSensor_value = sensor->isRemoteSensor ? "true" : "false";
            row.remoteDeviceId_value = sensor->remoteDeviceId;
            row.remoteDeviceName_value = sensor->remoteDeviceName;
            row.remoteDeviceLocation_value = sensor->remoteDeviceLocation;
            row.httpStatus_value = sensor->getHttpErrorStatus().first;
            row.sensorStatus_value = sensor->getSensorStatus();
            if(streams.size() > 0)
            {
                /* Add Sensor into the sensor details table */
                for(uint32_t i = 0; i < streams.size(); i++ )
                {
                    shared_ptr<StreamInfo> stream = streams[i];
                    if (stream->isMainStream)
                    {
                        ret = GET_DB_INSTANCE()->insertRowSensorDetails(row);
                        if ( ret == -1)
                        {
                            LOG(error) << "Error updating Camera details into DB" << endl;
                        }
                        break;
                    }
                }

                /* Add streams into the streams table */
                for(uint32_t i = 0; i < streams.size(); i++ )
                {
                    SensorStreamsDBColumns stream_row;
                    shared_ptr<StreamInfo> stream = streams[i];
                    stream_row.sensor_id_value = row.sensor_id_value;
                    stream_row.stream_id_value = stream->id;
                    stream_row.live_url_value = stream->live_url;
                    stream_row.replay_url_value = stream->replay_url;
                    stream_row.proxy_url_value = stream->live_proxy_url;

                    SensorVideoEncoderSettingsValues& enc_values = stream->getvideoEncoderValues();
                    stream_row.resolution_value = enc_values.resolution.getString();
                    stream_row.encoding_value = enc_values.encoding;
                    stream_row.encodingInterval_value = enc_values.encodingInterval;
                    stream_row.frameRate_value = enc_values.frameRate;
                    stream_row.duration_value = to_string(stream->duration);
                    stream_row.streamStatus_value = stream->getErrorStatus().first;
                    stream_row.streamType_value = stream->stream_type;
                    stream_row.encodingProfile_value = enc_values.encodingProfile;
                    stream_row.numFrames_value = enc_values.numFrames;
                    stream_row.bitrate_value = enc_values.bitrate;

                    // If cloud storage is enabled and type is minio, set storage location to cloud
                    if (GET_CONFIG().enable_cloud_storage && GET_CONFIG().cloud_storage_type == nv_vms::StorageConstants::MINIO_TYPE
                    && (stream->live_url.find("s3://") != string::npos || !stream->live_url.empty()))
                    {
                        stream_row.storageLocation_value = static_cast<int64_t>(StreamStorageTypeCloud);
                    }
                    else
                    {
                        stream_row.storageLocation_value = static_cast<int64_t>(stream->storageLocation);
                    }

                    // Add audio encoder values to database
                    SensorAudioEncoderSettingsValues& audio_enc_values = stream->getAudioEncoderValues();
                    stream_row.audio_container_value = audio_enc_values.container;
                    stream_row.audio_encoding_value = audio_enc_values.encoding;
                    stream_row.audio_sample_rate_value = audio_enc_values.sample_rate;
                    stream_row.audio_bps_value = audio_enc_values.bits_per_sample;
                    stream_row.audio_channels_value = audio_enc_values.channels;

                    if (sensor->type == SENSOR_TYPE_NVSTREAM)
                    {
                        // Using hardware_id field to store container format of file.
                        row.hardware_id_value = enc_values.container;
                    }
                    if (stream->isMainStream)
                    {
                        stream_row.isMainStream_value = "true";
                    }
                    stream_row.streamName_value = stream->name;
                    stream_row.isBframesPresent_value = enc_values.isBframesPresent ? 1 : 0;
                    ret = GET_DB_INSTANCE()->insertRowStream(stream_row);
                    if ( ret == -1)
                    {
                        LOG(error) << "Error updating Stream details into DB" << endl;
                    }
                }
            }
            else
            {
                ret = GET_DB_INSTANCE()->insertRowSensorDetails(row);
                if ( ret == -1)
                {
                    LOG(error) << "Error updating Camera details into DB" << endl;
                }
            }
        }
    }

    /* Generates a 2048-bit RSA key. */
    EVP_PKEY* generate_key()
    {
        EVP_PKEY *pkey = nullptr;
        EVP_PKEY_CTX *pctx = nullptr;

        // Create the context for key generation
        pctx = EVP_PKEY_CTX_new_id(EVP_PKEY_RSA, nullptr);
        if (!pctx)
        {
            LOG(error) << "Unable to create EVP_PKEY_CTX." << endl;
            return nullptr;
        }

        // Initialize key generation
        if (EVP_PKEY_keygen_init(pctx) <= 0)
        {
            LOG(error) << "Unable to initialize key generation." << endl;
            EVP_PKEY_CTX_free(pctx);
            return nullptr;
        }

        // Set RSA key size
        if (EVP_PKEY_CTX_set_rsa_keygen_bits(pctx, 2048) <= 0)
        {
            LOG(error) << "Unable to set RSA key size." << endl;
            EVP_PKEY_CTX_free(pctx);
            return nullptr;
        }

        // Generate the RSA key
        if (EVP_PKEY_keygen(pctx, &pkey) <= 0)
        {
            LOG(error) << "Unable to generate RSA key." << endl;
            EVP_PKEY_CTX_free(pctx);
            return nullptr;
        }

        // Clean up
        EVP_PKEY_CTX_free(pctx);
        return pkey;
    }

    /* Generates a self-signed x509 certificate. */
    X509* generate_x509(EVP_PKEY *pkey)
    {
        X509 *x509 = X509_new();
        if (!x509)
        {
            LOG(error) << "Unable to create X509 structure." << endl;
            return nullptr;
        }

        /* Set the serial number. */
        ASN1_INTEGER_set(X509_get_serialNumber(x509), 1);

        /* This certificate is valid from now until exactly 100 years from now. */
        X509_gmtime_adj(X509_get_notBefore(x509), 0);
        X509_gmtime_adj(X509_get_notAfter(x509), (long)60*60*24*365*100);

        /* Set the public key for our certificate. */
        X509_set_pubkey(x509, pkey);

        /* We want to copy the subject name to the issuer name. */
        X509_NAME * name = X509_get_subject_name(x509);

        /* Set the country code and common name. */
        string common_name = getRandomCommonName();
        X509_NAME_add_entry_by_txt(name, "C",  MBSTRING_ASC, (unsigned char *)"US",        -1, -1, 0);
        X509_NAME_add_entry_by_txt(name, "O",  MBSTRING_ASC, (unsigned char *)"NVIDIA", -1, -1, 0);
        X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC, (unsigned char *)(common_name.c_str()), -1, -1, 0);

        /* Now set the issuer name. */
        X509_set_issuer_name(x509, name);

        /* Actually sign the certificate with our key. */
        if (!X509_sign(x509, pkey, EVP_sha1()))
        {
            LOG(error) << "Error signing certificate." << endl;
            return nullptr;
        }
        return x509;
    }

    bool write_cert_to_disk(std::string certFile, EVP_PKEY * pkey, X509 * x509)
    {
        bool success = false;
        FILE *x509_file = fopen(certFile.c_str(), "wb");
        if (!x509_file)
        {
            LOG(error) << "Unable to open cert pem for writing => " << certFile << endl;
            return false;
        }

        /* Write the certificate to disk. */
        bool ret = PEM_write_X509(x509_file, x509);
        if (!ret)
        {
            LOG(error) << "Unable to write certificate to disk." << endl;
            success = false;
            goto exit;
        }

        /* Write the key to disk in same file */
        ret = PEM_write_PrivateKey(x509_file, pkey, nullptr, nullptr, 0, nullptr, nullptr);
        if (!ret)
        {
            LOG(error) << "Unable to write key to disk." << endl;
            success = false;
            goto exit;
        }
        success = true;
    exit:
        fclose(x509_file);
        return success;
    }

    std::string getSslCertificate()
    {
        string certFile = "";
        certFile = GET_CONFIG().vst_data_path + string("/") + SELF_SIGNED_CERTIFICATE_FILE_NAME;
        if (isFileExist(certFile))
        {
            LOG(info) << "SSL certificate file path:" << certFile << endl;
            return certFile;
        }

        /* Generate the key. */
        EVP_PKEY * pkey = generate_key();
        if (!pkey)
        {
            LOG(error) << "Failed to generate the RSA key" << endl;
            return "";
        }

        /* Generate the certificate. */
        X509 * x509 = generate_x509(pkey);
        if (!x509)
        {
            LOG(error) << "Failed to generate the X509 certificate" << endl;
            certFile = "";
            goto exit;
        }

        bool ret;
        ret = write_cert_to_disk(certFile, pkey, x509);
        if (!ret)
        {
            LOG(info) << "Failed to write ssl certificate:" << certFile << endl;
            certFile = "";
            goto exit;
        }
        updateFilePermissions(certFile, permissions::owner_read | permissions::group_read);
        LOG(info) << "Written ssl certificate Successfully at:" << certFile << endl;

    exit:
        EVP_PKEY_free(pkey);
        X509_free(x509);
        return certFile;
    }

    /* free memory and buffers */
    void evp_cleanup(EVP_CIPHER_CTX *ctx_)
    {
        EVP_CIPHER_CTX_free(ctx_);
        CRYPTO_cleanup_all_ex_data();
        EVP_cleanup();
    }

    void evp_setup(int dir, EVP_CIPHER_CTX *ctx_, const EVP_CIPHER *cryptoAlgorithm_, string &key_, string &iv_)
    {
        // AES requires 16-byte IV - pad or truncate the input IV to 16 bytes
        string paddedIV = iv_;
        const size_t AES_IV_SIZE = 16;
        
        if (paddedIV.length() < AES_IV_SIZE)
        {
            // Pad with zeros if IV is too short
            LOG(verbose) << "IV too short (" << paddedIV.length() << " bytes), padding to " 
                        << AES_IV_SIZE << " bytes for IV: " << iv_ << endl;
            paddedIV.resize(AES_IV_SIZE, '\0');
        }
        else if (paddedIV.length() > AES_IV_SIZE)
        {
            // Truncate if IV is too long
            LOG(verbose) << "IV too long (" << paddedIV.length() << " bytes), truncating to " 
                        << AES_IV_SIZE << " bytes for IV: " << iv_ << endl;
            paddedIV = paddedIV.substr(0, AES_IV_SIZE);
        }
        
        if (0 == dir)
        {
            EVP_EncryptInit_ex(ctx_, cryptoAlgorithm_, nullptr, nullptr, nullptr);
            EVP_CIPHER_CTX_set_key_length(ctx_, key_.size());
            EVP_EncryptInit_ex(ctx_, nullptr, nullptr,
                            (const unsigned char *)(key_.data()),
                            (const unsigned char *)(paddedIV.data()));
        }
        else
        {
            EVP_DecryptInit_ex(ctx_, cryptoAlgorithm_, nullptr, nullptr, nullptr);
            EVP_CIPHER_CTX_set_key_length(ctx_, key_.size());
            EVP_DecryptInit_ex(ctx_, nullptr, nullptr,
                            (const unsigned char *)(key_.data()),
                            (const unsigned char *)(paddedIV.data()));
        }

        EVP_CIPHER_CTX_set_padding(ctx_, 1);
    }

    int evp_blocksize(EVP_CIPHER_CTX *ctx_)
    {
        return EVP_CIPHER_CTX_block_size(ctx_);
    }

    bool evp_final(std::string &out, EVP_CIPHER_CTX *ctx_, int dir_)
    {
        out.resize(evp_blocksize(ctx_));
        int resultLength;
        if (0 == dir_)
        {
            if (0 == EVP_EncryptFinal_ex(ctx_,
                                        (unsigned char *)out.data(),
                                        &resultLength))
            {
                return false;
            }
        }
        else
        {
            if (0 == EVP_DecryptFinal_ex(ctx_,
                                        (unsigned char *)out.data(),
                                        &resultLength))
            {
                return false;
            }
        }
        out.resize(resultLength);
        return true;
    }

    bool evp_update(const std::string &in, std::string &out, EVP_CIPHER_CTX *ctx_, int dir_)
    {
        if (0 == in.size())
            return true;

        out.resize(in.size() + evp_blocksize(ctx_));
        int resultLength;
        if (0 == dir_)
        {
            if (0 == EVP_EncryptUpdate(ctx_,
                                    (unsigned char *)out.data(),
                                    &resultLength,
                                    (unsigned char *)in.data(),
                                    in.size()))
            {
                return false;
            }
        }
        else
        {
            if (0 == EVP_DecryptUpdate(ctx_,
                                    (unsigned char *)out.data(),
                                    &resultLength,
                                    (unsigned char *)in.data(),
                                    in.size()))
            {
                return false;
            }
        }
        out.resize(resultLength);
        return true;
    }

    /* encrypt input and get base64 encoded cipher text output */
    bool encrypt_data(const std::string &input, std::string &output, string &iv_)
    {
        string key_ = get_aes_key();
        if (key_.empty())
        {
            LOG(error) << "encrypt_data failed: AES key is empty" << endl;
            return false;
        }
        EVP_CIPHER_CTX *ctx_ = EVP_CIPHER_CTX_new();
        const EVP_CIPHER *cryptoAlgorithm_ = EVP_aes_256_cbc();
        evp_setup(0, ctx_, cryptoAlgorithm_, key_, iv_);
        std::string u, f;
        if (!evp_update(input, u, ctx_, 0))
        {
            LOG(error) << "encrypt_data failed: evp_update failed for IV: " << iv_ << endl;
            evp_cleanup(ctx_);
            return false;
        }

        if (!evp_final(f, ctx_, 0))
        {
            LOG(error) << "encrypt_data failed: evp_final failed for IV: " << iv_ << endl;
            evp_cleanup(ctx_);
            return false;
        }
        output = u + f;
        output = base64_encode(output.c_str(), output.length());
        evp_cleanup(ctx_);
        return true;
    }

    /* decrypt input and get base64 encoded plain text output */
    bool decrypt_data(const std::string &input, std::string &output, string &iv_)
    {
        string key_ = get_aes_key();
        if (key_.empty())
        {
            LOG(error) << "decrypt_data failed: AES key is empty" << endl;
            return false;
        }
        EVP_CIPHER_CTX *ctx_ = EVP_CIPHER_CTX_new();
        const EVP_CIPHER *cryptoAlgorithm_ = EVP_aes_256_cbc();
        string str = base64_decode(input);
        evp_setup(1, ctx_, cryptoAlgorithm_, key_, iv_);
        std::string u, f;
        if (!evp_update(str, u, ctx_, 1))
        {
            LOG(error) << "decrypt_data failed: evp_update failed for IV: " << iv_ << endl;
            evp_cleanup(ctx_);
            return false;
        }
        if (!evp_final(f, ctx_, 1))
        {
            LOG(error) << "decrypt_data failed: evp_final failed for IV: " << iv_ << endl;
            evp_cleanup(ctx_);
            return false;
        }
        output = u + f;
        evp_cleanup(ctx_);
        return true;
    }

    bool getSha256(const string &str, string &hashedStr)
    {
        EVP_MD_CTX *mdctx = nullptr;
        const EVP_MD *md = EVP_sha256();
        unsigned char hash[EVP_MAX_MD_SIZE];
        unsigned int hash_len = 0;

        // Create and initialize the message digest context
        mdctx = EVP_MD_CTX_new();
        if (!mdctx)
        {
            LOG(error) << "Unable to create EVP_MD_CTX." << endl;
            return false;
        }

        if (EVP_DigestInit_ex(mdctx, md, nullptr) <= 0)
        {
            LOG(error) << "Unable to initialize digest." << endl;
            EVP_MD_CTX_free(mdctx);
            return false;
        }

        if (EVP_DigestUpdate(mdctx, str.c_str(), str.size()) <= 0)
        {
            LOG(error) << "Unable to update digest." << endl;
            EVP_MD_CTX_free(mdctx);
            return false;
        }

        if (EVP_DigestFinal_ex(mdctx, hash, &hash_len) <= 0)
        {
            LOG(error) << "Unable to finalize digest." << endl;
            EVP_MD_CTX_free(mdctx);
            return false;
        }

        // Convert hash to hex string
        stringstream ss;
        for (unsigned int i = 0; i < hash_len; i++)
        {
            ss << hex << setw(2) << setfill('0') << (int)hash[i];
        }
        hashedStr = ss.str();

        EVP_MD_CTX_free(mdctx);
        return true;
    }

    void addSensorToRemoteDevice(shared_ptr<SensorInfo>& sensor, std::shared_ptr<DeviceManager> deviceManager)
    {
        string remoteDeviceAddress = GET_CONFIG().remote_vst_address + string("/api/v1/sensor/add");
        if(deviceManager && sensor.get())
        {
            string out;
            Json::Value in;
            in["sensorIp"] = sensor->ip;
            in["sensorId"] = sensor->id;
            in["isRemoteSensor"] = true;
            in["remoteDeviceId"] = deviceManager->id;
            in["remoteDeviceName"] = deviceManager->name;
            in["remoteDeviceLocation"] = deviceManager->location;
            in["name"] = sensor->name;
            in["sensorStatus"] = sensor->getHttpErrorStatus().first;

            LOG(info) << "Sending sensor/add request JSON: \n" << in << endl;
            if (curlPostRequest(remoteDeviceAddress, out, in, VmsConfigManager::getInstance()->getNGCAuthHeaders()))
            {
                Json::Value jout = stringToJson(out);
                LOG(info) << "Received response from remoteDeviceAddress JSON: \n" << jout.toStyledString() << endl;
            }
        }
    }

    void updateSensorInfoToRemoteVst(SensorInfo& sensorInfo)
    {
        string remoteVstaddress = GET_CONFIG().remote_vst_address + string("/api/v1/sensor/") + sensorInfo.id +  string("/info");

        string out;
        Json::Value in;
        in["method"] = "post";
        in["isRemoteSensor"] = true;
        in["sensorId"] = sensorInfo.id;
        in["name"] = sensorInfo.name;
        in["hardware"] = sensorInfo.hardware;
        in["manufacturer"] = sensorInfo.manufacturer;
        in["serialNumber"] = sensorInfo.serial_number;
        in["firmwareVersion"] = sensorInfo.firmware_version;
        in["hardwareId"] = sensorInfo.hardware_id;
        in["location"] = sensorInfo.location;
        in["position"]["tags"] = sensorInfo.tags;
        in["position"]["depth"] = sensorInfo.position.depth;
        in["position"]["fieldOfView"] = sensorInfo.position.fieldOfView;
        in["position"]["direction"] = sensorInfo.position.direction;
        in["position"]["origin"]["latitude"] = sensorInfo.position.origin.first;
        in["position"]["origin"]["longitude"] = sensorInfo.position.origin.second;
        in["position"]["coordinates"]["x"] = sensorInfo.position.coordinates.first;
        in["position"]["coordinates"]["y"] = sensorInfo.position.coordinates.second;
        in["position"]["geoLocation"]["latitude"] = sensorInfo.position.geoLocation.first;
        in["position"]["geoLocation"]["longitude"] = sensorInfo.position.geoLocation.second;


        LOG(info) << "Sending sensor/info request JSON: \n" << in << endl;
        if (curlPostRequest(remoteVstaddress, out, in, VmsConfigManager::getInstance()->getEdgeDeviceHeaders(true)))
        {
            Json::Value jout = stringToJson(out);
            LOG(info) << "Received response from remoteVst JSON: \n" << jout.toStyledString() << endl;
        }
    }

    void updateSensorNetworkInfoToRemoteVst(const SensorNetworkInfo &netInfo, const string& sensor_id)
    {
        string remoteVstaddress = GET_CONFIG().remote_vst_address + string("/api/v1/sensor/") + sensor_id +  string("/network");
        string out;
        Json::Value in;
        in["method"] = "post";
        in["isIpv4Enabled"] = netInfo.enableIpv4;
        in["dhcpV4"] = netInfo.enableDhcp4;
        in["ipAddressV4"] = netInfo.IPAddr4;
        in["subnetMaskV4"] = netInfo.prefixLen4;
        in["isIpv6Enabled"] = netInfo.enableIpv6;
        in["dhcpV6"] = netInfo.enableDhcp6;
        in["ipAddressV6"] = netInfo.IPAddr6;
        in["subnetMaskV6"] = netInfo.prefixLen6;


        LOG(info) << "Sending sensor/network request JSON: \n" << in << endl;
        if (curlPostRequest(remoteVstaddress, out, in, VmsConfigManager::getInstance()->getEdgeDeviceHeaders(true)))
        {
            Json::Value jout = stringToJson(out);
            LOG(info) << "Received response from remoteVst JSON: \n" << jout.toStyledString() << endl;
        }
    }

    void removeSensorFromRemoteDevice(const string& sensor_id)
    {
        vector<string> headers;
        string response;
        if (!sensor_id.empty())
        {
            string remote_device_url = GET_CONFIG().remote_vst_address + string("/api/v1/sensor/") + sensor_id;
            bool res = curlDeleteRequest(remote_device_url, VmsConfigManager::getInstance()->getEdgeDeviceHeaders(true), response);
            if (res == false)
            {
                LOG(error) << "curlDeleteRequest request failed for remoteDevice" << endl;
            };
        }
    }

    void notifyEvent(const SensorStatus& status, const string& sensor_url, const SensorVideoEncoderSettingsValues* encoder_values)
    {
        string change = vst_common::sensorStatusEventToString(status.event);

        Json::Value payload, event, metadata;
        event["camera_id"] = status.sensorId;
        event["camera_name"] = status.sensorName;
        event["camera_url"] = change == "camera_add" ? "" : sensor_url; // Use original URL for payload
        event["change"] = change;
        event["tags"] = status.tags;
        if (status.type.empty() == false)
        {
            metadata["sensor_type"] = status.type;
        }
        if (encoder_values != nullptr)
        {
            metadata["codec"] = encoder_values->encoding;
            metadata["resolution"] = encoder_values->resolution.getString();
            metadata["framerate"] = encoder_values->frameRate;
            event["metadata"] = metadata;
        }
        payload["created_at"] = status.timeStamp;
        payload["source"] = "vst";
        payload["alert_type"] = "camera_status_change";
        payload["event"] = event;

        INotificationInterface* notifier = NotificationFactory::CreatePlatformNotification();
        if (notifier)
        {
            notifier->sendMessage(payload);
        }
        else
        {
            LOG(error) << "Notification Manager instance is not created" << endl;
        }

        // Create a copy of payload for logging with masked URL
        Json::Value logPayload = payload;
        logPayload["event"]["camera_url"] = change == "camera_add" ? "" : secureUrlForLogging(sensor_url);
        LOG(info) << logPayload.toStyledString() << endl;
    }

    void notifySensorStatusEvent(SensorStatusEvent event, shared_ptr<SensorInfo> sensor)
    {
        if(sensor != nullptr)
        {
            vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
            if(streams.size() > 0 && streams[0])
            {
                SensorStatus status;
                status.timeStamp = getCurrentTime();
                status.event = event;
                status.sensorId = sensor->id;
                status.sensorName = streams[0]->name;
                string sensor_url = toDomainName(streams[0]->live_proxy_url, streams[0]->id); // Get first/main stream proxy url
                if (event == SensorStatusStreaming && sensor_url.empty())
                {
                    return; // Skip notification if camera url is not present.
                }
                LOG(info) << "notifySensorStatusEvent sensor:" << streams[0]->name << ", event:" << secureUrlForLogging(sensor_url) << endl;
                notifyEvent(status, sensor_url);
            }
        }
    }

    void notifyStreamStatusEvent(SensorStatusEvent event, shared_ptr<StreamInfo> stream)
    {
        if (stream == nullptr)
        {
            LOG(error) << "Invalid stream parameter for notifyStreamStatusEvent" << endl;
            return;
        }

        // Build and send notification for this specific stream
        SensorStatus status;
        status.timeStamp = getCurrentTime();
        status.event = event;
        status.sensorId = stream->id;
        status.sensorName = stream->name;
        string stream_url = toDomainName(stream->live_proxy_url, stream->id);

        if (event == SensorStatusStreaming && stream_url.empty())
        {
            return; // Skip notification if stream url is not present.
        }

        LOG(info) << "notifyStreamStatusEvent stream:" << stream->name << " (streamId: " << stream->id << "), event:" << secureUrlForLogging(stream_url) << endl;
        notifyEvent(status, stream_url);
    }

    int addSensorManually(shared_ptr<SensorInfo>& sensor, string& response, std::shared_ptr<DeviceManager> deviceManager)
    {
        if (deviceManager == nullptr || !sensor.get())
        {
            LOG(error) << "Device Manager or sensor object is NULL" << endl;
            return -1;
        }

        // Not applicable for remote sensor/ bypass
        if (deviceManager->isSpaceForNewSensor() == false)
        {
            LOG(info) << "Max count of supported sensors is reached, can't add new sensor" << endl;
            sensor->updateHttpErrorStatus(std::make_pair(405, "Sensors count limit reached"));
            return -1;
        }

        std::shared_ptr<SensorInfo> existed_sensor = deviceManager->findSensor(sensor->sensorId);
        bool is_existing_sensor = existed_sensor != nullptr ? existed_sensor == sensor : false;
        if(is_existing_sensor && existed_sensor->getSensorStatus() == SensorStatusOnline)
        {
            LOG(info) << "Found sensor is already with vms, so ignore it" << endl;
            sensor->updateHttpErrorStatus(std::make_pair(405, "Sensor already Exists"));
            return -1;
        }
        if (!sensor->isRemoteSensor)
        {
            sensor->isRemoteSensor = false;
            sensor->remoteDeviceId = deviceManager->getDeviceId();
            sensor->remoteDeviceName = deviceManager->getDeviceName();
            sensor->remoteDeviceLocation = deviceManager->getDeviceLocation();
        }
        std::shared_ptr<SensorInfo> updated_sensor = deviceManager->addOrUpdateSensor(*sensor);
        if (updated_sensor)
        {
            sensor = updated_sensor;
            SensorStatus status;
            status.timeStamp = getCurrentTime();
            status.sensorId = updated_sensor->id;
            status.sensorName = updated_sensor->name;
            status.serverId = deviceManager->getDeviceId();
            status.event = SensorStatusOnline;
            status.type = TYPE_VST;

            // Update sensor status only when sensor is local
            if (!sensor->isRemoteSensor)
            {
                deviceManager->updateSensorStatus(status);
            }

            if (status.event != SensorStatusUnknown)
            {
                LOG(info) << "New Sensor Found: " << sensor->ip << endl;
                LOG(info) << "Time: " << status.timeStamp << endl;
                LOG(info) << "EventId: " << SensorStatus::getEventString(status.event) << endl;
                LOG(info) << "CameraId: " << status.sensorId << endl;
                LOG(info) << "ServerId: " << status.serverId << endl;

                if (sensor->m_notify == true)
                {
                    notifyEvent(status, "");
                }
            }
        }
        else
        {
            LOG(error) << "Could Not Add/Update Sensor" << endl;
        }

        // return 0 for remote sensor
        if (sensor->type == SENSOR_TYPE_REMOTE)
        {
            return 0;
        }
        // Otherwise check status code
        if ((sensor->type == SENSOR_TYPE_WEBRTC) && (sensor->getHttpErrorStatus().first != 200))
        {
            response = sensor->getHttpErrorStatus().second;
            return -1;
        }
        return 0;
    }

#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
    bool fetchOptsFromDb(const string& sensorId, const string& startTime,
                         std::map<std::string, std::string, std::less<>>& dbOpts, string& errMsg)
    {
        auto dbHelper = GET_DB_INSTANCE();
        if (!dbHelper)
        {
            errMsg = "Error accessing database for sensor: " + sensorId + " and startTime: " + startTime;
            LOG(error) << errMsg << endl;
            return false;
        }

        int64_t epochStartTime = getEpocTimeInMS(startTime);
        auto rows = dbHelper->readVideoRecord(sensorId, epochStartTime, epochStartTime + 1);
        if (rows.empty())
        {
            errMsg = "No data found in database for sensor: " + sensorId + " and startTime: " + startTime;
            LOG(error) << errMsg << endl;
            return false;
        }

        if (!rows[0].codec_value.empty())
        {
            dbOpts["codec"] = rows[0].codec_value;
        }

        if (!rows[0].resolution_value.empty())
        {
            size_t xPos = rows[0].resolution_value.find('x');
            if (xPos != string::npos)
            {
                dbOpts["source_width"] = rows[0].resolution_value.substr(0, xPos);
                dbOpts["source_height"] = rows[0].resolution_value.substr(xPos + 1);
            }
        }

        LOG(info) << "Disconnected sensor: data retrieved from DB - codec: " << rows[0].codec_value
                  << ", resolution: " << rows[0].resolution_value << endl;
        return true;
    }

    VmsErrorCode getCameraPictureDisconnected(shared_ptr<DeviceManager> deviceManager, const string& sensor_id,
                                               const string& query_string, Json::Value &response, bool isURLRequested)
    {
        string err_msg;
        string start_time, w, h, expiryMinutesStr;
        std::map<std::string, std::string, std::less<>> opts;
        CodecStats stats;

        CivetServer::getParam(query_string, "startTime", start_time);
        if (sensor_id.empty() || start_time.empty())
        {
            err_msg = string("Invalid Sensor ID " + sensor_id + " or startTime " + start_time);
            LOG(error) << err_msg << endl;
            goto report_error;
        }

        stats.setElementName("Capture Image API (Disconnected)");
        stats.startProcessing();

        CivetServer::getParam(query_string, "width", w);
        CivetServer::getParam(query_string, "height", h);
        opts["startTime"] = start_time;
        opts["resize_width"] = w;
        opts["resize_height"] = h;
        opts["image_capture"] = "true";
        opts["peerid"] = "image_capture";
        opts["sensorId"] = sensor_id;
        opts["overlay"] = "false";

        {
            std::map<std::string, std::string, std::less<>> dbOpts;
            if (!fetchOptsFromDb(sensor_id, start_time, dbOpts, err_msg))
            {
                goto report_error;
            }
            opts.insert(dbOpts.begin(), dbOpts.end());
        }

        {
            string url = "file://vod/" + sensor_id + "?startTime=" + start_time;

            LOG(info) << "=== DISCONNECTED SENSOR IMAGE CAPTURE ===" << endl;
            LOG(info) << "Sensor ID: " << sensor_id << ", URL: " << url << endl;

            string buffer;
            try
            {
                std::unique_ptr<CommonVideoSource> videoSource = std::make_unique<CommonVideoSource>(url, opts);
                buffer = videoSource->getBuffer();
                LOG(info) << "Disconnected sensor image capture completed, buffer size: " << buffer.size() << endl;

                std::thread([videoSource = std::move(videoSource)]() mutable {
                    videoSource.reset();
                }).detach();
            }
            catch (const std::exception& e)
            {
                err_msg = e.what();
                LOG(error) << err_msg << endl;
                goto report_error;
            }

            stats.finishProcessing();
            stats.printTotalStats();

            if (!buffer.empty())
            {
                if (isURLRequested)
                {
                    shared_ptr<SensorInfo> tempSensor = std::make_shared<SensorInfo>();
                    tempSensor->id = sensor_id;
                    tempSensor->name = sensor_id;
                    CivetServer::getParam(query_string, "expiryMinutes", expiryMinutesStr);
                    VmsErrorCode urlResult = processUrlGeneration(buffer, start_time, expiryMinutesStr,
                                                                  tempSensor, deviceManager, response, err_msg);
                    if (urlResult != VmsErrorCode::NoError)
                    {
                        LOG(error) << err_msg << endl;
                        return urlResult;
                    }
                }
                else
                {
                    response["content_type"] = "image/jpeg";
                    response["data"] = buffer;
                }
            }
            else
            {
                err_msg = "Encoder image timeout for disconnected sensor";
                LOG(error) << err_msg << endl;
                goto report_error;
            }
            return VmsErrorCode::NoError;
        }
        report_error:
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, err_msg.c_str())
            return VmsErrorCode::VMSInternalError;
    }
#endif

    VmsErrorCode getCameraPicture(shared_ptr<DeviceManager> deviceManager, const string sensor_id,
                                    const string& query_string, Json::Value &response, bool isURLRequested)
    {
#if defined(LIVE_STREAM_MODULE) || defined(REPLAY_STREAM_MODULE) || defined(STREAMBRIDGE_MODULE)
        if (deviceManager)
        {
            string err_msg;
            string start_time;
            string url;
            string w, h;
            string is_overlay = "false";
            string overlay_str;
            Json::Value overlay_json;
            string is_bboxDebug;
            shared_ptr<SensorInfo> sensor = deviceManager->searchSensor(sensor_id);
            shared_ptr<StreamInfo> stream = nullptr;
            std::map<std::string,std::string, std::less<>> opts;
            CodecStats stats;
            string expiryMinutesStr;
            bool is_hhmmss_time = false;

            stats.setElementName("Capture Image API");
            stats.startProcessing();


            if (sensor.get() == nullptr)
            {
                CivetServer::getParam(query_string, "startTime", start_time);
                if (sensor_id.empty() || start_time.empty())
                {
                    err_msg = string("Invalid Sensor ID " + sensor_id);
                    LOG(error) << err_msg << endl;
                    goto report_error;
                }

                return getCameraPictureDisconnected(deviceManager, sensor_id, query_string, response, isURLRequested);
            }
            stream =  sensor->getStream(sensor_id);
            if (stream == nullptr)
            {
                err_msg = "stream is not found";
                goto report_error;
            }

            CivetServer::getParam(query_string, "startTime", start_time);
            opts["startTime"] = start_time;

            CivetServer::getParam(query_string, "expiryMinutes", expiryMinutesStr);
            if (sensor->type == SENSOR_TYPE_FILE)
            {
                // Determine if time format is HH:MM:SS (not ISO 8601 or epoch)
                // ISO 8601 format contains 'T' or 'Z' (e.g., 2025-10-24T12:30:00.094Z)
                // HH:MM:SS format contains ':' but not 'T' or 'Z' (e.g., 12:30:00)
                // Epoch format is all numeric digits
                if (!start_time.empty())
                {
                    bool is_iso_format = (start_time.find('T') != string::npos || start_time.find('Z') != string::npos);
                    bool is_time_format = (start_time.find(':') != string::npos);
                    is_hhmmss_time = is_time_format && !is_iso_format;
                }
            }
            if (deviceManager->getDeviceType() == TYPE_STREAMER || is_hhmmss_time)
            {
                int64_t abs_time_ms = -1;
                if (start_time.empty() == false)
                {
                    abs_time_ms = convertStringToSeconds(start_time);
                }
                else
                {
                    string frameId = "-1";
                    CivetServer::getParam(query_string, "frameId", frameId);
                    int64_t frame_id = stringToInt(frameId, -1);
                    if (frame_id > -1)
                    {
                        double frameRate = stringToDouble(stream->settings.encoderValues.frameRate);
                        if (frameRate <= 0)
                        {
                            LOG(error) << "Framerate info not present, use HH:MM:SS time to get picture"<< endl;
                            err_msg = "Framerate info not present, use HH:MM:SS time to get picture";
                            goto report_error;
                        }
                        abs_time_ms = frame_id * (1000.00/frameRate);
                    }
                }
                /* Return error in case wrong time or frameId provided */
                if (abs_time_ms == -1)
                {
                    err_msg = "Wrong time format or frameId provided";
                    goto report_error;
                }
                /* Check if requested time is execeeeding the file total_duration */
                if ((abs_time_ms / 1000) >= stream->duration)
                {
                    LOG(error) << "Requeted time exceeds the file duration:" << stream->duration << endl;
                    err_msg = "Requeted time exceeds the file duration";
                    goto report_error;
                }
                start_time = to_string(abs_time_ms);
                opts["startTime"] = start_time;
                opts["sensor_type"] = SENSOR_TYPE_NVSTREAM;
                LOG(info) << "Streamer start_time:" << start_time << endl;
            }

            /* TODO MMS Phase 2: Remove this once we add full support of replay for MMS */
            /*
            if (deviceManager->getDeviceType() == TYPE_MMS)
            {
                if (start_time.empty() == false)
                {
                    eraseString(start_time, "-");
                    eraseString(start_time, ":");
                }
            }
            */

            if (!start_time.empty()
                && deviceManager->getDeviceType() != TYPE_STREAMER
                && sensor->type != SENSOR_TYPE_FILE
                && !is_hhmmss_time)
            {
                const std::string clampedStartTime = clampPictureStartTimeForGap(sensor->id, start_time);
                if (clampedStartTime != start_time)
                {
                    start_time = clampedStartTime;
                    opts["startTime"] = start_time;
                }
            }

            /* Prepare the rtsp/file url */
            if (start_time.empty())
            {
                url = stream->live_proxy_url.empty() ? stream->live_url : stream->live_proxy_url;
            }
            else
            {
                url = stream ->replay_url + string("?startTime=") + start_time;
            }

            CivetServer::getParam(query_string, "width", w);
            CivetServer::getParam(query_string, "height", h);
            CivetServer::getParam(query_string, "overlay", overlay_str);
            CivetServer::getParam(query_string, "debug", is_bboxDebug);
            if (!overlay_str.empty())
            {
                overlay_json["overlay"] = stringToJson(overlay_str);
                Json::Value overlay = overlay_json.get("overlay", EMPTY_STRING);
                setOverlayOptsBasedOnJson(opts, overlay);
                if (opts["overlayBbox"] == "true" || opts["overlayTripwire"] == "true" ||
                    opts["overlayRoi"] == "true" || opts["overlayPose"] == "true")
                {
                    is_overlay = "true";
                }
                else
                {
                    is_overlay = "false";
                }
            }
            else
            {
                opts["overlayBbox"] = is_overlay;
            }

            try
            {
                opts["framerate"] = stream->settings.encoderValues.frameRate;
                opts["source_width"] = stream->settings.encoderValues.resolution.width;
                opts["source_height"] = stream->settings.encoderValues.resolution.height;
                opts["container"] = stream->settings.encoderValues.container;
                opts["codec"] = stream->settings.encoderValues.encoding;
                // Determine storage location based on live/replay request
                if (!start_time.empty())
                {
                    opts["storageLocation"] = stream->storageLocation == StreamStorageTypeCloud ? "cloud" : "local";
                }
                else
                {
                    opts["storageLocation"] = "local";
                }
                LOG(info) << "---- Storage location: " << opts["storageLocation"] << endl;
                opts["resize_width"] = w;
                opts["resize_height"] = h;
                opts["image_capture"] = "true";
                opts["peerid"] = "image_capture";  // Set peer ID for image capture mode
                opts["sensorID"] = sensor->name;
                opts["sensorId"] = sensor->id;
                opts["overlay"] = is_overlay;
                if (!is_bboxDebug.empty())
                {
                    opts["bboxDebug"] = is_bboxDebug;
                }

                // Print all options for debugging
                LOG(info) << "=== IMAGE CAPTURE OPTIONS ===" << endl;
                for (const auto& opt : opts) {
                    LOG(info) << "Option: " << opt.first << " = " << opt.second << endl;
                }
                LOG(info) << "=============================" << endl;

                string buffer;

                if (stream->stream_type == StreamType::Webrtc)
                {
                    opts["sensor_type"] = SENSOR_TYPE_WEBRTC;
                }
                if (sensor->type == SENSOR_TYPE_MMS_ONVIF)
                {
                    opts["sensor_type"] = SENSOR_TYPE_MMS_ONVIF;
                }
                else if (url.find("vod") != std::string::npos ||
                    (url.find("webrtc/") != std::string::npos && !start_time.empty()) ||
                    deviceManager->getDeviceType() == TYPE_STREAMER)
                {
                    bool res = replaceString(url, "rtsp://", "file://");
                    if (!res)
                    {
                        LOG(error) << "Replace string failed" << endl;
                    }
                }
                else if (deviceManager->getDeviceType() == TYPE_VST || deviceManager->getDeviceType() == TYPE_MMS)
                {
                    opts["new_dec"] = "true";
                }

                if (sensor->type == SENSOR_TYPE_FILE)
                {
                    opts["sensor_type"] = SENSOR_TYPE_FILE;
                    // Only add file:// prefix if URL doesn't already have a scheme (like s3://, http://, https://)
                    if (url.find("://") == std::string::npos)
                    {
                        url = "file://" + url;
                    }
                }

                // Use CommonVideoSource directly for image capture - unified approach
                try {
                    std::unique_ptr<CommonVideoSource> videoSource = std::make_unique<CommonVideoSource>(url, opts);
                    buffer = videoSource->getBuffer();
                    LOG(info) << "Image capture completed using CommonVideoSource, buffer size: " << buffer.size() << endl;

                    // Async cleanup - destructor runs in background thread to avoid blocking response
                    std::thread([videoSource = std::move(videoSource)]() mutable {
                        videoSource.reset();  // Destructor called here, in background
                        LOG(info) << "Async pipeline cleanup completed for image capture" << endl;
                    }).detach();

                } catch (const std::exception& e) {
                    err_msg = e.what();
                    LOG(error) << err_msg << endl;
                    goto report_error;
                }

                // END: Total API timing and print statistics
                stats.finishProcessing();
                LOG(info) << "📊 API PERFORMANCE SUMMARY for sensor->name:" << sensor->name << ", start_time:" << start_time << endl;
                stats.printTotalStats();

                if (buffer.empty() == false)
                {
                    if (isURLRequested)
                    {
                        // Process URL generation using helper function
                        VmsErrorCode urlResult = processUrlGeneration(buffer, start_time, expiryMinutesStr,
                                                                    sensor, deviceManager, response, err_msg);
                        if (urlResult != VmsErrorCode::NoError)
                        {
                            LOG(error) << err_msg << endl;
                            return urlResult;
                        }
                    }
                    else
                    {
                        response["content_type"] = "image/jpeg";
                        response["data"] = buffer;
                    }
                }
                else
                {
                    err_msg = "Encoder image timeout";
                    LOG(error) << err_msg << endl;
                    goto report_error;
                }
                return VmsErrorCode::NoError;
            }
            catch(const std::invalid_argument& e)
            {
                err_msg = e.what();
                LOG(error) << err_msg << endl;
                goto report_error;
            }
        report_error:
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, err_msg.c_str())
            return VmsErrorCode::VMSInternalError;
        }
#endif
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        return VmsErrorCode::VMSInternalError;
    }

    std::vector <VideoFileInfo> getStreamerFileName(std::string url)
    {
        std::vector <VideoFileInfo> list;
        string token = string(NV_STREAMER);
        string fileName_with_options = url.substr(url.find(token) + token.size());
        string fileName;
        if (fileName_with_options.find("?") != string::npos)
        {
            fileName = fileName_with_options.substr(0, fileName_with_options.find("?"));
        }
        else
        {
            fileName = fileName_with_options;
        }

        LOG(info) << "file Name: " << fileName << endl;
        if (isFileExist(fileName))
        {
            VideoFileInfo file;
            file.m_filePath = fileName;
            list.push_back(file);
        }
        else
        {
            LOG(error) << "File " << fileName << " not present." << endl;
        }
        return list;
    }

    void deleteWebrtcSensorDetails(shared_ptr<SensorInfo> sensor)
    {
        // Remove streams
        if(sensor != nullptr)
        {
            vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
            for (uint32_t j = 0; j < streams.size(); j++)
            {
                shared_ptr<StreamInfo> stream = streams[j];
                if (!stream.get())
                {
                    continue;
                }
                DecoderPool::getInstance()->removeStream(stream->live_proxy_url);

                StreamMonitor* streamMonitor = StreamMonitor::getInstance();
                if (streamMonitor)
                {
                    streamMonitor->removeStream(stream);
                }
                /* Free ports in case they are allocated */
                if (stream->stream_type == StreamType::Udp)
                {
                    vector<string> url_info = splitString(stream->live_url, ":");
                    for (size_t k = 1; k < url_info.size(); k++)
                    {
                        int port = stringToInt(url_info[k], 0);
                        UdpClientPool::getInstance()->freeUdpPort(port);
                    }
                }
            }
        }

        GET_DB_INSTANCE()->deleteSensorDetails(sensor->id); // remove DB entry
    }

    int deleteWebrtcSensor(const string sensor_id, std::shared_ptr<DeviceManager> deviceManager)
    {
        int ret = 0;

        shared_ptr<SensorInfo> sensor = deviceManager->getSensorInfo(sensor_id);
        if (sensor)
        {
            StreamRecorder* recorder = GET_RECORDER();
            if (recorder)
            {
                vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
                for (uint32_t j = 0; j < streams.size(); j++)
                {
                    shared_ptr<StreamInfo> stream = streams[j];
                    if (stream.get() && stream->isMainStream)
                    {
                        vst_recorder::removeStream(stream->id);
                    }
                }
            }
        }

        deleteWebrtcSensorDetails(sensor);
        deviceManager->deleteSensor(sensor_id); // remove entry from cache

        return ret;
    }

    VmsErrorCode getRecordTimelines(const string stream_id, const string start_time,
                                    const string end_time, Json::Value &response)
    {
        int64_t startTime = 0, endTime = 0;
        Json::Value value;

        try
        {
            if (!start_time.empty())
            {
                startTime = isoToEpoch(start_time);
            }
            if (!end_time.empty())
            {
                endTime = isoToEpoch(end_time);
            }
        }
        catch (...)
        {
            return VmsErrorCode::InvalidParameterError;
        }

        /* Assume endTime as max int
        ** so that all records will be fetched
        */
        if (endTime == 0)
        {
            endTime = std::numeric_limits<int64_t>::max();
        }

        std::vector<VideoRecordDBColumns> rows = GET_DB_INSTANCE()->readVideoRecord(stream_id, startTime, endTime);
        Json::Value jsonObjTime;
        for (uint32_t i = 0; i < rows.size(); i++)
        {
            if (i == 0)
            {
                jsonObjTime["startTime"] = convertEpocToISO8601_2(int64_t(rows.at(i).start_time_value) * 1000);
            }
            if ((i + 1) < rows.size())
            {
                bool isOverlap = (rows.at(i + 1).start_time_value) < (rows.at(i).start_time_value + (rows.at(i).duration_value));
                if (isOverlap)
                {
                    continue;
                }
                else
                {
                    bool isSegmentGap = ((rows.at(i + 1).start_time_value - (rows.at(i).start_time_value + (rows.at(i).duration_value)) > MAX_TOLERANCE_SECS * 1000));
                    if (isSegmentGap)
                    {
                        jsonObjTime["endTime"] = convertEpocToISO8601_2(int64_t(rows.at(i).start_time_value + (rows.at(i).duration_value)) * 1000);
                        value.append(jsonObjTime);
                        jsonObjTime = Json::nullValue;
                        jsonObjTime["startTime"] = convertEpocToISO8601_2(int64_t(rows.at(i + 1).start_time_value) * 1000);
                    }
                }
            }
            else
            {
                jsonObjTime["endTime"] = convertEpocToISO8601_2(int64_t(rows.at(i).start_time_value + (rows.at(i).duration_value)) * 1000);
                value.append(jsonObjTime);
            }
        }
        response = value;
        return VmsErrorCode::NoError;
    }

    VmsErrorCode GetAllRecordTimelines(const Json::Value& req_info, Json::Value &out)
    {
        const string request_api = req_info.get("url", EMPTY_STRING).asString();
        const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (request_api.empty() || request_method == UNKNOWN_STRING)
        {
            LOG(error) << "Malformed HTTP request" << endl;
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, out)
            return VmsErrorCode::InvalidParameterError;
        }

        if (!(iequals(request_method, "get")))
        {
            LOG(error) << "Request Method is not supported" << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, out, "Request Method is not supported");
            return VmsErrorCode::VMSNotSupportedError;
        }

        int streamCount = 0;
        size_t pos = 0;
        string target = "streams=";
        while ((pos = query_string.find(target, pos)) != string::npos)
        {
            streamCount++;
            pos += target.length();
        }

        vector<string> streamList;
        size_t occurrence = 0;
        for (; streamCount > 0; streamCount--)
        {
            string streamId;
            if (CivetServer::getParam(query_string, "streams", streamId, occurrence) == false)
            {
                LOG(error) << "Failed to get streamId" << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, "Failed to get streamId");
                return VmsErrorCode::InvalidParameterError;
            }
            streamList.push_back(streamId);
            occurrence++;
        }

        for (auto it = streamList.begin(); it != streamList.end(); ++it)
        {
            LOG(verbose) << "Received stream Id: " << *it << endl;
        }

        std::vector<VideoRecordDBColumns> rows = GET_DB_INSTANCE()->readVideoRecord("", 0, std::numeric_limits<int64_t>::max(), streamList);

        std::map<std::string, Json::Value, std::less<>> recordSegments;
        std::map<std::string, std::vector<VideoRecordDBColumns>, std::less<>> streamRecords;

        for(uint32_t row_cnt = 0; row_cnt < rows.size(); row_cnt++)
        {
            std::string streamId = rows.at(row_cnt).stream_id_value;
            streamRecords[streamId].push_back(rows.at(row_cnt));
        }

        Json::Value jsonObjTime;
        for (const auto& entry : streamRecords)
        {
            const std::string& streamId = entry.first;
            const std::vector<VideoRecordDBColumns>& videoRecords = entry.second;

            // Iterate through the vector of VideoRecordDBColumns for this stream ID
            jsonObjTime.clear();
            recordSegments.clear();
            for(uint32_t row_cnt = 0; row_cnt < videoRecords.size(); row_cnt++)
            {
                if (row_cnt == 0)
                {
                    jsonObjTime["startTime"] = convertEpocToISO8601_2(int64_t(videoRecords.at(row_cnt).start_time_value) * 1000);
                }
                if ((row_cnt + 1)  < videoRecords.size())
                {
                    bool isOverlap = (videoRecords.at(row_cnt + 1).start_time_value) < (videoRecords.at(row_cnt).start_time_value + (videoRecords.at(row_cnt).duration_value));
                    if (isOverlap)
                    {
                        continue;
                    }
                    else
                    {
                        bool isSegmentGap = ((videoRecords.at(row_cnt + 1).start_time_value - (videoRecords.at(row_cnt).start_time_value + (videoRecords.at(row_cnt).duration_value)) > MAX_TOLERANCE_SECS * 1000));
                        if (isSegmentGap)
                        {
                            jsonObjTime["endTime"] = convertEpocToISO8601_2(int64_t(videoRecords.at(row_cnt).start_time_value + (videoRecords.at(row_cnt).duration_value)) * 1000);
                            recordSegments[streamId].append(jsonObjTime);
                            jsonObjTime = Json::nullValue;
                            jsonObjTime["startTime"] = convertEpocToISO8601_2(int64_t(videoRecords.at(row_cnt + 1).start_time_value) * 1000);
                        }
                    }
                }
                else
                {
                    jsonObjTime["endTime"] = convertEpocToISO8601_2(int64_t(videoRecords.at(row_cnt).start_time_value + (videoRecords.at(row_cnt).duration_value)) * 1000);
                    recordSegments[streamId].append(jsonObjTime);
                }
            }

            out[streamId] = recordSegments[streamId];
        }

        LOG(verbose) << "Record timelines: " << out.toStyledString() << endl;

        return VmsErrorCode::NoError;
    }

    string parseMetadataObject(map<string, float, std::less<>>& coordinates, string &obj_type,
                                double &confidence, Json::Value& objects, int index /*0*/)
    {
        string object_id = "";
        nv_vms::DeviceConfig config =  GET_CONFIG();
        bool use_protobuf = config.use_video_metadata_protobuf;

        if (use_protobuf)
        {
            Json::Value& curr_object = objects[index];
            if (curr_object.isObject())
            {
                Json::Value nullJson = Json::nullValue;
                Json::Value bbox3d = curr_object.get("bbox3d", nullJson);
                /* Check if bbox3d is valid and has valid coordinates and dimensions
                * bbox3d["coordinates"] is an array of 12 elements
                * bbox3d["coordinates"][3] is the width > 0
                * bbox3d["coordinates"][4] is the length > 0
                * bbox3d["coordinates"][5] is the height > 0
                */
                if (!bbox3d.isNull() && bbox3d.isObject() && bbox3d.isMember("coordinates") &&
                    bbox3d["coordinates"].isArray() && bbox3d["coordinates"].size() >= 12 &&
                    bbox3d["coordinates"][3].asFloat() > 0.0f && bbox3d["coordinates"][4].asFloat() > 0.0f &&
                    bbox3d["coordinates"][5].asFloat() > 0.0f)
                {
                    object_id = curr_object.get("id", EMPTY_STRING).asString();
                    Json::Value& coords = bbox3d["coordinates"];
                    coordinates["bbox3d"] = 1.0f;

                    // Position (x, y, z)
                    coordinates["x"] = coords[0].asFloat();
                    coordinates["y"] = coords[1].asFloat();
                    coordinates["z"] = coords[2].asFloat();

                    // Dimensions (width, length, height)
                    coordinates["width"] = coords[3].asFloat();
                    coordinates["length"] = coords[4].asFloat();
                    coordinates["height"] = coords[5].asFloat();

                    // Orientation (pitch, roll, yaw)
                    coordinates["pitch"] = coords[6].asFloat();
                    coordinates["roll"] = coords[7].asFloat();
                    coordinates["yaw"] = coords[8].asFloat();

                    // Velocity (vx, vy, vz)
                    coordinates["vx"] = coords[9].asFloat();
                    coordinates["vy"] = coords[10].asFloat();
                    coordinates["vz"] = coords[11].asFloat();

                    obj_type = curr_object.get("type", EMPTY_STRING).asString();
                    confidence = bbox3d.get("confidence", ZERO_FLOAT).asDouble();

                }
                else
                {
                    // Fallback to regular bbox if bbox3d is not available or invalid
                    Json::Value cords = curr_object.get("bbox", nullJson);
                    if (!cords.isNull())
                    {
                        object_id = curr_object.get("id", EMPTY_STRING).asString();

                        coordinates["leftX"] = cords.get("leftX", ZERO_FLOAT).asFloat();
                        coordinates["topY"] = cords.get("topY", ZERO_FLOAT).asFloat();
                        coordinates["bottomY"] = cords.get("bottomY", ZERO_FLOAT).asFloat();
                        coordinates["rightX"] = cords.get("rightX", ZERO_FLOAT).asFloat();
                        coordinates["bbox3d"] = 0.0f;  // Indicate regular bbox is used

                        obj_type = curr_object.get("type", EMPTY_STRING).asString();
                        confidence = curr_object.get("confidence", ZERO_FLOAT).asDouble();
                    }
                }
            }
            else
            {
                LOG(info) << "metadata schema mismatch, try with use_video_metadata_protobuf set to false" << endl;
            }
        }
        else
        {
            // using schema: object-Id|bbox.leftX|bbox.topY|bbox.rightX|bbox.bottomY|object-type|confidence
            auto coordinate_tokens = splitString(objects.get(index, EMPTY_STRING).asString(), "|");
            if (coordinate_tokens.size() >= 7)
            {
                object_id = coordinate_tokens[0];

                coordinates ["leftX"]   = stringToDouble(coordinate_tokens[1]);
                coordinates ["topY"]    = stringToDouble(coordinate_tokens[2]);
                coordinates ["rightX"]  = stringToDouble(coordinate_tokens[3]);
                coordinates ["bottomY"] = stringToDouble(coordinate_tokens[4]);

                obj_type = coordinate_tokens[5];
                confidence  = stringToDouble(coordinate_tokens[6]);
            }
        }

        LOG(verbose) << "Object id: " << object_id << endl;
        return object_id;
    }

    bool tryReuseCachedPictureUrl(const string& deviceId, const string& sensorId, const string& startTime,
                                  const string& expiryMinutesStr, TempFileScheduler& scheduler, Json::Value& response)
    {
        int64_t cachedStartMs = parseTimeToEpochMs(startTime);
        auto dbHelper = GET_DB_INSTANCE();
        if (cachedStartMs <= 0 || !dbHelper)
        {
            return false;
        }

        auto existing = dbHelper->findTempFileByStreamAndTime(
            deviceId, sensorId, cachedStartMs, 0,
            nv_vms::TempFilesDBColumns::FILE_TYPE_IMAGE);

        if (existing.file_path_value.empty() || !isFileExist(existing.file_path_value))
        {
            return false;
        }

        LOG(info) << "Reusing cached picture: " << existing.file_path_value << endl;

        // expiryMinutes is validated by handlePictureAction before this
        // function is called -- any value reaching here is already known
        // to satisfy expiryMinutesInt >= MIN_ALLOWED_EXPIRY_MINUTES (or
        // the parameter was omitted and we fall back to the config
        // default).
        int expiryMinutesInt = stringToInt(expiryMinutesStr, GET_CONFIG().default_file_expiry_minutes);

        int64_t expiryTimestamp, currentTimestamp;
        string expiryISO = calculateExpiryTime(expiryMinutesInt, expiryTimestamp, currentTimestamp);
        dbHelper->updateTempFileExpiry(existing.file_path_value, expiryTimestamp);

        std::string cachedFilename = std::filesystem::path(existing.file_path_value).filename().string();
        size_t dotPos = cachedFilename.find_last_of('.');
        std::string cachedTaskId = (dotPos != std::string::npos) ? cachedFilename.substr(0, dotPos) : cachedFilename;
        int64_t durationMs = static_cast<int64_t>(expiryMinutesInt) * 60 * 1000;
        scheduler.schedule(cachedTaskId, durationMs, existing.file_path_value);

        string baseUrl = getIngressBaseUrl();
        bool isReplay = !startTime.empty();
        generateUrlResponse(response, baseUrl, existing.file_path_value, sensorId,
                        isReplay, startTime, expiryMinutesInt, expiryTimestamp, expiryISO);
        return true;
    }

    VmsErrorCode handlePictureAction(shared_ptr<DeviceManager> deviceManager, const string& deviceId,
                                     const string& sensorId, const string& queryString, bool isURLRequested,
                                     TempFileScheduler& scheduler, Json::Value& response)
    {
        string startTime;
        string expiryMinutesStr;
        CivetServer::getParam(queryString, "startTime", startTime);
        CivetServer::getParam(queryString, "expiryMinutes", expiryMinutesStr);

        if (!startTime.empty() && !validateISOTime(startTime))
        {
            LOG(error) << "Time is not in valid UTC format: " << startTime << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Time is not in valid UTC format");
            return VmsErrorCode::InvalidParameterError;
        }

        // Validate the effective expiryMinutes once, before either the
        // cache-hit or fresh-generation paths. processUrlGeneration
        // enforces the same rule for the fresh path; centralising it
        // here closes the gap where tryReuseCachedPictureUrl was
        // silently coercing 0 / -1 into 1 and returning a 200.
        //
        // The check is intentionally unconditional: when the client
        // omits expiryMinutes we fall through to
        // default_file_expiry_minutes from config, and a
        // misconfigured default that lands below
        // MIN_ALLOWED_EXPIRY_MINUTES must be rejected too rather than
        // silently propagating into the URL response.
        const int expiryMinutesInt =
            stringToInt(expiryMinutesStr, GET_CONFIG().default_file_expiry_minutes);
        if (expiryMinutesInt < MIN_ALLOWED_EXPIRY_MINUTES)
        {
            LOG(error) << "Invalid expiryMinutes: " << expiryMinutesInt << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response,
                           "Invalid expiryMinutes: " + to_string(expiryMinutesInt) +
                           ". Must be greater than or equal to " +
                           to_string(MIN_ALLOWED_EXPIRY_MINUTES));
            return VmsErrorCode::InvalidParameterError;
        }

        if (isURLRequested && !startTime.empty())
        {
            if (tryReuseCachedPictureUrl(deviceId, sensorId, startTime, expiryMinutesStr, scheduler, response))
            {
                return VmsErrorCode::NoError;
            }
        }

        VmsErrorCode ret = getCameraPicture(deviceManager, sensorId, queryString, response, isURLRequested);

        if (isURLRequested && ret == VmsErrorCode::NoError && !startTime.empty())
        {
            string filePath = response.get("absolutePath", "").asString();
            if (!filePath.empty())
            {
                int expiryMinutesInt = response.get("expiryMinutes", GET_CONFIG().default_file_expiry_minutes).asInt();
                std::string filename = std::filesystem::path(filePath).filename().string();
                size_t dotPos = filename.find_last_of('.');
                std::string taskId = (dotPos != std::string::npos) ? filename.substr(0, dotPos) : filename;

                string webRoot = VmsConfigManager::getInstance()->getWebRootPath();
                string actualFilePath = webRoot + TEMP_STORAGE_DIR + "/" + filename;
                int64_t durationMs = static_cast<int64_t>(expiryMinutesInt) * 60 * 1000;
                scheduler.schedule(taskId, durationMs, actualFilePath);
            }
        }

        return ret;
    }

} // namespace vst_common

namespace nv_vms {

#if defined(RECORDER_MODULE)

namespace {
constexpr uint32_t kFileInitDuration = 1; // FILE_INIT_DURATION (database.h)
constexpr uint64_t kMergeWindowMs = 1000; // ONE_MIN (database.h)
} // namespace

void getActiveLocalRecording(const std::string& streamOrSensorId, int64_t t1, int64_t t2,
                             std::vector<VideoFileInfo>& list)
{
    StreamRecorder* rec = GET_RECORDER();
    if (rec == nullptr || streamOrSensorId.empty())
    {
        return;
    }

    VideoFileInfo active = rec->getActiveLocalRecording(streamOrSensorId);
    if (active.m_filePath.empty() || !isFileExist(active.m_filePath))
    {
        return;
    }

    const int64_t userEnd = (t2 <= 0) ? std::numeric_limits<int64_t>::max() : t2;
    if (!list.empty())
    {
        const VideoFileInfo& last = list.back();
        const int64_t lastEnd =
            static_cast<int64_t>(last.m_startTime) + static_cast<int64_t>(last.m_duration);
        if (userEnd <= lastEnd)
        {
            const bool recorderHasNewerSegment =
                (static_cast<int64_t>(active.m_startTime) > static_cast<int64_t>(last.m_startTime)) &&
                (active.m_filePath != last.m_filePath);
            if (!recorderHasNewerSegment)
            {
                return;
            }
        }
    }

    const uint64_t q1 = static_cast<uint64_t>(std::max<int64_t>(0, t1));
    const uint64_t q2 =
        (t2 <= 0) ? std::numeric_limits<uint64_t>::max() : static_cast<uint64_t>(t2);

    const int64_t nowMs = getCurrentUnixTimestampInMs();
    const uint64_t segStart = active.m_startTime;
    uint64_t segDur;
    if (active.m_duration == kFileInitDuration)
    {
        const int64_t elapsed = nowMs - static_cast<int64_t>(segStart);
        if (elapsed >= 0 && elapsed < (2 * static_cast<int64_t>(TYPICAL_FILE_DURATION_MS_INT)))
        {
            segDur = static_cast<uint64_t>(std::max<int64_t>(1, elapsed));
        }
        else
        {
            segDur = TYPICAL_FILE_DURATION_MS_INT;
        }
    }
    else
    {
        segDur = static_cast<uint64_t>(active.m_duration);
    }
    const uint64_t segEnd = segStart + segDur;

    if (segEnd + kMergeWindowMs <= q1 || segStart > q2 + kMergeWindowMs)
    {
        return;
    }

    for (const auto& f : list)
    {
        if (f.m_filePath == active.m_filePath)
        {
            return;
        }
    }

    VideoFileInfo toAdd = active;
    if (toAdd.m_duration == kFileInitDuration)
    {
        const int64_t elapsed = nowMs - static_cast<int64_t>(toAdd.m_startTime);
        if (elapsed >= 0 && elapsed < (2 * static_cast<int64_t>(TYPICAL_FILE_DURATION_MS_INT)))
        {
            toAdd.m_duration = static_cast<uint32_t>(std::max<int64_t>(1, elapsed));
        }
        else
        {
            toAdd.m_duration = static_cast<uint32_t>(TYPICAL_FILE_DURATION_MS_INT);
        }
    }

    list.push_back(toAdd);
    LOG(info) << "getActiveLocalRecording append path=" << toAdd.m_filePath << " start=" << toAdd.m_startTime
              << " duration=" << toAdd.m_duration << endl;
}

#else

void getActiveLocalRecording(const std::string&, int64_t, int64_t, std::vector<VideoFileInfo>&) {}

#endif

} // namespace nv_vms

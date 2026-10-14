/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <stdio.h>
#include <sqlite3.h>
#include <iostream>
#include <string>
#include <vector>
#include <assert.h>

#include "logger.h"
#include "query_builder.h"


#define APPEND_COLUMN(col, value, sql) \
    sql = sql + col + ",";
#define APPEND_COLUMN_INT(col, value, sql) \
    sql = sql + col + ",";

// SAFE ALTERNATIVES: Use these instead of the vulnerable macros above
#define APPEND_COLUMN_VALUE(value, params) \
    params.push_back(value);
#define APPEND_COLUMN_VALUE_INT(value, params) \
    params.push_back(std::to_string(value));
#define APPEND_COLUMN_VALUE_JSON(value, params) \
    params.push_back(QueryBuilder::escapeJsonString(value));

// Optional value handling macros
#define APPEND_COLUMN_OPTIONAL(col, value, sql) \
    if (value.has_value()) { sql = sql + col + ","; }
#define APPEND_COLUMN_VALUE_OPTIONAL(value, params) \
    if (value.has_value()) { params.push_back(*value); }

// PARAMETER PLACEHOLDER FUNCTIONS: Type-safe compile-time placeholder generation
// Using constexpr with string literals for maximum performance
constexpr const char* paramPlaceholder(size_t index)
{
    // For small indices, we can use compile-time string literals
    switch (index) {
        case 0: return "{0}";
        case 1: return "{1}";
        case 2: return "{2}";
        case 3: return "{3}";
        case 4: return "{4}";
        case 5: return "{5}";
        case 6: return "{6}";
        case 7: return "{7}";
        case 8: return "{8}";
        case 9: return "{9}";
        case 10: return "{10}";
        case 11: return "{11}";
        case 12: return "{12}";
        case 13: return "{13}";
        case 14: return "{14}";
        case 15: return "{15}";
        case 16: return "{16}";
        case 17: return "{17}";
        case 18: return "{18}";
        case 19: return "{19}";
        case 20: return "{20}";
        case 21: return "{21}";
        case 22: return "{22}";
        case 23: return "{23}";
        case 24: return "{24}";
        case 25: return "{25}";
        case 26: return "{26}";
        case 27: return "{27}";
        case 28: return "{28}";
        case 29: return "{29}";
        case 30: return "{30}";
        default: return nullptr; // Fallback for larger indices
    }
}

constexpr const char* paramPlaceholderComma(size_t index)
{
    switch (index) {
        case 0: return "{0},";
        case 1: return "{1},";
        case 2: return "{2},";
        case 3: return "{3},";
        case 4: return "{4},";
        case 5: return "{5},";
        case 6: return "{6},";
        case 7: return "{7},";
        case 8: return "{8},";
        case 9: return "{9},";
        case 10: return "{10},";
        case 11: return "{11},";
        case 12: return "{12},";
        case 13: return "{13},";
        case 14: return "{14},";
        case 15: return "{15},";
        case 16: return "{16},";
        case 17: return "{17},";
        case 18: return "{18},";
        case 19: return "{19},";
        case 20: return "{20},";
        case 21: return "{21},";
        case 22: return "{22},";
        case 23: return "{23},";
        case 24: return "{24},";
        case 25: return "{25},";
        case 26: return "{26},";
        case 27: return "{27},";
        case 28: return "{28},";
        case 29: return "{29},";
        case 30: return "{30},";
        default: return nullptr; // Fallback for larger indices
    }
}

constexpr const char* paramPlaceholderLast(size_t index)
{
    return paramPlaceholder(index); // Same as paramPlaceholder
}

// Runtime fallback for larger indices (when constexpr version returns nullptr)
inline std::string paramPlaceholderRuntime(size_t index)
{
    return "{" + std::to_string(index) + "}";
}

inline std::string paramPlaceholderCommaRuntime(size_t index)
{
    return "{" + std::to_string(index) + "},";
}

// Smart wrapper that uses constexpr when possible, runtime when needed
inline std::string paramPlaceholderSmart(size_t index)
{
    const char* result = paramPlaceholder(index);
    return result ? std::string(result) : paramPlaceholderRuntime(index);
}

inline std::string paramPlaceholderCommaSmart(size_t index) {
    const char* result = paramPlaceholderComma(index);
    return result ? std::string(result) : paramPlaceholderCommaRuntime(index);
}

// Backward compatibility macros (deprecated - use constexpr functions instead)
#define PARAM_PLACEHOLDER(index) paramPlaceholderSmart(index)
#define PARAM_PLACEHOLDER_COMMA(index) paramPlaceholderCommaSmart(index)
#define PARAM_PLACEHOLDER_LAST(index) paramPlaceholderSmart(index)

// AUTOMATIC PARAMETER PLACEHOLDER BUILDER: Builds placeholders automatically
inline void buildParamPlaceholders(std::string& queryTemplate, const std::vector<std::string>& params)
{
    for (size_t i = 0; i < params.size(); i++)
    {
        queryTemplate += paramPlaceholderSmart(i);
        if (i < params.size() - 1) queryTemplate += ",";
    }
}

// ADVANCED PARAMETER BUILDERS: Complete query building automation
inline void buildValuesClause(std::string& queryTemplate, const std::vector<std::string>& params)
{
    queryTemplate += ") VALUES (";
    buildParamPlaceholders(queryTemplate, params);
    queryTemplate += ")";
}

inline void buildWhereClause(std::string& queryTemplate, const std::string& column, size_t paramIndex)
{
    // Validate column name to prevent SQL injection
    std::string safeColumn = QueryBuilder::validateColumnName(column);
    if (safeColumn.empty())
    {
        // Return without adding WHERE clause for invalid column names
        LOG(error) << "Invalid column name: " << column << std::endl;
        return;
    }
    queryTemplate += " WHERE " + safeColumn + " = " + paramPlaceholderSmart(paramIndex);
}

inline void buildUpdateSet(std::string& queryTemplate, const std::string& column, size_t paramIndex)
{
    // Validate column name to prevent SQL injection
    std::string safeColumn = QueryBuilder::validateColumnName(column);
    if (safeColumn.empty())
    {
        // Return without adding SET clause for invalid column names
        LOG(error) << "Invalid column name: " << column << std::endl;
        return;
    }
    queryTemplate += safeColumn + " = " + paramPlaceholderSmart(paramIndex);
}

// Backward compatibility macros (deprecated - use inline functions instead)
#define BUILD_PARAM_PLACEHOLDERS(queryTemplate, params) buildParamPlaceholders(queryTemplate, params)
#define BUILD_VALUES_CLAUSE(queryTemplate, params) buildValuesClause(queryTemplate, params)
#define BUILD_WHERE_CLAUSE(queryTemplate, column, paramIndex) buildWhereClause(queryTemplate, column, paramIndex)
#define BUILD_UPDATE_SET(queryTemplate, column, paramIndex) buildUpdateSet(queryTemplate, column, paramIndex)
#define GET_COLUMN_TEXT(value, col_num)                                      \
    {                                                                        \
        col_num += 1;                                                        \
        const char *text = (const char *)sqlite3_column_text(stmt, col_num); \
        if (text != nullptr)                                                    \
            value = text;                                                    \
    }
#define GET_COLUMN_INT(value, col_num)                             \
    {                                                              \
        col_num += 1;                                              \
        const int text = (int)sqlite3_column_int64(stmt, col_num); \
        if (text != 0)                                             \
            value = text;                                          \
    }
#define GET_COLUMN_UINT64(value, col_num)                                    \
    {                                                                        \
        col_num += 1;                                                        \
        const uint64_t text = (uint64_t)sqlite3_column_int64(stmt, col_num); \
        if (text != 0)                                                       \
            value = text;                                                    \
    }

inline constexpr int FILEPATH_BATCH_SIZE = 1000;

inline constexpr int TYPICAL_FILE_DURATION_MS_INT = 60000;
inline constexpr int TYPICAL_FILE_DURATION_MAX_MS_INT = 80000;

inline constexpr const char* TYPICAL_FILE_DURATION_MS = "60000";
inline constexpr const char* TYPICAL_FILE_DURATION_MAX_MS = "80000";

inline constexpr const char* VST_DB_VERSION = "0";

namespace nv_vms
{
    class DBColumns
    {
    public:
        inline static const std::string device_id = "DEVICE_ID";
        inline static const std::string sensor_id = "SENSOR_ID";
        inline static const std::string row_id = "ROW_ID";
        inline static const std::string created_date_time = "CREATED_DATE_TIME";
        inline static const std::string modified_date_time = "MODIFIED_DATE_TIME";

        std::string sensor_id_value;
        std::string device_id_value;
        std::string row_id_value;
        std::string created_date_time_value;
        std::string modified_date_time_value;

        DBColumns() : sensor_id_value(""),
                      device_id_value(""),
                      row_id_value(""),
                      created_date_time_value(""),
                      modified_date_time_value("") {}

        DBColumns(std::string &sensor_id,
                  std::string &device_id,
                  std::string &row_id,
                  std::string &created_date_time,
                  std::string &modified_date_time) : sensor_id_value(sensor_id),
                                                device_id_value(device_id),
                                                row_id_value(row_id),
                                                created_date_time_value(created_date_time),
                                                modified_date_time_value(modified_date_time) {}
    };

    class DbDetailsColumns : public DBColumns
    {
    public:
        inline static const std::string table_name = "DB_DETAILS";
        inline static const std::string db_version = "DB_VERSION";

        std::string db_version_value;

        DbDetailsColumns() : db_version_value("") {}

        void printInfo()
        {
            LOG(info) << "\tdb_version_value: " << db_version_value << std::endl;
        }
    };

    class LocalDeviceDetailsDBColumns : public DBColumns
    {
    public:
        inline static const std::string table_name = "LOCAL_DEVICE_DETAILS";
        inline static const std::string id = "ID";
        inline static const std::string name = "NAME";
        inline static const std::string location = "LOCATION";

        std::string id_value;
        std::string name_value;
        std::string location_value;

        LocalDeviceDetailsDBColumns() : id_value(""), name_value(""), location_value("") {}

        explicit LocalDeviceDetailsDBColumns(std::string &id) : id_value(id), name_value(name), location_value(location) {}

        void printInfo()
        {
            LOG(info) << "\tid_value: " << id_value << std::endl;
            LOG(info) << "\tname_value: " << name_value << std::endl;
            LOG(info) << "\tlocation_value: " << location_value << std::endl;
        }
    };

    class EventDBColumns : public DBColumns
    {
    public:
        inline static const std::string table_name = "EVENTS";
        inline static const std::string start_time = "START_TIME";
        inline static const std::string end_time = "END_TIME";
        inline static const std::string event_name = "EVENT_NAME";
        inline static const std::string event_id = "EVENT_ID";
        inline static const std::string video_path = "VIDEO_PATH";

        std::string start_time_value;
        std::string end_time_value;
        std::string event_name_value;
        std::string event_id_value;
        std::string video_path_value;

        EventDBColumns() : start_time_value(""),
                           end_time_value(""),
                           event_name_value(""),
                           event_id_value(""),
                           video_path_value("") {}

        EventDBColumns(std::string &video_path,
                       std::string &start_time,
                       std::string &end_time,
                       std::string &event_name,
                       std::string &event_id) : start_time_value(start_time),
                                           end_time_value(end_time),
                                           event_name_value(event_name),
                                           event_id_value(event_id),
                                           video_path_value(video_path) {}

        void printInfo()
        {
            LOG(info) << "\tvideo_path_value: " << video_path_value << std::endl;
            LOG(info) << "\tdevice_id_value: " << device_id_value << std::endl;
            LOG(info) << "\tsensor_id_value: " << sensor_id_value << std::endl;
            LOG(info) << "\tstart_time_value: " << start_time_value << std::endl;
            LOG(info) << "\tend_time_value: " << end_time_value << std::endl;
            LOG(info) << "\tevent_name_value: " << event_name_value << std::endl;
            LOG(info) << "\tevent_id_value: " << event_id_value << std::endl;
        }
    };

    class SensorDetailsDBColumns : public virtual DBColumns
    {
    public:
        inline static const std::string table_name = "SENSOR_DETAILS";
        inline static const std::string username = "USERNAME";
        inline static const std::string password = "PASSWORD";
        inline static const std::string sensor_hw_id = "SENSOR_HW_ID";
        inline static const std::string name = "NAME";
        inline static const std::string ip = "IPADDRESS";
        inline static const std::string user_given_name;
        inline static const std::string hardware = "HARDWARE";
        inline static const std::string manufacturer = "MANUFACTURER";
        inline static const std::string firmware_version = "FIRMWARE_VERSION";
        inline static const std::string serial_number = "SERIAL_NUMBER";
        inline static const std::string hardware_id = "HARDWARE_ID";
        inline static const std::string location = "LOCATION";
        inline static const std::string tags = "TAGS";
        inline static const std::string url = "URL";
        inline static const std::string type = "TYPE";
        inline static const std::string position = "POSITION";
        inline static const std::string users = "USERS";
        inline static const std::string isRemoteSensor = "IS_REMOTE";
        inline static const std::string remoteDeviceId = "REMOTE_DEVICE_ID";
        inline static const std::string remoteDeviceName = "REMOTE_DEVICE_NAME";
        inline static const std::string remoteDeviceLocation = "REMOTE_DEVICE_LOCATION";
        inline static const std::string httpStatus = "HTTP_STATUS";
        inline static const std::string sensorStatus = "SENSOR_STATUS";

        std::string username_value;
        std::string password_value;
        std::string sensor_hw_id_value;
        std::string name_value;
        std::string ip_value;
        std::string user_given_name_value;
        std::string manufacturer_value;
        std::string firmware_version_value;
        std::string serial_number_value;
        std::string hardware_id_value;
        std::string hardware_value;
        std::string location_value;
        std::string tags_value;
        std::string url_value;
        std::string type_value;
        std::string position_value;
        std::string users_value;
        std::string isRemoteSensor_value;
        std::string remoteDeviceId_value;
        std::string remoteDeviceName_value;
        std::string remoteDeviceLocation_value;
        int64_t httpStatus_value;
        int64_t sensorStatus_value;

        SensorDetailsDBColumns() : username_value(""),
                                   password_value(""),
                                   sensor_hw_id_value(""),
                                   name_value(""),
                                   ip_value(""),
                                   user_given_name_value(""),
                                   manufacturer_value(""),
                                   firmware_version_value(""),
                                   serial_number_value(""),
                                   hardware_id_value(""),
                                   hardware_value(""),
                                   location_value(""),
                                   tags_value(""),
                                   url_value(""),
                                   type_value(""),
                                   position_value(""),
                                   users_value(""),
                                   isRemoteSensor_value(""),
                                   remoteDeviceId_value(""),
                                   remoteDeviceName_value(""),
                                   remoteDeviceLocation_value(""),
                                   httpStatus_value(-1),
                                   sensorStatus_value(SensorStatusUnknown)
        {
        }

        SensorDetailsDBColumns(std::string &usename,
                               std::string &password,
                               std::string &sensor_hw_id,
                               std::string &name,
                               std::string &ip,
                               std::string &manufacturer,
                               std::string &serial_number,
                               std::string &firmware_version,
                               std::string &hardware_id,
                               std::string &hardware,
                               std::string &location,
                               std::string &tags,
                               std::string &url,
                               std::string &type,
                               std::string &users,
                               std::string &isRemoteSensor,
                               std::string &remoteDeviceId,
                               std::string &remoteDeviceName,
                               std::string &remoteDeviceLocation,
                               int64_t &httpStatus,
                               int64_t &sensorStatus) : username_value(usename),
                                                        password_value(password),
                                                        sensor_hw_id_value(sensor_hw_id),
                                                        name_value(name),
                                                        ip_value(ip),
                                                        manufacturer_value(manufacturer),
                                                        firmware_version_value(firmware_version),
                                                        serial_number_value(serial_number),
                                                        hardware_id_value(hardware_id),
                                                        hardware_value(hardware),
                                                        location_value(location),
                                                        tags_value(tags),
                                                        url_value(url),
                                                        type_value(type),
                                                        users_value(users),
                                                        isRemoteSensor_value(isRemoteSensor),
                                                        remoteDeviceId_value(remoteDeviceId),
                                                        remoteDeviceName_value(remoteDeviceName),
                                                        remoteDeviceLocation_value(remoteDeviceLocation),
                                                        httpStatus_value(httpStatus),
                                                        sensorStatus_value(sensorStatus) {}
    };

    class VideoRecordDBColumns : public DBColumns
    {
    public:
        inline static const std::string table_name = "VIDEO_RECORD_DETAILS";
        inline static const std::string stream_id = "STREAM_ID";
        inline static const std::string resolution = "RESOLUTION";
        inline static const std::string start_time = "START_TIME";
        inline static const std::string duration = "FILE_DURATION";
        inline static const std::string file_path = "FILE_PATH";
        inline static const std::string file_size = "FILE_SIZE";
        inline static const std::string file_fps = "FILE_FPS";
        inline static const std::string sensor_name = "SENSOR_NAME";
        inline static const std::string record_config = "RECORD_CONFIG";
        inline static const std::string codec = "FILE_CODEC";
        inline static const std::string file_protection = "FILE_PROTECTION";
        inline static const std::string metadata_file_path = "METADATA_FILE_PATH";
        inline static const std::string metadata_json = "METADATA_JSON";
        inline static const std::string object_id = "OBJECT_ID";
        inline static const std::string storage_location = "STORAGE_LOCATION";
        inline static const std::string bucket_name = "BUCKET_NAME";

        std::string stream_id_value;
        std::string resolution_value;
        uint64_t start_time_value;
        unsigned int duration_value;
        std::string filepath_value;
        uint64_t filesize_value;
        uint64_t filefps_value;
        std::string sensor_name_value;
        std::string record_config_value;
        std::string codec_value;
        std::string file_protection_value;
        std::string metadata_file_path_value;
        std::string metadata_json_value;
        std::string object_id_value;
        int64_t storage_location_value;
        std::string bucket_name_value;

        VideoRecordDBColumns() : stream_id_value(""),
                                 resolution_value(""),
                                 start_time_value(0),
                                 duration_value(0),
                                 filepath_value(""),
                                 filesize_value(0),
                                 filefps_value(0),
                                 sensor_name_value(""),
                                 record_config_value(""),
                                 codec_value(""),
                                 file_protection_value("0"),
                                 metadata_file_path_value(""),
                                 metadata_json_value(""),
                                 object_id_value(""),
                                 storage_location_value(StreamStorageTypeLocal),
                                 bucket_name_value("") {}

        VideoRecordDBColumns(std::string &stream_id,
                             std::string &resolution,
                             unsigned int &startTime,
                             unsigned int &duration,
                             std::string &filePath,
                             uint64_t &fileSize,
                             uint64_t &fileFPS,
                             std::string &sensorName,
                             std::string &recordConfig,
                             std::string &codec,
                             std::string &file_protection,
                             std::string &metadata_file_path,
                             std::string &metadata_json,
                             std::string &object_id) : stream_id_value(stream_id),
                                                        resolution_value(resolution),
                                                        start_time_value(startTime),
                                                        duration_value(duration),
                                                        filepath_value(filePath),
                                                        filesize_value(fileSize),
                                                        filefps_value(fileFPS),
                                                        sensor_name_value(sensorName),
                                                        record_config_value(recordConfig),
                                                        codec_value(codec),
                                                        file_protection_value(file_protection),
                                                        metadata_file_path_value(metadata_file_path),
                                                        metadata_json_value(metadata_json),
                                                        object_id_value(object_id) {}
    };

    class VideoRecordScheduleDBColumns : public DBColumns
    {
    public:
        inline static const std::string table_name = "VIDEO_RECORD_SCHEDULE_DETAILS";
        inline static const std::string stream_id = "STREAM_ID";
        inline static const std::string start_time = "START_TIME";
        inline static const std::string end_time = "END_TIME";

        std::string start_time_value;
        std::string end_time_value;
        std::string stream_id_value;

        VideoRecordScheduleDBColumns() : start_time_value(""),
                                         end_time_value(""),
                                         stream_id_value("") {}

        VideoRecordScheduleDBColumns(std::string &startTime,
                                     std::string &endTime,
                                     std::string &stream_id) : start_time_value(startTime),
                                                          end_time_value(endTime),
                                                          stream_id_value(stream_id) {}
    };

    class SensorStreamsDBColumns : public virtual DBColumns
    {
    public:
        inline static const std::string table_name = "SENSOR_STREAMS";
        inline static const std::string live_url = "STREAM_LIVE_URL";
        inline static const std::string replay_url = "STREAM_REPLAY_URL";
        inline static const std::string proxy_url = "STREAM_PROXY_URL";
        inline static const std::string resolution = "STREAM_RESOLUTION";
        inline static const std::string frameRate = "STREAM_FRAMERATE";
        inline static const std::string encoding = "STREAM_ENCODING";
        inline static const std::string stream_id = "STREAM_ID";
        inline static const std::string streamStatus = "STREAM_STATUS";
        inline static const std::string type = "STREAM_TYPE";
        inline static const std::string encodingProfile = "STREAM_ENCODING_PROFILE";
        inline static const std::string encodingInterval = "STREAM_ENCODING_INTERVAl";
        inline static const std::string duration = "STREAM_DURATION";
        inline static const std::string isMainStream = "STREAM_ISMAINSTREAM";
        inline static const std::string isAlwaysRecording = "STREAM_ISALWAYSRECORDING";
        inline static const std::string storageLocation = "STREAM_STORAGE_LOCATION";
        inline static const std::string bitrate = "BITRATE";
        inline static const std::string numFrames = "NUM_OF_FRAMES";
        inline static const std::string audio_container = "AUDIO_CONTAINER";
        inline static const std::string audio_encoding = "AUDIO_ENCODING";
        inline static const std::string audio_sample_rate = "AUDIO_SAMPLE_RATE";
        inline static const std::string audio_bps = "AUDIO_BPS";
        inline static const std::string audio_channels = "AUDIO_CHANNELS";
        inline static const std::string streamName = "STREAM_NAME";
        inline static const std::string isBframesPresent = "IS_BFRAMES_PRESENT";

        std::string live_url_value;
        std::string replay_url_value;
        std::string proxy_url_value;
        std::string resolution_value;
        std::string frameRate_value;
        std::string encoding_value;
        std::string stream_id_value;
        int64_t streamStatus_value;
        int64_t streamType_value;
        std::string encodingProfile_value;
        std::string encodingInterval_value;
        std::string duration_value;
        std::string isMainStream_value;
        std::string isAlwaysRecording_value;
        int64_t storageLocation_value;
        std::string bitrate_value;
        std::string numFrames_value;
        std::string audio_container_value;
        std::string audio_encoding_value;
        std::string audio_sample_rate_value;
        std::string audio_bps_value;
        std::string audio_channels_value;
        std::string streamName_value;
        int isBframesPresent_value;

        SensorStreamsDBColumns() : live_url_value(""),
                                   replay_url_value(""),
                                   proxy_url_value(""),
                                   resolution_value(""),
                                   frameRate_value(""),
                                   encoding_value(""),
                                   stream_id_value(""),
                                   streamStatus_value(STREAM_STATUS_UNKNOWN),
                                   streamType_value(-1),
                                   encodingProfile_value(""),
                                   encodingInterval_value(""),
                                   duration_value(""),
                                   isMainStream_value(""),
                                   isAlwaysRecording_value(""),
                                   storageLocation_value(-1),
                                   bitrate_value(""),
                                   numFrames_value(""),
                                   audio_container_value(""),
                                   audio_encoding_value(""),
                                   audio_sample_rate_value(""),
                                   audio_bps_value(""),
                                   audio_channels_value(""),
                                   streamName_value(""),
                                   isBframesPresent_value(-1) {}

        SensorStreamsDBColumns(
            std::string &live_url,
            std::string &replay_url,
            std::string &proxy_url,
            std::string &resolution,
            std::string &frameRate,
            std::string &encoding,
            std::string &stream_id,
            int64_t &streamStatus,
            int64_t &type,
            std::string &encodingProfile,
            std::string &encoding_inteval,
            std::string &duration,
            std::string &isMainStream,
            std::string &isAlwaysRecording,
            std::string &bitrate,
            std::string &numFrames,
            std::string &audio_container,
            std::string &audio_encoding,
            std::string &audio_sample_rate,
            std::string &audio_bps,
            std::string &audio_channels,
            std::string &streamName,
            int isBframesPresent) : live_url_value(live_url), replay_url_value(replay_url), proxy_url_value(proxy_url), resolution_value(resolution), frameRate_value(frameRate), encoding_value(encoding), stream_id_value(stream_id), streamStatus_value(streamStatus), streamType_value(type), encodingProfile_value(encodingProfile), encodingInterval_value(encoding_inteval), duration_value(duration), isMainStream_value(isMainStream), isAlwaysRecording_value(isAlwaysRecording), bitrate_value(bitrate), numFrames_value(numFrames), audio_container_value(audio_container), audio_encoding_value(audio_encoding), audio_sample_rate_value(audio_sample_rate), audio_bps_value(audio_bps), audio_channels_value(audio_channels), streamName_value(streamName), isBframesPresent_value(isBframesPresent) {}
    };

    class SensorInfoDBColumns : public SensorStreamsDBColumns, public SensorDetailsDBColumns
    {
    };

    class UserDetailsDBColumns : public DBColumns
    {
    public:
        inline static const std::string table_name = "USER_DETAILS";
        inline static const std::string username = "USERNAME";
        inline static const std::string password_hash = "PASSWORD_HASH";

        std::string username_value;
        std::string password_hash_value;

        UserDetailsDBColumns() : username_value(""), password_hash_value("") {}

        UserDetailsDBColumns(
            std::string &username,
            std::string &password_hash) : username_value(username), password_hash_value(password_hash) {}
    };

    class UserSessionsDBColumns : public DBColumns
    {
    public:
        inline static const std::string table_name = "USER_SESSIONS";
        inline static const std::string username = "USERNAME";
        inline static const std::string session_cookie = "SESSION_COOKIE";
        inline static const std::string cookie_max_age = "COOKIE_MAX_AGE";

        std::string username_value;
        std::string session_cookie_value;
        int64_t cookie_max_age_value;

        UserSessionsDBColumns() : username_value(""), session_cookie_value(""), cookie_max_age_value(0) {}

        UserSessionsDBColumns(
            std::string &username,
            std::string &session_cookie,
            int64_t &cookie_max_age) : username_value(username), session_cookie_value(session_cookie), cookie_max_age_value(cookie_max_age) {}
    };

    class RecordingStatusDBColumns : public DBColumns
    {
    public:
        inline static const std::string table_name = "RECORDING_STATUS";
        inline static const std::string sensor_id = "SENSOR_ID";
        inline static const std::string stream_id = "STREAM_ID";
        inline static const std::string recordingStatus = "RECORDING_STATUS";
        inline static const std::string created_date_time = "CREATED_DATE_TIME";
        inline static const std::string modified_date_time = "MODIFIED_DATE_TIME";

        std::string stream_id_value;
        int64_t recordingStatus_value;
    };

    typedef struct _VideoFileInfo
    {
        std::string m_filePath;
        uint64_t m_startTime;
        uint32_t m_duration;
        uint64_t m_fileSize;
        uint64_t m_fileFPS;
        std::string m_codec;
        std::string m_objectId;
        std::string m_metadataFilePath;
        std::string m_metadataJson;

        _VideoFileInfo() : m_filePath(""), m_startTime(0), m_duration(0), m_fileSize(0), m_fileFPS(0), m_codec(""), m_objectId(""), m_metadataFilePath(""), m_metadataJson("")
        {
        }

        explicit _VideoFileInfo(const VideoRecordDBColumns &row)
        {
            this->m_filePath = row.filepath_value;
            this->m_startTime = row.start_time_value;
            this->m_duration = row.duration_value;
            this->m_fileSize = row.filesize_value;
            this->m_fileFPS = row.filefps_value;
            this->m_codec = row.codec_value;
            this->m_metadataFilePath = row.metadata_file_path_value;
            this->m_metadataJson = row.metadata_json_value;
            this->m_objectId = row.object_id_value;
        }

        void operator=(const VideoRecordDBColumns &row)
        {
            this->m_filePath = row.filepath_value;
            this->m_startTime = row.start_time_value;
            this->m_duration = row.duration_value;
            this->m_fileSize = row.filesize_value;
            this->m_fileFPS = row.filefps_value;
            this->m_codec = row.codec_value;
            this->m_metadataFilePath = row.metadata_file_path_value;
            this->m_metadataJson = row.metadata_json_value;
            this->m_objectId = row.object_id_value;
        }

        bool operator==(const _VideoFileInfo &obj) const
        {
            return this->m_startTime == obj.m_startTime;
        }

        bool operator<(const _VideoFileInfo &obj) const
        {
            return this->m_startTime < obj.m_startTime;
        }

        bool operator>(const _VideoFileInfo &obj) const
        {
            return this->m_startTime > obj.m_startTime;
        }

    } VideoFileInfo;

    class TempFilesDBColumns : public virtual DBColumns
    {
    public:
        inline static const std::string table_name = "TEMP_VIDEO_FILES";
        inline static const std::string file_path = "FILE_PATH";
        inline static const std::string expiry_timestamp = "EXPIRY_TIMESTAMP";
        inline static const std::string created_timestamp = "CREATED_TIMESTAMP";
        inline static const std::string stream_id = "STREAM_ID";
        inline static const std::string file_size = "FILE_SIZE";
        inline static const std::string start_time_ms = "START_TIME_MS";
        inline static const std::string end_time_ms = "END_TIME_MS";
        inline static const std::string file_type = "FILE_TYPE";
        inline static const std::string container_format = "CONTAINER_FORMAT";
        inline static const std::string config_hash = "CONFIG_HASH";

        static constexpr const char* FILE_TYPE_VIDEO = "video";
        static constexpr const char* FILE_TYPE_IMAGE = "image";
        static constexpr int64_t CACHE_TIME_TOLERANCE_MS = 33;

        std::string file_path_value;
        int64_t expiry_timestamp_value;
        int64_t created_timestamp_value;
        std::string stream_id_value;
        int64_t file_size_value;
        int64_t start_time_ms_value;
        int64_t end_time_ms_value;
        std::string file_type_value;
        std::string container_format_value;
        // Hex SHA-256 of caller-supplied options that affect the produced
        // bytes (overlay, transcode, audio, container for video URLs;
        // overlay, resize hints, debug flag for picture URLs). Populated
        // for both the video URL flow (computeConfigHash) and the picture
        // URL flow (computePictureConfigHash). Left empty only by callers
        // that genuinely do not vary output by request configuration -
        // currently the full-file symlink fast path in StorageManagement,
        // which is gated to non-transformed pass-through of the raw
        // recording. Cache lookups must match on this column when
        // non-empty so the same (stream, time-range, type, container) hits
        // a distinct cached file per request configuration.
        std::string config_hash_value;

        TempFilesDBColumns() : file_path_value(""),
                               expiry_timestamp_value(0),
                               created_timestamp_value(0),
                               stream_id_value(""),
                               file_size_value(0),
                               start_time_ms_value(0),
                               end_time_ms_value(0),
                               file_type_value(""),
                               container_format_value(""),
                               config_hash_value("") {}

        TempFilesDBColumns(const std::string& filePath,
                           int64_t expiryTs,
                           int64_t createdTs,
                           const std::string& streamId,
                           int64_t fileSize,
                           int64_t startTimeMs = 0,
                           int64_t endTimeMs = 0,
                           const std::string& fileType = "",
                           const std::string& containerFormat = "",
                           const std::string& configHash = "") :
                           file_path_value(filePath),
                           expiry_timestamp_value(expiryTs),
                           created_timestamp_value(createdTs),
                           stream_id_value(streamId),
                           file_size_value(fileSize),
                           start_time_ms_value(startTimeMs),
                           end_time_ms_value(endTimeMs),
                           file_type_value(fileType),
                           container_format_value(containerFormat),
                           config_hash_value(configHash) {}

        void printInfo()
        {
            LOG(info) << "\tfile_path_value: " << file_path_value << std::endl;
            LOG(info) << "\tdevice_id_value: " << device_id_value << std::endl;
            LOG(info) << "\texpiry_timestamp_value: " << expiry_timestamp_value << std::endl;
            LOG(info) << "\tcreated_timestamp_value: " << created_timestamp_value << std::endl;
            LOG(info) << "\tstream_id_value: " << stream_id_value << std::endl;
            LOG(info) << "\tfile_size_value: " << file_size_value << std::endl;
            LOG(info) << "\tstart_time_ms_value: " << start_time_ms_value << std::endl;
            LOG(info) << "\tend_time_ms_value: " << end_time_ms_value << std::endl;
            LOG(info) << "\tfile_type_value: " << file_type_value << std::endl;
            LOG(info) << "\tcontainer_format_value: " << container_format_value << std::endl;
            LOG(info) << "\tconfig_hash_value: " << config_hash_value << std::endl;
        }
    };

} // nv_vms

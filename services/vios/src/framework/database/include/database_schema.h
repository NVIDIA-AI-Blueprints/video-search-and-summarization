/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

using namespace std;

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
        LOG(error) << "Invalid column name: " << column << endl;
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
        LOG(error) << "Invalid column name: " << column << endl;
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
        inline static const string device_id = "DEVICE_ID";
        inline static const string sensor_id = "SENSOR_ID";
        inline static const string row_id = "ROW_ID";
        inline static const string created_date_time = "CREATED_DATE_TIME";
        inline static const string modified_date_time = "MODIFIED_DATE_TIME";

        string sensor_id_value;
        string device_id_value;
        string row_id_value;
        string created_date_time_value;
        string modified_date_time_value;

        DBColumns() : sensor_id_value(""),
                      device_id_value(""),
                      row_id_value(""),
                      created_date_time_value(""),
                      modified_date_time_value("") {}

        DBColumns(string &sensor_id,
                  string &device_id,
                  string &row_id,
                  string &created_date_time,
                  string &modified_date_time) : sensor_id_value(sensor_id),
                                                device_id_value(device_id),
                                                row_id_value(row_id),
                                                created_date_time_value(created_date_time),
                                                modified_date_time_value(modified_date_time) {}
    };

    class DbDetailsColumns : public DBColumns
    {
    public:
        inline static const string table_name = "DB_DETAILS";
        inline static const string db_version = "DB_VERSION";

        string db_version_value;

        DbDetailsColumns() : db_version_value("") {}

        void printInfo()
        {
            LOG(info) << "\tdb_version_value: " << db_version_value << endl;
        }
    };

    class LocalDeviceDetailsDBColumns : public DBColumns
    {
    public:
        inline static const string table_name = "LOCAL_DEVICE_DETAILS";
        inline static const string id = "ID";
        inline static const string name = "NAME";
        inline static const string location = "LOCATION";

        string id_value;
        string name_value;
        string location_value;

        LocalDeviceDetailsDBColumns() : id_value(""), name_value(""), location_value("") {}

        LocalDeviceDetailsDBColumns(string &id) : id_value(id), name_value(name), location_value(location) {}

        void printInfo()
        {
            LOG(info) << "\tid_value: " << id_value << endl;
            LOG(info) << "\tname_value: " << name_value << endl;
            LOG(info) << "\tlocation_value: " << location_value << endl;
        }
    };

    class EventDBColumns : public DBColumns
    {
    public:
        inline static const string table_name = "EVENTS";
        inline static const string start_time = "START_TIME";
        inline static const string end_time = "END_TIME";
        inline static const string event_name = "EVENT_NAME";
        inline static const string event_id = "EVENT_ID";
        inline static const string video_path = "VIDEO_PATH";

        string start_time_value;
        string end_time_value;
        string event_name_value;
        string event_id_value;
        string video_path_value;

        EventDBColumns() : start_time_value(""),
                           end_time_value(""),
                           event_name_value(""),
                           event_id_value(""),
                           video_path_value("") {}

        EventDBColumns(string &video_path,
                       string &start_time,
                       string &end_time,
                       string &event_name,
                       string &event_id) : start_time_value(start_time),
                                           end_time_value(end_time),
                                           event_name_value(event_name),
                                           event_id_value(event_id),
                                           video_path_value(video_path) {}

        void printInfo()
        {
            LOG(info) << "\tvideo_path_value: " << video_path_value << endl;
            LOG(info) << "\tdevice_id_value: " << device_id_value << endl;
            LOG(info) << "\tsensor_id_value: " << sensor_id_value << endl;
            LOG(info) << "\tstart_time_value: " << start_time_value << endl;
            LOG(info) << "\tend_time_value: " << end_time_value << endl;
            LOG(info) << "\tevent_name_value: " << event_name_value << endl;
            LOG(info) << "\tevent_id_value: " << event_id_value << endl;
        }
    };

    class SensorDetailsDBColumns : public virtual DBColumns
    {
    public:
        inline static const string table_name = "SENSOR_DETAILS";
        inline static const string username = "USERNAME";
        inline static const string password = "PASSWORD";
        inline static const string sensor_hw_id = "SENSOR_HW_ID";
        inline static const string name = "NAME";
        inline static const string ip = "IPADDRESS";
        inline static const string user_given_name;
        inline static const string hardware = "HARDWARE";
        inline static const string manufacturer = "MANUFACTURER";
        inline static const string firmware_version = "FIRMWARE_VERSION";
        inline static const string serial_number = "SERIAL_NUMBER";
        inline static const string hardware_id = "HARDWARE_ID";
        inline static const string location = "LOCATION";
        inline static const string tags = "TAGS";
        inline static const string url = "URL";
        inline static const string type = "TYPE";
        inline static const string position = "POSITION";
        inline static const string users = "USERS";
        inline static const string isRemoteSensor = "IS_REMOTE";
        inline static const string remoteDeviceId = "REMOTE_DEVICE_ID";
        inline static const string remoteDeviceName = "REMOTE_DEVICE_NAME";
        inline static const string remoteDeviceLocation = "REMOTE_DEVICE_LOCATION";
        inline static const string httpStatus = "HTTP_STATUS";
        inline static const string sensorStatus = "SENSOR_STATUS";

        string username_value;
        string password_value;
        string sensor_hw_id_value;
        string name_value;
        string ip_value;
        string user_given_name_value;
        string manufacturer_value;
        string firmware_version_value;
        string serial_number_value;
        string hardware_id_value;
        string hardware_value;
        string location_value;
        string tags_value;
        string url_value;
        string type_value;
        string position_value;
        string users_value;
        string isRemoteSensor_value;
        string remoteDeviceId_value;
        string remoteDeviceName_value;
        string remoteDeviceLocation_value;
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

        SensorDetailsDBColumns(string &usename,
                               string &password,
                               string &sensor_hw_id,
                               string &name,
                               string &ip,
                               string &manufacturer,
                               string &serial_number,
                               string &firmware_version,
                               string &hardware_id,
                               string &hardware,
                               string &location,
                               string &tags,
                               string &url,
                               string &type,
                               string &users,
                               string &isRemoteSensor,
                               string &remoteDeviceId,
                               string &remoteDeviceName,
                               string &remoteDeviceLocation,
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
        inline static const string table_name = "VIDEO_RECORD_DETAILS";
        inline static const string stream_id = "STREAM_ID";
        inline static const string resolution = "RESOLUTION";
        inline static const string start_time = "START_TIME";
        inline static const string duration = "FILE_DURATION";
        inline static const string file_path = "FILE_PATH";
        inline static const string file_size = "FILE_SIZE";
        inline static const string file_fps = "FILE_FPS";
        inline static const string sensor_name = "SENSOR_NAME";
        inline static const string record_config = "RECORD_CONFIG";
        inline static const string codec = "FILE_CODEC";
        inline static const string file_protection = "FILE_PROTECTION";
        inline static const string metadata_file_path = "METADATA_FILE_PATH";
        inline static const string metadata_json = "METADATA_JSON";
        inline static const string object_id = "OBJECT_ID";
        inline static const string storage_location = "STORAGE_LOCATION";
        inline static const string bucket_name = "BUCKET_NAME";

        string stream_id_value;
        string resolution_value;
        uint64_t start_time_value;
        unsigned int duration_value;
        string filepath_value;
        uint64_t filesize_value;
        uint64_t filefps_value;
        string sensor_name_value;
        string record_config_value;
        string codec_value;
        string file_protection_value;
        string metadata_file_path_value;
        string metadata_json_value;
        string object_id_value;
        int64_t storage_location_value;
        string bucket_name_value;

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

        VideoRecordDBColumns(string &stream_id,
                             string &resolution,
                             unsigned int &startTime,
                             unsigned int &duration,
                             string &filePath,
                             uint64_t &fileSize,
                             uint64_t &fileFPS,
                             string &sensorName,
                             string &recordConfig,
                             string &codec,
                             string &file_protection,
                             string &metadata_file_path,
                             string &metadata_json,
                             string &object_id) : stream_id_value(stream_id),
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
        inline static const string table_name = "VIDEO_RECORD_SCHEDULE_DETAILS";
        inline static const string stream_id = "STREAM_ID";
        inline static const string start_time = "START_TIME";
        inline static const string end_time = "END_TIME";

        string start_time_value;
        string end_time_value;
        string stream_id_value;

        VideoRecordScheduleDBColumns() : start_time_value(""),
                                         end_time_value(""),
                                         stream_id_value("") {}

        VideoRecordScheduleDBColumns(string &startTime,
                                     string &endTime,
                                     string &stream_id) : start_time_value(startTime),
                                                          end_time_value(endTime),
                                                          stream_id_value(stream_id) {}
    };

    class SensorStreamsDBColumns : public virtual DBColumns
    {
    public:
        inline static const string table_name = "SENSOR_STREAMS";
        inline static const string live_url = "STREAM_LIVE_URL";
        inline static const string replay_url = "STREAM_REPLAY_URL";
        inline static const string proxy_url = "STREAM_PROXY_URL";
        inline static const string resolution = "STREAM_RESOLUTION";
        inline static const string frameRate = "STREAM_FRAMERATE";
        inline static const string encoding = "STREAM_ENCODING";
        inline static const string stream_id = "STREAM_ID";
        inline static const string streamStatus = "STREAM_STATUS";
        inline static const string type = "STREAM_TYPE";
        inline static const string encodingProfile = "STREAM_ENCODING_PROFILE";
        inline static const string encodingInterval = "STREAM_ENCODING_INTERVAl";
        inline static const string duration = "STREAM_DURATION";
        inline static const string isMainStream = "STREAM_ISMAINSTREAM";
        inline static const string isAlwaysRecording = "STREAM_ISALWAYSRECORDING";
        inline static const string storageLocation = "STREAM_STORAGE_LOCATION";
        inline static const string bitrate = "BITRATE";
        inline static const string numFrames = "NUM_OF_FRAMES";
        inline static const string audio_container = "AUDIO_CONTAINER";
        inline static const string audio_encoding = "AUDIO_ENCODING";
        inline static const string audio_sample_rate = "AUDIO_SAMPLE_RATE";
        inline static const string audio_bps = "AUDIO_BPS";
        inline static const string audio_channels = "AUDIO_CHANNELS";
        inline static const string streamName = "STREAM_NAME";
        inline static const string isBframesPresent = "IS_BFRAMES_PRESENT";

        string live_url_value;
        string replay_url_value;
        string proxy_url_value;
        string resolution_value;
        string frameRate_value;
        string encoding_value;
        string stream_id_value;
        int64_t streamStatus_value;
        int64_t streamType_value;
        string encodingProfile_value;
        string encodingInterval_value;
        string duration_value;
        string isMainStream_value;
        string isAlwaysRecording_value;
        int64_t storageLocation_value;
        string bitrate_value;
        string numFrames_value;
        string audio_container_value;
        string audio_encoding_value;
        string audio_sample_rate_value;
        string audio_bps_value;
        string audio_channels_value;
        string streamName_value;
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
            string &live_url,
            string &replay_url,
            string &proxy_url,
            string &resolution,
            string &frameRate,
            string &encoding,
            string &stream_id,
            int64_t &streamStatus,
            int64_t &type,
            string &encodingProfile,
            string &encoding_inteval,
            string &duration,
            string &isMainStream,
            string &isAlwaysRecording,
            string &bitrate,
            string &numFrames,
            string &audio_container,
            string &audio_encoding,
            string &audio_sample_rate,
            string &audio_bps,
            string &audio_channels,
            string &streamName,
            int isBframesPresent) : live_url_value(live_url), replay_url_value(replay_url), proxy_url_value(proxy_url), resolution_value(resolution), frameRate_value(frameRate), encoding_value(encoding), stream_id_value(stream_id), streamStatus_value(streamStatus), streamType_value(type), encodingProfile_value(encodingProfile), encodingInterval_value(encoding_inteval), duration_value(duration), isMainStream_value(isMainStream), isAlwaysRecording_value(isAlwaysRecording), bitrate_value(bitrate), numFrames_value(numFrames), audio_container_value(audio_container), audio_encoding_value(audio_encoding), audio_sample_rate_value(audio_sample_rate), audio_bps_value(audio_bps), audio_channels_value(audio_channels), streamName_value(streamName), isBframesPresent_value(isBframesPresent) {}
    };

    class SensorInfoDBColumns : public SensorStreamsDBColumns, public SensorDetailsDBColumns
    {
    };

    class UserDetailsDBColumns : public DBColumns
    {
    public:
        inline static const string table_name = "USER_DETAILS";
        inline static const string username = "USERNAME";
        inline static const string password_hash = "PASSWORD_HASH";

        string username_value;
        string password_hash_value;

        UserDetailsDBColumns() : username_value(""), password_hash_value("") {}

        UserDetailsDBColumns(
            string &username,
            string &password_hash) : username_value(username), password_hash_value(password_hash) {}
    };

    class UserSessionsDBColumns : public DBColumns
    {
    public:
        inline static const string table_name = "USER_SESSIONS";
        inline static const string username = "USERNAME";
        inline static const string session_cookie = "SESSION_COOKIE";
        inline static const string cookie_max_age = "COOKIE_MAX_AGE";

        string username_value;
        string session_cookie_value;
        int64_t cookie_max_age_value;

        UserSessionsDBColumns() : username_value(""), session_cookie_value(""), cookie_max_age_value(0) {}

        UserSessionsDBColumns(
            string &username,
            string &session_cookie,
            int64_t &cookie_max_age) : username_value(username), session_cookie_value(session_cookie), cookie_max_age_value(cookie_max_age) {}
    };

    class RecordingStatusDBColumns : public DBColumns
    {
    public:
        inline static const string table_name = "RECORDING_STATUS";
        inline static const string sensor_id = "SENSOR_ID";
        inline static const string stream_id = "STREAM_ID";
        inline static const string recordingStatus = "RECORDING_STATUS";
        inline static const string created_date_time = "CREATED_DATE_TIME";
        inline static const string modified_date_time = "MODIFIED_DATE_TIME";

        string stream_id_value;
        int64_t recordingStatus_value;
    };

    typedef struct _VideoFileInfo
    {
        string m_filePath;
        uint64_t m_startTime;
        uint32_t m_duration;
        uint64_t m_fileSize;
        uint64_t m_fileFPS;
        string m_codec;
        string m_objectId;
        string m_metadataFilePath;
        string m_metadataJson;

        _VideoFileInfo() : m_filePath(""), m_startTime(0), m_duration(0), m_fileSize(0), m_fileFPS(0), m_codec(""), m_objectId(""), m_metadataFilePath(""), m_metadataJson("")
        {
        }

        _VideoFileInfo(const VideoRecordDBColumns &row)
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
        inline static const string table_name = "TEMP_VIDEO_FILES";
        inline static const string file_path = "FILE_PATH";
        inline static const string expiry_timestamp = "EXPIRY_TIMESTAMP";
        inline static const string created_timestamp = "CREATED_TIMESTAMP";
        inline static const string stream_id = "STREAM_ID";
        inline static const string file_size = "FILE_SIZE";
        inline static const string start_time_ms = "START_TIME_MS";
        inline static const string end_time_ms = "END_TIME_MS";
        inline static const string file_type = "FILE_TYPE";
        inline static const string container_format = "CONTAINER_FORMAT";

        static constexpr const char* FILE_TYPE_VIDEO = "video";
        static constexpr const char* FILE_TYPE_IMAGE = "image";
        static constexpr int64_t CACHE_TIME_TOLERANCE_MS = 33;

        string file_path_value;
        int64_t expiry_timestamp_value;
        int64_t created_timestamp_value;
        string stream_id_value;
        int64_t file_size_value;
        int64_t start_time_ms_value;
        int64_t end_time_ms_value;
        string file_type_value;
        string container_format_value;

        TempFilesDBColumns() : file_path_value(""),
                               expiry_timestamp_value(0),
                               created_timestamp_value(0),
                               stream_id_value(""),
                               file_size_value(0),
                               start_time_ms_value(0),
                               end_time_ms_value(0),
                               file_type_value(""),
                               container_format_value("") {}

        TempFilesDBColumns(const string& filePath, 
                           int64_t expiryTs, 
                           int64_t createdTs,
                           const string& streamId,
                           int64_t fileSize,
                           int64_t startTimeMs = 0,
                           int64_t endTimeMs = 0,
                           const string& fileType = "",
                           const string& containerFormat = "") : 
                           file_path_value(filePath),
                           expiry_timestamp_value(expiryTs),
                           created_timestamp_value(createdTs),
                           stream_id_value(streamId),
                           file_size_value(fileSize),
                           start_time_ms_value(startTimeMs),
                           end_time_ms_value(endTimeMs),
                           file_type_value(fileType),
                           container_format_value(containerFormat) {}

        void printInfo()
        {
            LOG(info) << "\tfile_path_value: " << file_path_value << endl;
            LOG(info) << "\tdevice_id_value: " << device_id_value << endl;
            LOG(info) << "\texpiry_timestamp_value: " << expiry_timestamp_value << endl;
            LOG(info) << "\tcreated_timestamp_value: " << created_timestamp_value << endl;
            LOG(info) << "\tstream_id_value: " << stream_id_value << endl;
            LOG(info) << "\tfile_size_value: " << file_size_value << endl;
            LOG(info) << "\tstart_time_ms_value: " << start_time_ms_value << endl;
            LOG(info) << "\tend_time_ms_value: " << end_time_ms_value << endl;
            LOG(info) << "\tfile_type_value: " << file_type_value << endl;
            LOG(info) << "\tcontainer_format_value: " << container_format_value << endl;
        }
    };

} // nv_vms

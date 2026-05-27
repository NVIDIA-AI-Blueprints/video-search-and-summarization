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

#include "database.h"
#include "streamrecorder.h"
#include "config.h"
#include "gst_utils.h"
#include "storage_management.h"
#include "vst_common.h"
#include "database_common.h"
#include "modules_apis.h"
#include "utils.h"
#include <optional>
#include <iostream>

using namespace std;
using namespace nv_vms;

constexpr const char* DB_FILE_NAME = "nvvst.db";
#define CHECK_DB_IF_NULL_RETURN(ret) \
    if (m_db == nullptr)             \
        return ret;

Sqlite *Sqlite::m_instance = nullptr;

// Callback function to fill up query result
static int fillResultCallback(void *userData, int argc, char **argv, char **azColName)
{
    auto result = static_cast<queryResult *>(userData);
    std::unordered_map<std::string, std::string> row;
    for (int i = 0; i < argc; i++)
    {
        row[azColName[i]] = argv[i] ? argv[i] : EMPTY_STRING;
    }
    result->push_back(row);
    return 0;
}

static void sensorDetailsHelper(SensorDetailsDBColumns &row, unordered_map<string, string> &entries)
{
    for (auto column : entries)
    {
        if (iequals(column.first, SensorDetailsDBColumns::device_id))
        {
            row.device_id_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::sensor_id))
        {
            row.sensor_id_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::sensor_hw_id))
        {
            row.sensor_hw_id_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::username))
        {
            row.username_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::password))
        {
            std::string encryptedPassword = column.second;
            std::string decryptedPassword = EMPTY_STRING;
            if (!encryptedPassword.empty())
            {
                if (!vst_common::decrypt_data(encryptedPassword, decryptedPassword, row.sensor_id_value))
                {
                    LOG(error) << "Failed to decrypt password for sensor: " << row.sensor_id_value 
                               << ", encrypted password length: " << encryptedPassword.length() 
                               << ", IV: " << row.sensor_id_value 
                               << ". Password may have been encrypted with different key. Please re-enter credentials." << endl;
                    // Leave decryptedPassword empty to indicate credentials need to be re-entered
                }
            }
            row.password_value = decryptedPassword;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::name))
        {
            row.name_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::ip))
        {
            row.ip_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::hardware))
        {
            row.hardware_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::manufacturer))
        {
            row.manufacturer_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::serial_number))
        {
            row.serial_number_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::firmware_version))
        {
            row.firmware_version_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::hardware_id))
        {
            row.hardware_id_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::location))
        {
            row.location_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::tags))
        {
            row.tags_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::url))
        {
            row.url_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::type))
        {
            row.type_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::position))
        {
            row.position_value = column.second;
            if (row.position_value.size() >= 2)
            {
                // remove quotation from JSON string
                row.position_value = row.position_value.substr(1, row.position_value.size() - 2);
            }
        }
        else if (iequals(column.first, SensorDetailsDBColumns::users))
        {
            row.users_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::isRemoteSensor))
        {
            row.isRemoteSensor_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::remoteDeviceId))
        {
            row.remoteDeviceId_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::remoteDeviceName))
        {
            row.remoteDeviceName_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::remoteDeviceLocation))
        {
            row.remoteDeviceLocation_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::httpStatus))
        {
            row.httpStatus_value = stringToLong(column.second);
        }
        else if (iequals(column.first, SensorDetailsDBColumns::sensorStatus))
        {
            row.sensorStatus_value = stringToLong(column.second);
        }
        else if (iequals(column.first, SensorDetailsDBColumns::created_date_time))
        {
            row.created_date_time_value = column.second;
        }
        else if (iequals(column.first, SensorDetailsDBColumns::modified_date_time))
        {
            row.modified_date_time_value = column.second;
        }
    }
}

#ifdef UNIT_TEST
std::vector<VideoRecordDBColumns> Sqlite::getLastRecordVideoRecord(string streamId)
{
    std::vector<VideoRecordDBColumns> rows;
    VideoRecordDBColumns row;
    queryResult result;
    string id = streamId;
    CHECK_DB_IF_NULL_RETURN(rows)

    /* Create SQL statement to update the record using dynamic parameter macros */
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE ROWID in ( SELECT max(ROWID) FROM " + VideoRecordDBColumns::table_name + " WHERE " + VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ");";
    
    // Build parameterized values using safe macros
    std::vector<std::string> params;
    APPEND_COLUMN_VALUE(id, params)
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}
#endif

Sqlite::Sqlite() : m_db(nullptr), m_connected(false)
{
}

Sqlite::~Sqlite()
{
    if (m_db)
    {
        sqlite3_close(m_db);
        m_connected = false;
    }
}

bool Sqlite::executeQuery(const std::string &query, queryResult &result)
{
    char *zErrMsg = nullptr;
    int rc;

    rc = sqlite3_exec(m_db, query.c_str(), fillResultCallback, &result, &zErrMsg);

    if (rc != SQLITE_OK)
    {
        LOG(error) << "SQL error in query: " << query << "\n";
        LOG(error) << "SQLite error: " << zErrMsg << "\n";
        sqlite3_free(zErrMsg);
        return false;
    }
    return true;
}

bool Sqlite::executeQuery(const std::string &query)
{
    char *zErrMsg = nullptr;
    int rc;

    rc = sqlite3_exec(m_db, query.c_str(), nullptr, nullptr, &zErrMsg);

    if (rc != SQLITE_OK)
    {
        LOG(error) << "SQL error in query: " << query << "\n";
        LOG(error) << "SQLite error: " << zErrMsg << "\n";
        sqlite3_free(zErrMsg);
        return false;
    }
    return true;
}

bool Sqlite::executeQuery(const std::string &queryTemplate, const std::vector<std::string> &params, queryResult &result)
{
    // Build the safe query using QueryBuilder with SQLite-specific escaping
    std::string safeQuery = QueryBuilder::buildQuery(queryTemplate, params, DatabaseType::SQLite);
    // Execute the safe query
    return executeQuery(safeQuery, result);
}

bool Sqlite::executeQuery(const std::string &queryTemplate, const std::vector<std::string> &params)
{
    // Build the safe query using QueryBuilder with SQLite-specific escaping
    std::string safeQuery = QueryBuilder::buildQuery(queryTemplate, params, DatabaseType::SQLite);
    // Execute the safe query
    return executeQuery(safeQuery);
}

bool Sqlite::isConnected()
{
    return m_connected;
}

bool Sqlite::connect()
{
    int rc;
    const string prepend_dir = GET_CONFIG().vst_data_path;
    string vst_data_path = prepend_dir + string("/") + DB_FILE_NAME;
    rc = sqlite3_open(vst_data_path.c_str(), &m_db);

    if (rc)
    {
        LOG(error) << "Can't open database: " << sqlite3_errmsg(m_db) << endl;
        return -1;
    }
    else
    {
        LOG(verbose) << "Opened database successfully" << endl;
        m_connected = true;
    }

    // Enable foreign key support
    {
        const char *sql = "PRAGMA foreign_keys = ON;";
        char *errMsg = nullptr;
        int result = sqlite3_exec(m_db, sql, nullptr, nullptr, &errMsg);

        if (result != SQLITE_OK)
        {
            std::cerr << "Error executing SQL: " << errMsg << std::endl;
            sqlite3_free(errMsg);
        }
    }

    /* Create EVENTS Table */
    string sql = string("CREATE TABLE IF NOT EXISTS " + EventDBColumns::table_name + "(") +
                 DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
                 EventDBColumns::video_path + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
                 DBColumns::device_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
                 DBColumns::sensor_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
                 EventDBColumns::start_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
                 EventDBColumns::end_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
                 EventDBColumns::event_name + string(" VARCHAR(1024) ") + string(" , ") +
                 EventDBColumns::event_id + string(" VARCHAR(1024) ") + string(" , ") +
                 DBColumns::created_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
                 DBColumns::modified_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL ") +
                 string(");");

    LOG(verbose) << "SQL query: Table: " << EventDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create SENSOR_DETAILS Table */
    sql = string("CREATE TABLE IF NOT EXISTS " + SensorDetailsDBColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          DBColumns::device_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::sensor_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          SensorDetailsDBColumns::sensor_hw_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          SensorDetailsDBColumns::username + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::password + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::name + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::ip + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::hardware + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::manufacturer + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::serial_number + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::firmware_version + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::hardware_id + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::location + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::tags + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::url + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::type + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::position + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::users + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::isRemoteSensor + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::remoteDeviceId + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::remoteDeviceName + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::remoteDeviceLocation + string(" VARCHAR(1024) ") + string(" , ") +
          SensorDetailsDBColumns::httpStatus + string(" INTEGER ") + string(" , ") +
          SensorDetailsDBColumns::sensorStatus + string(" INTEGER ") + string(" , ") +
          DBColumns::created_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::modified_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          string("UNIQUE ( ") + DBColumns::sensor_id + string(" )") +
          string(");");

    LOG(verbose) << "SQL query: Table: " << SensorDetailsDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create VIDEO_RECORD_DETAILS Table */
    sql = string("CREATE TABLE IF NOT EXISTS " + VideoRecordDBColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          DBColumns::sensor_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          VideoRecordDBColumns::stream_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          VideoRecordDBColumns::resolution + string(" VARCHAR(1024) ") + string(" , ") +
          VideoRecordDBColumns::start_time + string(" BIGINT ") + string(" , ") +
          VideoRecordDBColumns::duration + string(" BIGINT ") + string(" , ") +
          VideoRecordDBColumns::file_path + string(" VARCHAR(4096) ") + string(" , ") +
          VideoRecordDBColumns::file_size + string(" BIGINT ") + string(" , ") +
          VideoRecordDBColumns::file_fps + string(" BIGINT ") + string(" , ") +
          VideoRecordDBColumns::sensor_name + string(" VARCHAR(1024) ") + string(" , ") +
          VideoRecordDBColumns::record_config + string(" VARCHAR(1024) ") + string(" , ") +
          VideoRecordDBColumns::codec + string(" VARCHAR(1024) ") + string(" , ") +
          VideoRecordDBColumns::file_protection + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          VideoRecordDBColumns::metadata_file_path + string(" VARCHAR(4096) ") + string(" , ") +
          VideoRecordDBColumns::metadata_json + string(" VARCHAR(4096) ") + string(" , ") +
          VideoRecordDBColumns::object_id + string(" VARCHAR(4096) ") + string(" , ") +
          VideoRecordDBColumns::storage_location + string(" BIGINT ") + string(" NOT NULL DEFAULT 0, ") +
          VideoRecordDBColumns::bucket_name + string(" VARCHAR(1024) ") + string(" , ") +
          DBColumns::created_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::modified_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL ") +
          string(");");

    LOG(verbose) << "SQL query: Table: " << VideoRecordDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create VIDEO_RECORD_SCHEDULE_DETAILS Table */
    sql = string("CREATE TABLE IF NOT EXISTS " + VideoRecordScheduleDBColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          DBColumns::device_id + string(" VARCHAR(1024) ") + string(" , ") +
          DBColumns::sensor_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          VideoRecordScheduleDBColumns::stream_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          VideoRecordScheduleDBColumns::start_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          VideoRecordScheduleDBColumns::end_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::created_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::modified_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL ") +
          string(");");

    LOG(verbose) << "SQL query: Table: " << VideoRecordScheduleDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }
    /* Create SENSOR_STREAMS Table */
    sql = string("CREATE TABLE IF NOT EXISTS " + SensorStreamsDBColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          DBColumns::sensor_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          SensorStreamsDBColumns::stream_id + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          SensorStreamsDBColumns::live_url + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::replay_url + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::proxy_url + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::resolution + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::frameRate + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::encoding + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::streamStatus + string(" INTEGER ") + string(" , ") +
          SensorStreamsDBColumns::type + string(" INTEGER ") + string(" , ") +
          SensorStreamsDBColumns::encodingProfile + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::encodingInterval + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::duration + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::isMainStream + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::isAlwaysRecording + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::storageLocation + string(" INTEGER DEFAULT 0 ") + string(" , ") +
          SensorStreamsDBColumns::bitrate + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::numFrames + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::audio_container + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::audio_encoding + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::audio_sample_rate + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::audio_bps + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::audio_channels + string(" VARCHAR(1024) ") + string(" , ") +
          SensorStreamsDBColumns::streamName + string(" VARCHAR(1024) NOT NULL DEFAULT '' ") + string(" , ") +
          SensorStreamsDBColumns::isBframesPresent + string(" INTEGER DEFAULT 0 ") + string(" , ") +
          DBColumns::created_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::modified_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          string("UNIQUE ( ") + SensorStreamsDBColumns::stream_id + string(" ) ") +
          string("FOREIGN KEY (") + SensorStreamsDBColumns::sensor_id + string(") ") +
          string("REFERENCES ") + SensorDetailsDBColumns::table_name + string(" (") +
          SensorDetailsDBColumns::sensor_id + string(")") + string(");");

    LOG(verbose) << "SQL query: Table: " << SensorStreamsDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create RECORDING_STATUS table */
    sql = string("CREATE TABLE IF NOT EXISTS " + RecordingStatusDBColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          RecordingStatusDBColumns::sensor_id + string(" VARCHAR(1024), ") +
          RecordingStatusDBColumns::stream_id + string(" VARCHAR(1024) NOT NULL, ") +
          RecordingStatusDBColumns::recordingStatus + string(" INTEGER NOT NULL, ") +
          RecordingStatusDBColumns::created_date_time + string(" VARCHAR(1024) NOT NULL, ") +
          RecordingStatusDBColumns::modified_date_time + string(" VARCHAR(1024) NOT NULL, ") +
          string("UNIQUE (") + RecordingStatusDBColumns::stream_id + string(") ") +
          string(");");

    LOG(verbose) << "SQL query: Table: " << RecordingStatusDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create USER_DETAILS table */
    sql = string("CREATE TABLE IF NOT EXISTS " + UserDetailsDBColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          UserDetailsDBColumns::username + string(" VARCHAR(1024) ") + string(" , ") +
          UserDetailsDBColumns::password_hash + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::created_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::modified_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          string("UNIQUE ( ") + UserDetailsDBColumns::username + string(" )") +
          string(");");

    LOG(verbose) << "SQL query: Table: " << UserDetailsDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create USER_SESSIONS table */
    sql = string("CREATE TABLE IF NOT EXISTS " + UserSessionsDBColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          UserSessionsDBColumns::username + string(" VARCHAR(1024) ") + string(" , ") +
          UserSessionsDBColumns::session_cookie + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          UserSessionsDBColumns::cookie_max_age + string(" BIGINT ") + string(" NOT NULL, ") +
          DBColumns::created_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::modified_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          string("UNIQUE ( ") + UserSessionsDBColumns::session_cookie + string(" )") +
          string("FOREIGN KEY (") + UserSessionsDBColumns::username + string(") ") +
          string("REFERENCES ") + UserDetailsDBColumns::table_name + string(" (") +
          UserDetailsDBColumns::username + string(")") + string(" ON DELETE CASCADE ON UPDATE CASCADE);");

    LOG(verbose) << "SQL query: Table: " << UserSessionsDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create LOCAL_DEVICE_DETAILS table */
    sql = string("CREATE TABLE IF NOT EXISTS " + LocalDeviceDetailsDBColumns::table_name + " (")
            + " enforce_single_row BOOLEAN NOT NULL DEFAULT 1 UNIQUE, "
            + DBColumns::row_id + " INTEGER PRIMARY KEY AUTOINCREMENT, "
            + LocalDeviceDetailsDBColumns::id + " VARCHAR(1024), "
            + LocalDeviceDetailsDBColumns::location + " VARCHAR(1024), "
            + LocalDeviceDetailsDBColumns::name + " VARCHAR(1024), "
            + DBColumns::created_date_time + " VARCHAR(1024) NOT NULL, "
            + DBColumns::modified_date_time + " VARCHAR(1024) NOT NULL "
            + ");";

    LOG(verbose) << "SQL query: Table: " << LocalDeviceDetailsDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create DB_DETAILS table */
    sql = string("CREATE TABLE IF NOT EXISTS " + DbDetailsColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          DbDetailsColumns::db_version + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::created_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL, ") +
          DBColumns::modified_date_time + string(" VARCHAR(1024) ") + string(" NOT NULL ") +
          string(");");

    LOG(verbose) << "SQL query: Table: " << DbDetailsColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Create TEMP_VIDEO_FILES Table */
    sql = string("CREATE TABLE IF NOT EXISTS " + TempFilesDBColumns::table_name + "(") +
          DBColumns::row_id + string(" INTEGER PRIMARY KEY AUTOINCREMENT,") +
          DBColumns::device_id + string(" TEXT NOT NULL, ") +
          TempFilesDBColumns::file_path + string(" TEXT NOT NULL UNIQUE, ") +
          TempFilesDBColumns::expiry_timestamp + string(" INTEGER NOT NULL, ") +
          TempFilesDBColumns::created_timestamp + string(" INTEGER NOT NULL, ") +
          TempFilesDBColumns::stream_id + string(" TEXT, ") +
          TempFilesDBColumns::file_size + string(" INTEGER, ") +
          TempFilesDBColumns::start_time_ms + string(" INTEGER DEFAULT 0, ") +
          TempFilesDBColumns::end_time_ms + string(" INTEGER DEFAULT 0, ") +
          TempFilesDBColumns::file_type + string(" TEXT DEFAULT '', ") +
          TempFilesDBColumns::container_format + string(" TEXT DEFAULT '', ") +
          DBColumns::created_date_time + string(" TEXT NOT NULL, ") +
          DBColumns::modified_date_time + string(" TEXT NOT NULL ") +
          string(");");
    LOG(verbose) << "SQL query: Table: " << TempFilesDBColumns::table_name << " " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    /* Migrate existing tables: add new columns if missing.
       Checks column existence via SELECT before ALTER TABLE to avoid duplicate column errors. */
    auto addColumnIfMissing = [this](const std::string& table, const std::string& column, const std::string& type) {
        std::string checkSql = "SELECT " + column + " FROM " + table + " LIMIT 0;";
        queryResult dummy;
        if (!executeQuery(checkSql, dummy))
        {
            std::string alterSql = "ALTER TABLE " + table + " ADD COLUMN " + column + " " + type + ";";
            executeQuery(alterSql);
        }
    };
    addColumnIfMissing(TempFilesDBColumns::table_name, TempFilesDBColumns::start_time_ms, "INTEGER DEFAULT 0");
    addColumnIfMissing(TempFilesDBColumns::table_name, TempFilesDBColumns::end_time_ms, "INTEGER DEFAULT 0");
    addColumnIfMissing(TempFilesDBColumns::table_name, TempFilesDBColumns::file_type, "TEXT DEFAULT ''");
    addColumnIfMissing(TempFilesDBColumns::table_name, TempFilesDBColumns::container_format, "TEXT DEFAULT ''");

    /* Create indexes for TEMP_VIDEO_FILES table performance */
    sql = "CREATE INDEX IF NOT EXISTS idx_temp_files_expiry ON " + 
          TempFilesDBColumns::table_name + " (" + TempFilesDBColumns::expiry_timestamp + ");";
    if (!executeQuery(sql))
    {
        LOG(error) << "Error creating temp files expiry index: " << sql << endl;
    }

    sql = "CREATE INDEX IF NOT EXISTS idx_temp_files_device ON " + 
          TempFilesDBColumns::table_name + " (" + DBColumns::device_id + ");";
    if (!executeQuery(sql))
    {
        LOG(error) << "Error creating temp files device index: " << sql << endl;
    }

    sql = "CREATE INDEX IF NOT EXISTS idx_temp_files_stream_time ON " +
          TempFilesDBColumns::table_name + " (" +
          DBColumns::device_id + ", " +
          TempFilesDBColumns::stream_id + ", " +
          TempFilesDBColumns::start_time_ms + ", " +
          TempFilesDBColumns::end_time_ms + ", " +
          TempFilesDBColumns::file_type + ");";
    if (!executeQuery(sql))
    {
        LOG(error) << "Error creating temp files stream_time index: " << sql << endl;
    }

    /* Set a DB version */
    DbDetailsColumns db_detail_row;
    db_detail_row.db_version_value = VST_DB_VERSION;
    db_detail_row.row_id_value = "0";
    setDbVersion(db_detail_row);

    /**
     * username is UNIQUE in USER_DETAILS, the query will fail harmlessly
     * if admin user already exists on reboot
     */
    UserDetailsDBColumns row;
    row.username_value = DEFAULT_ADMIN_USERNAME;
    string passwordHash;
    vst_common::getSha256(DEFAULT_ADMIN_PASSWORD, passwordHash);
    row.password_hash_value = passwordHash;
    setUserDetail(row);
    return 0;
}

int Sqlite::insertRowEvent(EventDBColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string sql;
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement */

    /* Create SQL statement with parameterized query */
    sql = string("INSERT INTO " + EventDBColumns::table_name + "(");
    
    APPEND_COLUMN(EventDBColumns::video_path, row.video_path_value, sql)
    APPEND_COLUMN(DBColumns::device_id, row.device_id_value, sql)
    APPEND_COLUMN(DBColumns::sensor_id, row.sensor_id_value, sql)
    APPEND_COLUMN(EventDBColumns::start_time, row.start_time_value, sql)
    APPEND_COLUMN(EventDBColumns::end_time, row.end_time_value, sql)
    APPEND_COLUMN(EventDBColumns::event_name, row.event_name_value, sql)
    APPEND_COLUMN(EventDBColumns::event_id, row.event_id_value, sql)
    APPEND_COLUMN(EventDBColumns::created_date_time, currentUtcTime, sql)
    APPEND_COLUMN(EventDBColumns::modified_date_time, currentUtcTime, sql)
    sql.pop_back();
    
    // Build parameterized values using safe macros
    vector<string> params;
    APPEND_COLUMN_VALUE(row.video_path_value, params)
    APPEND_COLUMN_VALUE(row.device_id_value, params)
    APPEND_COLUMN_VALUE(row.sensor_id_value, params)
    APPEND_COLUMN_VALUE(row.start_time_value, params)
    APPEND_COLUMN_VALUE(row.end_time_value, params)
    APPEND_COLUMN_VALUE(row.event_name_value, params)
    APPEND_COLUMN_VALUE(row.event_id_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(sql, params);
    sql += ";";

    LOG(verbose) << "SQL query: " << sql << endl;
    
    /* Execute SQL statement with parameters */
    if (!executeQuery(sql, params))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }
    return 0;
}

int Sqlite::insertRowSensorDetails(SensorDetailsDBColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    string encryptedPassword = EMPTY_STRING;
    SensorDetailsDBColumns r = readSensorDetails(row.device_id_value, row.sensor_id_value);
    row.sensor_hw_id_value = row.sensor_hw_id_value.empty() ? r.sensor_hw_id_value : row.sensor_hw_id_value;
    row.username_value = row.username_value.empty() ? r.username_value : row.username_value;
    row.password_value = row.password_value.empty() ? r.password_value : row.password_value;
    if (!row.password_value.empty())
    {
        if (!vst_common::encrypt_data(row.password_value, encryptedPassword, row.sensor_id_value))
        {
            LOG(error) << "Failed to encrypt password for sensor: " << row.sensor_id_value << endl;
            encryptedPassword = EMPTY_STRING; // Ensure we don't store corrupted data
        }
    }
    row.name_value = row.name_value.empty() ? r.name_value : row.name_value;
    row.ip_value = row.ip_value.empty() ? r.ip_value : row.ip_value;
    row.hardware_value = row.hardware_value.empty() ? r.hardware_value : row.hardware_value;
    row.manufacturer_value = row.manufacturer_value.empty() ? r.manufacturer_value : row.manufacturer_value;
    row.serial_number_value = row.serial_number_value.empty() ? r.serial_number_value : row.serial_number_value;
    row.firmware_version_value = row.firmware_version_value.empty() ? r.firmware_version_value : row.firmware_version_value;
    row.hardware_id_value = row.hardware_id_value.empty() ? r.hardware_id_value : row.hardware_id_value;
    row.location_value = row.location_value.empty() ? r.location_value : row.location_value;
    row.tags_value = row.tags_value.empty() ? r.tags_value : row.tags_value;
    row.url_value = row.url_value.empty() ? r.url_value : row.url_value;
    row.type_value = row.type_value.empty() ? r.type_value : row.type_value;
    row.position_value = row.position_value.empty() ? r.position_value : row.position_value;
    row.users_value = row.users_value.empty() ? r.users_value : row.users_value;
    row.isRemoteSensor_value = row.isRemoteSensor_value.empty() ? r.isRemoteSensor_value : row.isRemoteSensor_value;
    row.remoteDeviceId_value = row.remoteDeviceId_value.empty() ? r.remoteDeviceId_value : row.remoteDeviceId_value;
    row.remoteDeviceName_value = row.remoteDeviceName_value.empty() ? r.remoteDeviceName_value : row.remoteDeviceName_value;
    row.remoteDeviceLocation_value = row.remoteDeviceLocation_value.empty() ? r.remoteDeviceLocation_value : row.remoteDeviceLocation_value;
    row.httpStatus_value = row.httpStatus_value == -1 ? r.httpStatus_value : row.httpStatus_value;
    row.sensorStatus_value = row.sensorStatus_value == SensorStatusUnknown ? r.sensorStatus_value : row.sensorStatus_value;
    row.created_date_time_value = r.created_date_time_value.empty() ? currentUtcTime : r.created_date_time_value;
    row.modified_date_time_value = currentUtcTime;

    /* Create SQL statement with parameterized query */
    string sql = string("INSERT OR REPLACE INTO " + SensorDetailsDBColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(DBColumns::device_id, row.device_id_value, sql)
    APPEND_COLUMN(DBColumns::sensor_id, row.sensor_id_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::sensor_hw_id, row.sensor_hw_id_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::username, row.username_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::password, encryptedPassword, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::name, row.name_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::ip, row.ip_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::hardware, row.hardware_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::manufacturer, row.manufacturer_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::serial_number, row.serial_number_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::firmware_version, row.firmware_version_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::hardware_id, row.hardware_id_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::location, row.location_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::tags, row.tags_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::url, row.url_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::type, row.type_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::position, row.position_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::users, row.users_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::isRemoteSensor, row.isRemoteSensor_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::remoteDeviceId, row.remoteDeviceId_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::remoteDeviceName, row.remoteDeviceName_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::remoteDeviceLocation, row.remoteDeviceLocation_value, sql)
    APPEND_COLUMN_INT(SensorDetailsDBColumns::httpStatus, row.httpStatus_value, sql)
    APPEND_COLUMN_INT(SensorDetailsDBColumns::sensorStatus, row.sensorStatus_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::created_date_time, row.created_date_time_value, sql)
    APPEND_COLUMN(SensorDetailsDBColumns::modified_date_time, row.modified_date_time_value, sql)
    sql.pop_back();
    
    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(row.device_id_value, params)
    APPEND_COLUMN_VALUE(row.sensor_id_value, params)
    APPEND_COLUMN_VALUE(row.sensor_hw_id_value, params)
    APPEND_COLUMN_VALUE(row.username_value, params)
    APPEND_COLUMN_VALUE(encryptedPassword, params)
    APPEND_COLUMN_VALUE(row.name_value, params)
    APPEND_COLUMN_VALUE(row.ip_value, params)
    APPEND_COLUMN_VALUE(row.hardware_value, params)
    APPEND_COLUMN_VALUE(row.manufacturer_value, params)
    APPEND_COLUMN_VALUE(row.serial_number_value, params)
    APPEND_COLUMN_VALUE(row.firmware_version_value, params)
    APPEND_COLUMN_VALUE(row.hardware_id_value, params)
    APPEND_COLUMN_VALUE(row.location_value, params)
    APPEND_COLUMN_VALUE(row.tags_value, params)
    APPEND_COLUMN_VALUE(row.url_value, params)
    APPEND_COLUMN_VALUE(row.type_value, params)
    APPEND_COLUMN_VALUE_JSON(row.position_value, params)
    APPEND_COLUMN_VALUE(row.users_value, params)
    APPEND_COLUMN_VALUE(row.isRemoteSensor_value, params)
    APPEND_COLUMN_VALUE(row.remoteDeviceId_value, params)
    APPEND_COLUMN_VALUE(row.remoteDeviceName_value, params)
    APPEND_COLUMN_VALUE(row.remoteDeviceLocation_value, params)
    APPEND_COLUMN_VALUE_INT(row.httpStatus_value, params)
    APPEND_COLUMN_VALUE_INT(row.sensorStatus_value, params)
    APPEND_COLUMN_VALUE(row.created_date_time_value, params)
    APPEND_COLUMN_VALUE(row.modified_date_time_value, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(sql, params);
    sql += ";";
    
    LOG(verbose) << "SQL query: " << sql << endl;
    
    /* Execute SQL statement with parameters */
    if (!executeQuery(sql, params))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }
    return 0;
}

SensorDetailsDBColumns Sqlite::readSensorDetails(string deviceId, string sensorId)
{
    SensorDetailsDBColumns row;
    CHECK_DB_IF_NULL_RETURN(row)
    queryResult result;
    
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {sensorId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        sensorDetailsHelper(row, entries);
    }
    return row;
}

SensorDetailsDBColumns Sqlite::readSensorDetailsByLocation(string location)
{
    SensorDetailsDBColumns row;
    CHECK_DB_IF_NULL_RETURN(row)
    queryResult result;
    
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name + " WHERE " + SensorDetailsDBColumns::location + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {location};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        sensorDetailsHelper(row, entries);
    }
    return row;
}

vector<SensorDetailsDBColumns> Sqlite::readSensorDetails(string deviceId)
{
    vector<SensorDetailsDBColumns> rowArray;
    CHECK_DB_IF_NULL_RETURN(rowArray)
    queryResult result;
    if (deviceId.empty() == false)
    {
        // Use parameterized query to prevent SQL injection
        string queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name + " WHERE " + DBColumns::device_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        std::vector<std::string> params = {deviceId};
        
        if (!executeQuery(queryTemplate, params, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return rowArray;
        }
    }
    else
    {
        string queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name + ";";
        if (!executeQuery(queryTemplate, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return rowArray;
        }
    }

    for (auto entries : result)
    {
        SensorDetailsDBColumns row;
        sensorDetailsHelper(row, entries);
        rowArray.push_back(row);
    }
    return rowArray;
}

vector<SensorDetailsDBColumns> Sqlite::readAllSensorSatus(string deviceId)
{
    vector<SensorDetailsDBColumns> rowArray;
    queryResult result;
    
    if (deviceId.empty() == false)
    {
        // Use parameterized query to prevent SQL injection
        string queryTemplate = "SELECT " + SensorDetailsDBColumns::sensor_id + ", " + SensorDetailsDBColumns::httpStatus + ", " + SensorDetailsDBColumns::sensorStatus + 
                              " FROM " + SensorDetailsDBColumns::table_name + " WHERE " + DBColumns::device_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        std::vector<std::string> params = {deviceId};
        
        if (!executeQuery(queryTemplate, params, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return rowArray;
        }
    }
    else
    {
        string sql = "SELECT " + SensorDetailsDBColumns::sensor_id + ", " + SensorDetailsDBColumns::httpStatus + ", " + SensorDetailsDBColumns::sensorStatus + " FROM " + SensorDetailsDBColumns::table_name;
        if (!executeQuery(sql, result))
        {
            LOG(error) << "Error executing SQL stmt: " << sql << endl;
            return rowArray;
        }
    }

    for (auto entries : result)
    {
        SensorDetailsDBColumns row;
        sensorDetailsHelper(row, entries);
        rowArray.push_back(row);
    }
    return rowArray;
}

int Sqlite::updateStreamInfo(string streamId, string proxyUrl, string replayUrl, std::pair<StreamStatus, string> status)
{
    string currentUtcTime = getCurrentUtcTime();
    
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + SensorStreamsDBColumns::table_name +
                " SET " + SensorStreamsDBColumns::proxy_url + " = " + PARAM_PLACEHOLDER(0) + ", " +
                SensorStreamsDBColumns::replay_url + " = " + PARAM_PLACEHOLDER(1) + ", " +
                SensorStreamsDBColumns::streamStatus + " = " + PARAM_PLACEHOLDER(2) + ", " +
                SensorStreamsDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(3) + " " +
                " WHERE " + SensorStreamsDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(4) + ";";
    
    std::vector<std::string> params = {proxyUrl, replayUrl, std::to_string(status.first), currentUtcTime, streamId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }

    return 0;
}

int Sqlite::deleteSensorDetails(string sensorId)
{
    // Execute all deletions in a single transaction to avoid foreign key constraint violations
    string id = sensorId;
    
    // Build the complete deletion query as a single transaction
    std::string queryTemplate = 
        "DELETE FROM " + SensorStreamsDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + "; "
        "DELETE FROM " + RecordingStatusDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + "; "
        "DELETE FROM " + SensorDetailsDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    
    std::vector<std::string> params = {id, id, id};
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    CHECK_DB_IF_NULL_RETURN(-1)

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;  // Return 0 on success
}

int Sqlite::insertRowVideoRecord(VideoRecordDBColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string sql;
    string id = row.sensor_id_value;
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query */
    sql = string("INSERT INTO " + VideoRecordDBColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(DBColumns::sensor_id, row.sensor_id_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::stream_id, row.stream_id_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::resolution, row.resolution_value, sql)
    APPEND_COLUMN_INT(VideoRecordDBColumns::start_time, row.start_time_value, sql)
    APPEND_COLUMN_INT(VideoRecordDBColumns::duration, row.duration_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::file_path, row.filepath_value, sql)
    APPEND_COLUMN_INT(VideoRecordDBColumns::file_size, row.filesize_value, sql)
    APPEND_COLUMN_INT(VideoRecordDBColumns::file_fps, row.filefps_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::sensor_name, row.sensor_name_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::record_config, row.record_config_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::codec, row.codec_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::file_protection, row.file_protection_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::metadata_file_path, row.metadata_file_path_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::metadata_json, row.metadata_json_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::object_id, row.object_id_value, sql)
    APPEND_COLUMN_INT(VideoRecordDBColumns::storage_location, row.storage_location_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::bucket_name, row.bucket_name_value, sql)
    APPEND_COLUMN(VideoRecordDBColumns::created_date_time, currentUtcTime, sql)
    APPEND_COLUMN(VideoRecordDBColumns::modified_date_time, currentUtcTime, sql)
    sql.pop_back();

    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(row.sensor_id_value, params)
    APPEND_COLUMN_VALUE(row.stream_id_value, params)
    APPEND_COLUMN_VALUE(row.resolution_value, params)
    APPEND_COLUMN_VALUE_INT(row.start_time_value, params)
    APPEND_COLUMN_VALUE_INT(row.duration_value, params)
    APPEND_COLUMN_VALUE(row.filepath_value, params)
    APPEND_COLUMN_VALUE_INT(row.filesize_value, params)
    APPEND_COLUMN_VALUE_INT(row.filefps_value, params)
    APPEND_COLUMN_VALUE(row.sensor_name_value, params)
    APPEND_COLUMN_VALUE(row.record_config_value, params)
    APPEND_COLUMN_VALUE(row.codec_value, params)
    APPEND_COLUMN_VALUE(row.file_protection_value, params)
    APPEND_COLUMN_VALUE(row.metadata_file_path_value, params)
    APPEND_COLUMN_VALUE(row.metadata_json_value, params)
    APPEND_COLUMN_VALUE(row.object_id_value, params)
    APPEND_COLUMN_VALUE_INT(row.storage_location_value, params)
    APPEND_COLUMN_VALUE(row.bucket_name_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)

    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(sql, params);
    sql += ";";

    LOG(verbose) << "SQL query: " << sql << endl;
    LOG(verbose) << "Parameters count: " << params.size() << endl;

    /* Execute SQL statement with parameters */
    if (!executeQuery(sql, params))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }
    return 0;
}

/* File Type Sensor Usecase for Storage Service Download when both Sensor ID and Object ID are provided */
std::vector<VideoRecordDBColumns> Sqlite::readVideoRecordSensorIdUniqueIdBased(string sensorId, string id)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(rows)

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
    VideoRecordDBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + " AND " +
    VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(1) + ";";
    
    std::vector<std::string> params = {sensorId, id};
    
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt : " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        /*
            * To fix.
            * Commenting due to bug 3945965.
            */
        if (row.duration_value == FILE_INIT_DURATION)
        {
            StorageManagement *storageMngt = GET_STORAGE_MNGT();
            if (storageMngt == nullptr)
            {
                LOG(error) << "Storage Management module is not loaded" << endl;
                return rows;
            }

            // updateActualDuration(row);
            LOG(verbose) << "Trying to get correct duration of file either open or improperly closed" << endl;
            vst_storage::addOrRemoveFileInProtectList(row.filepath_value, true);
            guint64 duration = getMediaFileDuration(row.filepath_value);
            if (duration)
            {
                row.duration_value = duration;
            }
            else
            {
                LOG(error) << "Get correct duration pipeline failed" << endl;
            }
            vst_storage::addOrRemoveFileInProtectList(row.filepath_value, false);
        }
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;

}

/* File Type Sensor Usecase for Storage Service Download when ONLY Object ID is provided */
std::vector<VideoRecordDBColumns> Sqlite::readVideoRecordUniqueIdBased(string id)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(rows)

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
    VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    
    std::vector<std::string> params = {id};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        /*
            * To fix.
            * Commenting due to bug 3945965.
            */
        if (row.duration_value == FILE_INIT_DURATION)
        {
            StorageManagement *storageMngt = GET_STORAGE_MNGT();
            if (storageMngt == nullptr)
            {
                LOG(error) << "Storage Management module is not loaded" << endl;
                return rows;
            }

            // updateActualDuration(row);
            LOG(verbose) << "Trying to get correct duration of file either open or improperly closed" << endl;
            vst_storage::addOrRemoveFileInProtectList(row.filepath_value, true);
            guint64 duration = getMediaFileDuration(row.filepath_value);
            if (duration)
            {
                row.duration_value = duration;
            }
            else
            {
                LOG(error) << "Get correct duration pipeline failed" << endl;
            }
            vst_storage::addOrRemoveFileInProtectList(row.filepath_value, false);
        }
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;

}

/* File Type Sensor Usecase for ReplayStream */
std::vector<VideoRecordDBColumns> Sqlite::readVideoRecordStreamIdBased(string streamId, int64_t startTime, int64_t endTime)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(rows)

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
            VideoRecordDBColumns::stream_id +
            " = " + PARAM_PLACEHOLDER(0) + " AND (" + VideoRecordDBColumns::start_time + " + " +
            VideoRecordDBColumns::duration + " >= " + PARAM_PLACEHOLDER(1) + " OR " +
            VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " ) AND " +
            VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(2) + " AND " +
            VideoRecordDBColumns::duration + " != " + PARAM_PLACEHOLDER(3) + ";";

    std::vector<std::string> params = {streamId, std::to_string(startTime), std::to_string(endTime), std::to_string(FILE_INIT_DURATION)};

    LOG(info) << "---- SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;
}

/* File Type Sensor Usecase for Storage Service Download when ONLY Sensor ID is provided */
std::vector<VideoRecordDBColumns> Sqlite::readVideoRecordSensorIdBased(string sensorId, int64_t startTime, int64_t endTime)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(rows)

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
            VideoRecordDBColumns::stream_id +
            " = " + PARAM_PLACEHOLDER(0) + " AND (" + VideoRecordDBColumns::start_time + " + " +
            VideoRecordDBColumns::duration + " >= " + PARAM_PLACEHOLDER(1) + " OR " +
            VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " ) AND " +
            VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(2) + " AND " +
            VideoRecordDBColumns::duration + " != " + PARAM_PLACEHOLDER(3) + ";";

    std::vector<std::string> params = {sensorId, std::to_string(startTime), std::to_string(endTime), std::to_string(FILE_INIT_DURATION)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;
}

std::vector<VideoRecordDBColumns> Sqlite::readVideoRecord(string sensorId, int64_t startTime, int64_t endTime, const std::vector<string>& streamIds)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    bool is_sensor_id = false;
    CHECK_DB_IF_NULL_RETURN(rows)

    if (streamIds.size())
    {
        // Use QueryBuilder to safely build WHERE IN clause to prevent SQL injection (SQLite-specific)
        std::string whereInClause = QueryBuilder::buildWhereInClause(VideoRecordDBColumns::stream_id, streamIds, DatabaseType::SQLite);
        if (whereInClause.empty())
        {
            LOG(error) << "Invalid column name: " << VideoRecordDBColumns::stream_id << endl;
            return rows;
        }
        string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " " + whereInClause + ";";

        std::vector<std::string> params = {};

        if (!executeQuery(queryTemplate, params, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return rows;
        }
    }
    /* If given stream id is empty then API will return all the video record details */
    else if (sensorId.empty())
    {
        string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + ";";

        std::vector<std::string> params = {};

        if (!executeQuery(queryTemplate, params, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return rows;
        }
    }
    else
    {
        // Use parameterized query to prevent SQL injection
        string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                (is_sensor_id ? VideoRecordDBColumns::sensor_id : VideoRecordDBColumns::stream_id) +
                " = " + PARAM_PLACEHOLDER(0) + " AND (" + VideoRecordDBColumns::start_time + " + " +
                VideoRecordDBColumns::duration + " >= " + PARAM_PLACEHOLDER(1) + " OR " +
                VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " ) AND " +
                VideoRecordDBColumns::start_time + " <= " + PARAM_PLACEHOLDER(2) + ";";

        std::vector<std::string> params = {sensorId, std::to_string(startTime), std::to_string(endTime)};

        if (!executeQuery(queryTemplate, params, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return rows;
        }
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        if (row.duration_value == FILE_INIT_DURATION)
        {
            int64_t currentTime = getCurrentUnixTimestampInMs();
            int64_t timeSinceStart = currentTime - row.start_time_value;
            if (timeSinceStart >= 0 && timeSinceStart < (2 * TYPICAL_FILE_DURATION_MS_INT))
            {
                row.duration_value = timeSinceStart;
            }
            else
            {
                row.duration_value = TYPICAL_FILE_DURATION_MS_INT;
            }
        }
        rows.push_back(row);
    }

    // Sort the rows locally based on start_time
    std::sort(rows.begin(), rows.end(), [](const VideoRecordDBColumns& a, const VideoRecordDBColumns& b) {
        return a.start_time_value < b.start_time_value;
    });

    return rows;
}

VideoRecordDBColumns Sqlite::readInProgressVideoRecord(string streamId, int64_t startTime)
{
    VideoRecordDBColumns row;
    queryResult result;
    string id = streamId;

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " + VideoRecordDBColumns::stream_id +
                 " = " + PARAM_PLACEHOLDER(0) + " AND (" + VideoRecordDBColumns::start_time + " + " +
                 "" + PARAM_PLACEHOLDER(2) + " > " + PARAM_PLACEHOLDER(1) + ") AND (" +
                 VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " OR " +
                 VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(1) + ") AND " +
                 " (" + VideoRecordDBColumns::duration + " = 1 " + ") " + ";";
    
    std::vector<std::string> params = {id, std::to_string(startTime), TYPICAL_FILE_DURATION_MAX_MS};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        videoRecordHelper(row, entries);
    }
    return row;
}

VideoRecordDBColumns Sqlite::readVideoRecordExactMatch(string streamId, int64_t startTime)
{
    VideoRecordDBColumns row;
    queryResult result;
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                 VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + " AND " +
                 VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + ";";
    
    std::vector<std::string> params = {streamId, std::to_string(startTime)};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        videoRecordHelper(row, entries);
    }
    return row;
}

VideoRecordDBColumns Sqlite::readVideoRecordExactMatchFilePath(string sensorId, string filePath, int64_t startTime)
{
    VideoRecordDBColumns row;
    queryResult result;
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                 VideoRecordDBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + " AND " +
                 VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(1) + " AND " +
                 VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(2) + ";";
    
    std::vector<std::string> params = {sensorId, filePath, std::to_string(startTime)};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        videoRecordHelper(row, entries);
    }
    return row;
}

int Sqlite::updateVideoRecordInDb(VideoRecordDBColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string filename = row.filepath_value;
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " +
          VideoRecordDBColumns::duration + " = " + PARAM_PLACEHOLDER(0) + ", " +
          VideoRecordDBColumns::file_size + " = " + PARAM_PLACEHOLDER(1) + ", " +
          VideoRecordDBColumns::file_fps + " = " + PARAM_PLACEHOLDER(2) + ", " +
          VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(3) + ", " +
          VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(4) + ", " +
          VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(5) + " " +
          " WHERE " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(6) + ";";
    
    std::vector<std::string> params = {
        std::to_string(row.duration_value),
        std::to_string(row.filesize_value),
        std::to_string(row.filefps_value),
        row.file_protection_value,
        row.object_id_value,
        currentUtcTime,
        filename
    };

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Sqlite::updateFilesProtectionInDb(bool fileProtection, const std::vector<string>& filePaths)
{
    CHECK_DB_IF_NULL_RETURN(-1)

    if (filePaths.empty())
    {
        return 0; // Nothing to update
    }

    string currentUtcTime = getCurrentUtcTime();

    // Use QueryBuilder to safely build WHERE IN clause to prevent SQL injection (SQLite-specific)
    std::string whereInClause = QueryBuilder::buildWhereInClause(VideoRecordDBColumns::file_path, filePaths, DatabaseType::SQLite);
    if (whereInClause.empty())
    {
        LOG(error) << "Invalid column name: " << VideoRecordDBColumns::file_path << endl;
        return -1;
    }

    /* Create SQL statement to update multiple records in a single query */
    string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " +
                 VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(0) +
                 ", " + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " " +
                 whereInClause + ";";
    std::vector<std::string> params = {fileProtection ? "1" : "0", currentUtcTime};

    LOG(info) << "Batch updating file protection for " << filePaths.size() << " files" << endl;
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing batch SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Sqlite::updateVideoRecordDurationBatch(const std::vector<VideoRecordDBColumns> &rows)
{
    // SQLite batch update not implemented - fall back to individual updates
    LOG(warning) << "Batch update not implemented for SQLite, using individual updates" << endl;
    int successCount = 0;
    for (auto row : rows)
    {
        if (row.duration_value > 0)
        {
            if (updateVideoRecordInDb(row) == 0)
            {
                successCount++;
            }
        }
    }
    return successCount;
}

int Sqlite::insertRowVideoRecordSchedule(VideoRecordScheduleDBColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query */
    string sql = string("INSERT INTO " + VideoRecordScheduleDBColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(DBColumns::device_id, row.device_id_value, sql)
    APPEND_COLUMN(DBColumns::sensor_id, row.sensor_id_value, sql)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::stream_id, row.stream_id_value, sql)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::start_time, row.start_time_value, sql)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::end_time, row.end_time_value, sql)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::created_date_time, currentUtcTime, sql)
    APPEND_COLUMN(VideoRecordScheduleDBColumns::modified_date_time, currentUtcTime, sql)
    sql.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(row.device_id_value, params)
    APPEND_COLUMN_VALUE(row.sensor_id_value, params)
    APPEND_COLUMN_VALUE(row.stream_id_value, params)
    APPEND_COLUMN_VALUE(row.start_time_value, params)
    APPEND_COLUMN_VALUE(row.end_time_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(sql, params);
    sql += ";";

    LOG(verbose) << "SQL query: " << sql << endl;

    /* Execute SQL statement with parameters */
    if (!executeQuery(sql, params))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    return 0;
}

std::vector<VideoRecordScheduleDBColumns> Sqlite::readVideoRecordSchedules(string streamId)
{
    vector<VideoRecordScheduleDBColumns> rowArray;
    queryResult result;

    CHECK_DB_IF_NULL_RETURN(rowArray)

    if (streamId.empty() == false)
    {
        // Use parameterized query to prevent SQL injection
        string queryTemplate = "SELECT * FROM " + VideoRecordScheduleDBColumns::table_name + " WHERE " +
              VideoRecordScheduleDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        std::vector<std::string> params = {streamId};
        
        if (!executeQuery(queryTemplate, params, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return rowArray;
        }
    }
    else
    {
        string queryTemplate = "SELECT * FROM " + VideoRecordScheduleDBColumns::table_name + ";";
        if (!executeQuery(queryTemplate, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return rowArray;
        }
    }

    for (auto entries : result)
    {
        VideoRecordScheduleDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordScheduleDBColumns::device_id))
            {
                row.device_id_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::sensor_id))
            {
                row.sensor_id_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::stream_id))
            {
                row.stream_id_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::start_time))
            {
                row.start_time_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::end_time))
            {
                row.end_time_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, VideoRecordScheduleDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
        rowArray.push_back(row);
    }
    return rowArray;
}

bool Sqlite::deleteVideoRecordSchedule(string streamId, string startTime, string endTime)
{
    VideoRecordScheduleDBColumns row;
    CHECK_DB_IF_NULL_RETURN(false)
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "DELETE FROM " + VideoRecordScheduleDBColumns::table_name + " WHERE " +
                 VideoRecordScheduleDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + " AND " +
                 VideoRecordScheduleDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " AND " +
                 VideoRecordScheduleDBColumns::end_time + " = " + PARAM_PLACEHOLDER(2) + ";";
    
    std::vector<std::string> params = {streamId, startTime, endTime};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return false;
    }
    return true;
}

int Sqlite::deleteVideoRecordings(vector<string> &filePaths)
{
    int ret = 0;
    int j = 0;
    vector<string> filePathsBatch;
    for (uint32_t i = 0; i < filePaths.size(); i++)
    {
        filePathsBatch.push_back(filePaths[i]);
        j++;
        /* Use safe WHERE IN clause and delete entries in batch */
        if (FILEPATH_BATCH_SIZE == j || i == filePaths.size() - 1)
        {
            // Use QueryBuilder to safely build WHERE IN clause to prevent SQL injection (SQLite-specific)
            std::string whereInClause = QueryBuilder::buildWhereInClause(VideoRecordDBColumns::file_path, filePathsBatch, DatabaseType::SQLite);
            if (whereInClause.empty())
            {
                LOG(error) << "Invalid column name: " << VideoRecordDBColumns::file_path << endl;
                return -1;
            }
            string sql = "DELETE FROM " + VideoRecordDBColumns::table_name + " " + whereInClause;

            LOG(verbose2) << "SQL query: " << sql << endl
                          << endl;

            /* Execute SQL statement */
            if (!executeQuery(sql))
            {
                LOG(error) << "Error executing SQL stmt: " << sql << endl;
                return -1;
            }
            filePathsBatch.clear();
            j = 0;
        }
    }
    return ret;
}

std::vector<VideoRecordDBColumns> Sqlite::readRecordsInBatch(uint32_t &batchSize, bool excludeCloudScanned)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    std::string queryTemplate;
    std::vector<std::string> params;
    
    if (excludeCloudScanned)
    {
        // Exclude cloud-scanned files (record_config = 'Cloud')
        // Note: LIMIT requires an integer, not a quoted string, so we append it directly
        queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                       VideoRecordDBColumns::file_protection + " = CAST(" + PARAM_PLACEHOLDER(0) + " AS text) AND " +
                       VideoRecordDBColumns::record_config + " != " + PARAM_PLACEHOLDER(1) + " ORDER BY " + 
                       VideoRecordDBColumns::start_time + " ASC LIMIT " + std::to_string(batchSize) + ";";
        params = {"0", RECORD_CONFIG_CLOUD_SCANNED};
    }
    else
    {
        // Default: return all unprotected files
        // Note: LIMIT requires an integer, not a quoted string, so we append it directly
        queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                       VideoRecordDBColumns::file_protection + " = CAST(" + PARAM_PLACEHOLDER(0) + " AS text) ORDER BY " + 
                       VideoRecordDBColumns::start_time + " ASC LIMIT " + std::to_string(batchSize) + ";";
        params = {"0"};
    }
    
    LOG(verbose) << "SQL query template: " << queryTemplate << " (excludeCloudScanned: " << excludeCloudScanned << ")" << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

std::vector<VideoRecordDBColumns> Sqlite::getVideoRecordFilePaths(string streamId, int64_t startTime, int64_t endTime)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT " + VideoRecordDBColumns::file_path + "," + VideoRecordDBColumns::duration + "," +
                    VideoRecordDBColumns::start_time + "," + VideoRecordDBColumns::object_id + "," +
                    VideoRecordDBColumns::storage_location + "," +
                    VideoRecordDBColumns::record_config +
                    " FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                    VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + " AND " +
                    "(" + VideoRecordDBColumns::start_time + " + " + VideoRecordDBColumns::duration + " > " + PARAM_PLACEHOLDER(1) + " OR " +
                    VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + " ) AND " +
                    VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {streamId, std::to_string(startTime), std::to_string(endTime)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordDBColumns::file_path))
            {
                row.filepath_value = column.second;
            }
            else if (iequals(column.first, VideoRecordDBColumns::duration))
            {
                row.duration_value = stringToLong(column.second);
            }
            else if (iequals(column.first, VideoRecordDBColumns::start_time))
            {
                row.start_time_value = stringToLong(column.second);
            }
            else if (iequals(column.first, VideoRecordDBColumns::object_id))
            {
                row.object_id_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}


std::vector<VideoRecordDBColumns> Sqlite::getVideoRecordFilePathsSensorIdBased(string sensorId, int64_t startTime, int64_t endTime)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    string sql = "";

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT " + VideoRecordDBColumns::file_path + "," +
                VideoRecordDBColumns::stream_id + "," +
                VideoRecordDBColumns::duration + "," +
                VideoRecordDBColumns::start_time + "," +
                VideoRecordDBColumns::object_id + "," +
                DBColumns::sensor_id + "," +
                VideoRecordDBColumns::metadata_file_path + "," +
                VideoRecordDBColumns::metadata_json + "," +
                VideoRecordDBColumns::storage_location + "," +
                VideoRecordDBColumns::record_config +
                " FROM " + VideoRecordDBColumns::table_name +
                " WHERE " + VideoRecordDBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + " " +
                " AND (" + VideoRecordDBColumns::start_time + " + " +
                VideoRecordDBColumns::duration + " > " + PARAM_PLACEHOLDER(1) + " " +
                " OR " + VideoRecordDBColumns::start_time + " = " + PARAM_PLACEHOLDER(1) + ") " +
                " AND " + VideoRecordDBColumns::start_time + " < " + PARAM_PLACEHOLDER(2) + " " +
                " ORDER BY " + VideoRecordDBColumns::start_time + ";";

    std::vector<std::string> params = {sensorId, std::to_string(startTime), std::to_string(endTime)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

std::vector<VideoRecordDBColumns> Sqlite::getVideoRecordFilePathsIdBased(string id)
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT " + VideoRecordDBColumns::file_path + "," +
                VideoRecordDBColumns::stream_id + "," +
                VideoRecordDBColumns::duration + "," +
                VideoRecordDBColumns::start_time + "," +
                VideoRecordDBColumns::object_id + "," +
                VideoRecordDBColumns::metadata_file_path + "," +
                VideoRecordDBColumns::metadata_json + "," +
                VideoRecordDBColumns::storage_location + "," +
                VideoRecordDBColumns::record_config +
                " FROM " + VideoRecordDBColumns::table_name +
                " WHERE " + VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    
    std::vector<std::string> params = {id};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

std::vector<VideoRecordDBColumns> Sqlite::getAllVideoRecordFilePaths()
{
    std::vector<VideoRecordDBColumns> rows;
    VideoRecordDBColumns row;
    queryResult result;
    string sql = "SELECT " + VideoRecordDBColumns::file_path + "," + VideoRecordDBColumns::duration + "," +
                 VideoRecordDBColumns::start_time + "," + VideoRecordDBColumns::object_id + "," +
                 DBColumns::sensor_id + "," + VideoRecordDBColumns::metadata_file_path + "," +
                 VideoRecordDBColumns::stream_id + "," +
                 VideoRecordDBColumns::metadata_json + "," +
                 VideoRecordDBColumns::storage_location + "," +
                 VideoRecordDBColumns::record_config +
                 " FROM " + VideoRecordDBColumns::table_name + ";";
    LOG(verbose) << "SQL query: " << sql << endl;

    /* Execute SQL statement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

int Sqlite::deleteStreamDetailsUsingSensorId(string sensorId)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "DELETE FROM " + SensorStreamsDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {sensorId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;  // Return 0 on success
}

int Sqlite::deleteRecordingStatusUsingSensorId(string sensorId)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "DELETE FROM " + RecordingStatusDBColumns::table_name + " WHERE " + RecordingStatusDBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {sensorId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;  // Return 0 on success
}

int Sqlite::deleteRowStream(string streamId)
{    
    CHECK_DB_IF_NULL_RETURN(-1)
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "DELETE FROM " + SensorStreamsDBColumns::table_name + " WHERE " + SensorStreamsDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {streamId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;  // Return 0 on success
}

int Sqlite::insertRowStream(SensorStreamsDBColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    SensorStreamsDBColumns r = readSensorStreams(row.stream_id_value);
    row.live_url_value = row.live_url_value.empty() ? r.live_url_value : row.live_url_value;
    row.replay_url_value = row.replay_url_value.empty() ? r.replay_url_value : row.replay_url_value;
    row.proxy_url_value = row.proxy_url_value.empty() ? r.proxy_url_value : row.proxy_url_value;
    row.resolution_value = row.resolution_value.empty() ? r.resolution_value : row.resolution_value;
    row.frameRate_value = row.frameRate_value.empty() ? r.frameRate_value : row.frameRate_value;
    row.encoding_value = row.encoding_value.empty() ? r.encoding_value : row.encoding_value;
    row.streamStatus_value = row.streamStatus_value == -1 ? r.streamStatus_value : row.streamStatus_value;
    row.streamType_value = row.streamType_value == -1 ? r.streamType_value : row.streamType_value;
    row.encodingProfile_value = row.encodingProfile_value.empty() ? r.encodingProfile_value : row.encodingProfile_value;
    row.encodingInterval_value = row.encodingInterval_value.empty() ? r.encodingInterval_value : row.encodingInterval_value;
    row.duration_value = row.duration_value.empty() ? r.duration_value : row.duration_value;
    row.isMainStream_value = row.isMainStream_value.empty() ? r.isMainStream_value : row.isMainStream_value;
    row.isAlwaysRecording_value = row.isAlwaysRecording_value.empty() ? r.isAlwaysRecording_value : row.isAlwaysRecording_value;
    row.storageLocation_value = row.storageLocation_value == -1 ? r.storageLocation_value : row.storageLocation_value;
    row.bitrate_value = row.bitrate_value.empty() ? r.bitrate_value : row.bitrate_value;
    row.numFrames_value = row.numFrames_value.empty() ? r.numFrames_value : row.numFrames_value;
    row.audio_container_value = row.audio_container_value.empty() ? r.audio_container_value : row.audio_container_value;
    row.audio_encoding_value = row.audio_encoding_value.empty() ? r.audio_encoding_value : row.audio_encoding_value;
    row.audio_sample_rate_value = row.audio_sample_rate_value.empty() ? r.audio_sample_rate_value : row.audio_sample_rate_value;
    row.audio_bps_value = row.audio_bps_value.empty() ? r.audio_bps_value : row.audio_bps_value;
    row.audio_channels_value = row.audio_channels_value.empty() ? r.audio_channels_value : row.audio_channels_value;
    row.streamName_value = row.streamName_value.empty() ? r.streamName_value : row.streamName_value;
    // Special handling for isBframesPresent: preserve database value 1 (true) when incoming is 0 (false)
    if (r.isBframesPresent_value == 1 && row.isBframesPresent_value == 0)
    {
        row.isBframesPresent_value = r.isBframesPresent_value;
    }
    row.created_date_time_value = r.created_date_time_value.empty() ? currentUtcTime : r.created_date_time_value;
    row.modified_date_time_value = currentUtcTime;

    /* Create SQL statement with parameterized query */
    string sql = string("INSERT OR REPLACE INTO " + SensorStreamsDBColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(DBColumns::sensor_id, row.sensor_id_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::live_url, row.live_url_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::replay_url, row.replay_url_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::proxy_url, row.proxy_url_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::resolution, row.resolution_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::frameRate, row.frameRate_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::encoding, row.encoding_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::stream_id, row.stream_id_value, sql)
    APPEND_COLUMN_INT(SensorStreamsDBColumns::streamStatus, row.streamStatus_value, sql)
    APPEND_COLUMN_INT(SensorStreamsDBColumns::type, row.streamType_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::encodingProfile, row.encodingProfile_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::encodingInterval, row.encodingInterval_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::duration, row.duration_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::isMainStream, row.isMainStream_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::isAlwaysRecording, row.isAlwaysRecording_value, sql)
    APPEND_COLUMN_INT(SensorStreamsDBColumns::storageLocation, row.storageLocation_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::bitrate, row.bitrate_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::numFrames, row.numFrames_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::audio_container, row.audio_container_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::audio_encoding, row.audio_encoding_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::audio_sample_rate, row.audio_sample_rate_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::audio_bps, row.audio_bps_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::audio_channels, row.audio_channels_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::streamName, row.streamName_value, sql)
    APPEND_COLUMN_INT(SensorStreamsDBColumns::isBframesPresent, row.isBframesPresent_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::created_date_time, row.created_date_time_value, sql)
    APPEND_COLUMN(SensorStreamsDBColumns::modified_date_time, row.modified_date_time_value, sql)
    sql.pop_back();
    
    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(row.sensor_id_value, params)
    APPEND_COLUMN_VALUE(row.live_url_value, params)
    APPEND_COLUMN_VALUE(row.replay_url_value, params)
    APPEND_COLUMN_VALUE(row.proxy_url_value, params)
    APPEND_COLUMN_VALUE(row.resolution_value, params)
    APPEND_COLUMN_VALUE(row.frameRate_value, params)
    APPEND_COLUMN_VALUE(row.encoding_value, params)
    APPEND_COLUMN_VALUE(row.stream_id_value, params)
    APPEND_COLUMN_VALUE_INT(row.streamStatus_value, params)
    APPEND_COLUMN_VALUE_INT(row.streamType_value, params)
    APPEND_COLUMN_VALUE(row.encodingProfile_value, params)
    APPEND_COLUMN_VALUE(row.encodingInterval_value, params)
    APPEND_COLUMN_VALUE(row.duration_value, params)
    APPEND_COLUMN_VALUE(row.isMainStream_value, params)
    APPEND_COLUMN_VALUE(row.isAlwaysRecording_value, params)
    APPEND_COLUMN_VALUE_INT(row.storageLocation_value, params)
    APPEND_COLUMN_VALUE(row.bitrate_value, params)
    APPEND_COLUMN_VALUE(row.numFrames_value, params)
    APPEND_COLUMN_VALUE(row.audio_container_value, params)
    APPEND_COLUMN_VALUE(row.audio_encoding_value, params)
    APPEND_COLUMN_VALUE(row.audio_sample_rate_value, params)
    APPEND_COLUMN_VALUE(row.audio_bps_value, params)
    APPEND_COLUMN_VALUE(row.audio_channels_value, params)
    APPEND_COLUMN_VALUE(row.streamName_value, params)
    APPEND_COLUMN_VALUE_INT(row.isBframesPresent_value, params)
    APPEND_COLUMN_VALUE(row.created_date_time_value, params)
    APPEND_COLUMN_VALUE(row.modified_date_time_value, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(sql, params);
    sql += ";";
    
    LOG(verbose) << "SQL query: " << sql << endl;
    /* Execute SQL statement with parameters */
    if (!executeQuery(sql, params))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }
    return 0;
}

SensorStreamsDBColumns Sqlite::readSensorStreams(string streamId)
{
    SensorStreamsDBColumns row;
    CHECK_DB_IF_NULL_RETURN(row)
    queryResult result;
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + SensorStreamsDBColumns::table_name + " WHERE " + SensorStreamsDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {streamId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        sensorStreamHelper(row, entries);
    }
    return row;
}

vector<SensorStreamsDBColumns> Sqlite::readAllStreams()
{
    vector<SensorStreamsDBColumns> rows;

    queryResult result;
    string sql = "SELECT * FROM " + SensorStreamsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query: " << sql << endl;

    /* Execute SQL statement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return rows;
    }

    for (auto entries : result)
    {
        SensorStreamsDBColumns row;
        sensorStreamHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

vector<SensorStreamsDBColumns> Sqlite::readAllStreamsForGivenSensorID(string sensorId)
{
    vector<SensorStreamsDBColumns> rows;
    CHECK_DB_IF_NULL_RETURN(rows)
    queryResult result;
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + SensorStreamsDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {sensorId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        SensorStreamsDBColumns row;
        sensorStreamHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

/* Search Sensor ID based on Object ID */
string Sqlite::searchSensorFileIdBased(const string &id)
{
    string sensorId = "";
    queryResult result;
    
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT " + VideoRecordDBColumns::sensor_id + " FROM " + VideoRecordDBColumns::table_name +
                          " WHERE " + VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {id};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return sensorId;
    }
    
    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordDBColumns::sensor_id))
            {
                sensorId = column.second;
            }
        }
    }
    return sensorId;
}

vector<SensorInfoDBColumns> Sqlite::readSensorInfo(string sensorId)
{
    vector<SensorInfoDBColumns> rows;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(rows)
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + SensorDetailsDBColumns::table_name +
                    " INNER JOIN " + SensorStreamsDBColumns::table_name + " USING (" + DBColumns::sensor_id + ") WHERE " +
                    SensorStreamsDBColumns::table_name + "." + DBColumns::sensor_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {sensorId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        SensorInfoDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, SensorInfoDBColumns::device_id))
            {
                row.device_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::sensor_id))
            {
                row.sensor_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::sensor_hw_id))
            {
                row.sensor_hw_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::username))
            {
                row.username_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::password))
            {
                std::string encryptedPassword = column.second;
                std::string decryptedPassword = EMPTY_STRING;
                if (!encryptedPassword.empty() &&
                    vst_common::decrypt_data(encryptedPassword, decryptedPassword, row.sensor_id_value) == false)
                {
                    LOG(error) << "failed to decrypt" << endl;
                }
                row.password_value = decryptedPassword;
            }
            else if (iequals(column.first, SensorInfoDBColumns::name))
            {
                row.name_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::ip))
            {
                row.ip_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::hardware))
            {
                row.hardware_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::manufacturer))
            {
                row.manufacturer_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::serial_number))
            {
                row.serial_number_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::firmware_version))
            {
                row.firmware_version_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::hardware_id))
            {
                row.hardware_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::location))
            {
                row.location_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::tags))
            {
                row.tags_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::url))
            {
                row.url_value = column.second;
            }
            else if (iequals(column.first, SensorDetailsDBColumns::type))
            {
                row.type_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::position))
            {
                row.position_value = column.second;
                if (row.position_value.size() >= 2)
                {
                    // remove quotation from JSON string
                    row.position_value = row.position_value.substr(1, row.position_value.size() - 2);
                }
            }
            else if (iequals(column.first, SensorInfoDBColumns::users))
            {
                row.users_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::isRemoteSensor))
            {
                row.isRemoteSensor_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::remoteDeviceId))
            {
                row.remoteDeviceId_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::remoteDeviceName))
            {
                row.remoteDeviceName_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::remoteDeviceLocation))
            {
                row.remoteDeviceLocation_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::httpStatus))
            {
                row.httpStatus_value = stringToLong(column.second);
            }
            else if (iequals(column.first, SensorInfoDBColumns::sensorStatus))
            {
                row.sensorStatus_value = stringToLong(column.second);
            }
            else if (iequals(column.first, SensorInfoDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::stream_id))
            {
                row.stream_id_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::live_url))
            {
                row.live_url_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::replay_url))
            {
                row.replay_url_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::proxy_url))
            {
                row.proxy_url_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::resolution))
            {
                row.resolution_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::frameRate))
            {
                row.frameRate_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::encoding))
            {
                row.encoding_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::streamStatus))
            {
                row.streamStatus_value = stringToLong(column.second);
            }
            else if (iequals(column.first, SensorStreamsDBColumns::type))
            {
                row.streamType_value = stringToLong(column.second);
            }
            else if (iequals(column.first, SensorInfoDBColumns::encodingProfile))
            {
                row.encodingProfile_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::encodingInterval))
            {
                row.encodingInterval_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::duration))
            {
                row.duration_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::isMainStream))
            {
                row.isMainStream_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::isAlwaysRecording))
            {
                row.isAlwaysRecording_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::bitrate))
            {
                row.bitrate_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::numFrames))
            {
                row.numFrames_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_container))
            {
                row.audio_container_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_encoding))
            {
                row.audio_encoding_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_sample_rate))
            {
                row.audio_sample_rate_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_bps))
            {
                row.audio_bps_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::audio_channels))
            {
                row.audio_channels_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, SensorInfoDBColumns::isBframesPresent))
            {
                row.isBframesPresent_value = stringToInt(column.second, 0);
            }
            else if (iequals(column.first, SensorInfoDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}

string Sqlite::readStreamProperty(string streamId, string property)
{
    string value = "";
    CHECK_DB_IF_NULL_RETURN(value)
    queryResult result;

    // Validate property name against whitelist to prevent SQL injection
    std::string safeProperty = validateSensorStreamTableProperty(property);
    if (safeProperty.empty()) 
    {
        LOG(error) << "Invalid property name: " << property << endl;
        return value;
    }

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT " + safeProperty + " FROM " + SensorStreamsDBColumns::table_name + " WHERE " + SensorStreamsDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {streamId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return value;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, property))
            {
                value = column.second;
            }
        }
    }
    return value;
}

std::vector<VideoRecordDBColumns> Sqlite::getRecordedVideoSize()
{
    // get the file size row from the Video records table
    vector<VideoRecordDBColumns> rows;
    CHECK_DB_IF_NULL_RETURN(rows)
    queryResult result;
    string sql = "SELECT " + VideoRecordDBColumns::file_size + " FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                 VideoRecordDBColumns::file_size + " IS NOT NULL;";
    LOG(verbose) << "SQL query: " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return rows;
    }
    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordDBColumns::file_size))
            {
                row.filesize_value = stringToLong(column.second);
            }
        }
        rows.push_back(row);
    }
    return rows;
}

UserDetailsDBColumns Sqlite::getUserDetail(const string username)
{
    string user = username;
    UserDetailsDBColumns row;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(row)

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + UserDetailsDBColumns::table_name + " WHERE " + UserDetailsDBColumns::username + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {username};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return row;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, UserDetailsDBColumns::username))
            {
                row.username_value = column.second;
            }
            else if (iequals(column.first, UserDetailsDBColumns::password_hash))
            {
                row.password_hash_value = column.second;
            }
            else if (iequals(column.first, UserDetailsDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, UserDetailsDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
    }
    return row;
}

int Sqlite::setUserDetail(UserDetailsDBColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query */
    string sql = string("INSERT OR IGNORE INTO " + UserDetailsDBColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(UserDetailsDBColumns::username, row.username_value, sql)
    APPEND_COLUMN(UserDetailsDBColumns::password_hash, row.password_hash_value, sql)
    APPEND_COLUMN(UserDetailsDBColumns::created_date_time, currentUtcTime, sql)
    APPEND_COLUMN(UserDetailsDBColumns::modified_date_time, currentUtcTime, sql)
    sql.pop_back();
    
    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(row.username_value, params)
    APPEND_COLUMN_VALUE(row.password_hash_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(sql, params);
    sql += ";";

    LOG(verbose) << "SQL query: " << sql << endl;

    /* Execute SQL statement with parameters */
    if (!executeQuery(sql, params))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }
    return 0;
}

int Sqlite::deleteUserDetails(const string username)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    if (username == DEFAULT_ADMIN_USERNAME)
    {
        LOG(error) << "Cannot delete admin user" << endl;
        return -1;
    }
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "DELETE FROM " + UserDetailsDBColumns::table_name + " WHERE " + UserDetailsDBColumns::username + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {username};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

std::vector<UserSessionsDBColumns> Sqlite::getUserSessions(const string username)
{
    deleteExpiredUserSessions();
    string user = username;
    vector<UserSessionsDBColumns> rows;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(rows)

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + UserSessionsDBColumns::table_name + " WHERE " + UserSessionsDBColumns::username + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {username};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return rows;
    }

    for (auto entries : result)
    {
        UserSessionsDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, UserSessionsDBColumns::username))
            {
                row.username_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::session_cookie))
            {
                row.session_cookie_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::cookie_max_age))
            {
                row.cookie_max_age_value = stringToLong(column.second);
            }
            else if (iequals(column.first, UserSessionsDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}

int Sqlite::deleteUserSession(const string username, const string sessionId)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "DELETE FROM " + UserSessionsDBColumns::table_name + " WHERE " + UserSessionsDBColumns::username +
                    " = " + PARAM_PLACEHOLDER(0) + " AND " + UserSessionsDBColumns::session_cookie + " = " + PARAM_PLACEHOLDER(1) + ";";
    std::vector<std::string> params = {username, sessionId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Sqlite::setUserSession(UserSessionsDBColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query */
    string sql = string("INSERT INTO " + UserSessionsDBColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(UserSessionsDBColumns::username, row.username_value, sql)
    APPEND_COLUMN(UserSessionsDBColumns::session_cookie, row.session_cookie_value, sql)
    APPEND_COLUMN_INT(UserSessionsDBColumns::cookie_max_age, row.cookie_max_age_value, sql)
    APPEND_COLUMN(UserSessionsDBColumns::created_date_time, currentUtcTime, sql)
    APPEND_COLUMN(UserSessionsDBColumns::modified_date_time, currentUtcTime, sql)
    sql.pop_back();
    
    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(row.username_value, params)
    APPEND_COLUMN_VALUE(row.session_cookie_value, params)
    APPEND_COLUMN_VALUE_INT(row.cookie_max_age_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(sql, params);
    sql += ";";

    LOG(verbose) << "SQL query: " << sql << endl;

    /* Execute SQL statement with parameters */
    if (!executeQuery(sql, params))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }

    return 0;
}

void Sqlite::deleteExpiredUserSessions()
{
    int64_t currentUnixTimestamp = getCurrentUnixTimestamp();
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "DELETE FROM " + UserSessionsDBColumns::table_name + " WHERE " + UserSessionsDBColumns::cookie_max_age + " <= " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {std::to_string(currentUnixTimestamp)};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }
    return;
}

void Sqlite::extendSession(const string username, const string sessionId)
{
    string currentUtcTime = getCurrentUtcTime();
    int64_t newMaxAge = getCurrentUnixTimestamp() + GET_CONFIG().session_max_age_sec;
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + UserSessionsDBColumns::table_name + " SET " +
                 UserSessionsDBColumns::cookie_max_age + " = " + PARAM_PLACEHOLDER(0) + ", " + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " " +
                 " WHERE " + UserSessionsDBColumns::username + " = " + PARAM_PLACEHOLDER(2) + " AND " + UserSessionsDBColumns::session_cookie + " = " + PARAM_PLACEHOLDER(3) + ";";
    std::vector<std::string> params = {std::to_string(newMaxAge), currentUtcTime, username, sessionId};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
    }
    return;
}

std::vector<UserSessionsDBColumns> Sqlite::getAllSessions()
{
    deleteExpiredUserSessions();
    vector<UserSessionsDBColumns> rows;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(rows)

    string sql = "SELECT * FROM " + UserSessionsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query: " << sql << endl;
    /* Execute SQL statement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return rows;
    }

    for (auto entries : result)
    {
        UserSessionsDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, UserSessionsDBColumns::username))
            {
                row.username_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::session_cookie))
            {
                row.session_cookie_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::cookie_max_age))
            {
                row.cookie_max_age_value = stringToLong(column.second);
            }
            else if (iequals(column.first, UserSessionsDBColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, UserSessionsDBColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}

bool Sqlite::checkVideoRecordExists(string streamId)
{
    bool ret = false;
    VideoRecordDBColumns row;
    string id = streamId;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(ret)

    // Use parameterized query to prevent SQL injection
    string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name + " WHERE " + VideoRecordDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {id};
    
    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return false;
    }
    if (result.size() > 0)
    {
        ret = true;
    }
    return ret;
}

std::vector<VideoRecordDBColumns> Sqlite::getAllDisconnectedSensorId()
{
    std::vector<VideoRecordDBColumns> rows;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(rows)

    string sql = "SELECT DISTINCT  " + DBColumns::sensor_id + ", " + VideoRecordDBColumns::sensor_name + " FROM " + VideoRecordDBColumns::table_name + " WHERE " + DBColumns::sensor_id + " NOT IN (SELECT DISTINCT  " + DBColumns::sensor_id + " FROM " + SensorDetailsDBColumns::table_name + ");";

    LOG(verbose) << "SQL query: " << sql << endl;

    /* Execute SQL statement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return rows;
    }

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, VideoRecordDBColumns::sensor_id))
            {
                row.sensor_id_value = column.second;
            }
            else if (iequals(column.first, VideoRecordDBColumns::sensor_name))
            {
                row.sensor_name_value = column.second;
            }
        }
        rows.push_back(row);
    }
    return rows;
}

std::string Sqlite::getLocalDeviceId()
{
    LocalDeviceDetailsDBColumns row;
    queryResult result;
    string sql = "SELECT " + LocalDeviceDetailsDBColumns::id + " FROM " + LocalDeviceDetailsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query: " << sql << endl;

    /* Execute SQL statement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return row.id_value;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, LocalDeviceDetailsDBColumns::id))
            {
                row.id_value = column.second;
            }
        }
    }

    return row.id_value;
}

VmsErrorCode Sqlite::getRecordingStatus(std::map<std::string, RecordingStatusDBColumns, std::less<>> &allStatus, const std::optional<string> &streamId)
{
    queryResult result;
    string sql;
    if (streamId.has_value())
    {
        // Use parameterized query to prevent SQL injection
        string queryTemplate = "SELECT * FROM " + RecordingStatusDBColumns::table_name + " WHERE " + RecordingStatusDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(0) + ";";
        std::vector<std::string> params = {*streamId};
        
        if (!executeQuery(queryTemplate, params, result))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return VmsErrorCode::VMSInternalError;
        }
    }
    else
    {
        sql = "SELECT * FROM " + RecordingStatusDBColumns::table_name + ";";
        if (!executeQuery(sql, result))
        {
            LOG(error) << "Error executing SQL stmt: " << sql << endl;
            return VmsErrorCode::VMSInternalError;
        }
    }

    for (auto entries : result)
    {
        RecordingStatusDBColumns row;
        for (auto column : entries)
        {
            if (iequals(column.first, RecordingStatusDBColumns::sensor_id))
            {
                row.sensor_id_value = column.second;
            }
            else if (iequals(column.first, RecordingStatusDBColumns::stream_id))
            {
                row.stream_id_value = column.second;
            }
            else if (iequals(column.first, RecordingStatusDBColumns::recordingStatus))
            {
                row.recordingStatus_value = stoi(column.second);
            }
            else if (iequals(column.first, RecordingStatusDBColumns::created_date_time))
            {
                row.created_date_time_value = stoi(column.second);
            }
            else if (iequals(column.first, RecordingStatusDBColumns::modified_date_time))
            {
                row.modified_date_time_value = stoi(column.second);
            }
        }
        std::string streamId = entries.at(RecordingStatusDBColumns::stream_id);
        allStatus[streamId] = row;
    }
    return VmsErrorCode::NoError;
}

std::string Sqlite::getLocalDeviceName()
{
    LocalDeviceDetailsDBColumns row;
    queryResult result;
    string sql = "SELECT " + LocalDeviceDetailsDBColumns::name + " FROM " + LocalDeviceDetailsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query: " << sql << endl;
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return row.name_value;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, LocalDeviceDetailsDBColumns::name))
            {
                row.name_value = column.second;
            }
        }
    }

    return row.name_value;
}

int Sqlite::setLocalDeviceId(const string deviceId)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query */
    string queryTemplate = string("INSERT INTO " + LocalDeviceDetailsDBColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(LocalDeviceDetailsDBColumns::id, deviceId, queryTemplate)
    APPEND_COLUMN(DBColumns::created_date_time, currentUtcTime, queryTemplate)
    APPEND_COLUMN(DBColumns::modified_date_time, currentUtcTime, queryTemplate)
    queryTemplate.pop_back(); // Remove trailing comma
    
    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(deviceId, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(queryTemplate, params);
    queryTemplate += ";";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;
    /* Execute SQL statement with parameters */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Sqlite::setRecordingStatus(const std::string &streamId, RecordState new_status, const std::optional<string> &sensorId)
{
    queryResult result;
    string currentUtcTime = getCurrentUtcTime();

    if (sensorId.has_value())
    {
        // If sensorId is provided, use INSERT OR REPLACE with the given sensorId
        // Use parameterized query to prevent SQL injection
        string queryTemplate = string("INSERT INTO " + RecordingStatusDBColumns::table_name + " (");
        vector<string> params;
        
        APPEND_COLUMN(RecordingStatusDBColumns::stream_id, streamId, queryTemplate)
        APPEND_COLUMN_OPTIONAL(RecordingStatusDBColumns::sensor_id, sensorId, queryTemplate)
        APPEND_COLUMN_INT(RecordingStatusDBColumns::recordingStatus, static_cast<int>(new_status), queryTemplate)
        APPEND_COLUMN(RecordingStatusDBColumns::created_date_time, currentUtcTime, queryTemplate)
        APPEND_COLUMN(RecordingStatusDBColumns::modified_date_time, currentUtcTime, queryTemplate)
        queryTemplate.pop_back();
        // Build parameterized values using safe macros
        APPEND_COLUMN_VALUE(streamId, params)
        APPEND_COLUMN_VALUE_OPTIONAL(sensorId, params)
        APPEND_COLUMN_VALUE_INT(static_cast<int>(new_status), params)
        APPEND_COLUMN_VALUE(currentUtcTime, params)
        APPEND_COLUMN_VALUE(currentUtcTime, params)
        
        // Add VALUES clause with automatic placeholders using advanced macro
        BUILD_VALUES_CLAUSE(queryTemplate, params);
        
        // Add the additional parameter for the ON CONFLICT clause before building the query
        APPEND_COLUMN_VALUE(currentUtcTime, params)
        
        queryTemplate += " ON CONFLICT(" + RecordingStatusDBColumns::stream_id +
              ") DO UPDATE SET " +
              RecordingStatusDBColumns::sensor_id + " = EXCLUDED." + RecordingStatusDBColumns::sensor_id + ", " +
              RecordingStatusDBColumns::recordingStatus + " = EXCLUDED." + RecordingStatusDBColumns::recordingStatus + ", " +
              RecordingStatusDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(params.size() - 1) + ";";
        
        if (!executeQuery(queryTemplate, params))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return -1;
        }
    }
    else
    {
        // If sensorId is not provided, only update the recordingStatus for the given streamId
        // Use parameterized query to prevent SQL injection
        string queryTemplate = "UPDATE " + RecordingStatusDBColumns::table_name +
              " SET " + RecordingStatusDBColumns::recordingStatus + " = " + PARAM_PLACEHOLDER(0) + ", " +
              RecordingStatusDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " " +
              " WHERE " + RecordingStatusDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(2) + ";";
        
        std::vector<std::string> params = {std::to_string(static_cast<int>(new_status)), currentUtcTime, streamId};
        
        if (!executeQuery(queryTemplate, params))
        {
            LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
            return -1;
        }
    }

    return 0;
}

int Sqlite::setLocalDeviceName(const string &deviceName, const string &deviceId)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement */
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + LocalDeviceDetailsDBColumns::table_name + " SET " +
            LocalDeviceDetailsDBColumns::name + " = " + PARAM_PLACEHOLDER(0) + ", " +
            LocalDeviceDetailsDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " " +
            " WHERE " + LocalDeviceDetailsDBColumns::id + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {deviceName, currentUtcTime, deviceId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

std::string Sqlite::getLocalDeviceLocation()
{
    LocalDeviceDetailsDBColumns row;
    queryResult result;
    string sql = "SELECT " + LocalDeviceDetailsDBColumns::location + " FROM " + LocalDeviceDetailsDBColumns::table_name + ";";
    LOG(verbose) << "SQL query: " << sql << endl;
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return row.location_value;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, LocalDeviceDetailsDBColumns::location))
            {
                row.location_value = column.second;
            }
        }
    }
    return row.location_value;
}

int Sqlite::setLocalDeviceLocation(const string &deviceLocation, const string &deviceId)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement */
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + LocalDeviceDetailsDBColumns::table_name + " SET " +
            LocalDeviceDetailsDBColumns::location + " = " + PARAM_PLACEHOLDER(0) + ", " +
            LocalDeviceDetailsDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " " +
            " WHERE " + LocalDeviceDetailsDBColumns::id + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {deviceLocation, currentUtcTime, deviceId};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;
    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

/* File Type Sensor Usecase for ReplayStream */
std::vector<VideoFileInfo> Sqlite::getFileListStreamIdBased(std::string streamId, int64_t t1,
                                               int64_t t2)
{
    LOG(info) << "Sqlite::getFileListStreamIdBased stream id: " << streamId << " startTime: " << t1 << " endTime: " << t2 << endl;
    std::vector<VideoFileInfo> list;


    /* Assume t2 as max int if not specified
    ** so that all records will be fetched
    */
    if (t2 == 0)
    {
        t2 = std::numeric_limits<int64_t>::max();
    }

    VideoRecordDBColumns dbRow;
    std::vector<VideoRecordDBColumns> rows;
    rows = readVideoRecordStreamIdBased(streamId, t1, t2);

    for (size_t i = 0; i < rows.size(); i++)
    {
        VideoFileInfo file(rows[i]);

        {
            // Only normalize paths for LOCAL files, not cloud object keys
            if (rows[i].storage_location_value == StreamStorageTypeLocal)
            {
                // Normalize relative paths by prepending nv_streamer_directory_path
                string originalPath = file.m_filePath;
                file.m_filePath = normalizeRelativePath(file.m_filePath, GET_CONFIG().nv_streamer_directory_path);
                if (originalPath != file.m_filePath)
                {
                    LOG(info) << "Normalized relative path for LOCAL file: " << originalPath << " -> " << file.m_filePath << endl;
                }
                if (isFileExist(file.m_filePath) || !file.m_objectId.empty())
                {
                    list.push_back(file);
                    LOG(info) << "File path = " << file.m_filePath << endl;
                    LOG(info) << "File start time = " << file.m_startTime << endl;
                }
                else
                {
                    LOG(error) << "Local file " << file.m_filePath << " not present." << endl;
                }
            }
            else // StreamStorageTypeCloud
            {
                // For cloud files, keep the object key as-is (don't normalize)
                LOG(info) << "Cloud file - keeping object key as-is: " << file.m_filePath << endl;
                list.push_back(file);
            }
        }
    }

    return list;
}

/* File Type Sensor Usecase for Storage Service Download */
std::vector<VideoFileInfo> Sqlite::getFileListUniqueIdSensorIdBased(std::string uniqueId, std::string sensorId, int64_t t1,
                                               int64_t t2)
{
    LOG(info) << "Sqlite::getFileListUniqueIdSensorIdBased sensor id: " << sensorId << " startTime: " << t1 << " endTime: " << t2 << endl;
    std::vector<VideoFileInfo> list;


    /* Assume t2 as max int if not specified
    ** so that all records will be fetched
    */
    if (t2 == 0)
    {
        t2 = std::numeric_limits<int64_t>::max();
    }

    VideoRecordDBColumns dbRow;
    std::vector<VideoRecordDBColumns> rows;
    if (uniqueId.empty() == false)
    {
        if (sensorId.empty() == false)
        {
            rows = readVideoRecordSensorIdUniqueIdBased(sensorId, uniqueId);
        }
        else
        {
            rows = readVideoRecordUniqueIdBased(uniqueId);
        }
    }
    else
    {
        rows = readVideoRecordSensorIdBased(sensorId, t1, t2);
    }

    for (size_t i = 0; i < rows.size(); i++)
    {
        VideoFileInfo file(rows[i]);

        {
            // Only normalize paths for LOCAL files, not cloud object keys
            if (rows[i].storage_location_value == StreamStorageTypeLocal)
            {
                // Normalize relative paths by prepending nv_streamer_directory_path
                string originalPath = file.m_filePath;
                file.m_filePath = normalizeRelativePath(file.m_filePath, GET_CONFIG().nv_streamer_directory_path);
                if (originalPath != file.m_filePath)
                {
                    LOG(info) << "Normalized relative path for LOCAL file: " << originalPath << " -> " << file.m_filePath << endl;
                }
                if (isFileExist(file.m_filePath) || !file.m_objectId.empty())
                {
                    list.push_back(file);
                    LOG(info) << "File path = " << file.m_filePath << endl;
                    LOG(info) << "File start time = " << file.m_startTime << endl;
                }
                else
                {
                    LOG(error) << "Local file " << file.m_filePath << " not present." << endl;
                }
            }
            else // StreamStorageTypeCloud
            {
                // For cloud files, keep the object key as-is (don't normalize)
                LOG(info) << "Cloud file - keeping object key as-is: " << file.m_filePath << endl;
                list.push_back(file);
            }
        }
    }

    return list;
}

/* Normal VST Usecase for Storage Service Download and ReplayStream */
std::vector<VideoFileInfo> Sqlite::getFileList(std::string sensorId, int64_t t1,
                                               int64_t t2, size_t maxDownloadSize, bool accurate /*false*/)
{
    LOG(info) << "Sqlite::getFileList sensor id: " << sensorId << " startTime: " << t1 << " endTime: " << t2 << endl;
    std::vector<VideoFileInfo> list;
    uint32_t max_size = 0;

    if (accurate && (t1 == 0 || t2 == 0))
    {
        LOG(error) << "Cannot enforce accurate flag when start or end is not provided" << endl;
        return list;
    }

    /* Assume t2 as max int if not specified
    ** so that all records will be fetched
    */
    if (t2 == 0)
    {
        t2 = std::numeric_limits<int64_t>::max();
    }

    VideoRecordDBColumns dbRow;
    std::vector<VideoRecordDBColumns> rows = readVideoRecord(sensorId, t1, t2);

    uint64_t maxFiles = 0;
    if (maxDownloadSize)
    {
        uint64_t totalRecordSize = 0;
        uint64_t maxDownloadSize_bytes = to_bytes(maxDownloadSize);
        for (maxFiles = 0; maxFiles < rows.size(); ++maxFiles)
        {
            totalRecordSize += rows[maxFiles].filesize_value;
            if (totalRecordSize > maxDownloadSize_bytes)
            {
                if (maxFiles)
                {
                    --maxFiles;
                }
                break;
            }
        }
    }

    max_size = rows.size();
    if (maxFiles)
    {
        max_size = std::min(rows.size(), maxFiles);
        if (max_size != rows.size())
        {
            LOG(warning) << "Max download limit exceeded, serving only " << max_size << " files of data" << endl;
        }
    }

    // Preform checks if accurate flag is set
    if (accurate && rows.size() != 0)
    {
        // check if last file being written
        LOG(info) << "Check last file being written" << endl;
        if (rows[max_size - 1].duration_value == FILE_INIT_DURATION)
        {
            LOG(error) << "Last file not ready" << endl;
            return list;
        }

        // check continuity
        LOG(info) << "Check continuity" << endl;
        for (size_t i = 0; i < max_size - 1; ++i)
        {
            uint64_t start_time = rows[i].start_time_value;
            uint64_t duration = rows[i].duration_value;
            uint64_t next_start = rows[i + 1].start_time_value;
            // compare current file end time with 1 sec tolerance
            if (start_time + duration + ONE_MIN < next_start)
            {
                LOG(error) << "Recorded files continuity error" << endl;
                return list;
            }
        }

        // check max duration is greater or equal to asked duration
        LOG(info) << "Check max duration is greater or equal to asked duration" << endl;
        int64_t asked_duration = t2 - t1;
        uint64_t start_time_first_file = rows[0].start_time_value;
        uint64_t start_time_last_file = rows[max_size - 1].start_time_value;
        uint64_t duration_last_file = rows[max_size - 1].duration_value;
        uint64_t end_time_last_file = start_time_last_file + duration_last_file;
        int64_t max_possible_duration = end_time_last_file - start_time_first_file;
        if (max_possible_duration < asked_duration)
        {
            LOG(error) << "Could not retrieve asked amount of data" << endl;
            return list;
        }

        // check for t1 and t2 time present in retrieved files
        LOG(info) << "Check for t1 and t2 time present in retrieved files" << endl;
        uint64_t ut1 = (uint64_t)t1, ut2 = (uint64_t)t2;
        if (ut1 + ONE_MIN < start_time_first_file || ut2 > end_time_last_file + ONE_MIN)
        {
            LOG(error) << "Could not retrieve for exact start or end time" << endl;
            return list;
        }
    }

    for (size_t i = 0; i < max_size; i++)
    {
        VideoFileInfo file(rows[i]);
        // Check if file is present locally or if it has object id (for on-demand download)
        if (isFileExist(file.m_filePath) || !file.m_objectId.empty())
        {
            list.push_back(file);
            LOG(verbose) << "File path = " << file.m_filePath << endl;
            LOG(verbose) << "File start time = " << file.m_startTime << endl;
        }
        else
        {
            LOG(error) << "File " << file.m_filePath << " not present." << endl;
        }
    }

    getActiveLocalRecording(sensorId, t1, t2, list);

    return list;
}

std::vector<VideoFileInfo> Sqlite::getNextFileList(std::string streamId, int64_t t1)
{
    std::vector<VideoFileInfo> next_file_list;
    int64_t t2 = std::numeric_limits<int64_t>::max();
    std::vector<VideoRecordDBColumns> rows = readVideoRecord(streamId, t1, t2);

    if (rows.size() == 0)
    {
        LOG(info) << "Reached at end of file, no next file available" << endl;
        return next_file_list;
    }

    for (size_t i = 0; i < rows.size(); ++i)
    {
        string file_name = rows[i].filepath_value;
        uint64_t start_time = rows[i].start_time_value;
        string object_name = rows[i].object_id_value;
        
        // Include file if it exists locally OR if it has MinIO object name (for on-demand download)
        bool includeFile = isFileExist(file_name) || !object_name.empty();
        
        if (includeFile)
        {
            VideoFileInfo next_file(rows[i]);
            LOG(info) << "File path = " << file_name << endl;
            LOG(info) << "File start time = " << start_time << endl;
            LOG(info) << "File duration = " << rows[i].duration_value << endl;
            LOG(info) << "File FPS = " << rows[i].filefps_value << endl;
            LOG(info) << "File object name = " << next_file.m_objectId << endl;

            next_file_list.push_back(next_file);
        }
        else
        {
            LOG(error) << "File " << file_name << " not present locally and no MinIO object name available." << endl;
        }
    }
    return next_file_list;
}

VideoFileInfo Sqlite::getInProgressRecordFile(std::string streamId, int64_t startTime)
{
    LOG(info) << "Sqlite::getInProgressRecordFile stream id: " << streamId << " startTime: " << startTime << endl;
    VideoFileInfo file;

    VideoRecordDBColumns dbRow;
    VideoRecordDBColumns row = readInProgressVideoRecord(streamId, startTime);

    if (isFileExist(row.filepath_value))
    {
        file = row;
        LOG(verbose) << "DBG File path = " << file.m_filePath << endl;
        LOG(verbose) << "DBG File start time = " << file.m_startTime << endl;
    }
    else
    {
        LOG(error) << "File " << row.filepath_value << " not present." << endl;
    }

    return file;
}

VideoFileInfo Sqlite::getRecordFileInfo(std::string streamId, int64_t startTime)
{
    VideoFileInfo file;

    VideoRecordDBColumns dbRow;
    VideoRecordDBColumns row = readVideoRecordExactMatch(streamId, startTime);

    if (isFileExist(row.filepath_value))
    {
        file = row;
        LOG(verbose) << "DBG File path = " << file.m_filePath << endl;
        LOG(verbose) << "DBG File start time = " << file.m_startTime << endl;
    }
    else
    {
        LOG(error) << "File " << row.filepath_value << " not present." << endl;
    }

    return file;
}

int Sqlite::getAllStreams(std::vector<shared_ptr<StreamInfo>> &streamInfo, const std::string &deviceId)
{
    if (deviceId.empty())
    {
        LOG(error) << "Invalid VMS ID provided" << endl;
        return -1;
    }

    vector<SensorDetailsDBColumns> rowArray = readSensorDetails(deviceId);
    for (uint32_t i = 0; i < rowArray.size(); i++)
    {
        SensorDetailsDBColumns row = rowArray[i];

        /* Main Stream */
        shared_ptr<StreamInfo> main_stream(new StreamInfo);
        getMainStreamFromDB(main_stream, row);

        if (main_stream->live_url.empty() == false)
        {
            streamInfo.push_back(main_stream);
        }

        /* Sub Streams */
        vector<SensorStreamsDBColumns> sub_stream = Sqlite::getInstance()->readAllStreamsForGivenSensorID(row.sensor_id_value);
        for (uint32_t i = 0; i < sub_stream.size(); i++)
        {
            if (sub_stream[i].sensor_id_value == row.sensor_id_value)
            {
                shared_ptr<StreamInfo> stream(new StreamInfo);
                getSubStreamFromDB(stream, sub_stream[i], row.name_value);

                if (stream->live_url.empty() == false)
                {
                    streamInfo.push_back(stream);
                }
            }
        }
    }

    return 0;
}

int Sqlite::getAllSensors(vector<shared_ptr<SensorInfo>> &sensorInfo, const std::string &deviceId)
{
    if (deviceId.empty())
    {
        LOG(error) << "Invalid VMS ID provided" << endl;
        return -1;
    }

    vector<SensorDetailsDBColumns> rowArray = readSensorDetails(deviceId);
    for (uint32_t i = 0; i < rowArray.size(); i++)
    {
        SensorDetailsDBColumns row = rowArray[i];
        shared_ptr<SensorInfo> sensor(new SensorInfo);
        getSensorInfoFromDB(sensor, row);
        sensorInfo.push_back(sensor);
    }

    return 0;
}

bool Sqlite::isSensorExists(const shared_ptr<SensorInfo> &in_device, const std::string &deviceId)
{
    return findExistingSensor(in_device, deviceId) != nullptr;
}

shared_ptr<SensorInfo> Sqlite::findExistingSensor(const shared_ptr<SensorInfo> &in_device, const std::string &deviceId)
{
    vector<shared_ptr<SensorInfo>> sensors;
    getAllSensors(sensors, deviceId);

    for (auto it = sensors.begin(); it != sensors.end(); ++it)
    {
        shared_ptr<SensorInfo> sensor = *it;
        if (in_device->streams.size() > 0 && sensor->streams.size() > 0)
        {
            shared_ptr<StreamInfo> in_stream = in_device->streams[0];
            shared_ptr<StreamInfo> stream = sensor->streams[0];
            if (stream->live_url == in_stream->live_url && stream->live_url.empty() == false && in_stream->live_url.empty() == false)
            {
                return sensor;
            }
        }
        if ((sensor->type == SENSOR_TYPE_ONVIF) && (sensor->ip == in_device->ip))
        {
            return sensor;
        }
        if (sensor->id == in_device->id && in_device->type == SENSOR_TYPE_FILE)
        {
            return sensor;
        }
    }
    return nullptr;
}

shared_ptr<SensorInfo> Sqlite::searchSensorAndGetSensorInfo(const string &searchSensorId, const std::string &deviceId)
{
    LOG(verbose) << "searchSensorId: " << searchSensorId << " deviceId: " << deviceId << endl;

    // OPTIMIZATION: Direct query instead of fetching all sensors
    // Step 1: Try to find by sensor_id directly
    SensorDetailsDBColumns sensorRow = readSensorDetails(deviceId, searchSensorId);
    if (!sensorRow.sensor_id_value.empty())
    {
        // Found sensor by ID - build SensorInfo
        shared_ptr<SensorInfo> sensor(new SensorInfo);
        getSensorInfoFromDB(sensor, sensorRow);
        LOG(verbose) << "Found sensor by sensor_id: " << searchSensorId << endl;
        return sensor;
    }

    // Step 2: Not found by sensor_id - try to find by stream_id
    SensorStreamsDBColumns streamRow = readSensorStreams(searchSensorId);
    if (!streamRow.stream_id_value.empty() && !streamRow.sensor_id_value.empty())
    {
        // Found stream - get the parent sensor
        SensorDetailsDBColumns parentSensorRow = readSensorDetails(deviceId, streamRow.sensor_id_value);
        if (!parentSensorRow.sensor_id_value.empty())
        {
            shared_ptr<SensorInfo> sensor(new SensorInfo);
            getSensorInfoFromDB(sensor, parentSensorRow);
            LOG(verbose) << "Found sensor by stream_id: " << searchSensorId << " -> sensor: " << streamRow.sensor_id_value << endl;
            return sensor;
        }
    }

    LOG(error) << "device " << searchSensorId << " not found" << endl;
    return nullptr;
}

int Sqlite::setDbVersion(DbDetailsColumns &row)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement with parameterized query */
    string sql = string("INSERT OR IGNORE INTO " + DbDetailsColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(DbDetailsColumns::row_id, row.row_id_value, sql)
    APPEND_COLUMN(DbDetailsColumns::db_version, row.db_version_value, sql)
    APPEND_COLUMN(DbDetailsColumns::created_date_time, currentUtcTime, sql)
    APPEND_COLUMN(DbDetailsColumns::modified_date_time, currentUtcTime, sql)
    sql.pop_back();
    
    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(row.row_id_value, params)
    APPEND_COLUMN_VALUE(row.db_version_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(sql, params);
    sql += ";";

    LOG(info) << "SQL query: " << sql << endl;

    /* Execute SQL statement with parameters */
    if (!executeQuery(sql, params))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return -1;
    }
    return 0;
}

DbDetailsColumns Sqlite::getDbVersion()
{
    DbDetailsColumns row;
    queryResult result;
    CHECK_DB_IF_NULL_RETURN(row)
    string sql = "SELECT * FROM " + DbDetailsColumns::table_name + ";";
    LOG(info) << "SQL query: " << sql << endl;
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return row;
    }

    for (auto entries : result)
    {
        for (auto column : entries)
        {
            if (iequals(column.first, DbDetailsColumns::db_version))
            {
                row.db_version_value = column.second;
            }
            else if (iequals(column.first, DbDetailsColumns::created_date_time))
            {
                row.created_date_time_value = column.second;
            }
            else if (iequals(column.first, DbDetailsColumns::modified_date_time))
            {
                row.modified_date_time_value = column.second;
            }
        }
    }

    LOG(info) << "Getting DB version: " << row.db_version_value << endl;
    return row;
}

int Sqlite::updateFileProtectionInDb(bool fileProtection, string filePath)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string sql;
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " + VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(0) + ", " + VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " WHERE " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {fileProtection ? "1" : "0", currentUtcTime, filePath};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Sqlite::updateObjectIdInDb(const std::string& objectId, const std::string& filePath)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " + 
            VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(0) + ", " + 
            VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + " " + 
            " WHERE " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(2) + ";";
    std::vector<std::string> params = {objectId, currentUtcTime, filePath};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Sqlite::updateFileProtectionAndObjectIdInDb(bool fileProtection, const std::string& objectId, const std::string& filePath)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " + 
            VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(0) + ", " +
            VideoRecordDBColumns::object_id + " = " + PARAM_PLACEHOLDER(1) + ", " + 
            VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(2) + " " + 
            " WHERE " + VideoRecordDBColumns::file_path + " = " + PARAM_PLACEHOLDER(3) + ";";
    std::vector<std::string> params = {fileProtection ? "1" : "0", objectId, currentUtcTime, filePath};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

int Sqlite::resetProtectedFlagsInDb()
{
    CHECK_DB_IF_NULL_RETURN(-1)
    string currentUtcTime = getCurrentUtcTime();
    /* Create SQL statement to update the record */
    // Use parameterized query to prevent SQL injection
    string queryTemplate = "UPDATE " + VideoRecordDBColumns::table_name + " SET " + VideoRecordDBColumns::file_protection + " = " + PARAM_PLACEHOLDER(0) + ", " +
          VideoRecordDBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) + ";";
    std::vector<std::string> params = {std::to_string(0), currentUtcTime};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    /* Execute SQL statement */
    if (!executeQuery(queryTemplate, params))
    {
        LOG(error) << "Error executing SQL stmt: " << queryTemplate << endl;
        return -1;
    }
    return 0;
}

uint64_t Sqlite::getTotalCurrentRecordSize()
{
    string sql;
    uint64_t totalSize = 0;
    queryResult result;

    /* Create SQL statement to get sum of file sizes */
    sql = "SELECT COALESCE(SUM(" + VideoRecordDBColumns::file_size + "), 0) as total_size FROM " + VideoRecordDBColumns::table_name + ";";

    LOG(verbose) << "SQL query: " << sql << endl;

    /* Execute SQL statement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return totalSize;
    }

    if (!result.empty() && result[0].find("total_size") != result[0].end())
    {
        try
        {
            std::string value = result[0]["total_size"];
            if (!value.empty())
            {
                totalSize = std::stoull(value);
            }
        }
        catch (const std::exception &e)
        {
            LOG(error) << "Error converting sum to uint64_t: " << e.what() << endl;
        }
    }

    return totalSize;
}

std::vector<VideoRecordDBColumns> Sqlite::getProtectedFilesFromDB()
{
    // get the file size row from the Video records table
    vector<VideoRecordDBColumns> rows;
    CHECK_DB_IF_NULL_RETURN(rows)
    queryResult result;
    string sql = "SELECT " + VideoRecordDBColumns::file_path + "," +
                 VideoRecordDBColumns::storage_location + "," +
                 VideoRecordDBColumns::record_config +
                 " FROM " + VideoRecordDBColumns::table_name + " WHERE " +
                 VideoRecordDBColumns::file_protection + " = '1';";
    /* Execute SQL statement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return rows;
    }

    LOG(verbose) << "SQL query: " << sql << endl;

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return rows;
}

VmsErrorCode Sqlite::getMainStreamFromDB(shared_ptr<StreamInfo> &mainStream, const SensorDetailsDBColumns &sensorDetails)
{
    if (mainStream.get() == nullptr)
    {
        return VmsErrorCode::VMSInternalError;
    }
    SensorStreamsDBColumns stream_row = readSensorStreams(sensorDetails.sensor_id_value);

    mainStream->id = mainStream->sensorId = sensorDetails.sensor_id_value;
    mainStream->isMainStream = stream_row.isMainStream_value == "true" ? true : false;
    mainStream->replay_url = stream_row.replay_url_value;
    mainStream->live_proxy_url = stream_row.proxy_url_value;
    mainStream->name = sensorDetails.name_value;
    mainStream->live_url = stream_row.live_url_value;
    mainStream->stream_type = (StreamType)stream_row.streamType_value;
    mainStream->storageLocation = static_cast<StreamStorageType>(stream_row.storageLocation_value);

    SensorVideoEncoderSettingsValues enc_values;
    enc_values.encoding = stream_row.encoding_value;
    enc_values.frameRate = stream_row.frameRate_value;
    enc_values.resolution = stream_row.resolution_value;
    enc_values.encodingInterval = stream_row.encodingInterval_value;
    enc_values.numFrames = stream_row.numFrames_value;
    enc_values.bitrate = stream_row.bitrate_value;
    enc_values.isBframesPresent = (stream_row.isBframesPresent_value == 1);
    if (sensorDetails.type_value == SENSOR_TYPE_NVSTREAM)
    {
        mainStream->live_proxy_url = stream_row.replay_url_value;
        enc_values.container = sensorDetails.hardware_id_value;
    }

    SensorAudioEncoderSettingsValues audioenc_values;
    audioenc_values.encoding = stream_row.audio_encoding_value;
    audioenc_values.container = stream_row.audio_container_value;
    audioenc_values.sample_rate = stream_row.audio_sample_rate_value;
    audioenc_values.bits_per_sample = stream_row.audio_bps_value;
    audioenc_values.channels = stream_row.audio_channels_value;

    mainStream->duration = stringToInt(stream_row.duration_value, -1);
    mainStream->updateVideoEncoderValues(enc_values, false);
    mainStream->updateAudioEncoderValues(audioenc_values, false);

    return VmsErrorCode::NoError;
}

VmsErrorCode Sqlite::getSubStreamFromDB(shared_ptr<StreamInfo> &subStream, const SensorStreamsDBColumns &streamDetails, const string device_name)
{
    if (subStream.get() == nullptr)
    {
        return VmsErrorCode::VMSInternalError;
    }

    subStream->id = streamDetails.stream_id_value;
    subStream->isMainStream = false;
    subStream->replay_url = streamDetails.replay_url_value;
    subStream->live_proxy_url = streamDetails.proxy_url_value;
    // Use stream_name from database if available, otherwise fall back to device_name
    subStream->name = streamDetails.streamName_value.empty() ? device_name : streamDetails.streamName_value;
    subStream->live_url = streamDetails.live_url_value;
    subStream->settings.encoderValues.encoding = streamDetails.encoding_value;
    subStream->settings.encoderValues.frameRate = streamDetails.frameRate_value;
    subStream->settings.encoderValues.resolution = streamDetails.resolution_value;
    subStream->settings.encoderValues.numFrames = streamDetails.numFrames_value;
    subStream->settings.encoderValues.bitrate = streamDetails.bitrate_value;
    subStream->settings.encoderValues.isBframesPresent = (streamDetails.isBframesPresent_value == 1);
    subStream->stream_type = (StreamType)streamDetails.streamType_value;
    subStream->storageLocation = static_cast<StreamStorageType>(streamDetails.storageLocation_value);

    // Add audio encoder values to stream
    subStream->settings.audioEncoderValues.encoding = streamDetails.audio_encoding_value;
    subStream->settings.audioEncoderValues.container = streamDetails.audio_container_value;
    subStream->settings.audioEncoderValues.sample_rate = streamDetails.audio_sample_rate_value;
    subStream->settings.audioEncoderValues.bits_per_sample = streamDetails.audio_bps_value;
    subStream->settings.audioEncoderValues.channels = streamDetails.audio_channels_value;
    return VmsErrorCode::NoError;
}

VmsErrorCode Sqlite::getSensorInfoFromDB(shared_ptr<SensorInfo> &sensorInfo, const SensorDetailsDBColumns &sensorDetails)
{
    if (sensorInfo.get() == nullptr)
    {
        return VmsErrorCode::VMSInternalError;
    }

    Json::Value origin;
    Json::Value geoLocation;
    Json::Value coordinates;
    Json::Value position;
    sensorInfo->id = sensorDetails.sensor_id_value;
    sensorInfo->sensorId = sensorDetails.sensor_hw_id_value;
    sensorInfo->hardware = sensorDetails.hardware_value;
    sensorInfo->manufacturer = sensorDetails.manufacturer_value;
    sensorInfo->serial_number = sensorDetails.serial_number_value;
    sensorInfo->firmware_version = sensorDetails.firmware_version_value;
    sensorInfo->hardware_id = sensorDetails.hardware_id_value;
    sensorInfo->location = sensorDetails.location_value;
    sensorInfo->tags = sensorDetails.tags_value;
    sensorInfo->url = sensorDetails.url_value;
    sensorInfo->user = sensorDetails.username_value;
    sensorInfo->password = sensorDetails.password_value;
    sensorInfo->name = sensorDetails.name_value;
    sensorInfo->ip = sensorDetails.ip_value;
    sensorInfo->type = sensorDetails.type_value;
    sensorInfo->isAutoDiscovered = true;
    sensorInfo->isRemoteSensor = sensorDetails.isRemoteSensor_value == "true" ? true : false;
    sensorInfo->remoteDeviceId = sensorDetails.remoteDeviceId_value;
    sensorInfo->remoteDeviceName = sensorDetails.remoteDeviceName_value;
    sensorInfo->remoteDeviceLocation = sensorDetails.remoteDeviceLocation_value;
    sensorInfo->sensorStatus = static_cast<SensorStatusEvent>(sensorDetails.sensorStatus_value);
    sensorInfo->httpStatusCode = translateVmsErrorCodeToCameraHttpErrorCode(translateCameraHttpErrorCodeToVmsErrorCode(sensorDetails.httpStatus_value));
    position = stringToJson(sensorDetails.position_value);
    if (position == Json::nullValue)
    {
        LOG(error) << "Getting invalid position object" << endl;
        return VmsErrorCode::VMSInternalError;
    }

    sensorInfo->position.direction = position.get("direction", EMPTY_STRING).asString();
    sensorInfo->position.depth = position.get("depth", EMPTY_STRING).asString();
    sensorInfo->position.fieldOfView = position.get("field_of_view", EMPTY_STRING).asString();
    origin = position.get("origin", Json::nullValue);
    geoLocation = position.get("geo_location", Json::nullValue);
    coordinates = position.get("coordinates", Json::nullValue);
    if (origin != Json::nullValue)
    {
        sensorInfo->position.origin.first = origin.get("latitude", EMPTY_STRING).asString();
        sensorInfo->position.origin.second = origin.get("longitude", EMPTY_STRING).asString();
    }
    if (geoLocation != Json::nullValue)
    {
        sensorInfo->position.geoLocation.first = geoLocation.get("latitude", EMPTY_STRING).asString();
        sensorInfo->position.geoLocation.second = geoLocation.get("longitude", EMPTY_STRING).asString();
    }
    if (coordinates != Json::nullValue)
    {
        sensorInfo->position.coordinates.first = coordinates.get("x", EMPTY_STRING).asString();
        sensorInfo->position.coordinates.second = coordinates.get("y", EMPTY_STRING).asString();
    }
    if (sensorInfo->type == SENSOR_TYPE_RTSP)
    {
        sensorInfo->isAutoDiscovered = false;
    }
    sensorInfo->addUsersFromString(sensorDetails.users_value);
    /* Main Stream - skip for file-based sensors as they store streams in SENSOR_STREAMS table */
    if (sensorInfo->type != SENSOR_TYPE_FILE)
    {
        shared_ptr<StreamInfo> main_stream(new StreamInfo);
        getMainStreamFromDB(main_stream, sensorDetails);

        if (main_stream->live_url.empty() == false)
        {
            sensorInfo->streams.push_back(main_stream);
        }
    }
    /* Sub Streams - for file-based sensors, process all streams regardless of isMainStream flag */
    vector<SensorStreamsDBColumns> sub_stream = readAllStreamsForGivenSensorID(sensorInfo->id);
    for (uint32_t i = 0; i < sub_stream.size(); i++)
    {
        // For non-file sensors, skip main streams as they're handled above
        if (sensorInfo->type != SENSOR_TYPE_FILE && sub_stream[i].isMainStream_value == "true")
        {
            continue;
        }
        if (sub_stream[i].sensor_id_value == sensorDetails.sensor_id_value)
        {
            shared_ptr<StreamInfo> stream(new StreamInfo);
            getSubStreamFromDB(stream, sub_stream[i], sensorInfo->name);
            stream->sensorId = sensorInfo->id;
            
            // For file-based sensors, always add streams (they have proxy_url instead of live_url)
            if (sensorInfo->type == SENSOR_TYPE_FILE || stream->live_url.empty() == false)
            {
                // Set isMainStream to false initially - will be corrected after all streams are loaded
                stream->isMainStream = false;
                sensorInfo->streams.push_back(stream);
            }
        }
    }
    
    // Ensure proper main stream assignment: first stream is main, others are not
    if (sensorInfo->streams.size() > 0)
    {
        for (size_t i = 0; i < sensorInfo->streams.size(); ++i)
        {
            sensorInfo->streams[i]->isMainStream = (i == 0);
        }
        LOG(verbose) << "Set main stream assignment for sensor " << sensorInfo->id << " with " << sensorInfo->streams.size() << " streams" << endl;
    }

    return VmsErrorCode::NoError;
}

/**
 * Select unique sensor IDs from the sensor details table, but only for those sensors that 
 * have corresponding entries in the video record details table.
 */
VmsErrorCode Sqlite::getSensorIdsWithRecordingTimelines(std::unordered_set<std::string> &sensorIds)
{
    queryResult result;
    std::string sql = "SELECT DISTINCT sd." + DBColumns::sensor_id + 
                      " FROM " + SensorDetailsDBColumns::table_name + " sd INNER JOIN " + 
                      VideoRecordDBColumns::table_name + " vrd ON sd." + 
                      DBColumns::sensor_id + " = vrd." + DBColumns::sensor_id + ";";    

    LOG(verbose) << "SQL query: " << sql << std::endl;

    /* Execute SQL statIn src/framework/database/sqlite/sqlite_helper.cpp around lines 3703 to 3761, the sqlite3_stmt pointer 'stmt' is used with sqlite3_finalize regardless of whether sqlite3_prepare_v2 succeeded, which can cause undefined behavior when prepare fails and 'stmt' is uninitialized. To fix this, initialize 'stmt' to nullptr before calling sqlite3_prepare_v2, and before finalizing, check if 'stmt' is not nullptr to safely call sqlite3_finalize only when valid.ement */
    if (!executeQuery(sql, result))
    {
        LOG(error) << "Error executing SQL statement: " << sql << std::endl;
        return VmsErrorCode::VMSInternalError;
    }

    for (const auto& row : result)
    {
        auto it = row.find(DBColumns::sensor_id);
        if (it != row.end())
        {
            sensorIds.insert(it->second);
        }
        else
        {
            LOG(warning) << "Sensor ID not found in row." << std::endl;
        }
    }

    return VmsErrorCode::NoError;
}

// Temp Files operations implementation
int Sqlite::insertTempFileRecord(TempFilesDBColumns &row)
{
    string currentUtcTime = getCurrentUtcTime();
    
    /* Create SQL statement with parameterized query */
    string insertQuery = string("INSERT OR REPLACE INTO " + TempFilesDBColumns::table_name + "(");
    vector<string> params;
    
    APPEND_COLUMN(DBColumns::device_id, row.device_id_value, insertQuery)
    APPEND_COLUMN(TempFilesDBColumns::file_path, row.file_path_value, insertQuery)
    APPEND_COLUMN_INT(TempFilesDBColumns::expiry_timestamp, row.expiry_timestamp_value, insertQuery)
    APPEND_COLUMN_INT(TempFilesDBColumns::created_timestamp, row.created_timestamp_value, insertQuery)
    APPEND_COLUMN(TempFilesDBColumns::stream_id, row.stream_id_value, insertQuery)
    APPEND_COLUMN_INT(TempFilesDBColumns::file_size, row.file_size_value, insertQuery)
    APPEND_COLUMN_INT(TempFilesDBColumns::start_time_ms, row.start_time_ms_value, insertQuery)
    APPEND_COLUMN_INT(TempFilesDBColumns::end_time_ms, row.end_time_ms_value, insertQuery)
    APPEND_COLUMN(TempFilesDBColumns::file_type, row.file_type_value, insertQuery)
    APPEND_COLUMN(TempFilesDBColumns::container_format, row.container_format_value, insertQuery)
    APPEND_COLUMN(DBColumns::created_date_time, currentUtcTime, insertQuery)
    APPEND_COLUMN(DBColumns::modified_date_time, currentUtcTime, insertQuery)
    insertQuery.pop_back();
    
    // Build parameterized values using safe macros
    APPEND_COLUMN_VALUE(row.device_id_value, params)
    APPEND_COLUMN_VALUE(row.file_path_value, params)
    APPEND_COLUMN_VALUE_INT(row.expiry_timestamp_value, params)
    APPEND_COLUMN_VALUE_INT(row.created_timestamp_value, params)
    APPEND_COLUMN_VALUE(row.stream_id_value, params)
    APPEND_COLUMN_VALUE_INT(row.file_size_value, params)
    APPEND_COLUMN_VALUE_INT(row.start_time_ms_value, params)
    APPEND_COLUMN_VALUE_INT(row.end_time_ms_value, params)
    APPEND_COLUMN_VALUE(row.file_type_value, params)
    APPEND_COLUMN_VALUE(row.container_format_value, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    APPEND_COLUMN_VALUE(currentUtcTime, params)
    
    // Add VALUES clause with automatic placeholders using advanced macro
    BUILD_VALUES_CLAUSE(insertQuery, params);
    insertQuery += ";";
    
    LOG(verbose) << "SQL query: " << insertQuery << endl;
    
    bool success = executeQuery(insertQuery, params);
    LOG(info) << "Inserted temp file record: " << row.file_path_value << " success: " << success << endl;
    
    return success ? 0 : -1;
}

int Sqlite::deleteTempFileRecord(const std::string& filePath)
{
    // Use parameterized query to prevent SQL injection
    std::string queryTemplate = "DELETE FROM " + TempFilesDBColumns::table_name + 
                               " WHERE " + TempFilesDBColumns::file_path + " = " + PARAM_PLACEHOLDER(0) + "";
    std::vector<std::string> params = {filePath};
    
    bool success = executeQuery(queryTemplate, params);
    LOG(info) << "Deleted temp file record: " << filePath << " success: " << success << endl;
    
    return success ? 0 : -1;
}

std::vector<TempFilesDBColumns> Sqlite::getAllTempFiles()
{
    std::vector<TempFilesDBColumns> result;
    queryResult queryRes;
    CHECK_DB_IF_NULL_RETURN(result)
    
    string sql = "SELECT * FROM " + TempFilesDBColumns::table_name + 
                 " ORDER BY " + TempFilesDBColumns::created_timestamp + ";";
    
    LOG(verbose) << "SQL query: " << sql << endl;
    
    /* Execute SQL statement */
    if (!executeQuery(sql, queryRes))
    {
        LOG(error) << "Error executing SQL stmt: " << sql << endl;
        return result;
    }
    
    for (auto entries : queryRes)
    {
        TempFilesDBColumns record;
        for (auto column : entries)
        {
            if (iequals(column.first, DBColumns::device_id))
            {
                record.device_id_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::file_path))
            {
                record.file_path_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::expiry_timestamp))
            {
                record.expiry_timestamp_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::created_timestamp))
            {
                record.created_timestamp_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::stream_id))
            {
                record.stream_id_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::file_size))
            {
                record.file_size_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::start_time_ms))
            {
                record.start_time_ms_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::end_time_ms))
            {
                record.end_time_ms_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::file_type))
            {
                record.file_type_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::container_format))
            {
                record.container_format_value = column.second;
            }
        }
        result.push_back(record);
    }
    
    return result;
}

int Sqlite::cleanupTempFileRecords(int64_t olderThanTimestamp)
{
    CHECK_DB_IF_NULL_RETURN(-1)
    
    // First get the count of records to be deleted
    string countQuery = "SELECT COUNT(*) as record_count FROM " + TempFilesDBColumns::table_name + 
                       " WHERE " + TempFilesDBColumns::created_timestamp + " < " + PARAM_PLACEHOLDER(0) + ";";
    
    std::vector<std::string> countParams = {std::to_string(olderThanTimestamp)};
    int recordCount = 0;
    queryResult queryRes;
    
    LOG(verbose) << "SQL query: " << countQuery << endl;
    
    if (executeQuery(countQuery, countParams, queryRes))
    {
        for (auto entries : queryRes)
        {
            for (auto column : entries)
            {
                if (iequals(column.first, "record_count"))
                {
                    recordCount = stringToInt(column.second, 0);
                    break;
                }
            }
        }
    }
    
    // Now delete the records
    string deleteQuery = "DELETE FROM " + TempFilesDBColumns::table_name + 
                        " WHERE " + TempFilesDBColumns::created_timestamp + " < " + PARAM_PLACEHOLDER(0) + ";";
    
    std::vector<std::string> deleteParams = {std::to_string(olderThanTimestamp)};
    
    LOG(verbose) << "SQL query: " << deleteQuery << endl;
    
    bool success = executeQuery(deleteQuery, deleteParams);
    LOG(info) << "Cleaned up " << recordCount << " old temp file records, success: " << success << endl;
    
    return success ? recordCount : -1;
}

TempFilesDBColumns Sqlite::findTempFileByStreamAndTime(
    const std::string& deviceId, const std::string& streamId,
    int64_t startTimeMs, int64_t endTimeMs,
    const std::string& fileType,
    const std::string& containerFormat)
{
    TempFilesDBColumns record;
    queryResult queryRes;
    CHECK_DB_IF_NULL_RETURN(record)

    auto now = std::chrono::system_clock::now();
    auto nowMs = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();

    const int64_t tol = TempFilesDBColumns::CACHE_TIME_TOLERANCE_MS;

    std::string queryTemplate = "SELECT " +
                TempFilesDBColumns::file_path + ", " +
                TempFilesDBColumns::expiry_timestamp + ", " +
                TempFilesDBColumns::stream_id +
                " FROM " + TempFilesDBColumns::table_name +
                " WHERE " + DBColumns::device_id + " = " + PARAM_PLACEHOLDER(0) +
                " AND " + TempFilesDBColumns::stream_id + " = " + PARAM_PLACEHOLDER(1) +
                " AND " + TempFilesDBColumns::start_time_ms + " BETWEEN " + PARAM_PLACEHOLDER(2) + " AND " + PARAM_PLACEHOLDER(3) +
                " AND " + TempFilesDBColumns::end_time_ms + " BETWEEN " + PARAM_PLACEHOLDER(4) + " AND " + PARAM_PLACEHOLDER(5) +
                " AND " + TempFilesDBColumns::file_type + " = " + PARAM_PLACEHOLDER(6) +
                " AND " + TempFilesDBColumns::expiry_timestamp + " > " + PARAM_PLACEHOLDER(7);

    std::vector<std::string> params = {
        deviceId, streamId,
        std::to_string(startTimeMs - tol), std::to_string(startTimeMs + tol),
        std::to_string(endTimeMs - tol), std::to_string(endTimeMs + tol),
        fileType, std::to_string(nowMs)
    };

    if (!containerFormat.empty())
    {
        queryTemplate += " AND " + TempFilesDBColumns::container_format + " = " + PARAM_PLACEHOLDER(8);
        params.push_back(containerFormat);
    }
    queryTemplate += " LIMIT 1;";

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, queryRes))
    {
        LOG(error) << "Error executing findTempFileByStreamAndTime query" << endl;
        return record;
    }

    for (auto& entries : queryRes)
    {
        for (auto& column : entries)
        {
            if (iequals(column.first, TempFilesDBColumns::file_path))
            {
                record.file_path_value = column.second;
            }
            else if (iequals(column.first, TempFilesDBColumns::expiry_timestamp))
            {
                record.expiry_timestamp_value = stringToLong(column.second);
            }
            else if (iequals(column.first, TempFilesDBColumns::stream_id))
            {
                record.stream_id_value = column.second;
            }
        }
        break;
    }

    return record;
}

int Sqlite::updateTempFileExpiry(const std::string& filePath, int64_t newExpiryTimestamp)
{
    CHECK_DB_IF_NULL_RETURN(-1)

    string currentUtcTime = getCurrentUtcTime();

    std::string queryTemplate = "UPDATE " + TempFilesDBColumns::table_name +
                " SET " + TempFilesDBColumns::expiry_timestamp + " = " + PARAM_PLACEHOLDER(0) +
                ", " + DBColumns::modified_date_time + " = " + PARAM_PLACEHOLDER(1) +
                " WHERE " + TempFilesDBColumns::file_path + " = " + PARAM_PLACEHOLDER(2) + ";";

    std::vector<std::string> params = {
        std::to_string(newExpiryTimestamp), currentUtcTime, filePath
    };

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    bool success = executeQuery(queryTemplate, params);
    LOG(info) << "Updated temp file expiry: " << filePath << " success: " << success << endl;

    return success ? 0 : -1;
}

int Sqlite::queryCrashedRecordings(std::vector<VideoRecordDBColumns> &rows)
{
    CHECK_DB_IF_NULL_RETURN(-1)

    LOG(info) << "Starting crash recovery - querying records with duration=" << FILE_INIT_DURATION << endl;

    queryResult result;
    std::string queryTemplate = "SELECT * FROM " + VideoRecordDBColumns::table_name +
                                " WHERE " + VideoRecordDBColumns::duration + " = " + PARAM_PLACEHOLDER(0) + ";";
    std::vector<std::string> params = {std::to_string(FILE_INIT_DURATION)};

    LOG(verbose) << "SQL query: " << queryTemplate << endl;

    if (!executeQuery(queryTemplate, params, result))
    {
        LOG(error) << "Error querying crashed recordings" << endl;
        return -1;
    }

    if (result.empty())
    {
        LOG(info) << "No crashed recordings found - nothing to recover" << endl;
        return 0;
    }

    LOG(info) << "Found " << result.size() << " crashed recording(s) to fix" << endl;

    for (auto entries : result)
    {
        VideoRecordDBColumns row;
        videoRecordHelper(row, entries);
        rows.push_back(row);
    }
    return 0;
}
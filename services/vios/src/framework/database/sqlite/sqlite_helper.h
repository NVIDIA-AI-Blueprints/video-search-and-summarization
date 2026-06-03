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

#pragma once

#include <stdio.h>
#include <sqlite3.h>
#include <iostream>
#include <string>
#include <assert.h>

#include "logger.h"
#include "database_schema.h"
#include "database_manager.h"
#include "device_manager.h"
#include "query_builder.h"
#include <optional>

using namespace std;

#define GET_SQLITE_INSTANCE Sqlite::getInstance

namespace nv_vms
{
    class Sqlite : public IDatabaseInterface
    {
    public:
        static Sqlite *getInstance()
        {
            if (m_instance == nullptr)
            {
                m_instance = new Sqlite;
                assert(m_instance->connect() == 0);
            }
            return m_instance;
        }
        static void destroy()
        {
            if (m_instance)
            {
                delete m_instance;
                m_instance = nullptr;
            }
        }

        // IDatabaseInterface mandatory
        bool connect() override;
        bool executeQuery(const std::string &query, queryResult &result) override;
        bool executeQuery(const std::string &query) override;
        bool isConnected() override;

        // Database type identification
        DatabaseType getDatabaseType() const override { return DatabaseType::SQLite; }
        const char* getDatabaseName() const override { return "SQLite"; }
        
        // Parameterized query methods to prevent SQL injection
        bool executeQuery(const std::string &queryTemplate, const std::vector<std::string> &params, queryResult &result) override;
        bool executeQuery(const std::string &queryTemplate, const std::vector<std::string> &params) override;

        // IDatabaseInterface optionals
        int insertRowEvent(EventDBColumns &row) override;
        int insertRowSensorDetails(SensorDetailsDBColumns &row) override;
        SensorDetailsDBColumns readSensorDetails(string deviceId, string sensorId) override;
        vector<SensorDetailsDBColumns> readSensorDetails(string deviceId) override;
        SensorDetailsDBColumns readSensorDetailsByLocation(string location) override;
        int deleteSensorDetails(string sensorId) override;
        int insertRowVideoRecord(VideoRecordDBColumns &row) override;
        std::vector<VideoRecordDBColumns> readVideoRecordStreamIdBased(string streamId, int64_t startTime, int64_t endTime) override;
        std::vector<VideoRecordDBColumns> readVideoRecordSensorIdBased(string sensorId, int64_t startTime, int64_t endTime) override;
        std::vector<VideoRecordDBColumns> readVideoRecord(string sensorId, int64_t startTime, int64_t endTime, const std::vector<string>& streamIds = std::vector<string>()) override;
        std::vector<VideoRecordDBColumns> readVideoRecordUniqueIdBased(string id) override;
        std::vector<VideoRecordDBColumns> readVideoRecordSensorIdUniqueIdBased(string sensorId, string id) override;
        VideoRecordDBColumns readInProgressVideoRecord(string streamId, int64_t startTime) override;
        VideoRecordDBColumns readVideoRecordExactMatch(string streamId, int64_t startTime) override;
        VideoRecordDBColumns readVideoRecordExactMatchFilePath(string sensorId, string filePath, int64_t startTime) override;
        int updateVideoRecordInDb(VideoRecordDBColumns &row) override;
        int updateVideoRecordDurationBatch(const std::vector<VideoRecordDBColumns> &rows) override;
        int insertRowVideoRecordSchedule(VideoRecordScheduleDBColumns &row) override;
        std::vector<VideoRecordScheduleDBColumns> readVideoRecordSchedules(string streamId = "") override;
        bool deleteVideoRecordSchedule(string sensorId, string startTime, string endTime) override;
        int deleteVideoRecordings(vector<string> &filePaths) override;
        std::vector<VideoRecordDBColumns> readRecordsInBatch(uint32_t &batchSize, bool excludeCloudScanned = false) override;
        std::vector<VideoRecordDBColumns> getVideoRecordFilePaths(string sensorId, int64_t startTime, int64_t endTime) override;
        std::vector<VideoRecordDBColumns> getVideoRecordFilePathsSensorIdBased(string sensorId, int64_t startTime, int64_t endTime) override;
        std::vector<VideoRecordDBColumns> getVideoRecordFilePathsIdBased(string id) override;
        int deleteStreamDetailsUsingSensorId(string sensorId) override;
        int deleteRecordingStatusUsingSensorId(string sensorId) override;
        int deleteRowStream(string streamId) override;
        SensorStreamsDBColumns readSensorStreams(string streamId) override;
        int insertRowStream(SensorStreamsDBColumns &row) override;
        vector<SensorStreamsDBColumns> readAllStreamsForGivenSensorID(string sensorId) override;
        vector<SensorInfoDBColumns> readSensorInfo(string sensorId) override;
        string readStreamProperty(string streamId, string property) override;
        std::vector<VideoRecordDBColumns> getRecordedVideoSize() override;
        bool checkVideoRecordExists(string sensorId) override;
        std::vector<VideoRecordDBColumns> getAllDisconnectedSensorId() override;
        UserDetailsDBColumns getUserDetail(const string username) override;
        int setUserDetail(UserDetailsDBColumns &row) override;
        std::vector<UserSessionsDBColumns> getUserSessions(const string username) override;
        int deleteUserSession(const string username, const string sessionId) override;
        int setUserSession(UserSessionsDBColumns &row) override;
        void deleteExpiredUserSessions() override;
        std::vector<UserSessionsDBColumns> getAllSessions() override;
        int deleteUserDetails(const string username) override;
        void extendSession(const string username, const string sessionId) override;
        std::string getLocalDeviceId() override;
        std::string getLocalDeviceName() override;
        int setLocalDeviceId(const string deviceId) override;
        int setLocalDeviceName(const string &deviceName, const string &deviceId) override;
        std::string getLocalDeviceLocation() override;
        int setLocalDeviceLocation(const string &deviceLocation, const string &deviceId) override;
        std::vector<VideoFileInfo> getFileList(std::string sensorId, int64_t t1, int64_t t2,
                                               size_t maxFiles = 0, bool accurate = false) override;
        std::vector<VideoFileInfo> getFileListStreamIdBased(std::string streamId, int64_t t1, int64_t t2) override;
        std::vector<VideoFileInfo> getFileListUniqueIdSensorIdBased(std::string uniqueId, std::string sensorId, int64_t t1, int64_t t2) override;
        std::vector<VideoFileInfo> getNextFileList(std::string streamId, int64_t t1) override;
        VideoFileInfo getInProgressRecordFile(std::string streamId, int64_t startTime) override;
        VideoFileInfo getRecordFileInfo(std::string streamId, int64_t startTime) override;
        int getAllStreams(std::vector<shared_ptr<StreamInfo>> &streamInfo, const std::string &deviceId) override;
        int getAllSensors(vector<shared_ptr<SensorInfo>> &sensorInfo, const std::string &deviceId) override;
        bool isSensorExists(const shared_ptr<SensorInfo> &in_device, const std::string &deviceId) override;
        shared_ptr<SensorInfo> findExistingSensor(const shared_ptr<SensorInfo> &in_device, const std::string &deviceId) override;
        shared_ptr<SensorInfo> searchSensorAndGetSensorInfo(const string &searchSensorId, const std::string &deviceId) override;
        std::vector<VideoRecordDBColumns> getAllVideoRecordFilePaths() override;
        int setDbVersion(DbDetailsColumns &row) override;
        DbDetailsColumns getDbVersion() override;
        int updateFileProtectionInDb(bool fileProtection, string filePath) override;
        int updateFilesProtectionInDb(bool fileProtection, const std::vector<string>& filePaths) override;
        int updateObjectIdInDb(const std::string& objectId, const std::string& filePath) override;
        int updateFileProtectionAndObjectIdInDb(bool fileProtection, const std::string& objectId, const std::string& filePath) override;
        int resetProtectedFlagsInDb() override;
        std::vector<VideoRecordDBColumns> getProtectedFilesFromDB() override;
        VmsErrorCode getMainStreamFromDB(shared_ptr<StreamInfo> &mainStream, const SensorDetailsDBColumns &sensorDetails) override;
        VmsErrorCode getSubStreamFromDB(shared_ptr<StreamInfo> &subStream, const SensorStreamsDBColumns &streamDetails, const string device_name) override;
        VmsErrorCode getSensorInfoFromDB(shared_ptr<SensorInfo> &sensorInfo, const SensorDetailsDBColumns &sensorDetails) override;
        uint64_t getTotalCurrentRecordSize() override;
        int setRecordingStatus(const std::string &streamId, RecordState new_status, const std::optional<string> &sensorId) override;
        VmsErrorCode getRecordingStatus(std::map<std::string, RecordingStatusDBColumns, std::less<>> &allStatus, const std::optional<string> &streamId) override;
        vector<SensorDetailsDBColumns> readAllSensorSatus(string deviceId) override;
        VmsErrorCode getSensorIdsWithRecordingTimelines(unordered_set<string> &sensorIds) override;
        int updateStreamInfo(string streamId, string proxyUrl, string replayUrl, std::pair<StreamStatus, string> status) override;
        vector<SensorStreamsDBColumns> readAllStreams() override;
        string searchSensorFileIdBased(const string &id) override;
        
        // Temp Files operations
        int insertTempFileRecord(TempFilesDBColumns &row) override;
        int deleteTempFileRecord(const std::string& filePath) override;
        std::vector<TempFilesDBColumns> getAllTempFiles() override;
        int cleanupTempFileRecords(int64_t olderThanTimestamp) override;
        TempFilesDBColumns findTempFileByStreamAndTime(
            const std::string& deviceId, const std::string& streamId,
            int64_t startTimeMs, int64_t endTimeMs,
            const std::string& fileType = "",
            const std::string& containerFormat = "") override;
        int updateTempFileExpiry(
            const std::string& filePath, int64_t newExpiryTimestamp) override;

        int queryCrashedRecordings(std::vector<VideoRecordDBColumns> &rows) override;

#ifdef UNIT_TEST
        vector<VideoRecordDBColumns> getLastRecordVideoRecord(string streamId) override;
#endif
    private:
        Sqlite();
        ~Sqlite();
        Sqlite(const Sqlite &) = delete;
        Sqlite &operator=(const Sqlite &) = delete;

    private:
        sqlite3 *m_db;
        static Sqlite *m_instance;
        bool m_connected;
    };

} // nv_vms
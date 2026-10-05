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

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include "database_schema.h"
#include "database_types.h"
#include "VideoQueue.h"
#include <optional>

using queryResult = std::vector<std::unordered_map<std::string, std::string>>;

// Database Interface
class IDatabaseInterface
{
public:
    virtual bool connect() = 0;
    virtual bool isConnected() = 0;
    virtual bool executeQuery(const std::string &query) = 0;
    virtual bool executeQuery(const std::string &query, queryResult &result) = 0;
    // Parameterized query methods to prevent SQL injection
    virtual bool executeQuery(const std::string &queryTemplate, const std::vector<std::string> &params) = 0;
    virtual bool executeQuery(const std::string &queryTemplate, const std::vector<std::string> &params, queryResult &result) = 0;
    
    virtual ~IDatabaseInterface() = default;

    // Database type identification
    virtual DatabaseType getDatabaseType() const = 0;
    virtual const char* getDatabaseName() const = 0;

    virtual int insertRowEvent(nv_vms::EventDBColumns &row) { return -1; };
    virtual int insertRowSensorDetails(nv_vms::SensorDetailsDBColumns &row) { return -1; };
    virtual nv_vms::SensorDetailsDBColumns readSensorDetails(std::string deviceId, std::string sensorId) { return nv_vms::SensorDetailsDBColumns(); };
    virtual std::vector<nv_vms::SensorDetailsDBColumns> readSensorDetails(std::string deviceId) { return {}; };
    virtual nv_vms::SensorDetailsDBColumns readSensorDetailsByLocation(std::string location) { return nv_vms::SensorDetailsDBColumns(); };
    virtual int CountSensorDetails(std::string deviceId) { return -1; };
    virtual int deleteSensorDetails(std::string sensorId) { return -1; };
    virtual int insertRowVideoRecord(nv_vms::VideoRecordDBColumns &row) { return -1; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> readVideoRecord(std::string sensorId, int64_t startTime, int64_t endTime, const std::vector<std::string>& streamIds = std::vector<std::string>()) { return {}; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> readVideoRecordStreamIdBased(std::string streamId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> readVideoRecordSensorIdBased(std::string sensorId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> readVideoRecordUniqueIdBased(std::string id) { return {}; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> readVideoRecordSensorIdUniqueIdBased(std::string sensorId, std::string id) { return {}; };
    virtual nv_vms::VideoRecordDBColumns readInProgressVideoRecord(std::string streamId, int64_t startTime) { return nv_vms::VideoRecordDBColumns(); };
    virtual nv_vms::VideoRecordDBColumns readVideoRecordExactMatch(std::string streamId, int64_t startTime) { return nv_vms::VideoRecordDBColumns(); };
    virtual nv_vms::VideoRecordDBColumns readVideoRecordExactMatchFilePath(std::string sensorId, std::string filePath, int64_t startTime) { return nv_vms::VideoRecordDBColumns(); };
    virtual int updateVideoRecordInDb(nv_vms::VideoRecordDBColumns &row) { return -1; };
    virtual int updateVideoRecordDurationBatch(const std::vector<nv_vms::VideoRecordDBColumns> &rows) { return -1; };
    virtual int insertRowVideoRecordSchedule(nv_vms::VideoRecordScheduleDBColumns &row) { return -1; };
    virtual std::vector<nv_vms::VideoRecordScheduleDBColumns> readVideoRecordSchedules(std::string streamId = "") { return {}; };
    virtual bool deleteVideoRecordSchedule(std::string streamId, std::string startTime, std::string endTime) { return false; };
    virtual int deleteVideoRecordings(std::vector<std::string> &filePaths) { return -1; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> readRecordsInBatch(uint32_t &batchSize, bool excludeCloudScanned = false) { return {}; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> getVideoRecordFilePaths(std::string streamId, int64_t startTime, int64_t endTime) { return {}; };
    virtual int deleteStreamDetailsUsingSensorId(std::string sensorId) { return -1; };
    virtual int deleteRecordingStatusUsingSensorId(std::string sensorId) { return -1; };
    virtual int deleteRowStream(std::string streamId) { return -1; };
    virtual nv_vms::SensorStreamsDBColumns readSensorStreams(std::string streamId) { return nv_vms::SensorStreamsDBColumns(); };
    virtual int insertRowStream(nv_vms::SensorStreamsDBColumns &row) { return -1; };
    virtual std::vector<nv_vms::SensorStreamsDBColumns> readAllStreamsForGivenSensorID(std::string sensorId) { return {}; };
    virtual std::vector<nv_vms::SensorInfoDBColumns> readSensorInfo(std::string sensorId) { return {}; };
    virtual std::string readStreamProperty(std::string streamId, std::string property) { return ""; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> getRecordedVideoSize() { return {}; };
    virtual bool checkVideoRecordExists(std::string streamId) { return false; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> getAllDisconnectedSensorId() { return {}; };
    virtual nv_vms::UserDetailsDBColumns getUserDetail(const std::string username) { return nv_vms::UserDetailsDBColumns(); };
    virtual int setUserDetail(nv_vms::UserDetailsDBColumns &row) { return -1; };
    virtual std::vector<nv_vms::UserSessionsDBColumns> getUserSessions(const std::string username) { return {}; };
    virtual int deleteUserSession(const std::string username, const std::string sessionId) { return -1; };
    virtual int setUserSession(nv_vms::UserSessionsDBColumns &row) { return -1; };
    virtual void deleteExpiredUserSessions() { /* Default no-op: backends without user session support have nothing to expire */ };
    virtual std::vector<nv_vms::UserSessionsDBColumns> getAllSessions() { return {}; };
    virtual int deleteUserDetails(const std::string username) { return -1; };
    virtual void extendSession(const std::string username, const std::string sessionId) { /* Default no-op; concrete database backends override this. */ };
    virtual std::string getLocalDeviceId() { return ""; };
    virtual std::string getLocalDeviceName() { return ""; };
    virtual int setLocalDeviceId(const std::string deviceId) { return -1; };
    virtual int setLocalDeviceName(const std::string &deviceName, const std::string &deviceId) { return -1; };
    virtual std::string getLocalDeviceLocation() { return ""; };
    virtual int setLocalDeviceLocation(const std::string &deviceLocation, const std::string &deviceId) { return -1; };
    virtual std::vector<nv_vms::VideoFileInfo> getFileList(std::string sensorId, int64_t t1, int64_t t2,
                                                   size_t maxFiles = 0, bool accurate = false) { return {}; };

    virtual std::vector<nv_vms::VideoFileInfo> getFileListStreamIdBased(std::string streamId, int64_t t1, int64_t t2) { return {}; };
    virtual std::vector<nv_vms::VideoFileInfo> getFileListUniqueIdSensorIdBased(std::string uniqueId, std::string sensorId, int64_t t1, int64_t t2) { return {}; };
    virtual std::vector<nv_vms::VideoFileInfo> getNextFileList(std::string streamId, int64_t t1) { return {}; };
    virtual nv_vms::VideoFileInfo getInProgressRecordFile(std::string streamId, int64_t startTime) { return nv_vms::VideoFileInfo(); };
    virtual nv_vms::VideoFileInfo getRecordFileInfo(std::string streamId, int64_t startTime) { return nv_vms::VideoFileInfo(); };
    virtual int getAllStreams(std::vector<std::shared_ptr<nv_vms::StreamInfo>> &streamInfo, const std::string &deviceId) { return -1; };
    virtual int getAllSensors(std::vector<std::shared_ptr<nv_vms::SensorInfo>> &deviceInfo, const std::string &deviceId) { return -1; };
    virtual bool isSensorExists(const std::shared_ptr<nv_vms::SensorInfo> &in_device, const std::string &deviceId) { return false; };
    virtual std::shared_ptr<nv_vms::SensorInfo> findExistingSensor(const std::shared_ptr<nv_vms::SensorInfo> &in_device, const std::string &deviceId) { return nullptr; };
    virtual std::shared_ptr<nv_vms::SensorInfo> searchSensorAndGetSensorInfo(const std::string &searchSensorId, const std::string &deviceId) { return {}; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> getAllVideoRecordFilePaths() { return {}; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> getVideoRecordFilePathsSensorIdBased(std::string sensorId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> getVideoRecordFilePathsIdBased(std::string id) { return {}; };
    virtual int setDbVersion(nv_vms::DbDetailsColumns &row) { return -1; };
    virtual nv_vms::DbDetailsColumns getDbVersion() { return nv_vms::DbDetailsColumns(); };
    virtual int updateFileProtectionInDb(bool fileProtection, std::string filePath) { return -1; };
    virtual int updateFilesProtectionInDb(bool fileProtection, const std::vector<std::string>& filePaths) { return -1; };
    virtual int resetProtectedFlagsInDb() { return -1; };
    virtual std::vector<nv_vms::VideoRecordDBColumns> getProtectedFilesFromDB() { return {}; };
    virtual void createDatabaseTables() { /* No-op default: backends without a fixed schema have no tables to create. */ };
    virtual nv_vms::VmsErrorCode getMainStreamFromDB(std::shared_ptr<nv_vms::StreamInfo> &mainStream, const nv_vms::SensorDetailsDBColumns &sensorDetails) { return nv_vms::VmsErrorCode::NoError; };
    virtual nv_vms::VmsErrorCode getSubStreamFromDB(std::shared_ptr<nv_vms::StreamInfo> &subStream, const nv_vms::SensorStreamsDBColumns &streamDetails, const std::string device_name) { return nv_vms::VmsErrorCode::NoError; };
    virtual nv_vms::VmsErrorCode getSensorInfoFromDB(std::shared_ptr<nv_vms::SensorInfo> &deviceInfo, const nv_vms::SensorDetailsDBColumns &sensorDetails) { return nv_vms::VmsErrorCode::NoError; };
    virtual uint64_t getTotalCurrentRecordSize() { return 0; };
    virtual int setRecordingStatus(const std::string &streamId, RecordState new_status, const std::optional<std::string> &sensorId) { return -1; };
    virtual nv_vms::VmsErrorCode getRecordingStatus(std::map<std::string, nv_vms::RecordingStatusDBColumns, std::less<>> &allStatus, const std::optional<std::string> &streamId) { return nv_vms::VmsErrorCode::NoError; };
    virtual std::vector<nv_vms::SensorDetailsDBColumns> readAllSensorSatus(std::string deviceId) { return {}; }
    virtual nv_vms::VmsErrorCode getSensorIdsWithRecordingTimelines(std::unordered_set<std::string> &sensorIds) { return nv_vms::VmsErrorCode::NoError; };
    virtual int updateStreamInfo(std::string streamId, std::string proxyUrl, std::string replayUrl, std::pair<nv_vms::StreamStatus, std::string> status) { return -1; };
    virtual std::vector<nv_vms::SensorStreamsDBColumns> readAllStreams() { return {}; };
    virtual int updateObjectIdInDb(const std::string& objectId, const std::string& filePath) { return -1; };
    virtual int updateFileProtectionAndObjectIdInDb(bool fileProtection, const std::string& objectId, const std::string& filePath) { return -1; };
    virtual std::string searchSensorFileIdBased(const std::string &id) { return {}; };
    
    // Temp Files operations
    virtual int insertTempFileRecord(nv_vms::TempFilesDBColumns &row) { return -1; };
    virtual int deleteTempFileRecord(const std::string& filePath) { return -1; };
    virtual std::vector<nv_vms::TempFilesDBColumns> getAllTempFiles() { return {}; };
    virtual int cleanupTempFileRecords(int64_t olderThanTimestamp) { return -1; };
    virtual nv_vms::TempFilesDBColumns findTempFileByStreamAndTime(
        const std::string& deviceId, const std::string& streamId,
        int64_t startTimeMs, int64_t endTimeMs,
        const std::string& fileType = "",
        const std::string& containerFormat = "",
        const std::string& configHash = "") { return {}; };
    virtual int updateTempFileExpiry(
        const std::string& filePath, int64_t newExpiryTimestamp) { return -1; };

    virtual int queryCrashedRecordings(std::vector<nv_vms::VideoRecordDBColumns> &rows) { return 0; };

#ifdef UNIT_TEST
    virtual std::vector<nv_vms::VideoRecordDBColumns> getLastRecordVideoRecord(std::string streamId)
    {
        return {};
    };
#endif
};

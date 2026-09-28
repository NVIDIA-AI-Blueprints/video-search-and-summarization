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

    virtual int insertRowEvent(EventDBColumns &row) { return -1; };
    virtual int insertRowSensorDetails(SensorDetailsDBColumns &row) { return -1; };
    virtual SensorDetailsDBColumns readSensorDetails(std::string deviceId, std::string sensorId) { return SensorDetailsDBColumns(); };
    virtual std::vector<SensorDetailsDBColumns> readSensorDetails(std::string deviceId) { return {}; };
    virtual SensorDetailsDBColumns readSensorDetailsByLocation(std::string location) { return SensorDetailsDBColumns(); };
    virtual int CountSensorDetails(std::string deviceId) { return -1; };
    virtual int deleteSensorDetails(std::string sensorId) { return -1; };
    virtual int insertRowVideoRecord(VideoRecordDBColumns &row) { return -1; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecord(std::string sensorId, int64_t startTime, int64_t endTime, const std::vector<std::string>& streamIds = std::vector<std::string>()) { return {}; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecordStreamIdBased(std::string streamId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecordSensorIdBased(std::string sensorId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecordUniqueIdBased(std::string id) { return {}; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecordSensorIdUniqueIdBased(std::string sensorId, std::string id) { return {}; };
    virtual VideoRecordDBColumns readInProgressVideoRecord(std::string streamId, int64_t startTime) { return VideoRecordDBColumns(); };
    virtual VideoRecordDBColumns readVideoRecordExactMatch(std::string streamId, int64_t startTime) { return VideoRecordDBColumns(); };
    virtual VideoRecordDBColumns readVideoRecordExactMatchFilePath(std::string sensorId, std::string filePath, int64_t startTime) { return VideoRecordDBColumns(); };
    virtual int updateVideoRecordInDb(VideoRecordDBColumns &row) { return -1; };
    virtual int updateVideoRecordDurationBatch(const std::vector<VideoRecordDBColumns> &rows) { return -1; };
    virtual int insertRowVideoRecordSchedule(VideoRecordScheduleDBColumns &row) { return -1; };
    virtual std::vector<VideoRecordScheduleDBColumns> readVideoRecordSchedules(std::string streamId = "") { return {}; };
    virtual bool deleteVideoRecordSchedule(std::string streamId, std::string startTime, std::string endTime) { return false; };
    virtual int deleteVideoRecordings(std::vector<std::string> &filePaths) { return -1; };
    virtual std::vector<VideoRecordDBColumns> readRecordsInBatch(uint32_t &batchSize, bool excludeCloudScanned = false) { return {}; };
    virtual std::vector<VideoRecordDBColumns> getVideoRecordFilePaths(std::string streamId, int64_t startTime, int64_t endTime) { return {}; };
    virtual int deleteStreamDetailsUsingSensorId(std::string sensorId) { return -1; };
    virtual int deleteRecordingStatusUsingSensorId(std::string sensorId) { return -1; };
    virtual int deleteRowStream(std::string streamId) { return -1; };
    virtual SensorStreamsDBColumns readSensorStreams(std::string streamId) { return SensorStreamsDBColumns(); };
    virtual int insertRowStream(SensorStreamsDBColumns &row) { return -1; };
    virtual std::vector<SensorStreamsDBColumns> readAllStreamsForGivenSensorID(std::string sensorId) { return {}; };
    virtual std::vector<SensorInfoDBColumns> readSensorInfo(std::string sensorId) { return {}; };
    virtual std::string readStreamProperty(std::string streamId, std::string property) { return ""; };
    virtual std::vector<VideoRecordDBColumns> getRecordedVideoSize() { return {}; };
    virtual bool checkVideoRecordExists(std::string streamId) { return false; };
    virtual std::vector<VideoRecordDBColumns> getAllDisconnectedSensorId() { return {}; };
    virtual UserDetailsDBColumns getUserDetail(const std::string username) { return UserDetailsDBColumns(); };
    virtual int setUserDetail(UserDetailsDBColumns &row) { return -1; };
    virtual std::vector<UserSessionsDBColumns> getUserSessions(const std::string username) { return {}; };
    virtual int deleteUserSession(const std::string username, const std::string sessionId) { return -1; };
    virtual int setUserSession(UserSessionsDBColumns &row) { return -1; };
    virtual void deleteExpiredUserSessions() { /* Default no-op: backends without user session support have nothing to expire */ };
    virtual std::vector<UserSessionsDBColumns> getAllSessions() { return {}; };
    virtual int deleteUserDetails(const std::string username) { return -1; };
    virtual void extendSession(const std::string username, const std::string sessionId) { /* Default no-op; concrete database backends override this. */ };
    virtual std::string getLocalDeviceId() { return ""; };
    virtual std::string getLocalDeviceName() { return ""; };
    virtual int setLocalDeviceId(const std::string deviceId) { return -1; };
    virtual int setLocalDeviceName(const std::string &deviceName, const std::string &deviceId) { return -1; };
    virtual std::string getLocalDeviceLocation() { return ""; };
    virtual int setLocalDeviceLocation(const std::string &deviceLocation, const std::string &deviceId) { return -1; };
    virtual std::vector<VideoFileInfo> getFileList(std::string sensorId, int64_t t1, int64_t t2,
                                                   size_t maxFiles = 0, bool accurate = false) { return {}; };

    virtual std::vector<VideoFileInfo> getFileListStreamIdBased(std::string streamId, int64_t t1, int64_t t2) { return {}; };
    virtual std::vector<VideoFileInfo> getFileListUniqueIdSensorIdBased(std::string uniqueId, std::string sensorId, int64_t t1, int64_t t2) { return {}; };
    virtual std::vector<VideoFileInfo> getNextFileList(std::string streamId, int64_t t1) { return {}; };
    virtual VideoFileInfo getInProgressRecordFile(std::string streamId, int64_t startTime) { return VideoFileInfo(); };
    virtual VideoFileInfo getRecordFileInfo(std::string streamId, int64_t startTime) { return VideoFileInfo(); };
    virtual int getAllStreams(std::vector<std::shared_ptr<StreamInfo>> &streamInfo, const std::string &deviceId) { return -1; };
    virtual int getAllSensors(std::vector<std::shared_ptr<SensorInfo>> &deviceInfo, const std::string &deviceId) { return -1; };
    virtual bool isSensorExists(const std::shared_ptr<SensorInfo> &in_device, const std::string &deviceId) { return false; };
    virtual std::shared_ptr<SensorInfo> findExistingSensor(const std::shared_ptr<SensorInfo> &in_device, const std::string &deviceId) { return nullptr; };
    virtual std::shared_ptr<SensorInfo> searchSensorAndGetSensorInfo(const std::string &searchSensorId, const std::string &deviceId) { return {}; };
    virtual std::vector<VideoRecordDBColumns> getAllVideoRecordFilePaths() { return {}; };
    virtual std::vector<VideoRecordDBColumns> getVideoRecordFilePathsSensorIdBased(std::string sensorId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<VideoRecordDBColumns> getVideoRecordFilePathsIdBased(std::string id) { return {}; };
    virtual int setDbVersion(DbDetailsColumns &row) { return -1; };
    virtual DbDetailsColumns getDbVersion() { return DbDetailsColumns(); };
    virtual int updateFileProtectionInDb(bool fileProtection, std::string filePath) { return -1; };
    virtual int updateFilesProtectionInDb(bool fileProtection, const std::vector<std::string>& filePaths) { return -1; };
    virtual int resetProtectedFlagsInDb() { return -1; };
    virtual std::vector<VideoRecordDBColumns> getProtectedFilesFromDB() { return {}; };
    virtual void createDatabaseTables() { /* No-op default: backends without a fixed schema have no tables to create. */ };
    virtual VmsErrorCode getMainStreamFromDB(std::shared_ptr<StreamInfo> &mainStream, const SensorDetailsDBColumns &sensorDetails) { return VmsErrorCode::NoError; };
    virtual VmsErrorCode getSubStreamFromDB(std::shared_ptr<StreamInfo> &subStream, const SensorStreamsDBColumns &streamDetails, const std::string device_name) { return VmsErrorCode::NoError; };
    virtual VmsErrorCode getSensorInfoFromDB(std::shared_ptr<SensorInfo> &deviceInfo, const SensorDetailsDBColumns &sensorDetails) { return VmsErrorCode::NoError; };
    virtual uint64_t getTotalCurrentRecordSize() { return 0; };
    virtual int setRecordingStatus(const std::string &streamId, RecordState new_status, const std::optional<std::string> &sensorId) { return -1; };
    virtual VmsErrorCode getRecordingStatus(std::map<std::string, RecordingStatusDBColumns, std::less<>> &allStatus, const std::optional<std::string> &streamId) { return VmsErrorCode::NoError; };
    virtual std::vector<SensorDetailsDBColumns> readAllSensorSatus(std::string deviceId) { return {}; }
    virtual VmsErrorCode getSensorIdsWithRecordingTimelines(std::unordered_set<std::string> &sensorIds) { return VmsErrorCode::NoError; };
    virtual int updateStreamInfo(std::string streamId, std::string proxyUrl, std::string replayUrl, std::pair<StreamStatus, std::string> status) { return -1; };
    virtual std::vector<SensorStreamsDBColumns> readAllStreams() { return {}; };
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

    virtual int queryCrashedRecordings(std::vector<VideoRecordDBColumns> &rows) { return 0; };

#ifdef UNIT_TEST
    virtual std::vector<VideoRecordDBColumns> getLastRecordVideoRecord(std::string streamId)
    {
        return {};
    };
#endif
};

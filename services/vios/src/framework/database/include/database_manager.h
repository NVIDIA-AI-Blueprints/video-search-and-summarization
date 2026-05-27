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
    
    virtual ~IDatabaseInterface() {}

    // Database type identification
    virtual DatabaseType getDatabaseType() const = 0;
    virtual const char* getDatabaseName() const = 0;

    virtual int insertRowEvent(EventDBColumns &row) { return -1; };
    virtual int insertRowSensorDetails(SensorDetailsDBColumns &row) { return -1; };
    virtual SensorDetailsDBColumns readSensorDetails(string deviceId, string sensorId) { return SensorDetailsDBColumns(); };
    virtual vector<SensorDetailsDBColumns> readSensorDetails(string deviceId) { return {}; };
    virtual SensorDetailsDBColumns readSensorDetailsByLocation(string location) { return SensorDetailsDBColumns(); };
    virtual int CountSensorDetails(string deviceId) { return -1; };
    virtual int deleteSensorDetails(string sensorId) { return -1; };
    virtual int insertRowVideoRecord(VideoRecordDBColumns &row) { return -1; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecord(string sensorId, int64_t startTime, int64_t endTime, const std::vector<string>& streamIds = std::vector<string>()) { return {}; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecordStreamIdBased(string streamId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecordSensorIdBased(string sensorId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecordUniqueIdBased(string id) { return {}; };
    virtual std::vector<VideoRecordDBColumns> readVideoRecordSensorIdUniqueIdBased(string sensorId, string id) { return {}; };
    virtual VideoRecordDBColumns readInProgressVideoRecord(string streamId, int64_t startTime) { return VideoRecordDBColumns(); };
    virtual VideoRecordDBColumns readVideoRecordExactMatch(string streamId, int64_t startTime) { return VideoRecordDBColumns(); };
    virtual VideoRecordDBColumns readVideoRecordExactMatchFilePath(string sensorId, string filePath, int64_t startTime) { return VideoRecordDBColumns(); };
    virtual int updateVideoRecordInDb(VideoRecordDBColumns &row) { return -1; };
    virtual int updateVideoRecordDurationBatch(const std::vector<VideoRecordDBColumns> &rows) { return -1; };
    virtual int insertRowVideoRecordSchedule(VideoRecordScheduleDBColumns &row) { return -1; };
    virtual std::vector<VideoRecordScheduleDBColumns> readVideoRecordSchedules(string streamId = "") { return {}; };
    virtual bool deleteVideoRecordSchedule(string streamId, string startTime, string endTime) { return false; };
    virtual int deleteVideoRecordings(vector<string> &filePaths) { return -1; };
    virtual std::vector<VideoRecordDBColumns> readRecordsInBatch(uint32_t &batchSize, bool excludeCloudScanned = false) { return {}; };
    virtual std::vector<VideoRecordDBColumns> getVideoRecordFilePaths(string streamId, int64_t startTime, int64_t endTime) { return {}; };
    virtual int deleteStreamDetailsUsingSensorId(string sensorId) { return -1; };
    virtual int deleteRecordingStatusUsingSensorId(string sensorId) { return -1; };
    virtual int deleteRowStream(string streamId) { return -1; };
    virtual SensorStreamsDBColumns readSensorStreams(string streamId) { return SensorStreamsDBColumns(); };
    virtual int insertRowStream(SensorStreamsDBColumns &row) { return -1; };
    virtual vector<SensorStreamsDBColumns> readAllStreamsForGivenSensorID(string sensorId) { return {}; };
    virtual vector<SensorInfoDBColumns> readSensorInfo(string sensorId) { return {}; };
    virtual string readStreamProperty(string streamId, string property) { return ""; };
    virtual std::vector<VideoRecordDBColumns> getRecordedVideoSize() { return {}; };
    virtual bool checkVideoRecordExists(string streamId) { return false; };
    virtual std::vector<VideoRecordDBColumns> getAllDisconnectedSensorId() { return {}; };
    virtual UserDetailsDBColumns getUserDetail(const string username) { return UserDetailsDBColumns(); };
    virtual int setUserDetail(UserDetailsDBColumns &row) { return -1; };
    virtual std::vector<UserSessionsDBColumns> getUserSessions(const string username) { return {}; };
    virtual int deleteUserSession(const string username, const string sessionId) { return -1; };
    virtual int setUserSession(UserSessionsDBColumns &row) { return -1; };
    virtual void deleteExpiredUserSessions() {};
    virtual std::vector<UserSessionsDBColumns> getAllSessions() { return {}; };
    virtual int deleteUserDetails(const string username) { return -1; };
    virtual void extendSession(const string username, const string sessionId) {};
    virtual std::string getLocalDeviceId() { return ""; };
    virtual std::string getLocalDeviceName() { return ""; };
    virtual int setLocalDeviceId(const string deviceId) { return -1; };
    virtual int setLocalDeviceName(const string &deviceName, const string &deviceId) { return -1; };
    virtual std::string getLocalDeviceLocation() { return ""; };
    virtual int setLocalDeviceLocation(const string &deviceLocation, const string &deviceId) { return -1; };
    virtual std::vector<VideoFileInfo> getFileList(std::string sensorId, int64_t t1, int64_t t2,
                                                   size_t maxFiles = 0, bool accurate = false) { return {}; };

    virtual std::vector<VideoFileInfo> getFileListStreamIdBased(std::string streamId, int64_t t1, int64_t t2) { return {}; };
    virtual std::vector<VideoFileInfo> getFileListUniqueIdSensorIdBased(std::string uniqueId, std::string sensorId, int64_t t1, int64_t t2) { return {}; };
    virtual std::vector<VideoFileInfo> getNextFileList(std::string streamId, int64_t t1) { return {}; };
    virtual VideoFileInfo getInProgressRecordFile(std::string streamId, int64_t startTime) { return VideoFileInfo(); };
    virtual VideoFileInfo getRecordFileInfo(std::string streamId, int64_t startTime) { return VideoFileInfo(); };
    virtual int getAllStreams(std::vector<shared_ptr<StreamInfo>> &streamInfo, const std::string &deviceId) { return -1; };
    virtual int getAllSensors(vector<shared_ptr<SensorInfo>> &deviceInfo, const std::string &deviceId) { return -1; };
    virtual bool isSensorExists(const shared_ptr<SensorInfo> &in_device, const std::string &deviceId) { return false; };
    virtual shared_ptr<SensorInfo> findExistingSensor(const shared_ptr<SensorInfo> &in_device, const std::string &deviceId) { return nullptr; };
    virtual shared_ptr<SensorInfo> searchSensorAndGetSensorInfo(const string &searchSensorId, const std::string &deviceId) { return {}; };
    virtual std::vector<VideoRecordDBColumns> getAllVideoRecordFilePaths() { return {}; };
    virtual std::vector<VideoRecordDBColumns> getVideoRecordFilePathsSensorIdBased(string sensorId, int64_t startTime, int64_t endTime) { return {}; };
    virtual std::vector<VideoRecordDBColumns> getVideoRecordFilePathsIdBased(string id) { return {}; };
    virtual int setDbVersion(DbDetailsColumns &row) { return -1; };
    virtual DbDetailsColumns getDbVersion() { return DbDetailsColumns(); };
    virtual int updateFileProtectionInDb(bool fileProtection, string filePath) { return -1; };
    virtual int updateFilesProtectionInDb(bool fileProtection, const std::vector<string>& filePaths) { return -1; };
    virtual int resetProtectedFlagsInDb() { return -1; };
    virtual std::vector<VideoRecordDBColumns> getProtectedFilesFromDB() { return {}; };
    virtual void createDatabaseTables() {};
    virtual VmsErrorCode getMainStreamFromDB(shared_ptr<StreamInfo> &mainStream, const SensorDetailsDBColumns &sensorDetails) { return VmsErrorCode::NoError; };
    virtual VmsErrorCode getSubStreamFromDB(shared_ptr<StreamInfo> &subStream, const SensorStreamsDBColumns &streamDetails, const string device_name) { return VmsErrorCode::NoError; };
    virtual VmsErrorCode getSensorInfoFromDB(shared_ptr<SensorInfo> &deviceInfo, const SensorDetailsDBColumns &sensorDetails) { return VmsErrorCode::NoError; };
    virtual uint64_t getTotalCurrentRecordSize() { return 0; };
    virtual int setRecordingStatus(const std::string &streamId, RecordState new_status, const std::optional<string> &sensorId) { return -1; };
    virtual VmsErrorCode getRecordingStatus(std::map<std::string, RecordingStatusDBColumns, std::less<>> &allStatus, const std::optional<string> &streamId) { return VmsErrorCode::NoError; };
    virtual vector<SensorDetailsDBColumns> readAllSensorSatus(string deviceId) { return {}; }
    virtual VmsErrorCode getSensorIdsWithRecordingTimelines(std::unordered_set<string> &sensorIds) { return VmsErrorCode::NoError; };
    virtual int updateStreamInfo(string streamId, string proxyUrl, string replayUrl, std::pair<StreamStatus, string> status) { return -1; };
    virtual vector<SensorStreamsDBColumns> readAllStreams() { return {}; };
    virtual int updateObjectIdInDb(const std::string& objectId, const std::string& filePath) { return -1; };
    virtual int updateFileProtectionAndObjectIdInDb(bool fileProtection, const std::string& objectId, const std::string& filePath) { return -1; };
    virtual string searchSensorFileIdBased(const string &id) { return {}; };
    
    // Temp Files operations
    virtual int insertTempFileRecord(nv_vms::TempFilesDBColumns &row) { return -1; };
    virtual int deleteTempFileRecord(const std::string& filePath) { return -1; };
    virtual std::vector<nv_vms::TempFilesDBColumns> getAllTempFiles() { return {}; };
    virtual int cleanupTempFileRecords(int64_t olderThanTimestamp) { return -1; };
    virtual nv_vms::TempFilesDBColumns findTempFileByStreamAndTime(
        const std::string& deviceId, const std::string& streamId,
        int64_t startTimeMs, int64_t endTimeMs,
        const std::string& fileType = "",
        const std::string& containerFormat = "") { return {}; };
    virtual int updateTempFileExpiry(
        const std::string& filePath, int64_t newExpiryTimestamp) { return -1; };

    virtual int queryCrashedRecordings(std::vector<VideoRecordDBColumns> &rows) { return 0; };

#ifdef UNIT_TEST
    virtual vector<VideoRecordDBColumns> getLastRecordVideoRecord(string streamId)
    {
        return {};
    };
#endif
};
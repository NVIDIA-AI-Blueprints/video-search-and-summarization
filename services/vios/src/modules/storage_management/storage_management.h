/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <vector>
#include <unordered_map>
#include <jsoncpp/json/json.h>
#include <Scheduler.h>
#include <unordered_set>
#include <thread>
#include <atomic>
#include "device_manager.h"
#include "event_loop.h"
#include "database.h"
#include "vstmodule.h"
#include "storage_management.h"
#include "unified_storage_manager.h"
#include "unified_storage_reader.h"
#include "unified_storage_manager_factory.h"
#include "unified_storage_manager_utils.h"
#include "unified_storage_reader_utils.h"
#include "VideoGeneratorTaskManager.h"
#include "TempFileScheduler.h"
#include <optional>

using namespace std;

inline constexpr const char* CONTAINER_FORMAT_QUICKTIME = "Quicktime";
inline constexpr const char* CONTAINER_FORMAT_MATROSKA = "Matroska";

inline constexpr const char* STORAGE_FILE_API = "/api/v1/storage/file/*";
#define FILE_DOWNLOAD_HEADER(fileName) "Content-Disposition: attachment;filename=" + fileName +"\r\n";

inline constexpr const char* STORAGE_API = "/api/v1/storage/*";
inline constexpr const char* STORAGE_FILE_API_PREFIX = "/api/v1/storage/file";
inline constexpr const char* STORAGE_FILEPATH_API_PREFIX = "/api/v1/storage/file/path";
inline constexpr const char* STORAGE_API_PREFIX = "/api/v1/storage";

namespace nv_vms
{
    class StorageManagement : public IVstModule
    {
    public:
            IVstModule* createStorageManagementObject();
            void deleteStorageManagementObject( IVstModule* object );

            StorageManagement(const string deviceType, const string deviceId, std::shared_ptr<DeviceManager> deviceMngr);
            string getDeviceTypeName() { return m_sensorType; }

            int deleteFilesByTime(const string stream_id, const int64_t startTime, const int64_t endTime, uint32_t &spaceSaved);
            int getCurrentUsedStorageSize(unordered_map<string, string> &cameraIdStatusMap, double &gbPerDay, const std::vector<string>& streamList, const string requiredTimelines, Json::Value &response);
            void StorageMonitorTask();
            int deleteOldMediaFiles(uint64_t &deletionSize);
            void addFileInProtectList(string &filePath, bool remove);
            // Remove the temp_files entry (symlink) associated with the
            // given source recording, so the link produced by the
            // /url?fullFile=true fast path is not left dangling when the
            // source recording itself is deleted (explicit DELETE API or
            // aging job). Independently muxed/transcoded clips that share
            // the same time window are intentionally left in place.
            void deleteTempLinksForGivenFile(const VideoRecordDBColumns& row);
            void addFilesInProtectList(std::vector<string>& filePaths, bool protect);
            bool isFileProtected(const string& file_path);
            void updateStorageSize(uint64_t frameSize, bool flag);
            uint64_t deleteMediaFile(const string file_name);
            void sendCurrentUsedStorageSizeToPrometheus();
            uint64_t getCurrentUsedStorageSize();
            void checkandCreateFreeSpaceInStorage(size_t newDataSize);
            bool checkStorageCapacity(size_t requiredCapacity);
            VmsErrorCode getStorageConfiguration(const Json::Value &, Json::Value &response);
            VmsErrorCode getVersion(string& version);
            VmsErrorCode getFileMetadata(const Json::Value &req_info, Json::Value &response);
            VmsErrorCode handleMediaFileDownload(const string &filePath, struct mg_connection *conn);
            VmsErrorCode handleMediaURLRequest(const Json::Value& req_info, Json::Value &response, struct mg_connection *conn);
            VmsErrorCode handleActiveTaskRequest(VideoGeneratorTaskManager* taskManager, const std::string& taskId,
                                                 const std::string& filename, bool isStreamable,
                                                 struct mg_connection* conn, Json::Value& response);
            VmsErrorCode handleBlockingTaskWait(VideoGeneratorTaskManager* taskManager, const std::string& taskId,
                                                struct mg_connection* conn, Json::Value& response);
            VmsErrorCode handleStreamingTaskRequest(VideoGeneratorTaskManager* taskManager, const std::string& taskId,
                                                    const std::string& filename, struct mg_connection* conn,
                                                    Json::Value& response);
            VmsErrorCode handleCompletedTaskRequest(VideoGeneratorTaskManager* taskManager, const std::string& taskId,
                                                    const std::string& filename, struct mg_connection* conn,
                                                    Json::Value& response);
            void serveMediaFile(struct mg_connection* conn, const std::string& filePath);
            VmsErrorCode parseStorageUrl(const std::string& requestApi, std::string& filename, Json::Value& response);
            std::string extractTaskId(const std::string& filename);
            const std::string& getDeviceId() const { return m_deviceId; }
            TempFileScheduler& getImageCleanupScheduler() { return *m_imageCleanupScheduler; }
            VmsErrorCode processUploadMetadata(const Json::Value& metadata, const string& filePath, Json::Value& response);
            VmsErrorCode getUsedStorageSize(const Json::Value& req_info, Json::Value &response);
            VmsErrorCode deleteFilesByTime(const Json::Value& req_info, Json::Value &response);
            VmsErrorCode getSpecificStreamRecordSize(const string& stream_id, Json::Value& stream_record_size);
            VmsErrorCode addOrRemoveFileInProtectList(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
            VmsErrorCode deleteFilesByNames(const Json::Value& req_info, Json::Value &response);
            VmsErrorCode getStorageInfo(const Json::Value& req_info, Json::Value &response);
            VmsErrorCode getProtectedFiles(const Json::Value& req_info, Json::Value &response);
            void getInvalidFilesIfAny(vector<string> fileList, vector<string>& invalidFileList);
            VmsErrorCode doAging(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
            VmsErrorCode updateStorageSize(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
            void storageManagementApis();
            VmsErrorCode handleStorageFileAPIrequest(const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn);
            VmsErrorCode checkStorageCapacity(const Json::Value & req_info, const Json::Value &in, Json::Value &response);
            VmsErrorCode importFileFromCloud(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
            VmsErrorCode listCloudFiles(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
            VmsErrorCode listLocalFiles(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
            VmsErrorCode getFilePath(const string &streamId, const int64_t startTime, const int64_t endTime, Json::Value &response, bool get_metadata);
            VmsErrorCode getFilePathSensorIdBased(const string &sensorId, const int64_t startTime, const int64_t endTime, Json::Value &response, bool get_metadata);
            VmsErrorCode getFilePathIdBased(const string &id, Json::Value &response, bool get_metadata);
            VmsErrorCode getFileListSensorIdBased(const string &sensorId, const int offset, const int limit, Json::Value &response);
            VmsErrorCode getRecordTimelines(const string streamId, const string startTime,
                                    const string endTime, Json::Value &response);
            VmsErrorCode GetAllRecordTimelines(const Json::Value& req_info, Json::Value &out);
            int deleteSensorDetails(VideoRecordDBColumns& row);

            static void cleanupExpiredFile(const std::string& filePath);
            static void cleanupExpiredAsyncTask(const std::string& taskId, const std::string& filePath);

            // Event method to send events for file sensors
            void notifyFileSensorEvents();

            // Cloud storage scanning and auto-import at bootup
            void scanAndImportCloudFiles();
            void scanAndImportCloudFilesBackground();

            std::shared_ptr<UnifiedStorageReader> getUnifiedStorageReader() { return m_unifiedStorageReader; }
            std::shared_ptr<UnifiedStorageManager> getUnifiedStorageManager() { return m_unifiedStorageManager; }

            ~StorageManagement()
            {
                m_imageCleanupScheduler.reset();
                m_videoCleanupScheduler.reset();

                if (m_cloudScanThread.joinable())
                {
                    LOG(info) << "Stopping cloud storage scanning thread..." << endl;
                    m_cloudScanShouldStop.store(true);
                    m_cloudScanThread.join();
                    LOG(info) << "Cloud storage scanning thread stopped" << endl;
                }

                m_storage.reset();
                m_monitoring.reset();
            }
            static uint64_t getActualStorageCapacity(const std::string& path);

        private:
            std::unique_ptr<Bosma::Scheduler> m_storage;
            std::mutex m_storageMutex;
            std::mutex m_muxerMutex;
            uint64_t m_currentUsedStorage;
            std::unique_ptr<Bosma::Scheduler> m_monitoring = nullptr;

            std::unique_ptr<TempFileScheduler> m_videoCleanupScheduler;
            std::unique_ptr<TempFileScheduler> m_imageCleanupScheduler;
            std::string m_sensorType;
            std::string m_deviceId;
            std::shared_ptr<DeviceManager> m_deviceManager;
            static uint64_t m_cachedStorageCapacity;
            static bool m_isStorageCapacityInitialized;

            // Unified storage components
            std::shared_ptr<UnifiedStorageManager> m_unifiedStorageManager = nullptr;
            std::shared_ptr<UnifiedStorageReader> m_unifiedStorageReader = nullptr;
            std::atomic<bool> m_cloudStorageEnabled{false};

            // Cloud storage scanning thread
            std::thread m_cloudScanThread;
            std::atomic<bool> m_cloudScanInProgress{false};
            std::atomic<bool> m_cloudScanShouldStop{false};

            int getStreamRecordSize(const string stream_id, size_t &videoSize);
            void initialiseUsedStorageSize();
            bool isReceivedFilesInvalid(vector<string> fileList);
            unordered_set<string> getProtectedFilesList();
            VmsErrorCode downloadFileFromCloud(const string& bucketName, const string& objectKey,
                                           const string& localFilePath, const string& region,
                                           const string& accessKeyId, const string& secretAccessKey);
            VmsErrorCode listCloudObjects(const string& bucketName, const string& prefix,
                                      const string& region, const string& accessKeyId,
                                      const string& secretAccessKey, Json::Value& response);
            VmsErrorCode parseS3XMLResponse(const string& xmlContent, const string& bucketName,
                                           const string& prefix, Json::Value& response);

            // Unified storage initialization and management
            bool initUnifiedStorageReader();
            bool initUnifiedStorageManager();
            DeleteResult  deleteFileWithStatus(const string& filePath, const string& objectId = "", bool isCloudFile = false);
            bool isCloudStorageEnabled() const;
            bool isFileExist(const string& filePath, string objectId = "");

            // Video download and URL generation functions
            VmsErrorCode HandleFileDownload(const string& queryString, const string& streamId, Json::Value& response,
                                           struct mg_connection* conn, bool isURLRequested = false);
            VmsErrorCode generateReplayVideoUrlAsync(const VideoGenerationParam& params, Json::Value& response);
            VmsErrorCode generateReplayVideoUrlSync(const VideoGenerationParam& params, Json::Value& response);

            // Helper methods for video URL generation
            struct VideoUrlGenerationContext {
                int expiryMinutesInt;
                string baseUrl;
                shared_ptr<SensorInfo> sensor;
                string taskId;
                string extension;
                string webRoot;
                string outputFilePath;
                string videoUrl;
            };
            VmsErrorCode setupVideoUrlGenerationContext(const VideoGenerationParam& params, VideoUrlGenerationContext& context, Json::Value& response);
            bool tryReuseCachedTempFile(const VideoGenerationParam& params, VideoUrlGenerationContext& context, Json::Value& response);
            VmsErrorCode recordTempFileInDatabase(const VideoUrlGenerationContext& context, const VideoGenerationParam& params,
                                                 const string& actualFilePath, int64_t fileSize = 0);
            void buildVideoUrlResponse(const VideoUrlGenerationContext& context, const VideoGenerationParam& params, Json::Value& response);

            // Full-file fast path: when the requested clip covers an entire stored recording
            // (or when the caller passes fullFile=true), we can skip remux/mux and serve the
            // raw file directly (download API) or symlink it into temp_files (URL API).
            struct FullFileMatch {
                bool        eligible{false};
                std::string filePath;      // Absolute path of the stored recording
                std::string objectId;      // VideoRecordDBColumns.object_id_value
                uint64_t    startTimeMs{0};
                uint32_t    durationMs{0};
                std::string container;     // e.g. "mp4", "mkv" (derived from file extension)
            };
            FullFileMatch tryFindFullFileMatch(const std::string& sensorId,
                                               const std::string& id,
                                               int64_t startMs,
                                               int64_t endMs,
                                               const std::string& requestedContainer,
                                               const std::string& transcode,
                                               const std::string& enableOverlay,
                                               const std::string& uselibav,
                                               const std::string& disableAudio,
                                               const std::string& frameRate,
                                               bool  fullFileFlag);
            VmsErrorCode serveFullFileDownload(const FullFileMatch& match,
                                               const std::string& downloadFileName,
                                               struct mg_connection* conn,
                                               Json::Value& response);
            VmsErrorCode generateFullFileUrl(const FullFileMatch& match,
                                             const VideoGenerationParam& params,
                                             Json::Value& response);

        public:


    };

    inline StorageManagement* GET_STORAGE_MNGT()
    {
        return static_cast<StorageManagement*>(ModuleLoader::getInstance()->getStorageMngtInstance());
    }
} // nv_vms
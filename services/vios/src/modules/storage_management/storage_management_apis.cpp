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

#include "storage_management_utils.h"
#include "storage_management.h"
#include "database.h"
#include "logger.h"
#include <chrono>
#include "vst_common.h"
#include <fstream>
#include <thread>
#include <sys/stat.h>
#include <filesystem>
#include <atomic>
#include <memory>
#include <random>
#include "VideoGeneratorTaskManager.h"
#include "HttpServerRequestHandler.h"
#include "vstmodule.h"
#include "health_probes.h"

using namespace std;

static string gStorageManagementApiList = R"([
        {"method": "GET - To get total used storage size and used storage size for each recorded streams", "endpoint": "/api/v1/storage/size"},
        {"method": "GET - To get total used storage size by the specified stream", "endpoint": "/api/v1/storage/{streamId}"},
        {"method": "GET - To get storage management configurations", "endpoint": "/api/v1/storage/configuration"},
        {"method": "GET - To get version", "endpoint": "/api/v1/storage/version"},
        {"method": "GET - To get supported API list in the storage management", "endpoint": "/api/v1/storage/help"},
        {"method": "GET - To get metadata of the media file", "endpoint": "/api/v1/storage/file/mediainfo"},
        {"method": "POST - To upload a media file", "endpoint": "/api/v1/storage/file"},
        {"method": "PUT - To upload a media file with filename and timestamp", "endpoint": "/api/v1/storage/file/{filename}/{timestamp}"},
        {"method": "GET - To download a media files for the specified stream", "endpoint": "/api/v1/storage/file/{streamId}"},
        {"method": "GET - To get temporary URL for accessing stored video files", "endpoint": "/api/v1/storage/file/{streamId}/url"},
        {"method": "DELETE - To delete media files and specified stream", "endpoint": "/api/v1/storage/file/{streamId}"},
        {"method": "DELETE - To delete a media file by unique id", "endpoint": "/api/v1/storage/file/{id}"},
        {"method": "POST - To add/remove files into/from the protect list, It will protect a file from deletion", "endpoint": "/api/v1/storage/file/protect"},
        {"method": "DELETE - To delete a muliple media files", "endpoint": "/api/v1/storage/file"},
        {"method": "GET - To get storage information i.e. Total, Used and Available storage space", "endpoint": "/api/v1/storage/info"},
        {"method": "GET - To get protected file list", "endpoint": "/api/v1/storage/file/protected"},
        {"method": "POST - To import a media file from AWS S3", "endpoint": "/api/v1/storage/file/import"},
        {"method": "GET - To list files from AWS S3 bucket", "endpoint": "/api/v1/storage/file/list"},
        {"method": "GET - To get list of media files for specified sensor", "endpoint": "/api/v1/storage/file/{sensorId}/list"},
        {"method": "GET - To serve static video files and async-generated videos", "endpoint": "/storage/{filename}"},
        {"method": "GET - To get a picture snapshot for a stream at a given time", "endpoint": "/api/v1/storage/stream/{streamId}/picture"},
        {"method": "GET - To get a temporary URL for a picture snapshot", "endpoint": "/api/v1/storage/stream/{streamId}/picture/url"},
        {"method": "GET - To get the list of available streams", "endpoint": "/api/v1/storage/streams"}
])";

/* Supporting older downlaod API for backward campatibility */
constexpr const char* OLDER_FILE_DOWNLOAD_API_PREFIX = "/api/v1/file";
constexpr const char* OLDER_FILE_DOWNLOAD_API = "/api/v1/file/*";

void StorageManagement::storageManagementApis()
{
    m_func["/api/v1/storage/configuration"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }

        return storageMngt->getStorageConfiguration(req_info, response);
    };

    m_func["/api/v1/storage/version"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }

        string storage_mngt_version;
        storageMngt->getVersion(storage_mngt_version);
        response["storage_management_version"] = storage_mngt_version;
        return VmsErrorCode::NoError;
    };

    m_func["/api/v1/storage/help"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        Json::CharReaderBuilder builder;
        std::istringstream iss(gStorageManagementApiList);

        std::string errs;
        if (!Json::parseFromStream(builder, iss, &response, &errs))
        {
            LOG(error) << "Failed to parse the API list JSON string: " << errs << endl;
        }
        return VmsErrorCode::NoError;
    };

    m_func["/v1/live"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return VmsErrorCode::NoError;
    };

    m_func["/v1/ready"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_health_probes::checkReadinessProbe(conn, response);
    };

    m_func["/v1/startup"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        return vst_health_probes::checkCivetWebServerRunning(conn, response);
    };

    m_func["/api/v1/storage/file/mediainfo"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }

        return storageMngt->getFileMetadata(req_info, response);
    };

    m_func["/api/v1/storage/size"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }

        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return storageMngt->getUsedStorageSize(req_info, response);
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
            return VmsErrorCode::VMSNotSupportedError;
        }
    };

    m_func["/api/v1/storage/file/protect"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return storageMngt->addOrRemoveFileInProtectList(req_info, in, response);
    };

    m_func["/api/v1/storage/info"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return storageMngt->getStorageInfo(req_info, response);
    };

    m_func["/api/v1/storage/file/protected"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return storageMngt->getProtectedFiles(req_info, response);
    };



    m_func["/api/v1/storage/aging"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return storageMngt->doAging(req_info, in, response);
    };

    m_func["/api/v1/storage/size/update"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return storageMngt->updateStorageSize(req_info, in, response);
    };

    m_func["/api/v1/storage/capacity"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return storageMngt->checkStorageCapacity(req_info, in, response);
    };

    m_func["/api/v1/storage/file/list"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }
        return storageMngt->listLocalFiles(req_info, in, response);
    };

    m_func["/api/v1/storage/streams"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return vst_common::getSensorStreamListFromDB(m_deviceManager, response);
        }
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        return VmsErrorCode::MethodNotAllowedError;
    };

    m_func["/api/v1/storage/timelines"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &out, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }

        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return storageMngt->GetAllRecordTimelines(req_info, out);
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, out, "Request Method is not supported");
            return VmsErrorCode::VMSNotSupportedError;
        }
    };

    /* Supporting older download API for the backward compatibility */
    m_func[OLDER_FILE_DOWNLOAD_API] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return handleStorageFileAPIrequest(req_info, in, response, conn);
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
            return VmsErrorCode::VMSNotSupportedError;
        }
    };

    m_func[STORAGE_API] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        const string requestApi = req_info.get("url", EMPTY_STRING).asString();
        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (requestApi.empty() || requestMethod == UNKNOWN_STRING)
        {
            LOG(error) << "Malformed HTTP request" << endl;
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
            return VmsErrorCode::InvalidParameterError;
        }

        if ((requestApi.find(STORAGE_FILE_API_PREFIX) == 0) &&
            (iequals(requestMethod, "post") || iequals(requestMethod, "put")))
        {
            VmsErrorCode maxSensorsLimitResult = checkMaxSensorsLimit(m_deviceManager, response);
            if (maxSensorsLimitResult != VmsErrorCode::NoError)
            {
                LOG(error) << "Maximum number of streams limit reached" << endl;
                return maxSensorsLimitResult;
            }
        }

        if ((requestApi == STORAGE_FILE_API_PREFIX) && (iequals(requestMethod, "delete")))
        {
            StorageManagement* storageMngt = GET_STORAGE_MNGT();
            if (storageMngt == nullptr)
            {
                LOG(error) << "Storage Management module is not loaded" << endl;
                return VmsErrorCode::MethodNotAllowedError;
            }
            return storageMngt->deleteFilesByNames(req_info, response);
        }
        else
        {
            return handleStorageFileAPIrequest(req_info, in, response, conn);
        }
    };

    m_func["/storage/*"] = [this](const Json::Value& req_info, const Json::Value &in,
                                Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        StorageManagement* storageMngt = GET_STORAGE_MNGT();
        if (storageMngt == nullptr)
        {
            LOG(error) << "Storage Management module is not loaded" << endl;
            return VmsErrorCode::MethodNotAllowedError;
        }

        const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
        if (iequals(requestMethod, "get"))
        {
            return storageMngt->handleMediaURLRequest(req_info, response, conn);
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
            return VmsErrorCode::VMSNotSupportedError;
        }
    };

}

VmsErrorCode StorageManagement::handleStorageFileAPIrequest(const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestApi = req_info.get("url", EMPTY_STRING).asString();
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    const string queryString = req_info.get("query", EMPTY_STRING).asString();
    if (requestApi.empty() || requestMethod == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    LOG(info) <<"Storage Management API handler requestApi:" << requestApi << " requestMethod:" << requestMethod << " queryString:" << queryString <<  std::endl;

    /* Get file path based on unique id */
    if (requestApi.find(STORAGE_FILEPATH_API_PREFIX) != string::npos)
    {
        if (iequals(requestMethod, "get"))
        {
            string id;
            string metadata = "false";  // Default value
            CivetServer::getParam(queryString, "id", id);
            CivetServer::getParam(queryString, "metadata", metadata);
            bool get_metadata = (metadata == "true");
            if (id.empty())
            {
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Id is not provided");
                ret = VmsErrorCode::InvalidParameterError;
                return ret;
            }
            ret = getFilePathIdBased(id, response, get_metadata);
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
            ret = VmsErrorCode::VMSNotSupportedError;
        }
    }
    /* Upload API*/
    else if (iequals(requestApi, STORAGE_FILE_API_PREFIX) && iequals(requestMethod, "post"))
    {
        ret = handleFileUpload(m_deviceManager, mg_get_request_info(conn), conn, response);
    }
    /* Download & Delete API - Handle both current and legacy API endpoints */
    // STORAGE_FILE_API_PREFIX = "/api/v1/storage/file" (current API - supports PUT upload, GET download & DELETE)
    // OLDER_FILE_DOWNLOAD_API_PREFIX = "/api/v1/download/file" (legacy API for backward compatibility - GET download only)
    else if ((requestApi.find(STORAGE_FILE_API_PREFIX) != string::npos) || (requestApi.find(OLDER_FILE_DOWNLOAD_API_PREFIX) != string::npos))
    {
        // Determine which API base is being used
        // STORAGE_FILE_API = "/api/v1/storage/file"
        // OLDER_FILE_DOWNLOAD_API = "/api/v1/download/file"
        string baseAPI = (requestApi.find(STORAGE_FILE_API_PREFIX) != string::npos) ?
                         STORAGE_FILE_API : OLDER_FILE_DOWNLOAD_API;
        string start_time, end_time;
        CivetServer::getParam(queryString, "startTime", start_time);
        CivetServer::getParam(queryString, "endTime", end_time);

        // Extract path after the base API URL (with bounds checking)
        // Note: baseAPI may include wildcard (*), so we need to find the actual base path
        // Example: "/api/v1/storage/file/sensorId123/path" -> "/sensorId123/path"
        string pathAfterAPI;
        string actualBase = baseAPI;

        // Remove wildcard (*) from base if present
        if (!actualBase.empty() && actualBase.back() == '*')
        {
            actualBase = actualBase.substr(0, actualBase.length() - 1);
        }

        if (requestApi.length() > actualBase.length())
        {
            pathAfterAPI = requestApi.substr(actualBase.length());
        }
        LOG(info) << "Original Base API: '" << baseAPI << "' (length: " << baseAPI.length() << ")" << std::endl;
        LOG(info) << "Actual Base API: '" << actualBase << "' (length: " << actualBase.length() << ")" << std::endl;
        LOG(info) << "Request API: '" << requestApi << "' (length: " << requestApi.length() << ")" << std::endl;
        LOG(info) << "API path after base: '" << pathAfterAPI << "'" << std::endl;

        // Initialize path components that will be extracted
        string streamId;       // Will contain sensorId for Version 2 API (GET/DELETE)
        string action;         // Will contain action like "path", "delete", etc.
        string fileId;         // Will contain file unique id when route is /file/{id}
        string uploadFilename; // Will contain file name for PUT raw upload
        string uploadTimestamp; // Will contain timestamp for PUT raw upload
        
        // Check if there are path parameters (Version 2 API)
        // Version 1: "/api/v1/storage/file" or "/api/v1/storage/file/" -> no path params
        // Version 2: "/api/v1/storage/file/sensorId123" -> has path params
        if (!pathAfterAPI.empty() && pathAfterAPI != "/")
        {
            // Clean up the path by removing leading slash if present
            // "/sensorId123/action" -> "sensorId123/action"
            if (pathAfterAPI[0] == '/')
            {
                pathAfterAPI = pathAfterAPI.substr(1);
            }
            
            // Split path into components:
            //  - Download V2: "sensorId123/action" -> ["sensorId123", "action"]
            //  - Delete by id: "{id}"
            //  - Raw upload: "{filename}" or "{filename}/{timestamp}"
            vector<string> pathParts = splitString(pathAfterAPI, "/");

            // Defense in depth: reject any URL whose path contains a parent-
            // directory segment ('..'/'.') OR an empty segment (which means
            // the client sent something like "//etc/hosts"). Without this
            // guard the parser below silently treats the second segment as a
            // timestamp, so a payload like '/etc/hosts' would be stored under
            // filename "etc" and timestamp "hosts" — confusing and unsafe.
            for (const auto& part : pathParts)
            {
                if (part == ".." || part == "." || part.empty())
                {
                    const string label = part.empty() ? "<empty>" : part;
                    LOG(error) << "Rejecting malformed path segment '" << label
                               << "' in URL: " << pathAfterAPI << endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response,
                        ("Invalid path segment in URL: '" + label +
                         "' (parent-directory traversal or empty segments "
                         "not allowed)").c_str());
                    return VmsErrorCode::InvalidParameterError;
                }
            }

            if (!pathParts.empty() && !pathParts[0].empty())
            {
                // For DELETE without time range and single segment, treat as file id
                if (iequals(requestMethod, "delete") && pathParts.size() == 1 && start_time.empty() && end_time.empty())
                {
                    fileId = pathParts[0];
                    LOG(info) << "Parsed - FileId: " << fileId << endl;
                }
                // For PUT raw upload, first segment is filename and second (optional) is timestamp
                else if (iequals(requestMethod, "put") &&
                         (requestApi.find(STORAGE_FILE_API_PREFIX) != string::npos))
                {
                    uploadFilename = pathParts[0];
                    if (pathParts.size() >= 2)
                    {
                        uploadTimestamp = pathParts[1];
                        // The two-segment PUT form is `/file/{filename}/{timestamp}`.
                        // If the URL-derived timestamp is not a valid ISO-8601
                        // string, the client likely sent a malformed payload
                        // (e.g. `/file//etc/hosts` which normalises to
                        // `/file/etc/hosts` and would otherwise silently store
                        // a file named "etc" with timestamp "hosts"). Reject
                        // such requests with InvalidParameterError.
                        if (!validateISOTime(uploadTimestamp))
                        {
                            LOG(error) << "Rejecting non-ISO URL timestamp '"
                                       << uploadTimestamp << "' for filename '"
                                       << uploadFilename << "'" << endl;
                            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response,
                                ("URL path timestamp '" + uploadTimestamp +
                                 "' is not a valid ISO-8601 timestamp").c_str());
                            return VmsErrorCode::InvalidParameterError;
                        }
                    }
                    LOG(info) << "Parsed - Upload Filename: " << uploadFilename
                              << ", Timestamp: " << uploadTimestamp << endl;
                }
                else
                {
                    streamId = pathParts[0];
                    action = pathParts.size() >= 2 ? pathParts[1] : "";
                    LOG(info) << "Parsed - StreamId: " << streamId << ", Action: " << action << endl;
                }
            }
        }
        else
        {
            // No path parameters found - this is Version 1 API (query parameter based)
            // Example: "/api/v1/storage/file?id=uniqueId123&startTime=..."
            LOG(info) << "No path parameters - query-based API" << endl;
        }

        if (iequals(requestMethod, "put") &&
            (requestApi.find(STORAGE_FILE_API_PREFIX) != string::npos))
        {
            if (uploadFilename.empty())
            {
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Filename is missing in URL");
                ret = VmsErrorCode::InvalidParameterError;
            }
            else
            {
                std::string querySensorId;
                std::string queryTimestamp;
                bool isLegacyUpload = true;

                CivetServer::getParam(queryString, "sensorId", querySensorId);
                CivetServer::getParam(queryString, "timestamp", queryTimestamp);
                
                if (!querySensorId.empty() || !queryTimestamp.empty())
                {
                    if (uploadTimestamp.empty())
                    {
                        isLegacyUpload = false;
                        if (!queryTimestamp.empty())
                        {
                            uploadTimestamp = queryTimestamp;
                        }
                    }
                }

                LOG(info) << "Handling raw file upload - filename: " << uploadFilename
                          << ", timestamp: " << uploadTimestamp 
                          << ", sensorId: " << querySensorId 
                          << ", isLegacy: " << isLegacyUpload << endl;
                          
                ret = handleFileUpload(m_deviceManager, mg_get_request_info(conn), conn, response,
                                      true, uploadFilename, uploadTimestamp, querySensorId, isLegacyUpload);
            }
        }
        else if (iequals(requestMethod, "get"))
        {
            if (action.empty() || action == "url")
            {
                bool isURLRequested = (action == "url");
                ret = HandleFileDownload(queryString, streamId, response, conn, isURLRequested);
            }
            else if (action == "path")
            {
                string startTime;
                string endTime;
                bool isTimeCorrect = true;
                string metadata = "false";  // Default value
                CivetServer::getParam(queryString, "startTime", startTime);
                CivetServer::getParam(queryString, "endTime", endTime);
                CivetServer::getParam(queryString, "metadata", metadata);
                bool get_metadata = (metadata == "true");
                if (!startTime.empty() && !endTime.empty())
                {
                    /* this function returns true if inputs are valid */
                    isTimeCorrect = compareISOTime(startTime, endTime);
                }
                if (isTimeCorrect)
                {
                    // Get storage management instance
                    StorageManagement* storageMngt = GET_STORAGE_MNGT();
                    if (storageMngt == nullptr)
                    {
                        LOG(error) << "Storage Management module is not loaded" << endl;
                        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Storage Management module is not loaded");
                        return VmsErrorCode::MethodNotAllowedError;
                    }

                    // Convert time strings to epoch milliseconds
                    int64_t startTimeMs = getEpocTimeInMS(startTime);
                    int64_t endTimeMs = getEpocTimeInMS(endTime);

                    // Validate input: only providing end time is not allowed
                    if (startTime.empty() && !endTime.empty())
                    {
                        LOG(error) << "Only end time provided - start time is required" << endl;
                        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Only end time is provided");
                        return VmsErrorCode::InvalidParameterError;
                    }

                    // Handle different time range scenarios
                    if (startTime.empty())
                    {
                        // No time constraints - return all files
                        endTimeMs = std::numeric_limits<int64_t>::max();
                    }
                    else if (endTime.empty())
                    {
                        // Only start time provided - create minimal range for SQL query
                        endTimeMs = startTimeMs + 1;
                    }
                    // If both times provided, use them as-is

                    ret = storageMngt->getFilePathSensorIdBased(streamId, startTimeMs, endTimeMs, response, get_metadata);
                }
                else
                {
                    LOG(error) << "Invalid Start Time = ("  + startTime +  ") and End time = (" + endTime  + ")" << endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid Start Time = ("  + startTime +  ") and End time = (" + endTime  + ")");
                }
            }
            else if (action == "list")
            {
                string offsetStr;
                string limitStr;
                CivetServer::getParam(queryString, "offset", offsetStr);
                CivetServer::getParam(queryString, "limit", limitStr);

                int offset = 0;   // Default offset
                int limit = 0;    // Default: no limit (return all)

                // Parse offset if provided
                if (!offsetStr.empty())
                {
                    try
                    {
                        offset = std::stoi(offsetStr);
                    }
                    catch (const std::exception& e)
                    {
                        LOG(error) << "Invalid offset parameter: " << offsetStr << endl;
                        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid offset parameter");
                        ret = VmsErrorCode::InvalidParameterError;
                        return ret;
                    }
                }

                // Parse limit if provided
                if (!limitStr.empty())
                {
                    try
                    {
                        limit = std::stoi(limitStr);
                    }
                    catch (const std::exception& e)
                    {
                        LOG(error) << "Invalid limit parameter: " << limitStr << endl;
                        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid limit parameter");
                        ret = VmsErrorCode::InvalidParameterError;
                        return ret;
                    }
                }

                // Validate parameters
                if (offset < 0)
                {
                    LOG(error) << "Invalid offset parameter: " << offset << endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Offset must be non-negative");
                    ret = VmsErrorCode::InvalidParameterError;
                }
                else if (limit < 0)
                {
                    LOG(error) << "Invalid limit parameter: " << limit << endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Limit must be non-negative");
                    ret = VmsErrorCode::InvalidParameterError;
                }
                else
                {
                    // Get storage management instance
                    StorageManagement* storageMngt = GET_STORAGE_MNGT();
                    if (storageMngt == nullptr)
                    {
                        LOG(error) << "Storage Management module is not loaded" << endl;
                        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Storage Management module is not loaded");
                        return VmsErrorCode::MethodNotAllowedError;
                    }

                    ret = storageMngt->getFileListSensorIdBased(streamId, offset, limit, response);
                }
            }
            else
            {
                SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
                ret = VmsErrorCode::VMSNotSupportedError;
            }
        }
        else if((iequals(requestMethod, "delete")) && (requestApi.find(STORAGE_FILE_API_PREFIX) != string::npos))
        {
            LOG(info) << "Deleting file by id: " << fileId  << " or Start Time: " << start_time << " and End Time: " << end_time << endl;

            StorageManagement* storageMngt = GET_STORAGE_MNGT();
            if (storageMngt == nullptr)
            {
                LOG(error) << "Storage Management module is not loaded" << endl;
                string error_message = string("Storage Management module is not loaded");
                SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, error_message.c_str());
                return VmsErrorCode::MethodNotAllowedError;
            }

            /* Delete file by streamid for nvstreamer */
            if (storageMngt->getDeviceTypeName() == TYPE_STREAMER && start_time.empty() && end_time.empty())
            {
                ret = deleteFile(m_deviceManager, req_info, in, response);
            }
            /* Delete file by time range for recorded videos */
            else if (fileId.empty() && !start_time.empty() && !end_time.empty())
            {
                ret = storageMngt->deleteFilesByTime(req_info, response);
            }
            /* Delete file by unique id for external file upload */
            else if (!fileId.empty())
            {
                // DELETE /api/v1/storage/file/{id} -> reuse deleteFilesByNames via query param id
                Json::Value mutatedReq = req_info;
                string newQuery = req_info.get("query", EMPTY_STRING).asString();
                if (!newQuery.empty())
                {
                    newQuery += "&id=" + fileId;
                }
                else
                {
                    newQuery = "id=" + fileId;
                }
                mutatedReq["query"] = newQuery;

                StorageManagement* storageMngt = GET_STORAGE_MNGT();
                if (storageMngt == nullptr)
                {
                    LOG(error) << "Storage Management module is not loaded" << endl;
                    string error_message = string("Storage Management module is not loaded");
                    SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, error_message.c_str());
                    return VmsErrorCode::MethodNotAllowedError;
                }
                ret = storageMngt->deleteFilesByNames(mutatedReq, response);
            }
            else
            {
                SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
                ret = VmsErrorCode::VMSNotSupportedError;
            }
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
            ret = VmsErrorCode::VMSNotSupportedError;
        }
    }
    else if (requestApi.find(STORAGE_API_PREFIX) != string::npos)
    {
        string device_api(STORAGE_API);
        
        // Remove wildcard (*) from base if present (consistent with lines 413-424)
        string actualBase = device_api;
        if (!actualBase.empty() && actualBase.back() == '*')
        {
            actualBase = actualBase.substr(0, actualBase.length() - 1);
        }
        
        // Extract path after the base API URL with bounds checking
        string path;
        if (requestApi.length() > actualBase.length())
        {
            path = requestApi.substr(actualBase.length());
        }
        
        // Clean up the path by removing leading slash if present
        if (!path.empty() && path[0] == '/')
        {
            path = path.substr(1);
        }
        
        string action;
        string subAction;
        string streamId;

        LOG(info) << "API path: " << path << std::endl;
        vector<string> path_arr = splitString(path, "/");
        if (path_arr.size() > 0)
        {
            streamId = path_arr[0];
            action = path_arr.size() >= 2 ? path_arr[1] : "";
            subAction = path_arr.size() >= 3 ? path_arr[2] : "";
            if (path_arr[0] == "stream")
            {
                streamId = path_arr.size() >= 2 ? path_arr[1] : "";
                action = path_arr.size() >= 3 ? path_arr[2] : "";
                subAction = path_arr.size() >= 4 ? path_arr[3] : "";
            }
        }
        else
        {
            LOG(error) << "Requested API is not allowed" << std::endl;
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Requested API is not allowed");
            return VmsErrorCode::MethodNotAllowedError;
        }

        if (iequals(requestMethod, "get"))
        {
            if (action.empty() && !streamId.empty())
            {
                StorageManagement* storageMngt = GET_STORAGE_MNGT();
                if (storageMngt == nullptr)
                {
                    LOG(error) << "Storage Management module is not loaded" << endl;
                    string error_message = string("Storage Management module is not loaded");
                    SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, error_message.c_str());
                    return VmsErrorCode::MethodNotAllowedError;
                }

                ret = storageMngt->getSpecificStreamRecordSize(streamId, response);
                if (ret != VmsErrorCode::NoError)
                {
                    LOG(error) << "Failed to get recording size for streamId:" << streamId << endl;
                    SET_VMS_ERROR2(ret, response, "Failed to get recording size")
                }
            }
            else if (iequals(action, "timelines"))
            {
                string startTime;
                string endTime;
                CivetServer::getParam(queryString, "startTime", startTime);
                CivetServer::getParam(queryString, "endTime", endTime);
                StorageManagement* storageMngt = GET_STORAGE_MNGT();
                if (storageMngt == nullptr)
                {
                    LOG(error) << "Storage Management module is not loaded" << endl;
                    string error_message = string("Storage Management module is not loaded");
                    SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, error_message.c_str());
                    return VmsErrorCode::MethodNotAllowedError;
                }

                ret = storageMngt->getRecordTimelines(streamId, startTime, endTime, response);
                if (ret != VmsErrorCode::NoError)
                {
                    LOG(error) << "Failed to get recording timelines for streamId:" << streamId << endl;
                    SET_VMS_ERROR2(ret, response, "Failed to get recording timelines")
                }
            }
            else if (iequals(action, "picture"))
            {
                bool isURLRequested = iequals(subAction, "url");

                StorageManagement* storageMngt = GET_STORAGE_MNGT();
                if (storageMngt == nullptr)
                {
                    LOG(error) << "Storage Management module is not loaded" << endl;
                    SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Storage Management module is not loaded");
                    return VmsErrorCode::MethodNotAllowedError;
                }

                string sensorId = streamId;
                if (m_deviceManager)
                {
                    string resolved;
                    if (m_deviceManager->getSensorIdFromStreamId(streamId, resolved))
                    {
                        sensorId = resolved;
                    }
                }

                ret = vst_common::handlePictureAction(m_deviceManager, m_deviceManager->getDeviceId(),
                                                       sensorId, queryString, isURLRequested,
                                                       storageMngt->getImageCleanupScheduler(), response);
            }
            else
            {
                SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
                ret = VmsErrorCode::VMSNotSupportedError;
            }
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
            ret = VmsErrorCode::VMSNotSupportedError;
        }
    }
    else
    {
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        ret = VmsErrorCode::VMSNotSupportedError;
    }
    return ret;
}

VmsErrorCode StorageManagement::HandleFileDownload(const string& queryString, const string& streamId, Json::Value& response, struct mg_connection* conn, bool isURLRequested)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    string id;
    string sensor_type;
    shared_ptr<SensorInfo> sensor;
    CivetServer::getParam(queryString, "id", id);
    auto dbHelper = GET_DB_INSTANCE();
    if (streamId.empty())
    {
        string sensorId = dbHelper->searchSensorFileIdBased(id);
        if (sensorId.empty())
        {
            LOG(error) << "Sensor not found for ID: " << id << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Sensor not found for ID: " + id);
            return VmsErrorCode::InvalidParameterError;
        }
        sensor = dbHelper->searchSensorAndGetSensorInfo(sensorId, m_deviceManager->getDeviceId());
    }
    else
    {
        sensor = dbHelper->searchSensorAndGetSensorInfo(streamId, m_deviceManager->getDeviceId());
    }
    if (sensor != nullptr)
    {
        sensor_type = sensor->type;
    }
    else
    {
        LOG(warning) << "Sensor not found, continuing with discontinued sensor flow for streamId: " << streamId << endl;
    }

    string deviceName;
    string startTime;
    string endTime;
    string fileName;
    string fullLength;
    string container;
    string transcode;
    string uselibav;
    string disableAudio;
    string configuration;
    string enableOverlay = "false";
    OverlayBBoxParams olParams;
    deviceName = sensor ? sensor->name : "disconnected_device";
    bool isTimeCorrect = true;
    /* check start < end time */
    CivetServer::getParam(queryString, "startTime", startTime);
    CivetServer::getParam(queryString, "endTime", endTime);
    CivetServer::getParam(queryString, "fileName", fileName);
    CivetServer::getParam(queryString, "fullLength", fullLength);
    // fullLength must be empty (default) or a documented boolean string.
    // Reject anything else with InvalidParameterError so callers don't see
    // garbage values silently treated as 'false'. The reflected value is
    // truncated to a small bound so a hostile or oversized payload cannot
    // bloat logs or the error response body.
    if (!fullLength.empty() && fullLength != "true" && fullLength != "false")
    {
        constexpr size_t kMaxEcho = 32;
        const std::string echo = fullLength.size() > kMaxEcho
            ? fullLength.substr(0, kMaxEcho) + "..."
            : fullLength;
        LOG(error) << "Invalid fullLength value: '" << echo
                   << "' (expected 'true' or 'false')" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response,
            ("Invalid fullLength value '" + echo +
             "': expected 'true' or 'false'").c_str());
        return VmsErrorCode::InvalidParameterError;
    }
    CivetServer::getParam(queryString, "container", container);
    CivetServer::getParam(queryString, "transcode", transcode);
    CivetServer::getParam(queryString, "uselibav", uselibav);
    CivetServer::getParam(queryString, "disableAudio", disableAudio);
    CivetServer::getParam(queryString, "configuration", configuration);

    // Capture user-provided values BEFORE any auto-defaults/auto-fixups are
    // applied below. These originals are used by the full-file fast path to
    // decide eligibility: we only want to disqualify when the user
    // *explicitly* asked for a transformation (strip audio, change container).
    const string userDisableAudio = disableAudio;
    const string userContainer    = container;

    // Parse fullFile flag early: it relaxes the start/end-time requirement
    // below. When fullFile=true, the user is asking for the full file and
    // does not need to specify a time range.
    string fullFileStr;
    CivetServer::getParam(queryString, "fullFile", fullFileStr);
    const bool fullFileRequested = iequals(fullFileStr, "true");

    LOG(info) << startTime << " " << endTime << endl;
    LOG(warning) << "Audio: " << disableAudio << endl;
    LOG(verbose) << "Configuration: " << configuration << endl;

    container = container.empty() ? "mp4" : container;  // default mp4
    transcode = transcode.empty() ? "none" : transcode; // full, gop, none
    uselibav = uselibav.empty() ? "false" : uselibav;

    // Determine API version: Version 2 has streamId in path, Version 1 uses query params
    bool isVersion2 = !streamId.empty() && streamId != "/";

    if (isVersion2)
    {
        // Version 2: /api/v1/storage/file/sensorId - requires start/end time,
        // unless fullFile=true is set (whole-file fast path identifies the
        // recording from the sensor alone).
        LOG(warning) << "/api/v1/storage/file/sensorId - requires start/end time" << endl;
        if ((startTime.empty() || endTime.empty()) && !fullFileRequested)
        {
            LOG(error) << "Start and end time are required for sensorId: " << streamId << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Start and end time are required");
            return VmsErrorCode::InvalidParameterError;
        }
    }
    else
    {
        // Version 1: /api/v1/storage/file?id=... - requires id parameter
        LOG(error) << "/api/v1/storage/file?id=... - requires id parameter" << endl;
        if (id.empty())
        {
            LOG(error) << "Id is not provided for streamId: " << streamId << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Id is not provided");
            return VmsErrorCode::InvalidParameterError;
        }
    }
    if (!configuration.empty())
    {
        Json::Value configJson = stringToJson(configuration);
        LOG(verbose) << "Configuration JSON: " << configJson.toStyledString() << endl;

        if (configJson.isObject())
        {
            if (container.empty())
            {
                container = configJson.get("container", "mp4").asString();
            }
            if (disableAudio.empty())
            {
                disableAudio = configJson.get("disableAudio", "true").asString();
            }

            Json::Value overlayJson = configJson.get("overlay", EMPTY_STRING);
            if (overlayJson.isObject())
            {
                enableOverlay = "true";
                LOG(info) << "Overlay enabled in download pipeline" << endl;

                // Use the unified parsing function
                std::map<std::string, std::string> opts;
                setOverlayOptsBasedOnJson(opts, overlayJson);

                // Convert parsed options to olParams structure
                if (opts.count("overlayBbox") && opts.at("overlayBbox") == "true")
                {
                    olParams.m_enableBbox = true;

                    // Handle bboxShowAll
                    if (opts.count("bboxShowAll"))
                    {
                        bool showAll = opts.at("bboxShowAll") == "true";
                        if (showAll)
                        {
                            olParams.m_overlayIdList[BBOX].push_back("all");
                            olParams.m_overlayClassTypeList.push_back("all");
                        }
                        else
                        {
                            // Handle bboxObjectId
                            if (opts.count("bboxObjectId") && !opts.at("bboxObjectId").empty())
                            {
                                string bboxIds = opts.at("bboxObjectId");
                                auto tokens = splitString(bboxIds, ",");
                                for (const auto& token : tokens)
                                {
                                    if (!token.empty())
                                    {
                                        olParams.m_overlayIdList[BBOX].push_back(token);
                                    }
                                }
                            }

                            // Handle bboxClassType
                            if (opts.count("bboxClassType") && !opts.at("bboxClassType").empty())
                            {
                                string classTypes = opts.at("bboxClassType");
                                auto tokens = splitString(classTypes, ",");
                                for (const auto& token : tokens)
                                {
                                    if (!token.empty())
                                    {
                                        olParams.m_overlayClassTypeList.push_back(token);
                                    }
                                }
                            }

                            if (olParams.m_overlayIdList[BBOX].empty() && olParams.m_overlayClassTypeList.empty())
                            {
                                olParams.m_overlayIdList[BBOX].push_back("none");
                                olParams.m_overlayClassTypeList.push_back("none");
                            }
                            else if (olParams.m_overlayIdList[BBOX].empty())
                            {
                                olParams.m_overlayIdList[BBOX].push_back("all");
                            }
                            else if (olParams.m_overlayClassTypeList.empty())
                            {
                                olParams.m_overlayClassTypeList.push_back("all");
                            }
                        }
                    }

                    // Handle bbox ID display options
                    if (opts.count("bboxShowObjId"))
                    {
                        olParams.m_enableBboxId = opts.at("bboxShowObjId") == "true";
                    }
                    if (opts.count("bboxObjIdPosition"))
                    {
                        olParams.m_bboxIdPosition = (BBoxIdPosition)stringToInt(opts.at("bboxObjIdPosition"), MIDDLE);
                    }
                    if (opts.count("bboxObjIdTextColor"))
                    {
                        olParams.m_bboxIdColor = opts.at("bboxObjIdTextColor");
                    }
                    if (opts.count("bboxObjIdTextBGColor"))
                    {
                        olParams.m_bboxIdBgColor = opts.at("bboxObjIdTextBGColor");
                    }
                    if (olParams.m_bboxIdBgColor.empty() && opts.count("overlayColor"))
                    {
                        olParams.m_bboxIdBgColor = opts.at("overlayColor");
                    }
                }

                // Handle global overlay properties
                if (opts.count("overlayColor"))
                {
                    olParams.m_bboxColor = opts.at("overlayColor");
                }
                if (opts.count("overlayOpacity"))
                {
                    olParams.m_bboxOpacity = stringToInt(opts.at("overlayOpacity"), DEFAULT_BBOX_OPACITY);
                }
                if (opts.count("overlayThickness"))
                {
                    olParams.m_bboxThickness = stringToInt(opts.at("overlayThickness"), DEFAULT_BBOX_WIDTH);
                }
                if (opts.count("overlayDebug"))
                {
                    olParams.m_bboxDebug = opts.at("overlayDebug") == "true";
                }
                if (opts.count("overlayPose"))
                {
                    olParams.m_enablePose = opts.at("overlayPose") == "true";
                }
            }
        }
    }

    // Validate time parameters - supports both ISO format and millisecond integers
    if (!startTime.empty() && !endTime.empty())
    {
        // Try ISO format first
        isTimeCorrect = compareISOTime(startTime, endTime);

        // If ISO validation fails, try millisecond integer format
        if (!isTimeCorrect)
        {
            try {
                int64_t startMs = std::stoll(startTime);
                int64_t endMs = std::stoll(endTime);

                // Validate millisecond range
                if (startMs >= 0 && endMs >= 0 && startMs < endMs)
                {
                    // Convert to internal format for downstream processing
                    startTime = "ms:" + std::to_string(startMs);
                    endTime = "ms:" + std::to_string(endMs);
                    isTimeCorrect = true;
                    LOG(info) << "Using millisecond format - Start: " << startMs << "ms, End: " << endMs << "ms" << endl;
                }
            } catch (...) {
                LOG(error) << "Invalid time format - must be ISO (2025-01-01T10:00:00.000Z) or milliseconds (1000)" << endl;
            }
        }
    }

    /* Check if audio present & supported for the container */
    string audioEncoding;
    bool isCloudStream = false;
    shared_ptr<StreamInfo> stream = sensor ? sensor->getStream(streamId) : nullptr;
    if (stream != nullptr)
    {
        audioEncoding = stream->settings.audioEncoderValues.encoding;
        bool isAacPresent = audioEncoding.find("AAC") != string::npos;
        LOG(info) << "Stream audio encoding: " << audioEncoding << " isAacPresent: " << isAacPresent << endl;
        // Only audio is supported for mp4 container
        if (audioEncoding.empty() || (!isAacPresent && container == "mp4"))
        {
            LOG(info) << "Audio is not supported for container: " << container << endl;
            disableAudio = "true";
        }
        LOG(info) << "Stream live URL: " << stream->live_url << endl;
        if (stream->live_url.find("s3://") != string::npos || (stream->storageLocation == StreamStorageTypeCloud))
        {
            isCloudStream = true;
        }
    }

    string frameRate = "";
    frameRate = dbHelper->readStreamProperty(streamId, SensorStreamsDBColumns::frameRate);

    // Generate video file with appropriate path
    if (isTimeCorrect)
    {
        auto download_block_start_tp = std::chrono::steady_clock::now();

        // -----------------------------------------------------------------
        // Full-file fast path
        // -----------------------------------------------------------------
        // Skip makeVideoFile / remux entirely when the request can be served
        // directly from the raw recording file. Restricted to file-based
        // sensors only (uploaded video files); RTSP / NVStream live-stream
        // recordings always go through the standard processing path. Cloud
        // streams keep their own presigned-URL path and are also excluded
        // inside tryFindFullFileMatch.
        const bool isFileSensor = (sensor_type == SENSOR_TYPE_FILE);
        if (!isCloudStream && isFileSensor)
        {
            StorageManagement* storageMngt = GET_STORAGE_MNGT();
            if (storageMngt != nullptr)
            {
                const int64_t startMs = parseTimeToEpochMs(startTime);
                const int64_t endMs   = parseTimeToEpochMs(endTime);

                StorageManagement::FullFileMatch match = storageMngt->tryFindFullFileMatch(
                    streamId, id, startMs, endMs,
                    userContainer, transcode, enableOverlay, uselibav, userDisableAudio,
                    frameRate, fullFileRequested);

                if (match.eligible)
                {
                    VmsErrorCode fastRet = VmsErrorCode::NoError;
                    if (isURLRequested)
                    {
                        VideoGenerationParam ffParams;
                        ffParams.queryString = queryString;
                        ffParams.streamId    = streamId;
                        ffParams.sensorType  = sensor_type;
                        ffParams.startTime   = startTime;
                        ffParams.endTime     = endTime;
                        ffParams.container   = match.container;
                        fastRet = storageMngt->generateFullFileUrl(match, ffParams, response);
                    }
                    else
                    {
                        fastRet = storageMngt->serveFullFileDownload(match, fileName, conn, response);
                    }

                    auto download_block_end_tp = std::chrono::steady_clock::now();
                    auto download_block_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                        download_block_end_tp - download_block_start_tp).count();
                    LOG(warning) << "#### [FULL_FILE] Download perf for streamId: " << streamId
                                 << " took: " << download_block_ms << " ms ####" << endl;

                    return fastRet;
                }
                else if (fullFileRequested)
                {
                    // User explicitly asked for full-file but the request is
                    // not eligible (e.g. transcode/overlay requested, multiple
                    // files in range, mismatched container). Log and fall
                    // through to the standard processing path.
                    LOG(info) << "[FULL_FILE] fullFile=true requested but not eligible; "
                              << "falling back to normal processing for streamId: " << streamId << endl;
                }
            }
        }
        else if (fullFileRequested)
        {
            // fullFile=true is only honoured for file-based sensors; RTSP /
            // NVStream / cloud streams always go through standard processing.
            LOG(info) << "[FULL_FILE] fullFile=true ignored for non-file sensor "
                      << "(sensor_type='" << sensor_type << "', isCloudStream=" << isCloudStream
                      << ") for streamId: " << streamId << endl;
        }

        // The standard remux pipeline below requires a valid start/end time.
        // If the caller passed fullFile=true without time bounds and the fast
        // path did not engage, we cannot fall back safely - return a clear
        // error instead of letting makeVideoFile fail unpredictably.
        if ((startTime.empty() || endTime.empty()) && fullFileRequested)
        {
            LOG(error) << "[FULL_FILE] fullFile=true without time range did not "
                       << "match a stored recording for streamId: " << streamId << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response,
                "fullFile=true did not match any stored recording. Provide startTime/endTime or verify the sensor.");
            return VmsErrorCode::InvalidParameterError;
        }

        // -----------------------------------------------------------------
        // Standard path: remux / transcode via makeVideoFile
        // -----------------------------------------------------------------
        string video_codec;
        if (!isURLRequested)
        {
            nv_vms::IMediaInterface* mediaInterface = ModuleLoader::getInstance()->getMediaInterface();
            ret = makeVideoFile(startTime, endTime, streamId, id, deviceName, fileName,
                video_codec, fullLength, sensor_type, container, transcode, disableAudio,
                enableOverlay, &olParams, frameRate, mediaInterface, uselibav, isCloudStream);
        }

        if (ret != VmsErrorCode::NoError)
        {
            // Use specific error message for VMSNoDataError, generic message for other errors
            string error_message = (ret == VmsErrorCode::VMSNoDataError) ?
                                    "No valid stream found for given timestamps, please check timelines using /api/v1/storage/timelines" :
                                    "Unable to get requested file, please try after sometime";
            SET_VMS_ERROR2(ret, response, error_message.c_str());
            return ret;
        }

        // Handle URL generation request
        if (isURLRequested)
        {
            VideoGenerationParam params;
            params.queryString = queryString;
            params.fileName = ""; // Not used in URL generation path
            params.streamId = streamId;
            params.sensorType = sensor_type;
            params.startTime = startTime;
            params.endTime = endTime;
            params.container = container;
            params.disableAudio = disableAudio;
            params.enableOverlay = enableOverlay;
            params.uselibav = uselibav;
            params.transcode = transcode;
            params.overlayParams = olParams;  // Directly assign the value, will be wrapped in optional
            params.frameRate = frameRate;
            params.isCloudStream = isCloudStream;

            // Parse blocking parameter to decide between sync and async generation
            string blockingStr;
            CivetServer::getParam(queryString, "blocking", blockingStr);
            bool isBlocking = (blockingStr.empty() || blockingStr == "true");

            if (isBlocking)
            {
                LOG(info) << "[SYNC_MEDIA] Using synchronous video generation (blocking=true)" << endl;
                return generateReplayVideoUrlSync(params, response);
            }
            else
            {
                LOG(info) << "[ASYNC_MEDIA] Using asynchronous video generation (blocking=false)" << endl;
                return generateReplayVideoUrlAsync(params, response);
            }
        }
        else
        {
            // Original download logic (action.empty())
            StorageManagement* storageMngt = GET_STORAGE_MNGT();
            if (storageMngt == nullptr)
            {
                LOG(error) << "Storage Management module is not loaded" << endl;
                string error_message = string("Storage Management module is not loaded");
                SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, error_message.c_str());
                return VmsErrorCode::MethodNotAllowedError;
            }

            ret = storageMngt->handleMediaFileDownload(fileName, conn);
            /* Log download block time */
            {
                auto download_block_end_tp = std::chrono::steady_clock::now();
                auto download_block_ms = std::chrono::duration_cast<std::chrono::milliseconds>(download_block_end_tp - download_block_start_tp).count();
                LOG(warning) << "#### Download perf for streamId: " << streamId << " took: " << download_block_ms << " ms ####" << endl;
            }

            bool isFileDeleted = deleteFile (fileName);
            LOG(info) << "File : " << fileName << " deleted ? " << isFileDeleted << endl;
        }
    }
    else
    {
        string error_message = string("Invalid Start Time = ("  + startTime +  ") and End time = (" + endTime  + ")");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str());
        ret = VmsErrorCode::InvalidParameterError;
    }
    return ret;
}

VmsErrorCode StorageManagement::generateReplayVideoUrlAsync(const VideoGenerationParam& params, Json::Value& response)
{
    VideoUrlGenerationContext context;

    // Setup common context (parameters, sensor info, paths, etc.)
    VmsErrorCode result = setupVideoUrlGenerationContext(params, context, response);
    if (result != VmsErrorCode::NoError) {
        return result;
    }

    if (tryReuseCachedTempFile(params, context, response))
    {
        LOG(info) << "[ASYNC_MEDIA] Reusing cached temp file: " << context.outputFilePath << endl;
        return VmsErrorCode::NoError;
    }

    // Setup async task with TaskManager
    VideoGenerationParam mutableParams = params;
    mutableParams.deviceName = context.sensor ? context.sensor->name : "disconnected_device";
    mutableParams.sensorType = context.sensor ? context.sensor->type : "";

    VideoGeneratorTaskManager* taskManager = VideoGeneratorTaskManager::getInstance();
    if (!taskManager)
    {
        LOG(error) << "[ASYNC_MEDIA] Failed to get TaskManager instance" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "TaskManager not available");
        return VmsErrorCode::VMSInternalError;
    }

    // For async: use TaskManager to create and manage the task
    // Note: TaskManager will generate its own task ID, so we need to update our context
    string asyncTaskId = taskManager->addTask(mutableParams);
    if (asyncTaskId.empty())
    {
        LOG(error) << "[ASYNC_MEDIA] Failed to create video generation task" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Failed to create video generation task");
        return VmsErrorCode::VMSInternalError;
    }

    // Update context with TaskManager-generated ID and rebuild paths
    context.taskId = asyncTaskId;
    context.outputFilePath = context.webRoot + TEMP_STORAGE_DIR + "/" + context.taskId + context.extension;
    context.videoUrl = context.baseUrl + TEMP_STORAGE_PATH + "/" + context.taskId + context.extension;

    // Record in database (file size = 0 for async since file doesn't exist yet)
    recordTempFileInDatabase(context, params, context.outputFilePath, 0);

    // Build response
    buildVideoUrlResponse(context, params, response);

    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::generateReplayVideoUrlSync(const VideoGenerationParam& params, Json::Value& response)
{
    auto videoUrlStartTp = std::chrono::steady_clock::now();
    LOG(info) << "[SYNC_MEDIA] Starting synchronous video generation for stream: " << params.streamId << endl;

    VideoUrlGenerationContext context;

    // Setup common context (parameters, sensor info, task ID, paths, etc.)
    VmsErrorCode result = setupVideoUrlGenerationContext(params, context, response);
    if (result != VmsErrorCode::NoError) {
        return result;
    }

    if (tryReuseCachedTempFile(params, context, response))
    {
        auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - videoUrlStartTp).count();
        LOG(warning) << "#### Video URL perf for streamId: " << params.streamId << " took: " << elapsedMs << " ms (cached) ####" << endl;
        return VmsErrorCode::NoError;
    }

    LOG(info) << "[SYNC_MEDIA] Generating video synchronously to: " << context.outputFilePath << endl;

    // Prepare overlay parameters
    OverlayBBoxParams overlayParamsCopy;
    OverlayBBoxParams* overlayPtr = nullptr;
    if (params.overlayParams.has_value())
    {
        overlayParamsCopy = params.overlayParams.value();
        overlayPtr = &overlayParamsCopy;
    }

    // Generate video file synchronously using makeVideoFile
    string video_codec;
    string outputFile = context.outputFilePath;
    int64_t actualStartEpochMs = 0;  // Capture actual start time for remux mode
    nv_vms::IMediaInterface* mediaInterface = ModuleLoader::getInstance()->getMediaInterface();
    VmsErrorCode makeVideoResult = makeVideoFile(
        params.startTime,
        params.endTime,
        params.streamId,
        "",  // id
        context.sensor ? context.sensor->name : "disconnected_device",  // device name
        outputFile,
        video_codec,
        "",  // full length
        params.sensorType,
        params.container,
        params.transcode,
        params.disableAudio,
        params.enableOverlay,
        overlayPtr,
        params.frameRate,
        mediaInterface,
        params.uselibav,
        params.isCloudStream,
        &actualStartEpochMs  // Output: actual start time from keyframe in remux mode
    );

    if (makeVideoResult != VmsErrorCode::NoError)
    {
        LOG(error) << "[SYNC_MEDIA] Synchronous video generation failed for stream: " << params.streamId
                   << " Error code: " << static_cast<int>(makeVideoResult) << endl;
        string error_message = (makeVideoResult == VmsErrorCode::VMSNoDataError) ?
                               "No valid stream found for given timestamps, please check timelines using /api/v1/storage/timelines" :
                               "Synchronous video generation failed";
        SET_VMS_ERROR2(makeVideoResult, response, error_message.c_str());
        return makeVideoResult;
    }

    LOG(info) << "[SYNC_MEDIA] Video generated successfully: " << outputFile << endl;

    // Get actual file size and record in database
    int64_t fileSize = 0;
    if (isFileExist(outputFile)) {
        fileSize = static_cast<int64_t>(getFileSizeInBytes(outputFile));
    }

    recordTempFileInDatabase(context, params, outputFile, fileSize);

    // Build response
    buildVideoUrlResponse(context, params, response);
    
    // Add actual start time to response if available (remux mode returns keyframe-aligned start)
    if (actualStartEpochMs > 0)
    {
        response["startTime"] = convertEpocToISO8601_2(actualStartEpochMs * 1000);
        response["startTimeEpochMs"] = static_cast<Json::Int64>(actualStartEpochMs);
    }

    auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - videoUrlStartTp).count();
    LOG(warning) << "#### Video URL perf for streamId: " << params.streamId << " took: " << elapsedMs << " ms (generated) ####" << endl;

    return VmsErrorCode::NoError;
}

// Helper method implementations
bool StorageManagement::tryReuseCachedTempFile(const VideoGenerationParam& params, VideoUrlGenerationContext& context, Json::Value& response)
{
    int64_t startMs = parseTimeToEpochMs(params.startTime);
    int64_t endMs = parseTimeToEpochMs(params.endTime);

    if (startMs <= 0 || endMs <= 0)
    {
        return false;
    }

    auto dbHelper = GET_DB_INSTANCE();
    if (!dbHelper)
    {
        return false;
    }

    auto existing = dbHelper->findTempFileByStreamAndTime(
        m_deviceId, params.streamId, startMs, endMs, nv_vms::TempFilesDBColumns::FILE_TYPE_VIDEO, params.container);

    if (existing.file_path_value.empty() || !isFileExist(existing.file_path_value))
    {
        return false;
    }

    std::string cachedFilename = std::filesystem::path(existing.file_path_value).filename().string();
    std::string cachedTaskId = extractTaskId(cachedFilename);

    auto now = std::chrono::system_clock::now();
    auto expiryTime = now + std::chrono::minutes(context.expiryMinutesInt);
    auto expiryTsMs = std::chrono::duration_cast<std::chrono::milliseconds>(
        expiryTime.time_since_epoch()).count();
    dbHelper->updateTempFileExpiry(existing.file_path_value, expiryTsMs);
    int64_t durationMs = static_cast<int64_t>(context.expiryMinutesInt) * 60 * 1000;
    m_videoCleanupScheduler->schedule(cachedTaskId, durationMs, existing.file_path_value);

    context.outputFilePath = existing.file_path_value;
    context.taskId = cachedTaskId;
    context.videoUrl = context.baseUrl + TEMP_STORAGE_PATH + "/" + cachedFilename;

    buildVideoUrlResponse(context, params, response);

    response["startTime"] = convertEpocToISO8601_2(startMs * 1000);
    response["startTimeEpochMs"] = startMs;

    return true;
}

VmsErrorCode StorageManagement::setupVideoUrlGenerationContext(const VideoGenerationParam& params, VideoUrlGenerationContext& context, Json::Value& response)
{
    // Parse and validate expiry parameter
    string expiryMinutesStr;
    CivetServer::getParam(params.queryString, "expiryMinutes", expiryMinutesStr);

    context.expiryMinutesInt = stringToInt(expiryMinutesStr, GET_CONFIG().default_file_expiry_minutes);

    if (context.expiryMinutesInt < 1)
    {
        LOG(error) << "Invalid expiry minutes: " << context.expiryMinutesInt << ". Must be greater than or equal to 1" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid expiry minutes value");
        return VmsErrorCode::InvalidParameterError;
    }

    // Setup base context
    context.baseUrl = getIngressBaseUrl();
    auto dbHelper = GET_DB_INSTANCE();
    context.sensor = dbHelper->searchSensorAndGetSensorInfo(params.streamId, m_deviceManager->getDeviceId());

    // Generate task ID using same logic as TaskManager
    string sensorPrefix;
    if (context.sensor && !context.sensor->name.empty() && context.sensor->name != "disconnected_device")
    {
        const std::string candidate = sanitizePrefix(context.sensor->name);
        if (!candidate.empty()) {
            sensorPrefix = candidate + "_";
        }
    }

    context.taskId = (sensorPrefix.empty() ? VideoGeneratorTaskManager::VIDEO_TASK_PREFIX : "") + sensorPrefix +
                     getUniqueIdFromUTCTime(params.startTime, "");

    // Setup extension and paths
    context.extension = params.container.empty() ? ".mp4" :
                       (params.container[0] == '.' ? params.container : "." + params.container);
    context.webRoot = VmsConfigManager::getInstance()->getWebRootPath();
    context.outputFilePath = context.webRoot + TEMP_STORAGE_DIR + "/" + context.taskId + context.extension;
    context.videoUrl = context.baseUrl + TEMP_STORAGE_PATH + "/" + context.taskId + context.extension;

    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::recordTempFileInDatabase(const VideoUrlGenerationContext& context, const VideoGenerationParam& params,
                                                       const string& actualFilePath, int64_t fileSize)
{
    try
    {
        int64_t durationMs = static_cast<int64_t>(context.expiryMinutesInt) * 60 * 1000;
        m_videoCleanupScheduler->schedule(context.taskId, durationMs, actualFilePath);
        auto now = std::chrono::system_clock::now();
        auto createdTsMs = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
        auto expiryTime = now + std::chrono::minutes(context.expiryMinutesInt);
        auto expiryTsMs = std::chrono::duration_cast<std::chrono::milliseconds>(expiryTime.time_since_epoch()).count();

        auto dbHelper = GET_DB_INSTANCE();
        if (dbHelper)
        {
            nv_vms::TempFilesDBColumns tempRec;
            tempRec.device_id_value = m_deviceId;
            tempRec.file_path_value = actualFilePath;
            tempRec.expiry_timestamp_value = expiryTsMs;
            tempRec.created_timestamp_value = createdTsMs;
            tempRec.stream_id_value = params.streamId;
            tempRec.file_size_value = fileSize;
            tempRec.start_time_ms_value = parseTimeToEpochMs(params.startTime);
            tempRec.end_time_ms_value = parseTimeToEpochMs(params.endTime);
            tempRec.file_type_value = nv_vms::TempFilesDBColumns::FILE_TYPE_VIDEO;
            tempRec.container_format_value = params.container;

            int ins = dbHelper->insertTempFileRecord(tempRec);
            if (ins != 0)
            {
                LOG(warning) << "Failed to record temp video in DB: " << actualFilePath << endl;
            }
        }
    }
    catch (const std::exception& e)
    {
        LOG(warning) << "Exception while recording temp video in DB: " << e.what() << endl;
    }

    return VmsErrorCode::NoError;
}

void StorageManagement::buildVideoUrlResponse(const VideoUrlGenerationContext& context, const VideoGenerationParam& params, Json::Value& response)
{
    auto now = std::chrono::system_clock::now();
    auto expiryTime = now + std::chrono::minutes(context.expiryMinutesInt);
    auto expiryTimestamp = std::chrono::duration_cast<std::chrono::milliseconds>(expiryTime.time_since_epoch()).count();
    string expiryISO = convertEpocToISO8601_2(expiryTimestamp * 1000);

    response["absolutePath"] = getAbsolutePath(context.webRoot) + TEMP_STORAGE_DIR + "/" + context.taskId + context.extension;
    response["expiryISO"] = expiryISO;
    response["expiryMinutes"] = context.expiryMinutesInt;
    response["videoUrl"] = context.videoUrl;
    response["streamId"] = params.streamId;
    response["type"] = "replay";
}

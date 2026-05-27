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

#include "modules_apis.h"
#include "network_utils.h"
#include "rtspservermanager.h"
#include "streamrecorder.h"
#include "ReplayPeerConnection.h"
#include "sensor_management.h"

// Define missing sensor API constants
constexpr const char* SENSOR_API = "/api/v1/sensor/*";
constexpr const char* SENSOR_DATA_API = "/api/v1/sensor/data/*";
constexpr const char* SENSOR_STATUS_API = "/api/v1/sensor/status/*";
constexpr const char* REPLAY_STREAM_API = "/api/v1/replay/stream/*";

namespace vst_rtsp
{
    int addStream(const string& id, const string& name, string& url, string& vodUrl)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        // Mask credentials before logging to prevent exposure in logs
        string sanitizedUrl = secureUrlForLogging(url);
        LOG(info) << "id: " << id << " url: " << sanitizedUrl << endl;
        string api_key = "/api/v1/proxy/stream/add";

        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["id"] = id;
            in["name"] = name;
            in["url"] = url;
            if (rtsp_mgmt != nullptr &&
                rtsp_mgmt->m_func[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                url = out.get("url", "").asString();
                vodUrl = out.get("vodUrl", "").asString();
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            Json::Value in;
            in["id"] = id;
            in["name"] = name;
            in["url"] = url;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlPostRequest(vst_address, out, in, headers))
            {
                Json::Value jout = stringToJson(out);
                LOG(info) << jout.toStyledString() << endl;
                url = jout.get("url", "").asString();
                vodUrl = jout.get("vodUrl", "").asString();
                return 0;
            }
        }
        LOG(error) << "Failed to get proxy url : " << secureUrlForLogging(url) << endl;
        return -1;
    }

    int removeStream(const string& id)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        string api_key = string("/api/v1/proxy/stream/") + id;
        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            req_info["url"] = api_key;
            req_info["method"] = "delete";
            struct mg_connection *conn = nullptr;
            if (rtsp_mgmt != nullptr &&
                rtsp_mgmt->m_func[PROXY_STREAM_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to remove proxy url from RTSP proxy" << endl;
        return -1;
    }

    Json::Value activeClientSessions()
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        Json::Value jout;
        string api_key = "/api/v1/proxy/activeClientSessions";
        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            struct mg_connection *conn = nullptr;
            if (rtsp_mgmt != nullptr)
            {
                rtsp_mgmt->m_func[api_key](req_info, in, jout, conn);
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            if (curlGetRequest(vst_address, out))
            {
                jout = stringToJson(out);
            }
        }
        return jout;
    }

    string rtspUrlPrefix(const string& id)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        string url;
        string api_key = "/api/v1/proxy/urlPrefix";
        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();

        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["id"] = id;
            if (rtsp_mgmt != nullptr)
            {
                rtsp_mgmt->m_func[api_key](req_info, in, out, conn);
            }
            url = out.get("urlPrefix", "").asCString();
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            
            // Use GET request instead of POST - the RTSP server endpoint expects GET
            if (curlGetRequest(vst_address, out))
            {
                Json::Value jout = stringToJson(out);
                url = jout.get("urlPrefix", "").asString();
                return url;
            }
        }
        return url;
    }

    string rtspOriginalUrlPrefix(const string& id)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        string url;
        string api_key = "/api/v1/proxy/orignalUrlPrefix";
        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["id"] = id;
            if (rtsp_mgmt != nullptr)
            {
                rtsp_mgmt->m_func[api_key](req_info, in, out, conn);
            }
            url = out.get("orignalUrlPrefix", "").asCString();
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            
            // Use GET request instead of POST - the RTSP server endpoint expects GET
            if (curlGetRequest(vst_address, out))
            {
                Json::Value jout = stringToJson(out);
                url = jout.get("orignalUrlPrefix", "").asString();
                return url;
            }
        }
        return url;
    }

    string rtspServerDomainPrefix(const string& id)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        string url;
        string api_key = "/api/v1/proxy/rtspServerDomainPrefix";
        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["id"] = id;
            if (rtsp_mgmt != nullptr)
            {
                rtsp_mgmt->m_func[api_key](req_info, in, out, conn);
            }
            url = out.get("rtspServerDomainPrefix", "").asCString();
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            Json::Value in;
            in["id"] = id;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlPostRequest(vst_address, out, in, headers))
            {
                Json::Value jout = stringToJson(out);
                url = jout.get("rtspServerDomainPrefix", "").asString();
                return url;
            }
        }
        return url;
    }

    string vodServerDomainPrefix(const string& id)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        string url;
        string api_key = "/api/v1/proxy/rtspServerDomainPrefix";
        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["id"] = id;
            if (rtsp_mgmt != nullptr)
            {
                rtsp_mgmt->m_func[api_key](req_info, in, out, conn);
            }
            url = out.get("vodServerDomainPrefix", "").asCString();
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            Json::Value in;
            in["id"] = id;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlPostRequest(vst_address, out, in, headers))
            {
                Json::Value jout = stringToJson(out);
                url = jout.get("vodServerDomainPrefix", "").asString();
                return url;
            }
        }
        return url;
    }

    int removeServerMediaSession(const string& id)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        string api_key = string("/api/v1/proxy/session/") + id;
        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            req_info["url"] = api_key;
            req_info["method"] = "delete";
            struct mg_connection *conn = nullptr;
            if (rtsp_mgmt != nullptr && rtsp_mgmt->m_func.find(PROXY_SESSION_API) != rtsp_mgmt->m_func.end() &&
                rtsp_mgmt->m_func[PROXY_SESSION_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(info) << "Failed to remove media session from RTSP Server" << endl;
        return -1;
    }

    int updateUser(const string& username)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        string api_key = "/api/v1/proxy/user/update";
        nv_vms::RtspServerManager* rtsp_mgmt = GET_RTSPSERVER();
        if (rtsp_mgmt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["username"] = DEFAULT_USERNAME;
            if (rtsp_mgmt != nullptr &&
                rtsp_mgmt->m_func[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleRtspServer] + api_key;
            string out;
            Json::Value in;
            in["username"] = DEFAULT_USERNAME;
            if(curlPostRequest(vst_address, out, in))
            {
                LOG(error) << "Failed to update username in RTSP Server" << endl;
                return 0;
            }
            LOG(info) << "Response of [/proxy/user/update] " << out << endl;
        }
        return -1;
    }

    int addUser(const string& username, const string& passwordHash)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        return 0;
    }

    int removeUser(const string& username)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        return 0;
    }
} // namespace vst_rtsp

namespace vst_recorder
{
    int addStream(const string& id, const string& url)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        // Mask credentials before logging to prevent exposure in logs
        string sanitizedUrl = secureUrlForLogging(url);
        LOG(info) << "id: " << id << " url: " << sanitizedUrl << endl;
        string api_key = "/api/v1/record/stream/add";
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["id"] = id;
            in["url"] = url;
            if (recorder != nullptr && recorder->m_func.find(api_key) != recorder->m_func.end() &&
                recorder->m_func[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStreamRecorder] + api_key;
            string out;
            Json::Value in;
            in["id"] = id;
            in["url"] = url;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlPostRequest(vst_address, out, in, headers))
            {
                Json::Value jout = stringToJson(out);
                LOG(info) << jout.toStyledString() << endl;
                return 0;
            }
        }
        LOG(error) << "Failed to add stream into recorder url : " << secureUrlForLogging(url) << endl;
        return -1;
    }

    int removeStream(const string& id)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        LOG(info) << "id: " << id << endl;
        if (id.empty())
        {
            return -1;
        }

        string api_key = string("/api/v1/record/") + id;
        StreamRecorder* recorder = GET_RECORDER();
        if (recorder != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            req_info["url"] = api_key;
            req_info["method"] = "delete";
            if (recorder != nullptr && recorder->m_func.find(RECORD_API) != recorder->m_func.end() &&
                recorder->m_func[RECORD_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStreamRecorder] + api_key;
            string out;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to remove stream from recorder id:" << id << endl;
        return -1;
    }
}

namespace vst_storage
{
    int addOrRemoveFileInProtectList(const string& filePath, const bool& addOrRemove)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        LOG(info) << "filePath: " << filePath << " addOrRemove: " << addOrRemove << endl;
        
        // Skip API call if filePath is empty
        if (filePath.empty())
        {
            return 0; // Return success to avoid errors in caller
        }
        
        string api_key = "/api/v1/storage/file/protect";
        StorageManagement* storage_mngt = GET_STORAGE_MNGT();
        if (storage_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            Json::Value filePathArray(Json::arrayValue);
            filePathArray.append(filePath);
            in["filePath"] = filePathArray;
            in["protect"] = addOrRemove;
            req_info["url"] = api_key;
            req_info["method"] = "post";

            if (storage_mngt != nullptr && storage_mngt->m_func.find(api_key) != storage_mngt->m_func.end() &&
                storage_mngt->m_func[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStorageManagement] + api_key;
            string out;
            Json::Value in;
            Json::Value filePathArray(Json::arrayValue);
            filePathArray.append(filePath);
            in["filePath"] = filePathArray;
            in["protect"] = addOrRemove;
            if (curlPostRequest(vst_address, out, in))
            {
                return 0;
            }
        }
        LOG(error) << "addOrRemoveFileInProtectList failed for filePath : " << filePath << endl;
        return -1;
    }

    int addOrRemoveFilesInProtectList(const std::vector<string>& filePaths, const bool& addOrRemove)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        LOG(info) << "filePaths count: " << filePaths.size() << " addOrRemove: " << addOrRemove << endl;

        // Skip API call if filePaths is empty
        if (filePaths.empty())
        {
            return 0; // Return success to avoid errors in caller
        }

        string api_key = "/api/v1/storage/file/protect";
        StorageManagement* storage_mngt = GET_STORAGE_MNGT();
        if (storage_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            Json::Value filePathArray(Json::arrayValue);
            for (const auto& filePath : filePaths)
            {
                if (!filePath.empty())
                {
                    filePathArray.append(filePath);
                }
            }
            if (filePathArray.empty())
            {
                return 0; // All paths were empty
            }
            in["filePath"] = filePathArray;
            in["protect"] = addOrRemove;
            req_info["url"] = api_key;
            req_info["method"] = "post";

            if (storage_mngt != nullptr && storage_mngt->m_func.find(api_key) != storage_mngt->m_func.end() &&
                storage_mngt->m_func[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStorageManagement] + api_key;
            string out;
            Json::Value in;
            Json::Value filePathArray(Json::arrayValue);
            for (const auto& filePath : filePaths)
            {
                if (!filePath.empty())
                {
                    filePathArray.append(filePath);
                }
            }
            if (filePathArray.empty())
            {
                return 0; // All paths were empty
            }
            in["filePath"] = filePathArray;
            in["protect"] = addOrRemove;
            if (curlPostRequest(vst_address, out, in))
            {
                return 0;
            }
        }
        LOG(error) << "addOrRemoveFilesInProtectList failed for " << filePaths.size() << " files" << endl;
        return -1;
    }

    int updateStorageSize(const size_t& size, const bool& addOrRemove)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        LOG(verbose) << "size: " << size << " addOrRemove: " << addOrRemove << endl;
        string api_key = "/api/v1/storage/size/update";
        StorageManagement* storage_mngt = GET_STORAGE_MNGT();
        if (storage_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["size"] = size;
            in["addOrRemove"] = addOrRemove;
            req_info["url"] = api_key;
            req_info["method"] = "post";
            if (storage_mngt != nullptr && storage_mngt->m_func.find(api_key) != storage_mngt->m_func.end() &&
                storage_mngt->m_func[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStorageManagement] + api_key;
            string out;
            Json::Value in;
            in["size"] = size;
            in["addOrRemove"] = addOrRemove;
            if (curlPostRequest(vst_address, out, in))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to update storage size : " << size << " addOrRemove: " << addOrRemove << endl;
        return -1;
    }

    int doAging(const size_t& bytesToReserve)
    {
        LOG(verbose) << __METHOD_NAME__ << endl;
        LOG(verbose) << "bytesToReserve: " << bytesToReserve << endl;
        string api_key = "/api/v1/storage/aging";
        StorageManagement* storage_mngt = GET_STORAGE_MNGT();
        if (storage_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["bytesToReserve"] = bytesToReserve;
            req_info["url"] = api_key;
            req_info["method"] = "post";
            if (storage_mngt != nullptr && storage_mngt->m_func.find(api_key) != storage_mngt->m_func.end() &&
                storage_mngt->m_func[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStorageManagement] + api_key;
            string out;
            Json::Value in;
            in["bytesToReserve"] = bytesToReserve;
            if (curlPostRequest(vst_address, out, in))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to do aging for bytesToReserve: " << bytesToReserve << endl;
        return -1;
    }

    int deleteMediaFile(const string& filePath)
    {
        if (!filePath.size())
        {
            LOG(error) << "Invalid filePath: " << filePath << endl;
            return -1;
        }

        LOG(verbose) << __METHOD_NAME__ << endl;
        LOG(verbose) << "filePath: " << filePath << endl;
        string api_key = STORAGE_FILE_API_PREFIX;

        api_key += "?filePath=";
        api_key += filePath;
        StorageManagement* storage_mngt = GET_STORAGE_MNGT();
        if (storage_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            req_info["url"] = STORAGE_FILE_API_PREFIX;
            req_info["method"] = "delete";
            req_info["query"] = "filePath=" + filePath;
            struct mg_connection *conn = nullptr;
            if (storage_mngt != nullptr && storage_mngt->m_func.find(STORAGE_API) != storage_mngt->m_func.end() &&
                storage_mngt->m_func[STORAGE_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStorageManagement] + api_key;
            vector<string> headers;
            string out;
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to delete a media file: " << filePath << endl;
        return -1;
    }

    int deleteFilesByStream(const string& streamId)
    {
        // Reuses the existing DELETE /api/v1/storage/file/{streamId}?startTime=*&endTime=*
        // handler so a file-sensor delete fans out the same way the storage UI button does:
        // unlink each backing file, prune VIDEO_RECORD rows, and cascade
        // StorageManagement::deleteSensorDetails for sensor/stream cleanup.
        if (streamId.empty())
        {
            LOG(error) << "Empty streamId for deleteFilesByStream" << endl;
            return -1;
        }
        LOG(verbose) << __METHOD_NAME__ << " streamId: " << streamId << endl;

        const string url = string(STORAGE_FILE_API_PREFIX) + "/" + streamId;
        const string query = "startTime=*&endTime=*";

        StorageManagement* storage_mngt = GET_STORAGE_MNGT();
        if (storage_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            req_info["url"] = url;
            req_info["method"] = "delete";
            req_info["query"] = query;
            struct mg_connection *conn = nullptr;
            if (storage_mngt->m_func.find(STORAGE_API) != storage_mngt->m_func.end() &&
                storage_mngt->m_func[STORAGE_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStorageManagement] + url + "?" + query;
            vector<string> headers;
            string out;
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to delete files for stream: " << streamId << endl;
        return -1;
    }

    bool checkStorageCapacity(const size_t& size)
    {
        bool ret = false;
        LOG(verbose) << __METHOD_NAME__ << endl;
        LOG(verbose) << "Size to check capacity: " << size << endl;
        string api_key = "/api/v1/storage/capacity";
        StorageManagement* storage_mngt = GET_STORAGE_MNGT();
        if (storage_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["bytesToCheck"] = size;
            req_info["url"] = api_key;
            req_info["method"] = "post";
            if (storage_mngt != nullptr && storage_mngt->m_func.find(api_key) != storage_mngt->m_func.end() &&
                storage_mngt->m_func[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                ret = out.get("status", false).asBool();
                return ret;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleStorageManagement] + api_key;
            string out;
            Json::Value in;
            in["bytesToCheck"] = size;
            if (curlPostRequest(vst_address, out, in))
            {
                Json::Value jout = stringToJson(out);
                ret = jout.get("status", false).asBool();
                return ret;
            }
        }
        LOG(error) << "Failed to storage capacity size : " << size << endl;
        return ret;
    }
}

namespace vst_replaystream
{
    int addStream(const string& id, const string& url)
    {
        // Mask credentials before logging to prevent exposure in logs
        string sanitizedUrl = secureUrlForLogging(url);
        LOG(info) << "id: " << id << " url: " << sanitizedUrl << endl;
        string api_key = "/api/v1/replay/stream/add";
        ReplayPeerConnection* replay_mngt = GET_PEERCONNECTION_REPLAY_MNGR();
        if (replay_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            struct mg_connection *conn = nullptr;
            in["id"] = id;
            in["url"] = url;
            auto func_map = replay_mngt->getHttpApi();
            if (func_map.find(api_key) != func_map.end() &&
                func_map[api_key](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleReplayStream] + api_key;
            string out;
            Json::Value in;
            in["id"] = id;
            in["url"] = url;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlPostRequest(vst_address, out, in, headers))
            {
                Json::Value jout = stringToJson(out);
                LOG(info) << jout.toStyledString() << endl;
                return 0;
            }
        }
        LOG(error) << "Failed to add stream into replay manager url : " << secureUrlForLogging(url) << endl;
        return -1;
    }

    int removeStream(const string& id)
    {
        LOG(info) << "id: " << id << endl;
        if (id.empty())
        {
            return -1;
        }

        string api_key = string("/api/v1/replay/stream/");
        api_key += "?streamId=" + id;
        
        ReplayPeerConnection* replay_mngt = GET_PEERCONNECTION_REPLAY_MNGR();
        if (replay_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            req_info["url"] = api_key;
            req_info["method"] = "delete";
            req_info["query"] = "streamId=" + id;
            struct mg_connection *conn = nullptr;
            auto func_map = replay_mngt->getHttpApi();
            if (func_map.find(REPLAY_STREAM_API) != func_map.end() &&
                func_map[REPLAY_STREAM_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleReplayStream] + api_key;
            string out;
            vector<string> headers;
            string streamId = string("streamid: ") + id;
            headers.push_back(streamId);
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to remove stream from replay manager id:" << id << endl;
        return -1;
    }

    int removeSensor(const string& sensorId)
    {
        LOG(info) << "sensorId: " << sensorId << endl;
        if (sensorId.empty())
        {
            return -1;
        }

        string api_key = string("/api/v1/replay/stream/") + sensorId;
        ReplayPeerConnection* replay_mngt = GET_PEERCONNECTION_REPLAY_MNGR();
        if (replay_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            req_info["url"] = api_key;
            req_info["method"] = "delete";
            struct mg_connection *conn = nullptr;
            auto func_map = replay_mngt->getHttpApi();
            if (func_map.find(REPLAY_STREAM_API) != func_map.end() &&
                func_map[REPLAY_STREAM_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleReplayStream] + api_key;
            string out;
            vector<string> headers;
            string streamId = string("streamid: ") + sensorId;
            headers.push_back(streamId);
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to delete stream from replay service id:" << sensorId << endl;
        return -1;
    }
}

namespace vst_sensor
{
    int deleteSensor(const string& sensorId)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        LOG(info) << "sensorId: " << sensorId << endl;
        if (sensorId.empty())
        {
            LOG(error) << "Sensor ID is empty" << endl;
            return -1;
        }

        string api_key = string("/api/v1/sensor/") + sensorId;
        SensorManagement* sensor_mngt = GET_SENSOR_MNGT();
        if (sensor_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            req_info["url"] = api_key;
            req_info["method"] = "delete";
            struct mg_connection *conn = nullptr;
            if (sensor_mngt->m_func.find(SENSOR_API) != sensor_mngt->m_func.end() &&
                sensor_mngt->m_func[SENSOR_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleSensorManagement] + api_key;
            string out;
            vector<string> headers;
            string sensorHeader = string("sensorid: ") + sensorId;
            headers.push_back(sensorHeader);
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to delete sensor from sensor service id:" << sensorId << endl;
        return -1;
    }

    int deleteStream(const string& streamId)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        LOG(info) << "streamId: " << streamId << endl;
        if (streamId.empty())
        {
            LOG(error) << "Stream ID is empty" << endl;
            return -1;
        }

        string api_key = string("/api/v1/sensor/stream/") + streamId;
        SensorManagement* sensor_mngt = GET_SENSOR_MNGT();
        if (sensor_mngt != nullptr)
        {
            Json::Value req_info;
            Json::Value in;
            Json::Value out;
            req_info["url"] = api_key;
            req_info["method"] = "delete";
            struct mg_connection *conn = nullptr;
            if (sensor_mngt->m_func.find(SENSOR_API) != sensor_mngt->m_func.end() &&
                sensor_mngt->m_func[SENSOR_API](req_info, in, out, conn) == VmsErrorCode::NoError)
            {
                return 0;
            }
        }
        else
        {
            string vst_address = GET_CONFIG().module_endpoints[ModuleSensorManagement] + api_key;
            string out;
            vector<string> headers;
            string streamHeader = string("streamid: ") + streamId;
            headers.push_back(streamHeader);
            if (curlDeleteRequest(vst_address, headers, out))
            {
                return 0;
            }
        }
        LOG(error) << "Failed to delete stream from sensor service id:" << streamId << endl;
        return -1;
    }
}
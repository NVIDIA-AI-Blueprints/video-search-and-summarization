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

#include <jsoncpp/json/json.h>
#include "peerconnection_manager_apis.h"
#include "PeerConnectionManager.h"
#include "logger.h"
#include "utils.h"

#define CHECK_PEERCONNECTION_MANAGER_MODULE \
    do { \
        peerConnectionMngr = GET_PEERCONNECTION_MNGR();  \
        if (peerConnectionMngr == nullptr)  \
        {   \
            LOG(error) << "PeerConnection Manager module is not loaded" << endl; \
            return VmsErrorCode::MethodNotAllowedError; \
        }   \
    } while(0)

using namespace std;

PeerConnectionManagerApis::PeerConnectionManagerApis()
{
    m_func["/api/getVideoDeviceList"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        return peerConnectionMngr->getVideoDeviceList(response);
    };

    m_func["/api/getAudioDeviceList"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        return peerConnectionMngr->getAudioDeviceList(response);
    };

    m_func["/api/getIceServers"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid, remoteAddr;
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "peerid", peerid);
        }
        remoteAddr = req_info.get("remote_addr", EMPTY_STRING).asString();
        return peerConnectionMngr->getIceServers(peerid, remoteAddr, response);
    };

    m_func["/api/stream/pause"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid = "";
        CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
        peerid = in.get("peerid", EMPTY_STRING).asString();
        if (peerid.empty() == true)
        {
            LOG(warning) << "peerid is empty";
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerid is empty")
            return VmsErrorCode::InvalidParameterError;
        }
        #if 0 // This will be used when API versioning is implemented
        string mediaSessionId = in.get("mediaSessionId", EMPTY_STRING).asString();
        if (mediaSessionId.empty() == true)
        {
            LOG(warning) << "mediaSessionId is empty";
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "mediaSessionId is empty")
            return VmsErrorCode::InvalidParameterError;
        }
        #endif
        return peerConnectionMngr->controlStream("pause", peerid, in, response);
    };

    m_func["/api/stream/resume"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid = "";
        CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
        peerid = in.get("peerid", EMPTY_STRING).asString();
        if (peerid.empty() == true)
        {
            LOG(warning) << "peerid is empty";
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerid is empty")
            return VmsErrorCode::InvalidParameterError;
        }
        #if 0 // This will be used when API versioning is implemented
        string mediaSessionId = in.get("mediaSessionId", EMPTY_STRING).asString();
        if (mediaSessionId.empty() == true)
        {
            LOG(warning) << "mediaSessionId is empty";
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "mediaSessionId is empty")
            return VmsErrorCode::InvalidParameterError;
        }
        #endif
        return peerConnectionMngr->controlStream("resume", peerid, in, response);
    };

    m_func["/api/stream/seek"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid = "";
        std::string action = "";
        std::string mediaSessionId = "";
        peerid = in.get("peerid", EMPTY_STRING).asString();
        action = in.get("action", EMPTY_STRING).asString();
        mediaSessionId = in.get("mediaSessionId", EMPTY_STRING).asString();
        LOG(info) << "/api/stream/seek: " << peerid << " " << mediaSessionId << endl;
        const string request_method = req_info.get("method", EMPTY_STRING).asString();
        if(iequals(request_method, "get"))
        {
            const string query_string = req_info.get("query", EMPTY_STRING).asString();
            CivetServer::getParam(query_string, "peerid", peerid);
            CivetServer::getParam(query_string, "mediaSessionId", mediaSessionId);
            return peerConnectionMngr->getCurrentPosition(peerid, mediaSessionId, response);
        }
        return peerConnectionMngr->controlStream(action, peerid, in, response);
    };

    // TODO: add support for mediaSessionId
    m_func["/api/stream/stats"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        if (GET_CONFIG().enable_perf_logging == false)
        {
            LOG(error) << "VMS stats not enabled";
            SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "VMS stats not enabled")
            return VmsErrorCode::MethodNotAllowedError;
        }
        std::string peerid = "";
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "peerid", peerid);
        }
        string deviceid = "";
        if (peerid.empty())
        {
            CivetServer::getParam(query_string, "deviceid", deviceid);
        }
        return peerConnectionMngr->getStreamStats(peerid, response, deviceid);
    };

    m_func["/api/stream/status"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid = "", overlay = "";
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "peerid", peerid);
            if(!CivetServer::getParam(query_string, "overlay", overlay))
            {
                overlay = "false";
            }
        }
        return peerConnectionMngr->getStreamStatus(peerid, overlay, req_info, in, response);
    };

    m_func["/api/stream/query"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid = "";
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "peerid", peerid);
        }
        return peerConnectionMngr->getQuery(req_info, in, response);
    };

    m_func["/api/getIceCandidate"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid;
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "peerid", peerid);
        }
        return peerConnectionMngr->getIceCandidateList(peerid, response);
    };

    m_func["/api/addIceCandidate"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid;
        Json::Value candidate;
        CHECK_JSON_OBJECT_IF_ERROR_RETURN(in)
        peerid = in.get("peerid", EMPTY_STRING).asString();
        candidate = in.get("candidate", EMPTY_STRING);
        if (peerid.empty() == true)
        {
            LOG(warning) << "peerid is empty";
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "peerid is empty")
            return VmsErrorCode::InvalidParameterError;
        }
        if (candidate.empty() == true)
        {
            LOG(warning) << "candidate is empty";
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "candidate is empty")
            return VmsErrorCode::InvalidParameterError;
        }
        return peerConnectionMngr->addIceCandidate(peerid, in, response);
    };

    m_func["/api/getPeerConnectionList"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        return peerConnectionMngr->getPeerConnectionList(response);
    };

    m_func["/api/getStreamList"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        return peerConnectionMngr->getStreamList(response);
    };

    m_func["/api/createOffer"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid;
        std::string audiourl;
        std::string options;
        std::string sensorId;
        std::string startTime;
        std::string endTime;
        unordered_map<string, string> urlParameters;
        std::map<std::string, std::string, std::less<>> opts = getStreamOptions(in);
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "peerid", peerid);
            CivetServer::getParam(query_string, "sensorId", sensorId);
            CivetServer::getParam(query_string, "startTime", startTime);
            CivetServer::getParam(query_string, "endTime", endTime);
            CivetServer::getParam(query_string, "audiourl", audiourl);

            urlParameters["peerid"] = peerid;
            urlParameters["sensorId"] = sensorId;
            urlParameters["startTime"] = startTime;
            urlParameters["endTime"] = endTime;
            urlParameters["audiourl"] = audiourl;
        }
        return peerConnectionMngr->createOffer(urlParameters, opts, response);
    };

    m_func["/api/setAnswer"] = [this](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        PeerConnectionManager* peerConnectionMngr = nullptr;
        CHECK_PEERCONNECTION_MANAGER_MODULE;
        std::string peerid;
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "peerid", peerid);
        }
        return peerConnectionMngr->setAnswer(peerid, in, response);
    };

    m_func["/api/log"] = [](const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn) -> VmsErrorCode
    {
        std::string loglevel;
        const string query_string = req_info.get("query", EMPTY_STRING).asString();
        if (query_string.empty() == false)
        {
            CivetServer::getParam(query_string, "level", loglevel);
            if (!loglevel.empty())
            {
                try
                {
                    int log_level_int = std::stoi(loglevel); // Convert string to integer safely
                    rtc::LogMessage::LogToDebug(static_cast<rtc::LoggingSeverity>(log_level_int));
                }
                catch (const std::invalid_argument& e)
                {
                    // Handle case where conversion fails (e.g., non-numeric input)
                    LOG(error) << "Invalid log level: " << loglevel << " (" << e.what() << ")" << std::endl;
                }
                catch (const std::out_of_range& e)
                {
                    // Handle case where the number is out of range for an int
                    LOG(error) << "Log level out of range: " << loglevel << " (" << e.what() << ")" << std::endl;
                }
            }
        }
        response = rtc::LogMessage::GetLogToDebug();
        return VmsErrorCode::NoError;
    };
}

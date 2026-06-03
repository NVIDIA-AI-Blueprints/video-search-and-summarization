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

#ifdef USE_GRPC_CLIENT
#include "nvgrpc.h"
#include "gst_utils.h"
#endif
#include "websocket_apis.h"
#include "testRTSP.h"
#include "utils.h"
#include "logger.h"
#include "network_utils.h"

using namespace std;

// Plugin server API paths
constexpr const char* TOKKIO_PLUGIN_SERVER_USE_RAG_API_PATH = "/rag/use_rag";
constexpr const char* TOKKIO_PLUGIN_SERVER_RAG_ENDPOINT_API_PATH = "/rag/rag_endpoint";

constexpr const char* WEBSOCKET_SERVER_PATH = "/vms/ws?connectionId=";
#define EXECUTE_FUNC(func_key, arg1, arg2, arg3, agr4)  m_callbackMap[func_key](arg1, arg2, arg3, agr4);

WebsocketApis::WebsocketApis(std::shared_ptr<DeviceManager> deviceMngr): m_deviceManager(deviceMngr)
{
#ifndef RELEASE
    m_func["get_logs"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        return getVstLogs(receivedData, response, conn);
    };
#endif
#ifdef USE_GRPC_CLIENT
    m_func["addDummyUdpTrack"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        addDummyUdpTrack(receivedData);
    };
#endif
    m_func["/event/disconnect"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
#ifdef STREAMBRIDGE_MODULE
        handleWsDisconnect(conn);
#endif
    };

    // StreamBridge related apis
    m_func["api/v1/streambridge/iceServers"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/streambridge/iceServers" << endl;
        string api = "/api/v1/streambridge/iceServers";
        getIceServers(receivedData, api, response);
    };
    m_func["api/v1/streambridge/stream/start"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/streambridge/stream/start" << endl;
        string api = "/api/v1/streambridge/stream/start";
        streamStart(receivedData, api, response, conn);
    };
    m_func["api/v1/streambridge/stream/stop"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/streambridge/stream/stop" << endl;
        string api = "/api/v1/streambridge/stream/stop";
        streamStop(receivedData, api, response);
    };
    m_func["api/v1/streambridge/setAnswer"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/streambridge/setAnswer" << endl;
        string api = "/api/v1/streambridge/setAnswer";
        setAnswer(receivedData, api, response);
    };
    m_func["api/v1/streambridge/iceCandidate"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/streambridge/iceCandidate" << endl;
        if (GET_CONFIG().use_reverse_proxy == true)
        {
            /* Ignore the ice-candidates if RP is enabled */
        }
        else
        {
            string api = "/api/v1/streambridge/iceCandidate";
            postIceCandidate(receivedData, api, response);
        }
    };
    m_func["api/v1/streambridge/configuration"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/streambridge/configuration" << endl;
        string api = "/api/v1/streambridge/configuration";
        getConfiguration(receivedData, api, response);
    };
    m_func["api/v1/streambridge/ping"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        Json::Value ping_reponse;
        ping_reponse["apiKey"] = "api/v1/streambridge/ping";
        ping_reponse["data"] = Json::nullValue;
        response = ping_reponse;
    };
    // Plugin Server APIs
    m_func["api/v1/streambridge/rag/status"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        setRAGStatus(receivedData, response);
    };
    m_func["api/v1/streambridge/rag/endpoint"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        setRAGEndpoint(receivedData, response);
    };

    // Live PeerConnection related apis
    m_func["api/v1/live/iceServers"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/live/iceServers" << endl;
        string api = "/api/v1/live/iceServers";
        getIceServers(receivedData, api, response);
    };
    m_func["api/v1/live/stream/start"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/live/stream/start" << endl;
        string api = "/api/v1/live/stream/start";
        streamStart(receivedData, api, response, conn);
    };
    m_func["api/v1/live/stream/stop"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/live/stream/stop" << endl;
        string api = "/api/v1/live/stream/stop";
        streamStop(receivedData, api, response);
    };
    m_func["api/v1/live/iceCandidate"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/live/iceCandidate" << endl;
        if (GET_CONFIG().use_reverse_proxy == true)
        {
            /* Ignore the ice-candidates if RP is enabled */
        }
        else
        {
            string api = "/api/v1/live/iceCandidate";
            postIceCandidate(receivedData, api, response);
        }
    };
    m_func["api/v1/live/configuration"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/live/configuration" << endl;
        string api = "/api/v1/live/configuration";
        getConfiguration(receivedData, api, response);
    };
    m_func["api/v1/live/ping"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        Json::Value ping_reponse;
        ping_reponse["apiKey"] = "api/v1/live/ping";
        ping_reponse["data"] = Json::nullValue;
        response = ping_reponse;
    };
    m_func["api/v1/live/stream/query"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        string api = "api/v1/live/stream/query";
        getStreamQuery(receivedData, api, response);
    };
    m_func["api/v1/live/stream/status"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        string api = "/api/v1/live/stream/status";
        getStreamStatus(receivedData, api, response);
    };

    // Replay PeerConnection related apis
    m_func["api/v1/replay/iceServers"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/replay/iceServers" << endl;
        string api = "/api/v1/replay/iceServers";
        getIceServers(receivedData, api, response);
    };
    m_func["api/v1/replay/stream/start"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/replay/stream/start" << endl;
        string api = "/api/v1/replay/stream/start";
        streamStart(receivedData, api, response, conn);
    };
    m_func["api/v1/replay/stream/stop"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/replay/stream/stop" << endl;
        string api = "/api/v1/replay/stream/stop";
        streamStop(receivedData, api, response);
    };
    m_func["api/v1/replay/iceCandidate"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/replay/iceCandidate" << endl;
        if (GET_CONFIG().use_reverse_proxy == true)
        {
            /* Ignore the ice-candidates if RP is enabled */
        }
        else
        {
            string api = "/api/v1/replay/iceCandidate";
            postIceCandidate(receivedData, api, response);
        }
    };
    m_func["api/v1/replay/configuration"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "ws:api/v1/replay/configuration" << endl;
        string api = "/api/v1/replay/configuration";
        getConfiguration(receivedData, api, response);
    };
    m_func["api/v1/replay/ping"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        Json::Value ping_reponse;
        ping_reponse["apiKey"] = "api/v1/replay/ping";
        ping_reponse["data"] = Json::nullValue;
        response = ping_reponse;
    };
    m_func["api/v1/replay/stream/query"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        string api = "/api/v1/replay/stream/query";
        getStreamQuery(receivedData, api, response);
    };
    m_func["api/v1/replay/stream/status"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        string api = "/api/v1/replay/stream/status";
        getStreamStatus(receivedData, api, response);
    };

    m_func["/event/streambridge/ext/peerconnection/status"] = [this](const Json::Value& receivedData, Json::Value &response, struct mg_connection *conn) -> void
    {
        LOG(info) << "UE peerconnectionstatus : " << receivedData.toStyledString() << endl;
    };
}

void WebsocketApis::addRequestHandler(std::map<std::string, httpFunction, std::less<>>& func)
{
    // register handlers for streamBridge, live and replay module
    for (auto it : func)
    {
        m_callbackMap.insert({it.first, it.second});
    }
}

#ifndef RELEASE
void WebsocketApis::getVstLogs(const Json::Value& req_info, Json::Value &response, struct mg_connection *conn)
{
    const string logStr = GET_LOG()->getLogs();
    response["logs"] = logStr;
}
#endif

#ifdef USE_GRPC_CLIENT
void WebsocketApis::addDummyUdpTrack(const Json::Value& req_info)
{
    int32_t audio_port = 0, video_port = 0;
    string peerid = req_info.get("peerid", EMPTY_STRING).asString();
    GrpcClient::getInstance()->CreateDummyUDPDevice(peerid, audio_port, video_port);
    GstDummyUdpPipeline::getInstance()->startUdpPipeline(peerid, audio_port, video_port, true);
}
#endif

void WebsocketApis::handleWsDisconnect(struct mg_connection *conn)
{
    LOG(info) << __METHOD_NAME__ << endl;
    std::string connectionId = EMPTY_STRING;
    
    // Get the WebsocketData pointer directly
    WebsocketData *websocketData = (WebsocketData *)mg_get_user_connection_data(conn);
    if (websocketData == nullptr)
    {
        LOG(error) << "Unable to remove websocket connection" << endl;
        return;
    }
    connectionId = websocketData->peerId;

    string api = "/ws/streambridge/disconnect";
    Json::Value request, response;
    request["connectionId"] = connectionId;
    VmsErrorCode ret = EXECUTE_FUNC(api, request, request, response, nullptr)
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "error in handleWsDisconnect" << endl;
    }
}

void WebsocketApis::getIceServers(const Json::Value& req_info, string api, Json::Value &response)
{
    LOG(info) << __METHOD_NAME__ << endl;
    string peerId = req_info.get("peerId", EMPTY_STRING).asString();
    Json::Value request;
    request["method"] = "get";
    request["query"] = "peerId=" + peerId;
    VmsErrorCode ret = EXECUTE_FUNC(api, request, request, response, nullptr)
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "error in getting iceServers" << endl;
    }
    
    if (!api.empty() && api[0] == '/')
    {
        api.erase(0, 1);
    }
    Json::Value iceServerResponse;
    iceServerResponse["apiKey"] = api;
    iceServerResponse["peerId"] = peerId;
    iceServerResponse["data"] = response;
    response = iceServerResponse;
}

void WebsocketApis::streamStart(const Json::Value& req_info, string api, Json::Value &response, struct mg_connection *conn)
{
    LOG(info) << __METHOD_NAME__ << endl;
    Json::Value data = req_info.get("data", EMPTY_STRING);
    string peerId = req_info.get("peerId", EMPTY_STRING).asString();
    bool isClient = data.get("isClient", false).asBool();
    Json::Value jmessage = data.get("sessionDescription", EMPTY_STRING);
    Json::Value options = data.get("options", EMPTY_STRING);
    Json::Value request;
    request["method"] = "post";
    request["peerId"] = peerId;
    request["isClient"] = isClient;
    request["sessionDescription"] = jmessage;
    request["options"] = options;

    struct WebsocketData *websocketContext =  (struct WebsocketData *)mg_get_user_connection_data(conn);
    if (websocketContext == nullptr)
    {
        LOG(error) << "Unable to get websocket context" << endl;
        return;
    }
    string connectionId = websocketContext->peerId;
    LOG(info) << "wsConnectionId: " << connectionId << endl;

    data["wsConnectionId"] = connectionId;
    data["peerId"] = peerId;
    string apiResource = "/setAnswer";
    VmsErrorCode ret = EXECUTE_FUNC(api, request, data, response, nullptr)
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "error in stream start" << endl;
    }

    string apiPrefix = "/api/v1/";
    string apiModule = "";
    size_t prefixStart = api.find(apiPrefix);
    if (prefixStart != std::string::npos)
    {
        prefixStart += apiPrefix.length();
        size_t endPos = api.find('/', prefixStart);
        if (endPos == std::string::npos)
        {
            apiModule = api.substr(prefixStart);
        }
        else
        {
            apiModule = api.substr(prefixStart, endPos - prefixStart);
        }
    }
    Json::Value offerResponse;
    offerResponse["apiKey"] = "api/v1/" + apiModule + apiResource;
    offerResponse["peerId"] = peerId;
    offerResponse["data"] = response;
    response = offerResponse;
}

void WebsocketApis::setAnswer(const Json::Value& req_info, string api, Json::Value &response)
{
    LOG(info) << __METHOD_NAME__ << endl;
    Json::Value data = req_info.get("data", EMPTY_STRING);
    string peerId = req_info.get("peerId", EMPTY_STRING).asString();
    Json::Value jmessage = data.get("sessionDescription", EMPTY_STRING);
    Json::Value request, in;
    request["method"] = "post";
    request["query"] = "peerId=" + peerId;
    in["sessionDescription"] = jmessage;
    Json::Value apiResponse = Json::nullValue;
    VmsErrorCode ret = EXECUTE_FUNC(api, request, data, response, nullptr)
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "error in stream start" << endl;
        apiResponse = response;
    }

    if (!api.empty() && api[0] == '/')
    {
        api.erase(0, 1);
    }
    Json::Value answerResponse;
    if (GET_CONFIG().use_external_peerconnection == false)
    {
        answerResponse["apiKey"] = api;
        answerResponse["peerId"] = peerId;
        answerResponse["data"] = apiResponse;
    }
    response = answerResponse;
}

void WebsocketApis::postIceCandidate(const Json::Value& req_info, string api, Json::Value &response)
{
    LOG(info) << __METHOD_NAME__ << endl;
    Json::Value data = req_info.get("data", Json::nullValue);
    string peerId = req_info.get("peerId", EMPTY_STRING).asString();
    Json::Value request;
    request["method"] = "post";
    request["peerId"] = peerId;
    if (data.isNull())
    {
        LOG(error) << "ICE candidate data is null" << endl;
        return;
    }
    request["candidate"] = data.get("candidate", Json::nullValue);
    VmsErrorCode ret = EXECUTE_FUNC(api, request, request, response, nullptr)
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "error in peerconnection post iceCandidate" << endl;
        return;
    }
}

void WebsocketApis::getConfiguration(const Json::Value& req_info, string api, Json::Value &response)
{
    LOG(info) << __METHOD_NAME__ << endl;
    string peerId = req_info.get("peerId", EMPTY_STRING).asString();
    Json::Value request;
    request["method"] = "get";
    VmsErrorCode ret = EXECUTE_FUNC(api, request, request, response, nullptr)
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "error in get configuration" << endl;
    }

    if (!api.empty() && api[0] == '/')
    {
        api.erase(0, 1);
    }
    Json::Value configResponse;
    configResponse["apiKey"] = api;
    configResponse["peerId"] = peerId;
    configResponse["data"] = response;
    response = configResponse;
}

void WebsocketApis::streamStop(const Json::Value& req_info, string api, Json::Value &response)
{
    LOG(info) << __METHOD_NAME__ << endl;
    string peerId = req_info.get("peerId", EMPTY_STRING).asString();
    Json::Value request;
    request["method"] = "post";
    request["peerId"] = peerId;
    VmsErrorCode ret = EXECUTE_FUNC(api, request, request, response, nullptr)
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "error in " << api << endl;
    }

    if (!api.empty() && api[0] == '/')
    {
        api.erase(0, 1);
    }
    Json::Value stopResponse;
    stopResponse["apiKey"] = api;
    stopResponse["peerId"] = peerId;
    stopResponse["data"] = response;
    response = stopResponse;
}

void WebsocketApis::setRAGStatus(const Json::Value& req_info, Json::Value &response)
{
    LOG(info) << "api/v1/streambridge/rag/status" << endl;
    bool enableRAG = req_info.get("data", false).asBool();
    string pluginServerUrl = GET_CONFIG().tokkio_plugin_server_url;
    string api = pluginServerUrl + TOKKIO_PLUGIN_SERVER_USE_RAG_API_PATH;
    // Make the POST request to enable-disable RAG pipeline
    Json::Value postPayload;
    string postResponse;
    postPayload["use_rag"] = enableRAG;
    LOG(info) << "Calling plugin server API, POST: " << api << endl;
    if (curlPostRequest(api, postResponse, postPayload, {}))
    {
        Json::Value jout = stringToJson(postResponse);
        LOG(info) << "Received response from plugin server: \n" << jout.toStyledString() << endl;
    }
}

void WebsocketApis::setRAGEndpoint(const Json::Value& req_info, Json::Value &response)
{
    LOG(info) << "api/v1/streambridge/rag/endpoint" << endl;
    string ragEndpoint = req_info.get("data", EMPTY_STRING).asString();
    string pluginServerUrl = GET_CONFIG().tokkio_plugin_server_url;
    string api = pluginServerUrl + TOKKIO_PLUGIN_SERVER_RAG_ENDPOINT_API_PATH;
    // Make the POST request to set RAG endpoint
    Json::Value postPayload;
    string postResponse;
    // Query params
    std::map<string, string, std::less<>> queryParams;
    queryParams["rag_endpoint"] = ragEndpoint;
    api = getUrlWithQueryParameters(api, queryParams);
    // POST request
    LOG(info) << "Calling plugin server API, POST: " << api << endl;
    if (curlPostRequest(api, postResponse, postPayload, {}))
    {
        Json::Value jout = stringToJson(postResponse);
        LOG(info) << "Received response from plugin server: \n" << jout.toStyledString() << endl;
    }
}

void WebsocketApis::getStreamStatus(const Json::Value& req_info, string api, Json::Value &response) 
{
    // Extract peer ID once
    const string peerId = req_info.get("peerId", EMPTY_STRING).asString();
    
    // Prepare request
    Json::Value request;
    request["method"] = "get"; 
    request["query"] = "peerId=" + peerId;

    // Execute request
    VmsErrorCode ret = EXECUTE_FUNC(api, request, request, response, nullptr);
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Failed to get stream status for peer: " << peerId << endl;
        return;
    }

    // Build response
    if (!api.empty() && api[0] == '/')
    {
        api.erase(0, 1);
    }
    Json::Value streamStatus;
    streamStatus["data"] = response;
    streamStatus["apiKey"] = api;
    streamStatus["peerId"] = peerId;
    
    response = streamStatus;
}

void WebsocketApis::getStreamQuery(const Json::Value& req_info, string api, Json::Value &response)
{
    // Extract peer ID once
    const string peerId = req_info.get("peerId", EMPTY_STRING).asString();
    
    // Prepare request
    Json::Value request;
    request["method"] = "get";
    request["query"] = "peerId=" + peerId;

    // Execute request
    VmsErrorCode ret = EXECUTE_FUNC(api, request, request, response, nullptr);
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Failed to get stream query for peer: " << peerId << endl;
        return;
    }

    // Build response
    if (!api.empty() && api[0] == '/')
    {
        api.erase(0, 1);
    }
    Json::Value streamStatus;
    streamStatus["data"] = response;
    streamStatus["apiKey"] = api;
    streamStatus["peerId"] = peerId;
    
    response = streamStatus;
}

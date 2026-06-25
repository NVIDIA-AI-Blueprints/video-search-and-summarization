/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <jsoncpp/json/json.h>
#include "device_manager.h"

inline constexpr int PORT_TIMED_WAIT_STATE = 6;

// HTTP and JSON safety constants
inline constexpr int MAX_JSON_SIZE_BYTES = 1000000;
inline constexpr int MAX_JSON_DEPTH = 50;
inline constexpr int MAX_JSON_KEYS = 10000;
inline constexpr int MAX_JSON_OBJECT_KEYS = 1000;
inline constexpr int MAX_JSON_ARRAY_ELEMENTS = 10000;
inline constexpr int MAX_JSON_KEY_LENGTH = 256;
inline constexpr int MAX_JSON_STRING_LENGTH = 65536;
inline constexpr int MAX_JSON_STACK_LIMIT = 50;

typedef struct _curlRequestFields
{
    std::string m_url;
    std::string m_method;
    std::string m_jsonData;
    int m_timeout;
    int m_httpErrorCode;
    string m_httpErrorString;
} CurlRequestFields;

int openProbe();
int sendProbe(const std::string& inIpAddress = "");
int sendProbeToSensor(nv_vms::SensorInfo& sensor);
void closeProbe();
int stopOnvifDiscovery();
int getProbeMatch(nv_vms::SensorInfo& sensor);
bool isCameraOnline(nv_vms::SensorInfo& sensor);
bool curlGetRequest(const string& url, long& http_code);
bool curlGetRequest(const string url, string& outData);
bool curlGetRequest(const string& url, const string& username,
                              const string& password, string& outData);
bool curlPostRequest(const string& httpUrl, const string& username, const string& password,
        const string& rest_api, const string& params, string& response, bool is_digest = false, vector<string> headers = vector<string>());
bool curlPostRequest_2(const string& httpUrl, vector<string> header_list, string& response);
bool curlPostRequest(const string url, string& outData, const Json::Value& postData);
bool curlDeleteRequest(const string& httpUrl, vector<string> header_list, string& response);
int checkIfPortAvailable(const int& port, const string& proto = "udp");
bool curlGetRequest(const string url, string& outData, const vector<string>& customHeaders);
bool curlPostRequest(const string url, string& outData, const Json::Value& postData, const vector<string>& customHeaders);
int curlSendRequest(CurlRequestFields& curlFields, string& outData);
bool isTcpPortAvailable(int port);
bool isRtspServerReachable(const string& rtsp_server, bool is_url_provided = true);
string getUrlWithQueryParameters(const string& url, const std::map<string, string, std::less<>>& queryParams);

// HTTP utility functions for safer operations
bool safeStringEqual(const char* str1, const char* str2);
bool isValidHttpMethod(const char* method);
bool isValidContentLength(long long contentLength, long long maxLength, const char* httpMethod = nullptr);
std::string safeGetString(const char* ptr, const std::string& defaultValue = "", bool sanitize = false);

// JSON safety functions for preventing attacks
bool isJsonSafe(const std::string& jsonStr, size_t maxDepth = MAX_JSON_DEPTH, size_t maxKeys = MAX_JSON_KEYS);
Json::CharReaderBuilder createSafeJsonReaderBuilder();
bool validateJsonStructure(const Json::Value& json, size_t maxDepth = MAX_JSON_DEPTH, size_t currentDepth = 0);

/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/**
 * @file rtspserver.cpp
 * @brief Unit tests for RtspServerManager C++ methods
 *
 * Tests the RTSP proxy module by calling C++ methods directly.
 * Uses the handler map from getHttpApi() for routed endpoints, and
 * direct public methods for stream CRUD and base URL.
 */

#include "gtest/gtest.h"
#include <iostream>
#include <string>
#include <jsoncpp/json/json.h>

#include "rtspservermanager.h"
#include "vstmodule.h"
#include "utils/mock_civetweb.h"
#include "utils/rtspserver_utils.h"

namespace TestConfig
{
    extern std::string videoDirectory;
}

using namespace std;
using namespace nv_vms;

class RtspServerManagerTest : public ::testing::Test
{
protected:
    static RtspServerManager* s_rtspMgr;
    static std::map<std::string, HttpServerRequestHandler::httpFunction> s_handlers;
    static bool s_initialized;

    static void SetUpTestSuite()
    {
        cout << "\n[SUITE-SETUP] Initializing RtspServerManager suite..." << endl;

        if (!RtspServerTestUtils::start(TestConfig::videoDirectory))
            cout << "[SUITE-SETUP] RTSP server via test utils not loaded" << endl;

        ModuleLoader* moduleLoader = ModuleLoader::getInstance();
        int ret = moduleLoader->initialize(ModuleRtspServer);
        if (ret != 0)
        {
            cout << "[SUITE-SETUP] RtspServerManager module initialization failed" << endl;
            return;
        }

        s_rtspMgr = GET_RTSPSERVER();
        if (s_rtspMgr)
        {
            IVstModule* mod = ModuleLoader::getInstance()->getRtspServerMgmtInstance();
            if (mod)
                s_handlers = mod->getHttpApi();
            s_initialized = true;
            cout << "[SUITE-SETUP] RtspServerManager ready, "
                 << s_handlers.size() << " handlers" << endl;
        }
        else
        {
            cout << "[SUITE-SETUP] RtspServerManager instance is null" << endl;
        }
    }

    static void TearDownTestSuite()
    {
        cout << "[SUITE-CLEANUP] RtspServerManager suite done (cleanup deferred to process exit)\n" << endl;
        s_rtspMgr = nullptr;
        s_handlers.clear();
        s_initialized = false;
    }

    VmsErrorCode callHandler(const string& urlKey, const string& method,
                             const Json::Value& input, Json::Value& response)
    {
        auto it = s_handlers.find(urlKey);
        if (it == s_handlers.end())
            return VmsErrorCode::VMSNotSupportedError;

        Json::Value req_info;
        req_info["url"] = urlKey;
        req_info["method"] = method;

        MockConnection mockConn;
        mockConn.requestInfo.setMethod(method);
        mockConn.requestInfo.setUri(urlKey);

        return it->second(req_info, input, response,
                          reinterpret_cast<struct mg_connection*>(&mockConn));
    }

    VmsErrorCode callStreamAPI(const string& url, const string& method,
                               const Json::Value& input, Json::Value& response)
    {
        Json::Value req_info;
        req_info["url"] = url;
        req_info["method"] = method;

        MockConnection mockConn;
        mockConn.requestInfo.setMethod(method);
        mockConn.requestInfo.setUri(url);

        return s_rtspMgr->handleStreamAPIrequest(
            req_info, input, response,
            reinterpret_cast<struct mg_connection*>(&mockConn));
    }
};

RtspServerManager* RtspServerManagerTest::s_rtspMgr = nullptr;
std::map<std::string, HttpServerRequestHandler::httpFunction> RtspServerManagerTest::s_handlers;
bool RtspServerManagerTest::s_initialized = false;

TEST_F(RtspServerManagerTest, GetStreamList)
{
    if (!s_initialized) GTEST_SKIP() << "RtspServerManager not available";

    cout << "[TEST] GET /api/v1/proxy/streams" << endl;

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/proxy/streams", "GET",
                                     Json::Value(), response);

    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

TEST_F(RtspServerManagerTest, GetConfiguration)
{
    if (!s_initialized) GTEST_SKIP() << "RtspServerManager not available";

    cout << "[TEST] GET /api/v1/proxy/configuration" << endl;

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/proxy/configuration", "GET",
                                     Json::Value(), response);

    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isObject());
}

TEST_F(RtspServerManagerTest, GetRtspBaseUrl)
{
    if (!s_initialized) GTEST_SKIP() << "RtspServerManager not available";

    cout << "[TEST] getRtspBaseUrl()" << endl;

    string baseUrl = s_rtspMgr->getRtspBaseUrl();
    cout << "[TEST] Base URL: " << (baseUrl.empty() ? "(empty)" : baseUrl) << endl;

    EXPECT_FALSE(baseUrl.empty()) << "RTSP base URL should be non-empty when server is running";
}

TEST_F(RtspServerManagerTest, AddAndRemoveStream)
{
    if (!s_initialized) GTEST_SKIP() << "RtspServerManager not available";

    cout << "[TEST] Add stream then remove it" << endl;

    string testStreamUrl = RtspServerTestUtils::getTestStreamUrl();
    if (testStreamUrl.empty())
        GTEST_SKIP() << "No test RTSP stream URL available";

    Json::Value addInput;
    addInput["url"] = testStreamUrl;
    addInput["id"] = "gtest-proxy-stream";

    Json::Value addResponse;
    VmsErrorCode addResult = callHandler("/api/v1/proxy/stream/add", "POST",
                                        addInput, addResponse);
    cout << "[TEST] Add result: " << static_cast<int>(addResult) << endl;

    EXPECT_TRUE(addResult == VmsErrorCode::NoError ||
                addResult == VmsErrorCode::CameraNotFoundError)
        << "Add stream should succeed or return CameraNotFoundError (no sensor registered)";

    Json::Value removeResponse;
    VmsErrorCode removeResult = s_rtspMgr->removeStream("gtest-proxy-stream", removeResponse);
    cout << "[TEST] Remove result: " << static_cast<int>(removeResult) << endl;

    EXPECT_TRUE(removeResult == VmsErrorCode::NoError ||
                removeResult == VmsErrorCode::CameraNotFoundError);
}

TEST_F(RtspServerManagerTest, UnsupportedMethod)
{
    if (!s_initialized) GTEST_SKIP() << "RtspServerManager not available";

    cout << "[TEST] PUT on stream CRUD endpoint (unsupported)" << endl;

    Json::Value response;
    VmsErrorCode result = callStreamAPI("/api/v1/proxy/stream/some-id", "PUT",
                                       Json::Value(), response);

    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    EXPECT_NE(result, VmsErrorCode::NoError)
        << "PUT should not be a supported method on proxy stream CRUD";
}

TEST_F(RtspServerManagerTest, GetActiveClientSessions)
{
    if (!s_initialized) GTEST_SKIP() << "RtspServerManager not available";

    cout << "[TEST] GET /api/v1/proxy/activeClientSessions" << endl;

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/proxy/activeClientSessions", "GET",
                                     Json::Value(), response);

    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

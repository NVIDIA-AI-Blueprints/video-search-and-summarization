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
 * @file livestream.cpp
 * @brief Unit tests for LivePeerConnection C++ methods
 *
 * Tests the live streaming module by calling handler functions directly
 * from the getHttpApi() map. Uses static suite setup to avoid segfaults
 * from repeated PeerConnectionManager init/deinit.
 */

#include "gtest/gtest.h"
#include <iostream>
#include <string>
#include <jsoncpp/json/json.h>

#include "LivePeerConnection.h"
#include "vstmodule.h"
#include "utils/mock_civetweb.h"

using namespace std;

class LiveStreamTest : public ::testing::Test
{
protected:
    static IVstModule* s_module;
    static std::map<std::string, HttpServerRequestHandler::httpFunction> s_handlers;
    static bool s_initialized;

    static void SetUpTestSuite()
    {
        cout << "\n[SUITE-SETUP] Initializing LivePeerConnection suite..." << endl;

        ModuleLoader* moduleLoader = ModuleLoader::getInstance();
        int ret = moduleLoader->initialize(ModuleLiveStream);
        if (ret != 0)
        {
            cout << "[SUITE-SETUP] LivePeerConnection module initialization failed" << endl;
            return;
        }

        s_module = moduleLoader->getPeerConnectionLiveInstance();
        if (s_module)
        {
            s_handlers = s_module->getHttpApi();
            s_initialized = true;
            cout << "[SUITE-SETUP] LivePeerConnection ready, "
                 << s_handlers.size() << " handlers registered" << endl;
        }
        else
        {
            cout << "[SUITE-SETUP] LivePeerConnection instance is null" << endl;
        }
    }

    static void TearDownTestSuite()
    {
        // Do NOT call deInitialize() here -- PeerConnectionManager's destructor
        // corrupts WebRTC global state, which would prevent ReplayStreamTest
        // from initializing. Cleanup is deferred to process exit.
        cout << "[SUITE-CLEANUP] LivePeerConnection suite done (cleanup deferred)\n" << endl;
    }

    VmsErrorCode callHandler(const string& urlKey, const string& method,
                             const Json::Value& input, Json::Value& response)
    {
        auto it = s_handlers.find(urlKey);
        if (it == s_handlers.end())
        {
            cout << "[WARN] Handler not found for: " << urlKey << endl;
            return VmsErrorCode::VMSNotSupportedError;
        }

        Json::Value req_info;
        req_info["url"] = urlKey;
        req_info["method"] = method;

        MockConnection mockConn;
        mockConn.requestInfo.setMethod(method);
        mockConn.requestInfo.setUri(urlKey);

        return it->second(req_info, input, response,
                          reinterpret_cast<struct mg_connection*>(&mockConn));
    }
};

IVstModule* LiveStreamTest::s_module = nullptr;
std::map<std::string, HttpServerRequestHandler::httpFunction> LiveStreamTest::s_handlers;
bool LiveStreamTest::s_initialized = false;

TEST_F(LiveStreamTest, GetVersion)
{
    if (!s_initialized) GTEST_SKIP() << "LivePeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/live/version", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/live/version => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

TEST_F(LiveStreamTest, GetHelp)
{
    if (!s_initialized) GTEST_SKIP() << "LivePeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/live/help", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/live/help => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isArray());
    EXPECT_GT(response.size(), 0u);
}

TEST_F(LiveStreamTest, GetConfiguration)
{
    if (!s_initialized) GTEST_SKIP() << "LivePeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/live/configuration", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/live/configuration => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isObject());
}

TEST_F(LiveStreamTest, GetStreams)
{
    if (!s_initialized) GTEST_SKIP() << "LivePeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/live/streams", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/live/streams => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

TEST_F(LiveStreamTest, StartStreamMissingParams)
{
    if (!s_initialized) GTEST_SKIP() << "LivePeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/live/stream/*", "POST",
                                     Json::Value(), response);

    cout << "[TEST] POST /api/v1/live/stream/* (empty body) => " << static_cast<int>(result) << endl;
    EXPECT_NE(result, VmsErrorCode::NoError)
        << "Starting a stream with no parameters should fail";
}

TEST_F(LiveStreamTest, GetIceServers)
{
    if (!s_initialized) GTEST_SKIP() << "LivePeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/live/iceServers", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/live/iceServers => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

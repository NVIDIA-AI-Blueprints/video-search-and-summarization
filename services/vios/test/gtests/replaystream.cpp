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
 * @file replaystream.cpp
 * @brief Unit tests for ReplayPeerConnection C++ methods
 *
 * The PeerConnectionManager singleton cannot be re-created after destruction,
 * so these tests initialize ModuleReplayStream only if PeerConnectionManager
 * is not already loaded (i.e., LiveStreamTest hasn't run yet in this process).
 * If the module fails to initialize, all tests are skipped.
 */

#include "gtest/gtest.h"
#include <iostream>
#include <string>
#include <jsoncpp/json/json.h>

#include "ReplayPeerConnection.h"
#include "vstmodule.h"
#include "utils/mock_civetweb.h"

using namespace std;

class ReplayStreamTest : public ::testing::Test
{
protected:
    static IVstModule* s_module;
    static std::map<std::string, HttpServerRequestHandler::httpFunction> s_handlers;
    static bool s_initialized;
    static bool s_init_attempted;

    static void SetUpTestSuite()
    {
        if (s_init_attempted) return;
        s_init_attempted = true;

        cout << "\n[SUITE-SETUP] Initializing ReplayPeerConnection suite..." << endl;

        ModuleLoader* moduleLoader = ModuleLoader::getInstance();

        // PeerConnectionManager may already be loaded by LiveStreamTest.
        // If the replay instance is already present, just grab it.
        s_module = moduleLoader->getPeerConnectionReplayInstance();
        if (!s_module)
        {
            int ret = moduleLoader->initialize(ModuleReplayStream);
            if (ret != 0)
            {
                cout << "[SUITE-SETUP] ReplayPeerConnection module initialization failed" << endl;
                return;
            }
            s_module = moduleLoader->getPeerConnectionReplayInstance();
        }

        if (s_module)
        {
            s_handlers = s_module->getHttpApi();
            s_initialized = true;
            cout << "[SUITE-SETUP] ReplayPeerConnection ready, "
                 << s_handlers.size() << " handlers registered" << endl;
        }
        else
        {
            cout << "[SUITE-SETUP] ReplayPeerConnection instance is null" << endl;
        }
    }

    static void TearDownTestSuite()
    {
        // Cleanup deferred to process exit to avoid PeerConnectionManager
        // destructor corrupting WebRTC global state.
        cout << "[SUITE-CLEANUP] ReplayPeerConnection suite done (cleanup deferred)\n" << endl;
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

IVstModule* ReplayStreamTest::s_module = nullptr;
std::map<std::string, HttpServerRequestHandler::httpFunction> ReplayStreamTest::s_handlers;
bool ReplayStreamTest::s_initialized = false;
bool ReplayStreamTest::s_init_attempted = false;

TEST_F(ReplayStreamTest, GetVersion)
{
    if (!s_initialized) GTEST_SKIP() << "ReplayPeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/replay/version", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/replay/version => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

TEST_F(ReplayStreamTest, GetHelp)
{
    if (!s_initialized) GTEST_SKIP() << "ReplayPeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/replay/help", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/replay/help => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isArray());
    EXPECT_GT(response.size(), 0u);
}

TEST_F(ReplayStreamTest, GetConfiguration)
{
    if (!s_initialized) GTEST_SKIP() << "ReplayPeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/replay/configuration", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/replay/configuration => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isObject());
}

TEST_F(ReplayStreamTest, GetStreams)
{
    if (!s_initialized) GTEST_SKIP() << "ReplayPeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/replay/streams", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/replay/streams => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

TEST_F(ReplayStreamTest, StartStreamMissingParams)
{
    if (!s_initialized) GTEST_SKIP() << "ReplayPeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/replay/stream/*", "POST",
                                     Json::Value(), response);

    cout << "[TEST] POST /api/v1/replay/stream/* (empty body) => " << static_cast<int>(result) << endl;
    EXPECT_NE(result, VmsErrorCode::NoError)
        << "Starting a replay stream with no parameters should fail";
}

TEST_F(ReplayStreamTest, GetIceServers)
{
    if (!s_initialized) GTEST_SKIP() << "ReplayPeerConnection not available";

    Json::Value response;
    VmsErrorCode result = callHandler("/api/v1/replay/iceServers", "GET",
                                     Json::Value(), response);

    cout << "[TEST] GET /api/v1/replay/iceServers => " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

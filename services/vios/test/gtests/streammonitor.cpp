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
 * @file streammonitor.cpp
 * @brief Basic unit tests for StreamMonitor (RTSP client)
 *
 * StreamMonitor is an RTSP client that provides bitstream to consumers.
 * These tests load the RTSP server module via ModuleLoader to verify
 * client-side RTSP connection and data flow. No other tests are added.
 */

#include "gtest/gtest.h"
#include <iostream>
#include <string>
#include <chrono>
#include <thread>
#include <atomic>
#include <memory>

#include "stream_monitor.h"
#include "media_consumer.h"
#include "utils/rtspserver_utils.h"
#include "vstmodule.h"
#include "device_manager.h"

using namespace std;
using namespace nv_vms;

namespace TestConfig
{
    extern std::string videoDirectory;
}

/** Minimal consumer that counts frames received from StreamMonitor (RTSP client) */
class TestFrameCountConsumer : public IMediaDataConsumer
{
public:
    TestFrameCountConsumer() : IMediaDataConsumer("TestFrameCountConsumer") {}
    void onFrame(FrameParams&) override { m_count++; }
    void onFrame(std::shared_ptr<RawFrameParams> frame_data) override
    {
        if (frame_data && frame_data->m_map.size > 0)
            m_count++;
    }
    int getCount() const { return m_count.load(); }
private:
    std::atomic<int> m_count{0};
};

/**
 * @brief Fixture: loads RTSP module via ModuleLoader and injects a minimal
 * DeviceManager so StreamMonitor's QosRtspClient gets a valid instance (no null deref).
 */
class StreamMonitorTest : public ::testing::Test
{
protected:
    void SetUp() override
    {
        cout << "\n[SETUP] StreamMonitor tests..." << endl;
        m_deviceManager = std::make_shared<nv_vms::DeviceManager>();
        ModuleLoader::getInstance()->setDeviceManagerForTest(m_deviceManager);
        if (!RtspServerTestUtils::start(TestConfig::videoDirectory))
            cout << "[SETUP] RTSP server not started (port in use or failure)" << endl;
    }
    void TearDown() override
    {
        RtspServerTestUtils::stop();
        ModuleLoader::getInstance()->setDeviceManagerForTest(nullptr);
        cout << "[CLEANUP] StreamMonitor tests done\n" << endl;
    }

    std::shared_ptr<nv_vms::DeviceManager> m_deviceManager;
};

/**
 * @brief Test StreamMonitor singleton exists
 */
TEST_F(StreamMonitorTest, GetInstance)
{
    StreamMonitor* sm = StreamMonitor::getInstance();
    ASSERT_NE(sm, nullptr);
    EXPECT_TRUE(sm->isRunning());
}

/**
 * @brief Test RTSP client connects to RTSP module and receives bitstream
 *
 * Registers a consumer with the test RTSP URL; StreamMonitor starts the RTSP
 * client and delivers frames. Fixture injects a minimal DeviceManager so
 * QosRtspClient has a valid instance.
 */
TEST_F(StreamMonitorTest, RtspClient)
{
    if (!RtspServerTestUtils::isRunning())
        GTEST_SKIP() << "RTSP server not running";
    string url = RtspServerTestUtils::getTestStreamUrl();   
    if (url.empty())
        GTEST_SKIP() << "Test stream URL not available";
    StreamMonitor* sm = StreamMonitor::getInstance();
    ASSERT_NE(sm, nullptr);
    auto consumer = std::make_shared<TestFrameCountConsumer>();
    cout << "[TEST] Register consumer for " << url << endl;
    sm->registerDataCallback(url, consumer);
    std::this_thread::sleep_for(std::chrono::seconds(5));
    int count = consumer->getCount();
    cout << "[TEST] Frames received: " << count << endl;
    sm->deregisterDataCallback(consumer, url);
    cout << "[TEST] Deregistered" << endl;
    EXPECT_GT(count, 0) << "Expected at least one frame; 0 frames usually means 404 Stream Not Found "
                           "(ensure video dir has sample_10sec_h264.mp4 for /live/test-stream, e.g. --video-dir=tools/data)";
}

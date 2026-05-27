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
 * @file mediapipelinetest.cpp
 * @brief Unit tests for media pipelines (SingleStreamPipelineBuilder, CompositePipelineBuilder)
 *
 * Tests pipeline creation and frame flow using predefined RTSP streams from rtspserver_utils.
 * Verification is done via UNIT_TEST hooks in WebrtcSinkConsumer (getFrameCountForTest).
 * Tests are pipeline-focused, not e2e.
 */

#include "gtest/gtest.h"
#include <iostream>
#include <string>
#include <chrono>
#include <thread>
#include <functional>
#include <map>
#include <memory>
#include <vector>
#include <filesystem>

#include "CommonVideoSource.h"
#include "webrtc_sink_consumer.h"
#include "utils/rtspserver_utils.h"
#include "utils/db_test_utils.h"
#include "vstmodule.h"
#include "device_manager.h"
#include "config.h"
#include "sensor_info.h"

using namespace std;
using namespace nv_vms;

namespace TestConfig
{
    extern std::string videoDirectory;
    extern std::vector<std::string>& videoFiles;
}

/**
 * @brief Fixture: loads RTSP module and injects DeviceManager for pipeline tests.
 */
class MediaPipelineTest : public ::testing::Test
{
protected:
    void SetUp() override
    {
        cout << "\n[SETUP] Media pipeline tests..." << endl;
        m_deviceManager = std::make_shared<nv_vms::DeviceManager>();
        ModuleLoader::getInstance()->setDeviceManagerForTest(m_deviceManager);
        if (!RtspServerTestUtils::start(TestConfig::videoDirectory))
            cout << "[SETUP] RTSP server not started (port in use or failure)" << endl;
    }
    void TearDown() override
    {
        m_videoSource.reset();
        RtspServerTestUtils::stop();
        ModuleLoader::getInstance()->setDeviceManagerForTest(nullptr);
        cout << "[CLEANUP] Media pipeline tests done\n" << endl;
    }

    /**
     * Poll for image capture buffer (JPEG) for up to timeoutSeconds.
     * Returns non-empty string when capture succeeds, empty on timeout.
     */
    std::string pollImageBuffer(int timeoutSeconds)
    {
        if (!m_videoSource)
            return std::string();
        for (int i = 0; i < timeoutSeconds * 10; ++i)
        {
            std::string buf = m_videoSource->getBuffer();
            if (!buf.empty())
                return buf;
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        return m_videoSource->getBuffer();
    }

    /**
     * Poll WebrtcSinkConsumer frame count for up to timeoutSeconds.
     * Returns the count when at least minFrames received, or final count after timeout.
     */
    int pollFrameCount(WebrtcSinkConsumer* consumer, int minFrames, int timeoutSeconds)
    {
        if (!consumer)
            return 0;
        for (int i = 0; i < timeoutSeconds * 10; ++i)
        {
            int count = consumer->getFrameCountForTest();
            if (count >= minFrames)
                return count;
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        return consumer->getFrameCountForTest();
    }

    std::shared_ptr<nv_vms::DeviceManager> m_deviceManager;
    std::unique_ptr<CommonVideoSource> m_videoSource;
};

/**
 * @brief Test single-stream standard pipeline (decoder -> encoder -> webrtc)
 */
TEST_F(MediaPipelineTest, SingleStreamStandardPipeline)
{
    if (!RtspServerTestUtils::isRunning())
        GTEST_SKIP() << "RTSP server not running";
    string url = RtspServerTestUtils::getTestStreamUrl();
    if (url.empty())
        GTEST_SKIP() << "Test stream URL not available";
    std::map<std::string, std::string, std::less<>> opts;
    opts["peerid"] = "media-pipeline-test-peer";
    opts["streamId"] = "test-stream";
    cout << "[TEST] Creating standard pipeline for " << url << endl;
    try
    {
        m_videoSource = std::make_unique<CommonVideoSource>(url, opts);
        m_videoSource->createConsumerPipeline();
        m_videoSource->startStream();
    }
    catch (const std::exception& e)
    {
        FAIL() << "Failed to create pipeline: " << e.what();
    }
    std::shared_ptr<WebrtcSinkConsumer> consumer = m_videoSource->getWebrtcConsumer();
    ASSERT_NE(consumer, nullptr) << "WebrtcSinkConsumer should be present for standard pipeline";
    int count = pollFrameCount(consumer.get(), 1, 5);
    cout << "[TEST] Frames received at WebrtcSinkConsumer: " << count << endl;
    EXPECT_GT(count, 0) << "Expected at least one frame; 0 frames usually means 404 Stream Not Found "
                           "(ensure video dir has sample_10sec_h264.mp4, e.g. --video-dir=tools/data)";
}

/**
 * @brief Test composite pipeline (2+ decoders -> compositor -> encoder -> webrtc)
 *
 * DISABLED: NvCompositor::~NvCompositor() has a race condition where
 * decoder threads (nvsurfacepool) continue allocating surfaces as the
 * compositor destructor frees memory, causing heap corruption on teardown.
 * Re-enable once the NvCompositor teardown synchronization is fixed.
 */
TEST_F(MediaPipelineTest, DISABLED_CompositePipeline)
{
    if (!RtspServerTestUtils::isRunning())
        GTEST_SKIP() << "RTSP server not running";
    std::vector<std::string> urls = RtspServerTestUtils::getVideoFileUrls();
    if (urls.size() < 2)
        GTEST_SKIP() << "Composite pipeline needs at least 2 video files; found " << urls.size();
    std::string uri = urls[0] + "#sensor1," + urls[1] + "#sensor2";
    std::map<std::string, std::string, std::less<>> opts;
    opts["peerid"] = "media-pipeline-composite-peer";
    opts["streamId"] = "composite-stream";
    opts["do_composition"] = "true";
    cout << "[TEST] Creating composite pipeline for " << uri << endl;
    try
    {
        m_videoSource = std::make_unique<CommonVideoSource>(uri, opts);
        m_videoSource->createConsumerPipeline();
        m_videoSource->startStream();
    }
    catch (const std::exception& e)
    {
        FAIL() << "Test " << __func__ << " failed: " << "Composite pipeline requires HW (v4l2 enc/dec). Skipping: " << e.what();
    }
    // Composite pipelines may take longer to create the WebrtcSinkConsumer
    // (compositor thread needs to start up). Poll up to 5 seconds.
    std::shared_ptr<WebrtcSinkConsumer> consumer;
    for (int i = 0; i < 50 && !consumer; ++i)
    {
        consumer = m_videoSource->getWebrtcConsumer();
        if (!consumer)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    if (!consumer)
    {
        // Give pipeline threads time to reach a stable state before teardown
        // to avoid race conditions in compositor destructor.
        std::this_thread::sleep_for(std::chrono::seconds(3));
        GTEST_SKIP() << "WebrtcSinkConsumer not available for composite pipeline "
                        "(HW codec/compositor required or pipeline creation failed)";
    }
    int count = pollFrameCount(consumer.get(), 1, 5);
    cout << "[TEST] Frames received at WebrtcSinkConsumer: " << count << endl;
    EXPECT_GT(count, 0) << "Expected at least one frame from composite pipeline";
}

/**
 * @brief Test GodsEyeView pipeline (skip if floor map not configured)
 */
TEST_F(MediaPipelineTest, DISABLED_GodsEyeViewPipeline)
{
    if (!RtspServerTestUtils::isRunning())
        FAIL() << "Test " << __func__ << " failed: " << "RTSP server not running";
    string url = RtspServerTestUtils::getTestStreamUrl();
    if (url.empty())
        FAIL() << "Test " << __func__ << " failed: " << "Test stream URL not available";
    std::string floorMap = GET_CONFIG().floor_map_file_path;
    if (floorMap.empty())
        FAIL() << "Test " << __func__ << " failed: " << "GodsEyeView requires floor_map_file_path in config";
    std::map<std::string, std::string, std::less<>> opts;
    opts["peerid"] = "media-pipeline-gev-peer";
    opts["streamId"] = "gev-stream";
    opts["gods_eye_view"] = "true";
    cout << "[TEST] Creating GodsEyeView pipeline for " << url << endl;
    try
    {
        m_videoSource = std::make_unique<CommonVideoSource>(url, opts);
        m_videoSource->createConsumerPipeline();
        m_videoSource->startStream();
    }
    catch (const std::exception& e)
    {
        FAIL() << "Failed to create GodsEyeView pipeline: " << e.what();
    }
    std::shared_ptr<WebrtcSinkConsumer> consumer = m_videoSource->getWebrtcConsumer();
    ASSERT_NE(consumer, nullptr) << "WebrtcSinkConsumer should be present for GodsEyeView pipeline";
    int count = pollFrameCount(consumer.get(), 1, 5);
    cout << "[TEST] Frames received at WebrtcSinkConsumer: " << count << endl;
    EXPECT_GT(count, 0) << "Expected at least one frame from GodsEyeView pipeline";
}

/**
 * @brief Test live image capture (RTSP stream -> decoder -> image encoder -> JPEG)
 */
TEST_F(MediaPipelineTest, DISABLED_LiveImageCapture)
{
    if (!RtspServerTestUtils::isRunning())
        GTEST_SKIP() << "RTSP server not running";
    string url = RtspServerTestUtils::getTestStreamUrl();
    if (url.empty())
        GTEST_SKIP() << "Test stream URL not available";
    std::map<std::string, std::string, std::less<>> opts;
    opts["peerid"] = "image_capture";
    opts["streamId"] = "live-capture";
    opts["image_capture"] = "true";
    cout << "[TEST] Creating live image capture pipeline for " << url << endl;
    try
    {
        m_videoSource = std::make_unique<CommonVideoSource>(url, opts);
    }
    catch (const std::exception& e)
    {
        FAIL() << "Failed to create live image capture pipeline: " << e.what();
    }
    ASSERT_NE(m_videoSource->getImageEncoder(), nullptr) << "Image encoder should be present for image capture";
    std::string buffer = pollImageBuffer(5);
    cout << "[TEST] Live image capture buffer size: " << buffer.size() << " bytes" << endl;
    EXPECT_FALSE(buffer.empty()) << "Expected non-empty JPEG buffer from live image capture";
}

/**
 * @brief Fixture for ReplayImageCapture: adds dummy sensor/stream to DeviceManager and DB.
 *
 * Uses DbTestUtils::addDummyReplaySensorAndStream to create SensorInfo, StreamInfo,
 * add to DeviceManager, and write to SENSOR_DETAILS/SENSOR_STREAMS. Does not start
 * StreamRecorder or StorageManagement. Uses real video files from test directory.
 */
class ReplayImageCaptureTest : public ::testing::Test
{
protected:
    static constexpr const char* REPLAY_STREAM_ID = "replay-capture-stream";
    static constexpr const char* REPLAY_SENSOR_NAME = "replay-test-sensor";

    void SetUp() override
    {
        cout << "\n[SETUP] Replay image capture test..." << endl;
        if (TestConfig::videoFiles.empty())
        {
            cout << "[SETUP] No video files; test will skip" << endl;
            return;
        }
        m_filePath = TestConfig::videoFiles[0];
        m_fileUrl = "file://" + std::filesystem::absolute(m_filePath).string();

        // Initialize StorageManagement so DB is available (does not start recording)
        int ret = ModuleLoader::getInstance()->initialize(ModuleStorageManagement);
        if (ret != 0)
            cout << "[SETUP] StorageManagement init failed; DB may be unavailable" << endl;

        m_deviceManager = std::make_shared<DeviceManager>();
        m_deviceManager->type = TYPE_VST;

        // Prerequisite: add dummy sensor/stream to DeviceManager and DB
        m_setupOk = DbTestUtils::addDummyReplaySensorAndStream(m_deviceManager, REPLAY_STREAM_ID,
                                                               REPLAY_SENSOR_NAME, m_fileUrl);
        if (m_setupOk)
        {
            ModuleLoader::getInstance()->setDeviceManagerForTest(m_deviceManager);
            cout << "[SETUP] Added dummy sensor/stream and DB entry for " << m_fileUrl << endl;
        }
    }

    void TearDown() override
    {
        m_videoSource.reset();
        ModuleLoader::getInstance()->setDeviceManagerForTest(nullptr);
        ModuleLoader::getInstance()->deInitialize();
        cout << "[CLEANUP] Replay image capture test done\n" << endl;
    }

    std::string pollImageBuffer(int timeoutSeconds)
    {
        if (!m_videoSource)
            return std::string();
        for (int i = 0; i < timeoutSeconds * 10; ++i)
        {
            std::string buf = m_videoSource->getBuffer();
            if (!buf.empty())
                return buf;
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        return m_videoSource->getBuffer();
    }

    std::string m_filePath;
    std::string m_fileUrl;
    std::shared_ptr<DeviceManager> m_deviceManager;
    std::unique_ptr<CommonVideoSource> m_videoSource;
    bool m_setupOk = false;
};

/**
 * @brief Test replay image capture with dummy sensor/stream in DeviceManager.
 *
 * Uses real video files from test directory (--video-dir). Adds dummy sensor/stream
 * to DeviceManager for metadata lookup. Pipeline: file:// -> ClipReaderProducer
 * -> decoder -> image encoder -> JPEG.
 */
TEST_F(ReplayImageCaptureTest, ReplayImageCapture)
{
    if (TestConfig::videoFiles.empty())
        GTEST_SKIP() << "No video files found (use --video-dir or VIDEO_DIR)";
    if (!m_setupOk)
        GTEST_SKIP() << "addDummyReplaySensorAndStream prerequisite failed";
    std::map<std::string, std::string, std::less<>> opts;
    opts["peerid"] = "image_capture";
    opts["streamId"] = REPLAY_STREAM_ID;
    opts["image_capture"] = "true";
    opts["sensor_type"] = SENSOR_TYPE_FILE;
    cout << "[TEST] Creating replay image capture pipeline for " << m_fileUrl << endl;
    try
    {
        m_videoSource = std::make_unique<CommonVideoSource>(m_fileUrl, opts);
    }
    catch (const std::exception& e)
    {
        FAIL() << "Failed to create replay image capture pipeline: " << e.what();
    }
    ASSERT_NE(m_videoSource->getImageEncoder(), nullptr) << "Image encoder should be present for image capture";
    std::string buffer = pollImageBuffer(5);
    cout << "[TEST] Replay image capture buffer size: " << buffer.size() << " bytes" << endl;
    EXPECT_FALSE(buffer.empty()) << "Expected non-empty JPEG buffer from replay image capture";
}

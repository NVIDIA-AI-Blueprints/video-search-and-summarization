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

#ifndef RTSP_SERVER_UTILS_H
#define RTSP_SERVER_UTILS_H

#include <string>
#include <vector>

/**
 * @brief Utilities to load the RTSP server module in-process for tests.
 *
 * Start the module at the beginning of the test suite; stop at the end.
 * The production libnvrtspserver module is loaded via ModuleLoader; under
 * UNIT_TEST it serves local video files from the given media directory.
 */
namespace RtspServerTestUtils
{
    /**
     * Load the RTSP server module and register video directory.
     * @param mediaDir    Directory containing video files (.mp4, .h264, .h265).
     *                    Set as GTEST_VIDEO_DIR env var so DynamicRTSPServer's
     *                    UNIT_TEST branch can resolve file paths.
     * @return true if the module loaded successfully, false on failure.
     */
    bool start(const std::string& mediaDir);

    /**
     * Clear local state and unset GTEST_VIDEO_DIR.
     * Full module teardown is done by the test fixture via
     * ModuleLoader::getInstance()->deInitialize().
     */
    void stop();

    /**
     * Whether the RTSP module was successfully loaded.
     */
    bool isRunning();

    /**
     * Base URL for the server (e.g. "rtsp://127.0.0.1:8554").
     * Empty if not running.
     */
    std::string getBaseUrl();

    /**
     * URL for a stream by id (e.g. "rtsp://127.0.0.1:8554/test/test-stream").
     * Empty if not running.
     */
    std::string getStreamUrl(const std::string& streamId);

    /**
     * Get RTSP URLs for all video files found in the media directory.
     * Each file becomes rtsp://127.0.0.1:<port>/test/<filename>.
     * Empty if not running or media directory is empty/missing.
     */
    std::vector<std::string> getVideoFileUrls();

    /**
     * Get RTSP URL for a specific video file by its basename.
     * @param filename  Basename of the video file (e.g. "sample_10sec_h264.mp4").
     * @return URL like rtsp://127.0.0.1:<port>/test/<filename>, or empty.
     */
    std::string getStreamUrlForFile(const std::string& filename);

    /**
     * Get RTSP URL for a test stream.
     * @return URL like rtsp://127.0.0.1:<port>/test/sample_10sec_h264.mp4, or empty.
     */
    std::string getTestStreamUrl();
}

#endif /* RTSP_SERVER_UTILS_H */

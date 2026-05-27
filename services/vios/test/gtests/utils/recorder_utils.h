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
 * @file recorder_utils.h
 * @brief Common utilities for recorder unit tests
 * 
 * This file provides reusable helper functions for:
 * - Getting timelines for streams
 * - Getting timelines for all streams
 * - Common test data creation for recorder operations
 */

#ifndef RECORDER_UTILS_H
#define RECORDER_UTILS_H

#include <string>
#include <jsoncpp/json/json.h>
#include "streamrecorder.h"
#include "mock_civetweb.h"

using namespace std;
using namespace nv_vms;

/**
 * @brief Result of a timeline query operation in tests
 */
struct TestTimelineResult
{
    VmsErrorCode errorCode;
    Json::Value timelines;       // Timeline data returned from API
    string errorMessage;          // Error message if failed
    
    bool isSuccess() const { return errorCode == VmsErrorCode::NoError; }
};

/**
 * @brief Recorder test utilities namespace
 */
namespace RecorderTestUtils
{
    /**
     * @brief Get recording timelines for a specific stream and select time range
     * 
     * This function queries the recorder for all timelines of a stream and intelligently
     * selects a time range for download/playback:
     * - If video > 10 seconds: Randomly selects a 10-second segment
     * - If video ≤ 10 seconds: Uses full length
     * 
     * API: GET /api/v1/record/{streamId}/timelines
     * Note: The API returns ALL timelines for the stream (no time filter parameters)
     * 
     * @param recorder StreamRecorder instance
     * @param streamId Stream identifier
     * @param[out] startTime Output: Selected start time in ISO 8601 format
     * @param[out] endTime Output: Selected end time in ISO 8601 format
     * @param targetDurationMs Target duration in milliseconds (default: 10000 = 10 seconds)
     * @return TestTimelineResult with timeline data and status
     * 
     * Example usage:
     * @code
     * string selectedStart, selectedEnd;
     * auto result = RecorderTestUtils::getStreamTimelines(
     *     recorder, 
     *     "test-stream-123",
     *     selectedStart,    // OUTPUT
     *     selectedEnd       // OUTPUT
     * );
     * if (result.isSuccess()) {
     *     cout << "Selected range: " << selectedStart << " to " << selectedEnd << endl;
     * }
     * @endcode
     */
    TestTimelineResult getStreamTimelines(
        StreamRecorder* recorder,
        const string& streamId,
        string& startTime,
        string& endTime,
        int64_t targetDurationMs = 10000
    );
    
    /**
     * @brief Get recording timelines for all streams
     * 
     * API: GET /api/v1/record/timelines
     * 
     * @param recorder StreamRecorder instance
     * @return TestTimelineResult with timeline data for all streams
     * 
     * Example usage:
     * @code
     * auto result = RecorderTestUtils::getAllTimelines(recorder);
     * if (result.isSuccess()) {
     *     // Result contains timelines for all streams as object
     *     for (auto& streamId : result.timelines.getMemberNames()) {
     *         cout << "Stream: " << streamId << endl;
     *         cout << "Timelines: " << result.timelines[streamId].toStyledString() << endl;
     *     }
     * }
     * @endcode
     */
    TestTimelineResult getAllTimelines(StreamRecorder* recorder);
}

#endif // RECORDER_UTILS_H

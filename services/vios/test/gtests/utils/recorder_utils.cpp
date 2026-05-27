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
 * @file recorder_utils.cpp
 * @brief Implementation of common recorder test utilities
 */

#include "recorder_utils.h"
#include "utils.h"
#include <iostream>
#include <random>

using namespace std;

// ==================== RecorderTestUtils Implementation ====================

namespace RecorderTestUtils
{

TestTimelineResult getStreamTimelines(
    StreamRecorder* recorder,
    const string& streamId,
    string& startTime,
    string& endTime,
    int64_t targetDurationMs)
{
    cout << "[RECORDER-UTIL] Getting timelines for stream: " << streamId << endl;
    cout << "[RECORDER-UTIL] Target duration: " << targetDurationMs << " ms" << endl;
    
    TestTimelineResult result;
    result.errorCode = VmsErrorCode::VMSInternalError;
    
    // Clear output parameters
    startTime.clear();
    endTime.clear();
    
    if (recorder == nullptr)
    {
        result.errorMessage = "StreamRecorder is null";
        cout << "[RECORDER-UTIL] ❌ Error: " << result.errorMessage << endl;
        return result;
    }
    
    if (streamId.empty())
    {
        result.errorCode = VmsErrorCode::InvalidParameterError;
        result.errorMessage = "Stream ID cannot be empty";
        cout << "[RECORDER-UTIL] ❌ Error: " << result.errorMessage << endl;
        return result;
    }
    
    // Build request info - timeline API doesn't need query parameters
    // According to swagger.yaml, /v1/record/{streamId}/timelines is a simple GET
    // with no startTime/endTime parameters - it returns all timelines for the stream
    string url = "/api/v1/record/" + streamId + "/timelines";
    
    Json::Value req_info;
    req_info["url"] = url;
    req_info["method"] = "GET";
    
    cout << "[RECORDER-UTIL] Request: GET " << url << endl;
    
    // Create mock connection for the request
    MockConnection mockConn;
    mockConn.requestInfo.setMethod("GET");
    mockConn.requestInfo.setUri(url);
    
    // Call the recorder API
    Json::Value input;
    result.errorCode = recorder->handleRecordAPIrequest(
        req_info,
        input,
        result.timelines,
        reinterpret_cast<struct mg_connection*>(&mockConn)
    );
    
    if (result.errorCode != VmsErrorCode::NoError)
    {
        cout << "[RECORDER-UTIL] ❌ Failed to get timelines: error code " 
             << static_cast<int>(result.errorCode) << endl;
        
        if (result.timelines.isMember("error_message"))
        {
            result.errorMessage = result.timelines["error_message"].asString();
            cout << "[RECORDER-UTIL] Error message: " << result.errorMessage << endl;
        }
        return result;
    }
    
    cout << "[RECORDER-UTIL] ✅ Successfully retrieved timelines" << endl;
    
    // Parse timelines and select appropriate time range
    if (!result.timelines.isArray() || result.timelines.size() == 0)
    {
        cout << "[RECORDER-UTIL] ⚠️  No timeline segments found" << endl;
        result.errorCode = VmsErrorCode::VMSNoDataError;
        result.errorMessage = "No timeline segments available";
        return result;
    }
    
    cout << "[RECORDER-UTIL] Found " << result.timelines.size() << " timeline segment(s)" << endl;
    
    // Get the first timeline segment (could be enhanced to merge multiple segments)
    const Json::Value& firstSegment = result.timelines[0];
    
    if (!firstSegment.isMember("startTime") || !firstSegment.isMember("endTime"))
    {
        cout << "[RECORDER-UTIL] ❌ Timeline segment missing start/end times" << endl;
        result.errorCode = VmsErrorCode::VMSInternalError;
        result.errorMessage = "Invalid timeline format";
        return result;
    }
    
    string segmentStart = firstSegment["startTime"].asString();
    string segmentEnd = firstSegment["endTime"].asString();
    
    cout << "[RECORDER-UTIL] Timeline segment: " << segmentStart << " to " << segmentEnd << endl;
    
    // Check if we have duration information
    if (firstSegment.isMember("duration"))
    {
        int64_t durationMs = firstSegment["duration"].asInt64();
        cout << "[RECORDER-UTIL] Segment duration: " << durationMs << " ms" << endl;
        
        if (targetDurationMs > 0 && durationMs > targetDurationMs &&
            firstSegment.isMember("startEpochMs"))
        {
            int64_t segStartMs = firstSegment["startEpochMs"].asInt64();
            int64_t maxOffset = durationMs - targetDurationMs;

            static std::mt19937 rng(std::random_device{}());
            std::uniform_int_distribution<int64_t> dist(0, maxOffset);
            int64_t randomOffset = dist(rng);

            int64_t subStartMs = segStartMs + randomOffset;
            int64_t subEndMs = subStartMs + targetDurationMs;

            startTime = convertEpocToISO8601_2(subStartMs * 1000);
            endTime = convertEpocToISO8601_2(subEndMs * 1000);

            cout << "[RECORDER-UTIL] Selected " << targetDurationMs
                 << " ms sub-segment at offset " << randomOffset << " ms" << endl;
        }
        else
        {
            startTime = segmentStart;
            endTime = segmentEnd;
            cout << "[RECORDER-UTIL] Using full segment (" << durationMs
                 << " ms)" << endl;
        }
    }
    else
    {
        // No duration info, use the full segment
        startTime = segmentStart;
        endTime = segmentEnd;
        cout << "[RECORDER-UTIL] Using full segment (no duration info)" << endl;
    }
    
    cout << "[RECORDER-UTIL] ✅ Selected time range:" << endl;
    cout << "[RECORDER-UTIL]   Start: " << startTime << endl;
    cout << "[RECORDER-UTIL]   End: " << endTime << endl;
    
    return result;
}

TestTimelineResult getAllTimelines(StreamRecorder* recorder)
{
    cout << "[RECORDER-UTIL] Getting timelines for all streams" << endl;
    
    TestTimelineResult result;
    result.errorCode = VmsErrorCode::VMSInternalError;
    
    if (recorder == nullptr)
    {
        result.errorMessage = "StreamRecorder is null";
        cout << "[RECORDER-UTIL] ❌ Error: " << result.errorMessage << endl;
        return result;
    }
    
    Json::Value req_info;
    req_info["url"] = "/api/v1/record/timelines";
    req_info["method"] = "GET";
    cout << "[RECORDER-UTIL] Request info: " << req_info.toStyledString() << endl;
    
    cout << "[RECORDER-UTIL] Request: GET /api/v1/record/timelines" << endl;
    
    result.errorCode = recorder->GetAllRecordTimelines(req_info, result.timelines);
    
    if (result.errorCode == VmsErrorCode::NoError)
    {
        cout << "[RECORDER-UTIL] ✅ Successfully retrieved all timelines" << endl;
        cout << "[RECORDER-UTIL] Number of streams: " 
             << result.timelines.getMemberNames().size() << endl;
        
        // Print summary of each stream
        for (const auto& streamId : result.timelines.getMemberNames())
        {
            cout << "[RECORDER-UTIL]   Stream: " << streamId << endl;
        }
    }
    else
    {
        cout << "[RECORDER-UTIL] ❌ Failed to get all timelines: error code " 
             << static_cast<int>(result.errorCode) << endl;
        
        if (result.timelines.isMember("error_message"))
        {
            result.errorMessage = result.timelines["error_message"].asString();
            cout << "[RECORDER-UTIL] Error message: " << result.errorMessage << endl;
        }
    }
    
    return result;
}

} // namespace RecorderTestUtils

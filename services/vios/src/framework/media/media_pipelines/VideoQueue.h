/*
 * SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include <queue>
#include <mutex>
#include <string>
#include <vector>
#include <condition_variable>
#include <atomic>
#include <logger.h>

using namespace std;

enum RecordState
{
    OFF = 0,
    Schedule,
    User,
    Event,
    AlwaysOn,
    Error,
    UnknownState
};

enum FrameStatus
{
    ErrorFrame        = -2,
    DropFrame         = -1,
    FirstFrame        =  0,
    IntermediateFrame =  1,
    LastFrame
};

enum EventType
{
    EventTypeWaitForEvent = 0,
    EventTypeStartRecord,
    EventTypeStopRecord
};

struct FrameInfo
{
    string               codec;
    string               mediaType;
    bool                 isIDR;
    size_t               size;
    std::vector<uint8_t> content;
    int                  instFPS;
    struct timeval       presentationTime;
    FrameStatus          frameStatus;
    int64_t              currentClockTime;
    RecordState          recordState;

    FrameInfo() : codec("")
                , isIDR(false)
                , size (0)
                , instFPS (0)
                , frameStatus (IntermediateFrame)
                , currentClockTime(0)
                , recordState (OFF)
    {
        content.clear();
        presentationTime.tv_sec = 0;
        presentationTime.tv_usec = 0;
    }
};

const string recording_off            = "off";
const string recording_on_schedule    = "schedule";
const string recording_on_user        = "user";
const string recording_on_event       = "event";
const string recording_on_alwaysON    = "alwaysOn";
const string recording_error          = "error";
const string recording_status_unknown = "statusUnknown";

namespace
{
    inline string translateRecordStateToString(RecordState value)
    {
        switch(value)
        {
            case OFF:      return recording_off;
            case Schedule: return recording_on_schedule;
            case User:     return recording_on_user;
            case Event:    return recording_on_event;
            case AlwaysOn: return recording_on_alwaysON;
            case Error:    return recording_error;
            case UnknownState:
            default:       return recording_status_unknown;
        }
    }
}

class VideoQueue
{
public:
    VideoQueue();
    ~VideoQueue() {}

    void push(FrameInfo& frame);
    void pull(FrameInfo& frame);
    void release();
    void dropExtraFrames();
    void setRecordingState(RecordState record_state);
    int onEvent();
    void setError(bool is_error);
    void setDeviceId (string deviceId) { m_deviceId = deviceId; }
    void setPlaying (bool playing)
    { 
        LOG(info) << "Setting playing state = " << playing << " for " << m_deviceId << endl;
        m_isPlaying = playing;
    }
    bool isDropFrame(FrameInfo& frame);

private:
    std::queue<FrameInfo>               m_queue;
    std::mutex                          m_queueLock;
    std::condition_variable             m_condVar;
    std::atomic<bool>                   m_flowData{false};
    std::atomic<bool>                   m_isQueueExited{false};
    struct timeval                      m_oldestFrameTs = {0};
    struct timeval                      m_currentFrameTs = {0};
    RecordState                         m_recordState = OFF;
    long                                m_bufferTime = 0;
    std::atomic<bool>                   m_timedOut{false};
    bool                                m_isError = false;
    int64_t                             m_firstFrameTime = 0;
    EventType                           m_eventType = EventTypeWaitForEvent;
    std::string                         m_deviceId;
    bool                                m_dropFrame = false;
    uint64_t                            m_maxQueueSize = 0;
    uint64_t                            m_currentQueueSize = 0;
    std::atomic<bool>                   m_isPlaying;
    int64_t                             m_frameDropCount = 0;
    bool                                m_unbufferedMode = false;
};

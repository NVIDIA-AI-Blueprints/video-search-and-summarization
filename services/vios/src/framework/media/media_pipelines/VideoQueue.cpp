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

#include "VideoQueue.h"
#include "logger.h"
#include <algorithm>
#include <database.h>

constexpr int SEC_TO_MICRO_SEC = 1000 * 1000;
constexpr int MAX_EVENT_RECORDING_SECS = 60;
constexpr int MAX_RECORDING_BUFFER_SECS = 10;
constexpr int DEFAULT_MAX_QUEUE_SIZE_BYTES = 16000000;

#ifdef UNIT_TEST
    constexpr int MAX_SEGMENT_DURATION = 30 * 1000;
#else
    constexpr int MAX_SEGMENT_DURATION = 60 * 1000;
#endif

VideoQueue::VideoQueue() : m_flowData(false)
                         , m_bufferTime((GET_CONFIG().event_record_length_secs * SEC_TO_MICRO_SEC) / 2)
{
    m_isPlaying = false;
    if (GET_CONFIG().enable_mega_simulation)
    {
        m_bufferTime = 33*1000;
    }
    else
    {
        m_bufferTime = std::min (m_bufferTime, long(MAX_EVENT_RECORDING_SECS * SEC_TO_MICRO_SEC) / 2);
        if (m_bufferTime == 0)
        {
            m_bufferTime = 2 * SEC_TO_MICRO_SEC;
        }
    }
    m_maxQueueSize = std::max (GET_CONFIG().recorder_max_frame_queue_size_bytes, DEFAULT_MAX_QUEUE_SIZE_BYTES);
    LOG(info) << "Max queue size in stream recorder = " << m_maxQueueSize << endl;
    m_unbufferedMode = GET_CONFIG().recorder_low_latency;
    LOG(info) << "VideoQueue: Unbuffered (low latency) mode = " << (m_unbufferedMode ? "enabled" : "disabled") << endl;
}

void VideoQueue::release()
{
    std::lock_guard<std::mutex> frameLock(m_queueLock);
    m_flowData = true;
    m_isQueueExited = true;
    m_condVar.notify_all();
    std::queue<FrameInfo> empty;
    std::swap( m_queue, empty );
}

void VideoQueue::setRecordingState(RecordState record_state)
{
    std::unique_lock<std::mutex> lk(m_queueLock);
    m_firstFrameTime = 0;
    m_recordState = record_state;
    GET_DB_INSTANCE()->setRecordingStatus(m_deviceId, record_state, nullopt);
    if (GET_CONFIG().enable_mega_simulation && GET_CONFIG().event_recording == false)
    {
        m_bufferTime = 33*1000;
    }
    else
    {
        m_bufferTime = (std::min (GET_CONFIG().event_record_length_secs, (MAX_EVENT_RECORDING_SECS)) / 2) * SEC_TO_MICRO_SEC;
        if (m_recordState != Event || m_bufferTime == 0)
        {
            m_bufferTime = std::min(GET_CONFIG().record_buffer_length_secs, MAX_RECORDING_BUFFER_SECS) * SEC_TO_MICRO_SEC;;
            m_bufferTime = std::max(m_bufferTime, long(2 * SEC_TO_MICRO_SEC));
        }
    }
    LOG(warning) << "VideoQueue: set record state = " << translateRecordStateToString(record_state) << " and buffer time (secs) = " << m_bufferTime / 1000 / 1000 << endl;
}

int VideoQueue::onEvent()
{
    std::lock_guard<std::mutex> lock(m_queueLock);
    int ret = 0;
    if (m_recordState == Event)
    {
        m_eventType = EventTypeStartRecord;
        {
            /* Drop extra frames to avoid Queue size being increased to more
            ** than m_bufferTime
            */
            dropExtraFrames ();
        }
    }
    else
    {
        LOG(warning) << "VideoQueue: ignoring event, current recording state = " << m_recordState << endl;
        ret = -1;
    }
    return ret;
}

void VideoQueue::dropExtraFrames()
{
    FrameInfo frame;
    while (timevaldiff(m_oldestFrameTs, m_currentFrameTs) > m_bufferTime)
    {
        if (m_queue.size())
        {
            m_queue.pop();
            frame = m_queue.front();
            m_oldestFrameTs = frame.presentationTime;
        }
        else
        {
            break;
        }
    }
    LOG(info) << "Q size After drop: " << m_queue.size() << " and oldest frame ts = " << convertTimeValToEpochMs(m_oldestFrameTs) << endl;
}

void VideoQueue::setError(bool is_error)
{
    std::unique_lock<std::mutex> lk(m_queueLock);
    m_isError = is_error;
    /* As error is recovered, need to create new file */
    if (m_isError == false)
    {
        m_firstFrameTime = 0;
    }
}

bool VideoQueue::isDropFrame(FrameInfo& frame)
{
    if (m_currentQueueSize >= m_maxQueueSize)
    {
        if (m_dropFrame == false)
        {
            LOG(info) << "Frame Drop Started for " << m_deviceId << endl;
            ++m_frameDropCount;
        }
        m_dropFrame = true;
        return true;
    }
    if (m_currentQueueSize < m_maxQueueSize)
    {
        if (m_dropFrame)
        {
            if (frame.isIDR == false)
            {
                ++m_frameDropCount;
                return true;
            }
            else
            {
                LOG(info) << "Frame Drop Stopped for " << m_deviceId << " drop count = " << m_frameDropCount << endl;
                m_frameDropCount = 0;
                m_dropFrame = false;
                return false;
            }
        }
    }
    return false;
}

void VideoQueue::push(FrameInfo& frame)
{
    std::lock_guard<std::mutex> frameLock(m_queueLock);

    /* Drop only if it is enabled and video queue is in playing state */
    if (GET_CONFIG().recorder_enable_frame_drop && m_isPlaying)
    {
        bool drop_frame = isDropFrame (frame);
        if (drop_frame)
        {
            /* Notify even if dropping, as pull thread should
            ** not be blocked */
            m_flowData = true;
            m_condVar.notify_all();
            m_timedOut = false;
            return;
        }
    }
    if (m_queue.empty())
    {
        m_oldestFrameTs = frame.presentationTime;
    }
    m_queue.push(frame);
    m_currentQueueSize += frame.size;
    m_currentFrameTs = frame.presentationTime;

    m_flowData = true;
    m_condVar.notify_all();
    m_timedOut = false;
}

void VideoQueue::pull(FrameInfo& frame)
{
    std::unique_lock<std::mutex> lk(m_queueLock);

    if (!m_unbufferedMode)
    {
        while ((m_queue.empty() || m_flowData == false) && !m_isQueueExited && !m_timedOut)
        {
            /* Wait until (time_now + 2 seconds) to say data has not arrived */
            auto until     = std::chrono::system_clock::now() + 2s;

            m_flowData = false;
            if (GET_CONFIG().enable_mega_simulation == false)
            {
                if (m_condVar.wait_until(lk, until, [this]{ return m_flowData.load(); }) == false)
                {
                    m_timedOut = true;
                }
            }
            else
            {
                m_condVar.wait(lk, [this]{ return m_flowData == true; });
            }

            /* set m_flowData = true if time difference is more than buffer time or 
            ** frames are getting dropped then start pulling data */
            if(timevaldiff(m_oldestFrameTs, m_currentFrameTs) >= m_bufferTime || m_dropFrame == true)
            {
                m_flowData = true;
            }
            else
            {
                m_flowData = false;
                continue;
            }
        }
    }
    else
    {
        /* Wait for a frame to be pushed to the queue, with 2 second max wait time */
        while (m_queue.empty() && !m_isQueueExited && !m_timedOut)
        {
            auto until = std::chrono::system_clock::now() + 2s;
            m_flowData = false;
            if (GET_CONFIG().enable_mega_simulation == false)
            {
                if (m_condVar.wait_until(lk, until, [this]{ return m_flowData.load() || m_isQueueExited; }) == false)
                {
                    m_timedOut = true;
                }
            }
            else
            {
                m_condVar.wait(lk, [this]{ return m_flowData == true || m_isQueueExited; });
            }
        }
    }

    if (!m_isQueueExited && m_queue.size())
    {
        frame = m_queue.front();
        if (m_isError && !frame.isIDR)
        {
            /* If error in gstmux pipeline */
            frame.frameStatus = ErrorFrame;
            m_queue.pop();
            m_currentQueueSize -= frame.size;
            return;
        }
        m_isError = false;
        if (m_recordState == Event)
        {
            m_flowData = false;
        }
        m_queue.pop();
        m_currentQueueSize -= frame.size;
        if (m_queue.size())
        {
            m_oldestFrameTs = m_queue.front().presentationTime;
        }
        else
        {
            m_oldestFrameTs = frame.presentationTime;
        }
        int64_t current_ts   = convertTimeValToEpochMs(frame.presentationTime);
        bool send_event_frame = (m_recordState == Event) && (m_eventType == EventTypeStartRecord);
        bool drop_event_frame = (m_recordState == Event) && (m_eventType == EventTypeWaitForEvent);
        bool send_frame       = send_event_frame || m_recordState != Event;

        frame.recordState = m_recordState;
        if (drop_event_frame)
        {
            frame.frameStatus = DropFrame;
            return;
        }
        if (frame.isIDR)
        {
            if (m_firstFrameTime == 0 && send_frame)
            {
                m_firstFrameTime = current_ts;
                frame.frameStatus = FirstFrame;
            }
            else if ((current_ts - m_firstFrameTime) >= MAX_SEGMENT_DURATION && m_recordState != Event)
            {
                LOG(info) << "Video Queue Size bytes = " << m_currentQueueSize << " for = " << m_deviceId << endl;
                m_firstFrameTime = current_ts;
                frame.frameStatus = FirstFrame;
            }
        }
        else
        {
            frame.frameStatus = IntermediateFrame;
        }

        if ((current_ts - m_firstFrameTime) >= (m_bufferTime / 1000) * 2 && send_event_frame && m_firstFrameTime)
        {
            frame.frameStatus = LastFrame;
            m_eventType = EventTypeWaitForEvent;
            m_firstFrameTime = 0;
        }
    }
    else
    {
        /* 1) Setting frameStatus to LastFrame, to handle this case in gstmux class.
        ** 2) Also setting m_timedOut to false, so as to avoid pull getting called without any wait, this else condition
        **    will be executed when queue will be empty and there is no use to pull data from gstmux class without wait
        */
        frame.frameStatus       = LastFrame;
        m_timedOut              = false;
        m_firstFrameTime        = 0;
        return;
    }
}

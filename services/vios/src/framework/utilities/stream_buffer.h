/*
 * SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <string>
#include <mutex>
#include <condition_variable>
#include <queue>
#include <cstdint>
#include <atomic>
#include "logger.h"
#include "stats.h"
#include "config.h"

#define STREAM_MSG_EOS "_stream_msg_eos_"
#define STREAM_MSG_ERROR "_stream_msg_error_"
#define STREAM_DEFAULT_BUFFER_SIZE 2*1000*1000
#define DISCRETE_FRAME_QUEUE_MAX_SIZE 500

using namespace std;

typedef enum { eBufferInitialState = -1, eBufferPlayMode, eBufferPauseMode } eBufferState;

struct DiscreteFrame
{
    void setLatencyStartTime(struct timeval latency)
    {
        if (GET_CONFIG().enable_latency_logging)
        {
            m_latencyStartTime = latency;
        }
    }
    DiscreteFrame(std::vector<uint8_t> content, struct timeval presentationTime,
                struct timeval latencyStartTime = {std::numeric_limits<time_t>::max(), std::numeric_limits<time_t>::max()})
        : m_content(content)
        , m_presentationTime(presentationTime)
    {
        setLatencyStartTime (latencyStartTime);
    }

    std::vector<uint8_t> m_content;
    string m_codec;
    uint8_t m_nalType;
    struct timeval m_presentationTime;
    struct timeval m_latencyStartTime;
};

class StreamBuffer
{
public:
CodecStats webrtcToRtspLatencyStats;
int64_t lastStatsPrintTime = 0;

StreamBuffer(const string& streamName, size_t maxSize, bool isFramed = false)
    : m_streamName(streamName)
    , m_queueMutex()
    , m_pushCV()
    , m_popCV()
    , m_ringBufferState(eBufferInitialState)
{
    LOG(info) << "StreamBuffer::StreamBuffer stream:" << m_streamName << endl;
    play();
}

~StreamBuffer()
{
    LOG(info) << "StreamBuffer::~StreamBuffer stream:" << m_streamName << endl;
    m_frameInfoQueue = {};
    m_pushCV.notify_all();
    m_popCV.notify_all();
    m_consumerThreadBlockedCount = 0;
}

bool push(string item)
{
    bool ret = false;
    std::vector<uint8_t> vec(item.begin(), item.end());

    struct timeval presentationTime = {};
    std::shared_ptr<DiscreteFrame> frameInfo(
            new DiscreteFrame(vec, presentationTime));

    ret = push(frameInfo);
    return ret;
}

bool push(std::shared_ptr<DiscreteFrame> frameInfo)
{
    if (frameInfo == nullptr)
    {
        return false;
    }
    std::unique_lock<std::mutex> scopedLock(m_queueMutex);
    if (frameInfo->m_content.size() == strlen(STREAM_MSG_EOS)
        || frameInfo->m_content.size() == strlen(STREAM_MSG_ERROR))
    {
        std::string msg(frameInfo->m_content.begin(), frameInfo->m_content.end());
        if (msg == STREAM_MSG_EOS || msg == STREAM_MSG_ERROR)
        {
            std::vector<uint8_t> vec(msg.begin(), msg.end());
            eMsg = vec;
            m_frameInfoQueue.push(frameInfo);
            m_pushCV.notify_one();
            pause();
            return true;
        }
    }

    if (m_ringBufferState != eBufferPlayMode)
    {
        return false;
    }
    if (m_frameInfoQueue.size() == DISCRETE_FRAME_QUEUE_MAX_SIZE)
    {
        if (m_popCV.wait_for(scopedLock, std::chrono::seconds(10)) ==
            std::cv_status::timeout)
        {
            LOG(error) << "!!! Buffer full..." << " Stream:" << m_streamName
                    << ", skip framesize:" << frameInfo->m_content.size() << endl;
            m_consumerThreadBlockedCount++;
            if (m_consumerThreadBlockedCount.load() == 3) /* 30sec*/
            {
                /* Exiting to restart the process */
                LOG(error) << "FATAL ERROR observed - Restart the vst" << endl;
#ifndef UNIT_TEST
                assert(false);
#endif
            }
            return false;
        }
    }
    m_frameInfoQueue.push(frameInfo);
    return true;
}

std::shared_ptr<DiscreteFrame> pop(unsigned size = 0)
{
    std::shared_ptr<DiscreteFrame> frameInfo;
    m_consumerThreadBlockedCount = 0;
    if (isEmpty())
    {
        std::unique_lock<std::mutex> scopedLock(m_queueMutex);
        if (eMsg.size() > 0)
        {
            frameInfo = m_frameInfoQueue.front();
            if (frameInfo && eMsg.size() == frameInfo->m_content.size())
            {
                m_frameInfoQueue.pop();
                m_popCV.notify_one();
            }
        }
        return frameInfo;
    }

    std::unique_lock<std::mutex> scopedLock(m_queueMutex);
    if (!m_frameInfoQueue.empty())
    {
        frameInfo = m_frameInfoQueue.front();
        m_frameInfoQueue.pop();
    }
    m_popCV.notify_one();
    if (frameInfo->m_content.size() && GET_CONFIG().enable_latency_logging == true &&
        frameInfo->m_latencyStartTime.tv_sec != std::numeric_limits<time_t>::max())
    {
        struct timeval currTime;
        gettimeofday(&currTime, nullptr);
        int64_t latency = timevaldiff(frameInfo->m_latencyStartTime, currTime);

        if (latency > 0)
        {
            int64_t time = (currTime.tv_sec * 1000000) + (currTime.tv_usec);
            webrtcToRtspLatencyStats.setLatency(latency);
            if (!lastStatsPrintTime)
            {
                lastStatsPrintTime = time;
            }
            // Print stats every 1 min
            if ((time - lastStatsPrintTime) >= 60000000)
            {
                webrtcToRtspLatencyStats.setElementName("Inbound stream ");
                webrtcToRtspLatencyStats.printTotalStats();
                lastStatsPrintTime = time;
            }
        }
    }
    return frameInfo;
}

std::vector<uint8_t> read(unsigned size)
{
    std::vector<uint8_t> item;
    if (!isEmpty())
    {
        std::unique_lock<std::mutex> scopedLock(m_queueMutex);
        std::shared_ptr<DiscreteFrame> frameInfo = m_frameInfoQueue.front();
        item.insert(item.end(), frameInfo->m_content.begin(), frameInfo->m_content.begin() + size);
    }
    m_consumerThreadBlockedCount = 0;
    return item;
}

void clear()
{
    LOG(info) << "StreamBuffer::clear stream:" << m_streamName << endl;
    std::unique_lock<std::mutex> scopedLock(m_queueMutex);
    m_pushCV.notify_all();
    m_popCV.notify_all();
    m_frameInfoQueue = {};
    eMsg.clear();
}

void pause()
{
    m_ringBufferState = eBufferPauseMode;
    m_pushCV.notify_all();
    m_popCV.notify_all();
}

void play()
{
    m_ringBufferState = eBufferPlayMode;
}

int getQueueSize()
{
    std::unique_lock<std::mutex> scopedLock(m_queueMutex);
    return m_frameInfoQueue.size();
}

private:
    bool isEmpty()
    {
        std::unique_lock<std::mutex> scopedLock(m_queueMutex);
        return m_frameInfoQueue.empty();
    }

    bool isFull()
    {
        std::unique_lock<std::mutex> scopedLock(m_queueMutex);
        return (m_frameInfoQueue.size() == DISCRETE_FRAME_QUEUE_MAX_SIZE);
    }

    std::string m_streamName;
    std::mutex m_queueMutex;
    std::condition_variable m_pushCV, m_popCV;
    std::vector<uint8_t> eMsg;
    std::atomic<eBufferState> m_ringBufferState;
    std::queue<std::shared_ptr<DiscreteFrame>> m_frameInfoQueue;
    std::atomic<unsigned> m_consumerThreadBlockedCount{0};
};

/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "ByteStreamMemoryBufferSource.hh"
#include "NvMediaSource.hh"
#include <memory>
#include <vector>
#include <string>
#include <mutex>
#include <thread>
#include <condition_variable>
#include "utilities/stats.h"

class AvLoopSyncCoordinator;

class H264ByteStreamSource: public FramedSource
{
public:
    static H264ByteStreamSource*
    createNew(UsageEnvironment& env, std::string streamName, shared_ptr<NvMediaSource> mediasource,
        string url_params, string sourceState, string sessionId,
        unsigned preferredFrameSize = 0, unsigned playTimePerFrame = 0);
    // "fileNameArray" is a pointer to an array of (char const*) file names, with
    // A 'file name' of NULL indicating the end of the array
    void setStreamScale(float scaleFactor);
    void setStreamSeek(uint64_t start, uint64_t end);
    int64_t getSeekOffset() { return m_seekOffset; }
    string getUrlParams() { return m_url_params; }

protected:
    H264ByteStreamSource(UsageEnvironment& env, std::string streamName, shared_ptr<NvMediaSource> mediasource,
            string url_params, string sourceState, string sessionId,
            unsigned preferredFrameSize, unsigned playTimePerFrame);
    // called only by createNew()

    virtual ~H264ByteStreamSource();

private:
    // redefined virtual functions:
    virtual void doGetNextFrame();

private:
    static void onSourceClosure(void* clientData);
    void onSourceClosure1();
    static void afterGettingFrame(void* clientData,
          unsigned frameSize, unsigned numTruncatedBytes,
                                  struct timeval presentationTime,
          unsigned durationInMicroseconds);
    static void restartSource(H264ByteStreamSource* source);
    static void dataArrivalCheck(H264ByteStreamSource* source);
    static void retryGetFrame(void* clientData);
    void closeSource();
    /* Common loop-restart sequence shared by the immediate-restart path
     * (no coordinator) and the barrier-released path (called via the
     * coordinator's restart callback once both subsessions have EOSed). */
    void restartForLoop();
    unsigned calculateAdaptiveDuration();

private:
    unsigned fPreferredFrameSize;
    unsigned fPlayTimePerFrame;
    std::string m_streamName;
    std::string m_sessionId;
    shared_ptr<NvMediaSource> m_mediaSource;
    int64_t m_seekOffset;
    uint64_t m_start;
    uint64_t m_end;
    float m_seekRate;
    bool m_loop;
    string m_url_params;
    string m_sourceState;
    int m_restartCount;
    std::vector<uint8_t> m_trunctedBytes;
    double m_frameRate;
    int m_noDataRetryCount;
    bool m_lastFrameWasHeader;  // Track if previous frame was a header for pacing logic
    struct timeval m_lastFramePT;
    bool m_havePrevPT;
    /* AV-loop-sync coordinator. Non-null only when this source is one
     * subsession of an audio+video pair AND loop playback is enabled.
     * When set, EOS in closeSource() routes through the coordinator
     * instead of immediately calling seekToStart+retry. */
    std::shared_ptr<AvLoopSyncCoordinator> m_avLoopSync;

  public:
    TaskToken m_DataArrivalCheckTask;
    unsigned m_frameCount;
};

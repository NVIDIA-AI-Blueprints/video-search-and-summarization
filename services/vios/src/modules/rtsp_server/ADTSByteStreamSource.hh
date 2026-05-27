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

#ifndef _ADTS_BYTESTREAM_SOURCE_HH
#define _ADTS_BYTESTREAM_SOURCE_HH

#include "FramedSource.hh"
#include "NvMediaSource.hh"
#include <memory>
#include <vector>
#include <string>
#include <mutex>

using namespace std;

class AvLoopSyncCoordinator;

class ADTSByteStreamSource: public FramedSource
{
public:
    static ADTSByteStreamSource* createNew(UsageEnvironment& env, const string& streamName,
        shared_ptr<NvMediaSource> mediasource, string url_params, u_int8_t profile, unsigned samplingFrequency, u_int8_t channel);

    unsigned samplingFrequency() const { return m_samplingFrequency; }
    unsigned numChannels() const { return m_numChannels; }
    char const* configStr() const { return m_configStr; }
        // returns the 'AudioSpecificConfig' for this stream (in ASCII form)

protected:
    ADTSByteStreamSource(UsageEnvironment& env, const string& streamName, shared_ptr<NvMediaSource> mediasource, string url_params,
            u_int8_t profile, unsigned samplingFrequencyIndex, u_int8_t channelConfiguration);
    // called only by createNew()

    virtual ~ADTSByteStreamSource();

private:
    // redefined virtual functions:
    virtual void doGetNextFrame();
    static void onSourceClosure(void* clientData);
    void onSourceClosure1();
    static void afterGettingFrame(void* clientData,
          unsigned frameSize, unsigned numTruncatedBytes,
          struct timeval presentationTime,
          unsigned durationInMicroseconds);
    static void retryGetFrame(void* clientData);
    static void dataArrivalCheck(ADTSByteStreamSource* source);
    void closeSource();
    /* Common loop-restart sequence shared by the immediate-restart path
     * (no coordinator) and the barrier-released path (called via the
     * coordinator's restart callback). */
    void restartForLoop();

private:
    string m_streamName;
    shared_ptr<NvMediaSource> m_mediaSource;
    unsigned m_samplingFrequency;
    unsigned m_numChannels;
    unsigned m_uSecsPerFrame;
    char m_configStr[5];
    bool m_loop;
    /* AV-loop-sync coordinator. Non-null only when this source is one
     * subsession of an audio+video pair AND loop playback is enabled.
     * When set, EOS in closeSource() routes through the coordinator. */
    std::shared_ptr<AvLoopSyncCoordinator> m_avLoopSync;
public:
    TaskToken m_DataArrivalCheckTask;
    unsigned m_frameCount;
};

#endif

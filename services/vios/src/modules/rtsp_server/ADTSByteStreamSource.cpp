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

#include <iostream>
#include "ADTSByteStreamSource.hh"
#include "stream_buffer.h"
#include <GroupsockHelper.hh>
#include "logger.h"
#include "AvLoopSyncCoordinator.h"

#define ADTS_HEADER_SIZE 7
#define DATA_ARRIVAL_TIMEOUT_USEC 30*1000*1000

ADTSByteStreamSource
::ADTSByteStreamSource(UsageEnvironment& env, const string& streamName,
            shared_ptr<NvMediaSource> mediasource, string url_params, u_int8_t profile,
            unsigned samplingFrequency, u_int8_t channel)
  : FramedSource(env)
  , m_streamName(streamName)
  , m_mediaSource(mediasource)
  , m_samplingFrequency(samplingFrequency)
  , m_numChannels(channel)
  , m_uSecsPerFrame(64000)
  , m_loop(false)
  , m_DataArrivalCheckTask(nullptr)
  , m_frameCount(0)
{
    LOG(info) << "::ADTSByteStreamSource streamName:" << m_streamName << endl;

    if (m_samplingFrequency != 0)
    {
        m_uSecsPerFrame = (1024 * 1000000) / m_samplingFrequency;
    }

    m_loop = GET_CONFIG().nv_streamer_loop_playback;
    if (url_params.find("loop=true") != string::npos)
    {
        m_loop = true;
    }
    else if (url_params.find("loop=false") != string::npos)
    {
        m_loop = false;
    }
    if (m_mediaSource->getSourceType() == SourceTypeLive)
    {
        m_loop = false;
    }
    LOG(info) << "Stream:" << m_streamName << ", m_loop:" << m_loop
              << ", url_params:" << url_params << endl;

    /* Register with the AV-loop-sync coordinator iff the media source
     * was paired with one AND looping is actually enabled. Otherwise
     * leave m_avLoopSync null so closeSource() takes the unchanged
     * immediate-restart path. */
    if (m_mediaSource && m_loop)
    {
        m_avLoopSync = m_mediaSource->getAvLoopSync();
        if (m_avLoopSync)
        {
            m_avLoopSync->registerParticipant(this, [this]() {
                this->restartForLoop();
            });
        }
    }

    if (m_mediaSource)
    {
        m_mediaSource->m_streamBuf.clear();
        m_DataArrivalCheckTask = envir().taskScheduler().scheduleDelayedTask(DATA_ARRIVAL_TIMEOUT_USEC,
            (TaskFunc*)ADTSByteStreamSource::dataArrivalCheck, this);
    }
}

ADTSByteStreamSource::~ADTSByteStreamSource()
{
    LOG(info) << "~ ::ADTSByteStreamSource streamName:" << m_streamName << endl;
    envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalCheckTask);
    /* Unregister from the AV-loop-sync coordinator. If we were parked
     * at EOS waiting on the video side, this also releases the video
     * side so it doesn't deadlock waiting for a now-gone audio. */
    if (m_avLoopSync)
    {
        m_avLoopSync->unregisterParticipant(this);
        m_avLoopSync.reset();
    }
}

ADTSByteStreamSource*
ADTSByteStreamSource::createNew(UsageEnvironment& env, const string& streamName,
    shared_ptr<NvMediaSource> mediasource, string url_params, u_int8_t profile, unsigned samplingFrequency, u_int8_t channel)
{
    return new ADTSByteStreamSource(
                env, streamName, mediasource, url_params,
                profile, samplingFrequency, channel);
}

void ADTSByteStreamSource::doGetNextFrame()
{
    std::shared_ptr<DiscreteFrame> data;
    fFrameSize = 0;
    if (m_mediaSource)
    {
        data = m_mediaSource->m_streamBuf.pop();
        if (data)
        {
            fFrameSize = data->m_content.size();
        }
    }
    if (fFrameSize > 0)
    {
        envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalCheckTask);
        if ((fFrameSize == std::string(STREAM_MSG_EOS).size() || fFrameSize == std::string(STREAM_MSG_ERROR).size()) && data)
        {
            std::string msg(data->m_content.begin(), data->m_content.end());
            if (msg == STREAM_MSG_EOS)
            {
                LOG(info) << "ADTSByteStreamSource, received EOS" << endl;
                m_mediaSource->m_streamBuf.clear();
                if (!m_loop)
                {
                    m_mediaSource->sendSourceEvent(SourceEventEOF);
                }
                closeSource();
                return;
            }
            else if (msg == STREAM_MSG_ERROR)
            {
                LOG(error) << "ADTSByteStreamSource, received ERROR" << endl;
                m_mediaSource->m_streamBuf.clear();
                m_mediaSource->m_is_error = true;
                m_loop = false;
                m_mediaSource->sendSourceEvent(SourceEventError);
                closeSource();
                return;
            }
        }

        if (fFrameSize > fMaxSize)
        {
            fNumTruncatedBytes = fFrameSize - fMaxSize;
            fFrameSize = fMaxSize;
        }
        memmove(fTo, data->m_content.data(), fFrameSize);
        m_frameCount++;

        // Set the 'presentation time':
        fDurationInMicroseconds = m_uSecsPerFrame;
        fPresentationTime = data->m_presentationTime;
    }
    else
    {
        // To avoid infinite recursion, Provide delayed return since no data.
        fFrameSize = 0;
        nextTask() = envir().taskScheduler().scheduleDelayedTask(10*1000,
          (TaskFunc*)ADTSByteStreamSource::retryGetFrame, this);
        return;
    }

    // Deliver the frame to downstream client.
    FramedSource::afterGetting(this);
}

void ADTSByteStreamSource
::afterGettingFrame(void* clientData, unsigned frameSize,
		unsigned numTruncatedBytes,
		struct timeval presentationTime,
		unsigned durationInMicroseconds)
{
    ADTSByteStreamSource* source = (ADTSByteStreamSource*)clientData;
    source->fFrameSize = frameSize;
    source->fNumTruncatedBytes = numTruncatedBytes;
    source->fPresentationTime = presentationTime;
    source->fDurationInMicroseconds = durationInMicroseconds;
    FramedSource::afterGetting(source);
}

void ADTSByteStreamSource::onSourceClosure(void* clientData)
{
  ADTSByteStreamSource* source = (ADTSByteStreamSource*)clientData;
  source->onSourceClosure1();
}

void ADTSByteStreamSource::onSourceClosure1() {
  // This routine was called because the currently-read source was closed
  // (probably due to EOF).  Close this source down, and move to the
  // next one:
  closeSource();
}

void ADTSByteStreamSource::closeSource()
{
    LOG(info) << "Closing source..." << endl;
    if (m_loop)
    {
        if (m_avLoopSync)
        {
            /* AV-pair loop boundary: don't seek/restart yet. Park here
             * and let the coordinator fire restartForLoop() once the
             * peer subsession has also reached EOS. live555 will stop
             * polling this source until restartForLoop() schedules the
             * retryGetFrame task. */
            LOG(info) << "ADTSByteStreamSource parked at EOS for AV-sync" << endl;
            m_avLoopSync->signalEos(this);
            return;
        }
        restartForLoop();
    }
    else
    {
        handleClosure();
    }
    return;
}

void ADTSByteStreamSource::restartForLoop()
{
    LOG(info) << "ADTSByteStreamSource restartForLoop, stream:" << m_streamName << endl;
    if (m_mediaSource)
    {
        m_mediaSource->seekToStart();
    }
    nextTask() = envir().taskScheduler().scheduleDelayedTask(0,
        (TaskFunc*)ADTSByteStreamSource::retryGetFrame, this);
}

void ADTSByteStreamSource::retryGetFrame(void* clientData)
{
    ADTSByteStreamSource* source = (ADTSByteStreamSource*)clientData;
    if (source)
    {
        source->doGetNextFrame();
    }
}

void ADTSByteStreamSource::dataArrivalCheck(ADTSByteStreamSource* source)
{
  if (source)
  {
    if (source->m_frameCount == 0)
    {
        LOG(error) << "No data received for this stream, closing it" << endl;
        if (source->m_mediaSource)
        {
            source->m_mediaSource->m_is_error = true;
        }
        source->m_loop = false;
        source->closeSource();
    }
  }
  return;
}

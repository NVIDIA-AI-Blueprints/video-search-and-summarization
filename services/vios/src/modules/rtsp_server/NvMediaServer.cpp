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

#include "NvMediaServer.h"
#include "H264VideoRTPSink.hh"
#include "H265VideoRTPSink.hh"
#include "ByteStreamFileSource.hh"
#include "H264VideoStreamFramer.hh"
#include "H265VideoStreamFramer.hh"
#include "ADTSByteStreamSource.hh"
#include "liveMedia.hh"
#include "GroupsockHelper.hh"
#include "logger.h"
#include "garbagecollector.h"
#include "storage_management.h"
#include "RtspSyncPlayback.h"
#include "AvLoopSyncCoordinator.h"

constexpr int CHECK_STALE_MEDIA_SOURCE_INTERVAL_US = 120*1000*1000;

constexpr int DEFAULT_AUDIO_SAMPLING_FREQ = 16000;
constexpr int DEFAULT_AUDIO_CHANNELS = 2;
constexpr const char* DEFAULT_AUDIO_CODEC_AAC = "AAC";

NvFileServerMediaSubsession*
NvFileServerMediaSubsession::createNew(UsageEnvironment& env,
        std::string streamName, eMediaType mediaType, eSourceType sourceType,
        Boolean reuseFirstSource, string url_params)
{
    portNumBits initialPortNum = 6970;  // Default port settings
    if (GET_CONFIG().rtsp_out_base_udp_port_num != -1)
    {
        // Set the starting port number of outgoing rtp connections.
        initialPortNum = GET_CONFIG().rtsp_out_base_udp_port_num;
    }
    return new NvFileServerMediaSubsession(env, streamName,
            mediaType, sourceType, reuseFirstSource, initialPortNum, url_params);
}

NvFileServerMediaSubsession::NvFileServerMediaSubsession(UsageEnvironment& env,
        std::string streamName, eMediaType mediaType, eSourceType sourceType,
        Boolean reuseFirstSource, portNumBits initialPortNum, string url_params)
  : FileServerMediaSubsession(env, streamName.c_str(), reuseFirstSource, initialPortNum, GET_CONFIG().rtcp_rtp_port_multiplex)
  , fAuxSDPLine(nullptr)
  , fDoneFlag(0)
  , fDummyRTPSink(nullptr)
  , m_streamName(streamName)
  , m_mediaSource(nullptr)
  , m_url_params(url_params)
  , m_PlayModeCheckTask(nullptr)
  , m_stream_state(InitialState)
  , m_streamToken(nullptr)
  , m_mediaType(mediaType)
  , m_sourceType(sourceType)
  , m_startTime(0)
  , m_endTime(0)
{
    m_sessionId = generate_uuid();
    LOG(info) << this <<"::NvFileServerMediaSubsession streamName:" << m_streamName << ", mediaType:" << mediaTypeAsString(m_mediaType) << ", m_sessionId:" << m_sessionId << endl;
    createMediaSource(mediaType, sourceType);
}

NvFileServerMediaSubsession::~NvFileServerMediaSubsession()
{
    try {
        LOG(info) << this <<"::~NvFileServerMediaSubsession streamName:" << m_streamName << ", mediaType:" << mediaTypeAsString(m_mediaType) << ", m_sessionId:" << m_sessionId << endl;
        envir().taskScheduler().unscheduleDelayedTask(m_PlayModeCheckTask);
        if (m_mediaSource)
        {
            m_mediaSource->destroy();
            GarbageCollector::getInstace()->insert(move(m_mediaSource));
            m_mediaSource = nullptr;
        }
        m_stream_state = DestroyState;
        setDoneFlag();
        if (fAuxSDPLine != nullptr)
        {
            delete[] fAuxSDPLine;
        }
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~NvFileServerMediaSubsession: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~NvFileServerMediaSubsession" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void NvFileServerMediaSubsession::createMediaSource(eMediaType mediaType, eSourceType sourceType)
{
    m_mediaSource.reset(new NvMediaSource(m_streamName, mediaType, sourceType, m_url_params, m_sessionId));
    if (m_url_params.find("bbox=1") != string::npos || m_url_params.find("dbg=1") != string::npos)
    {
        m_vodEnableOverlay = true;
    }
    /* If the coordinator was already injected before createMediaSource()
     * (it isn't today -- setAvLoopSync runs after construction -- but
     * keep this defensive in case the call order changes), forward it.
     * The common path is setAvLoopSync() doing the propagation itself. */
    if (m_avLoopSync && m_mediaSource)
    {
        m_mediaSource->setAvLoopSync(m_avLoopSync);
    }
    m_mediaSource->create();
}

void NvFileServerMediaSubsession::setAvLoopSync(
    const std::shared_ptr<AvLoopSyncCoordinator>& coord)
{
    m_avLoopSync = coord;
    if (m_mediaSource)
    {
        m_mediaSource->setAvLoopSync(coord);
    }
}

void NvFileServerMediaSubsession::destroyMediaSource()
{
    if (m_mediaSource)
    {
        if (GET_CONFIG().nv_streamer_sync_file_count > 0)
        {
            RtspSyncPlayback::getInstance()->removeMediaSource(m_mediaSource);
        }
        m_mediaSource->destroy();
        if (m_vodEnableOverlay)
        {
            m_mediaSource->stopOverlayPipeline();
        }
        GarbageCollector::getInstace()->insert(move(m_mediaSource));
        m_mediaSource = nullptr;
    }
}

void NvFileServerMediaSubsession::checkIfSourceInPlayMode(void* clientData)
{
    NvFileServerMediaSubsession* sess = (NvFileServerMediaSubsession*)clientData;
    if (sess && sess->m_mediaSource && sess->m_stream_state == DescribeState)
    {
        LOG(info) << "Destroying stale media source, m_sessionId:" << sess->getSessionId() << endl;
        sess->destroyMediaSource();
    }
}

static void frameSourceEvent(eFrameSourceEvent sourceEvent, void *data)
{
    NvFileServerMediaSubsession *fileServer = (NvFileServerMediaSubsession *) data;
    if (fileServer)
    {
        fileServer->frameSourceEventChange(sourceEvent);
    }
}

void NvFileServerMediaSubsession::frameSourceEventChange(eFrameSourceEvent sourceEvent)
{
    if (m_stream_state == DestroyState || m_stream_state == InvalidState)
    {
        LOG(error) << "Source Event received in wrong state" << endl;
        return;
    }

    switch(sourceEvent)
    {
        case SourceEventEOF:
        {
            RTPSink const *rtpSink = nullptr;
            RTCPInstance const *rtcp = nullptr;
            bool isSeekable = GET_CONFIG().nv_streamer_seekable;
            if (m_url_params.find("seekable=true") != string::npos)
            {
                isSeekable = true;
            }
            else if (m_url_params.find("seekable=false") != string::npos)
            {
                isSeekable = false;
            }

            if (isSeekable == false)
            {
                if (m_streamToken)
                {
                    getRTPSinkandRTCP(m_streamToken, rtpSink, rtcp);
                    if (rtcp)
                    {
                        LOG(info) << "Sending RTCP BYE to stream:" << m_streamName << ", m_sessionId:" << m_sessionId << endl;
                        /* send BYE message 3 times in case client misses one */
                        for (int i = 0; i < 3; i++)
                        {
                            ((RTCPInstance *)rtcp)->sendBYE();
                        }
                    }
                }

                if (m_mediaSource)
                {
                    destroyMediaSource();
                    envir().taskScheduler().unscheduleDelayedTask(m_PlayModeCheckTask);
                }
            }
            m_stream_state = InitialState;
            break;
        }
        case SourceEventError:
        {
            if (m_mediaSource)
            {
                destroyMediaSource();
                envir().taskScheduler().unscheduleDelayedTask(m_PlayModeCheckTask);
            }
            m_stream_state = InitialState;
            break;
        }
        case SourceEventStart:
        {
            // Nothing to do
        }
    }
    return;
}

void NvFileServerMediaSubsession::setRtcpBaseTime()
{
    uint64_t startTime = 0;
    int attempts = 0;
    if (m_streamToken)
    {
        int sleep_time_ms = 5;
        int max_attempts = 10;
        if (m_vodEnableOverlay)
        {
            sleep_time_ms = 20;
            max_attempts = 50;
        }

        do
        {
            startTime = m_mediaSource->getActualStartTime();
            attempts++;
            if (startTime != 0)
            {
                /* Exit the loop if a non-zero value is received */
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(sleep_time_ms));
        } while (attempts < max_attempts);

        if (startTime == 0)
        {
            LOG(warning) << "getActualStartTime is 0, using filestartTime + offset" << endl;
            /* If still got the zero then use filestartTime + offset */
            int64_t seek_offset = m_videoStreamSource->getSeekOffset();
            startTime = m_mediaSource->getStartTime() + seek_offset;
        }

        struct timeval ntpTime;
        ntpTime.tv_sec = startTime / 1000;
        ntpTime.tv_usec = (startTime % 1000) * 1000;
        LOG(info) << "Setting RTCP base time for:" << m_streamName << ", ActualStartTime:" << startTime
                << ", ntpTime:" << ntpTime.tv_sec << "." << ntpTime.tv_usec << endl;
        StreamState* streamState = (StreamState*)m_streamToken;
        if (streamState != nullptr)
        {
            streamState->setRtcpBaseTime(ntpTime);
        }
    }
}

void NvFileServerMediaSubsession
::startStream(unsigned clientSessionId, void* streamToken, TaskFunc* rtcpRRHandler,
	      void* rtcpRRHandlerClientData, unsigned short& rtpSeqNum,
	      unsigned& rtpTimestamp,
	      ServerRequestAlternativeByteHandler* serverRequestAlternativeByteHandler,
	      void* serverRequestAlternativeByteHandlerClientData)
{
    LOG(info) << "startStream, clientSessionId:" << clientSessionId << ", streamName:" << m_streamName
            << ", mediaType:" << mediaTypeAsString(m_mediaType) << ", m_sessionId:" << m_sessionId << endl;
    m_streamToken = streamToken;
    if (m_mediaSource)
    {
        if (m_stream_state == PauseState)
        {
            m_mediaSource->resume();
        }
        else if (m_stream_state != PlayState)
        {
            if (m_vodEnableOverlay)
            {
                /* Destroy the existing media source and start the overlay pipeline */
                //m_mediaSource->destroy();
                m_mediaSource->playWithOverlay();
            }
            else if ((m_startTime > 0 || m_endTime > 0) && m_isSeekStreamDone == false)
            {
                /* In some cases, seekStream will not be invoked (Play range header). In such case, implement seek explicitly */
                LOG(info) << "Seek in StartStream: m_startTime: " <<  m_startTime << " m_endTime: " << m_endTime << endl;
                if (m_videoStreamSource)
                {
                    uint64_t file_start_time = m_mediaSource->getStartTime();
                    if (m_startTime > file_start_time)
                    {
                        m_videoStreamSource->setStreamSeek(m_startTime, m_endTime);
                        m_stream_state = PlayState;
                    }
                    else if (m_endTime != 0)
                    {
                        m_videoStreamSource->setStreamSeek(m_startTime, m_endTime);
                    }
                    m_isSeekStreamDone = true;
                }
            }
            else
            {
                if (GET_CONFIG().nv_streamer_sync_file_count > 0)
                {
                    RtspSyncPlayback *syncPlaybackInstance = RtspSyncPlayback::getInstance();
                    if (GET_CONFIG().nv_streamer_sync_file_count > 0)
                    {
                        RtspSyncPlayback::getInstance()->insertMediaSource(m_mediaSource);
                    }
                    if (syncPlaybackInstance && !syncPlaybackInstance->isSyncPlaybackStarted())
                    {
                        if (syncPlaybackInstance->getMediaSourceListSize() >= (size_t)GET_CONFIG().nv_streamer_sync_file_count)
                        {
                            syncPlaybackInstance->startPlayingAllSources();
                        }
                    }
                    else
                    {
                        m_mediaSource->play();
                    }
                }
                else
                {
                    m_mediaSource->play();
                }
            }
        }

        if (m_url_params.find("vodStream=true") != string::npos)
        {
            setRtcpBaseTime();
        }
    }
    m_stream_state = PlayState;

    // Call the original, default version of this routine:
    OnDemandServerMediaSubsession::startStream(clientSessionId, streamToken,
					     rtcpRRHandler, rtcpRRHandlerClientData,
					     rtpSeqNum, rtpTimestamp,
					     serverRequestAlternativeByteHandler, serverRequestAlternativeByteHandlerClientData);
}

void NvFileServerMediaSubsession
::pauseStream(unsigned clientSessionId, void* streamToken)
{
    LOG(info) << "pauseStream, clientSessionId:" << clientSessionId << ", streamName:" << m_streamName
    << ", mediaType:" << mediaTypeAsString(m_mediaType) << ", m_sessionId:" << m_sessionId << endl;
    if (m_mediaSource)
    {
        m_mediaSource->pause();
    }
    m_stream_state = PauseState;

    // Call the original, default version of this routine:
    OnDemandServerMediaSubsession::pauseStream(clientSessionId, streamToken);
}

/*
* deleteSTream is called in below cases:
1. When client sends TEARDOWN to server.
2. When liveness detector at server side finds that client is idle for 65seconds.
*/
void NvFileServerMediaSubsession
::deleteStream(unsigned clientSessionId, void*& streamToken)
{
    LOG(info) << "deleteStream clientSessionId:" << clientSessionId << ", streamName:" << m_streamName
            << ", mediaType:" << mediaTypeAsString(m_mediaType) << ", m_sessionId:" << m_sessionId << endl;
    if (m_mediaSource)
    {
        destroyMediaSource();
        envir().taskScheduler().unscheduleDelayedTask(m_PlayModeCheckTask);
    }
    m_stream_state = InitialState;
    // Call the original, default version of this routine:
    OnDemandServerMediaSubsession::deleteStream(clientSessionId, streamToken);
    m_streamToken = nullptr;
    m_isSeekStreamDone = false;
}

static void afterPlayingDummy(void* clientData)
{
    NvFileServerMediaSubsession* subsess = (NvFileServerMediaSubsession*)clientData;
    if (subsess)
    {
        subsess->afterPlayingDummy1();
    }
}

void NvFileServerMediaSubsession::afterPlayingDummy1()
{
    // Unschedule any pending 'checking' task:
    envir().taskScheduler().unscheduleDelayedTask(nextTask());
    // Signal the event loop that we're done:
    setDoneFlag();
}

static void checkForAuxSDPLine(void* clientData)
{
    NvFileServerMediaSubsession* subsess = (NvFileServerMediaSubsession*)clientData;
    if (subsess)
    {
        subsess->checkForAuxSDPLine1();
    }
}

void NvFileServerMediaSubsession::setDoneFlagAndResetSource()
{
    if (m_mediaSource)
    {
        m_mediaSource->pause();
    }
    setDoneFlag();
}

void NvFileServerMediaSubsession::checkForAuxSDPLine1()
{
    nextTask() = nullptr;

    if ((GET_CONFIG().nv_streamer_sync_playback == true ||
        (GET_CONFIG().nv_streamer_sync_file_count > 0 && RtspSyncPlayback::getInstance()->isSyncPlaybackStarted())) &&
        (m_syncSourceToGlobalFrameId == -1))
    {
        int frames_to_wait = RtspSyncPlayback::getInstance()->framesToWait();
        if (frames_to_wait == 0)
        {
            m_syncSourceToGlobalFrameId = 0;
        }
        else
        {
            m_syncSourceToGlobalFrameId = RtspSyncPlayback::getInstance()->getGlobalFrameId();
            m_syncSourceToGlobalFrameId += frames_to_wait;
        }

        int timeTosync_us = RtspSyncPlayback::getInstance()->timeToSync() * 1000;
        nextTask() = envir().taskScheduler().scheduleDelayedTask(timeTosync_us,
                (TaskFunc*)checkForAuxSDPLine, this);
        return;
    }

    char const* dasl;
    if (fAuxSDPLine != nullptr)
    {
        // Signal the event loop that we're done:
        setDoneFlagAndResetSource();
    }
    else
    {
        dasl = fDummyRTPSink ? fDummyRTPSink->auxSDPLine() : nullptr;
        if (fDummyRTPSink != nullptr && dasl != nullptr)
        {
            fAuxSDPLine = strDup(dasl);
            fDummyRTPSink = nullptr;

            // Signal the event loop that we're done:
            setDoneFlagAndResetSource();
        }
        else if (m_mediaSource && m_mediaSource->isError())
        {
            m_stream_state = InitialState;
            setDoneFlagAndResetSource();
        }
        else if (!fDoneFlag)
        {
            // try again after a brief delay:
            int uSecsToDelay = 50*1000; // 50 ms
            nextTask() = envir().taskScheduler().scheduleDelayedTask(uSecsToDelay,
                    (TaskFunc*)checkForAuxSDPLine, this);
        }
    }
}

char const* NvFileServerMediaSubsession::getAuxSDPLine(RTPSink* rtpSink, FramedSource* inputSource)
{
    if (fAuxSDPLine != nullptr)
    {
        return fAuxSDPLine; // it's already been set up (for a previous client)
    }

    if (m_mediaSource && m_mediaSource->isError())
    {
        return nullptr;
    }

    if (fDummyRTPSink == nullptr && rtpSink != nullptr)
    {
        // we're not already setting it up for another, concurrent stream
        // Note: For MP4 video files, the 'config' information ("profile-level-id" and "sprop-parameter-sets") isn't known
        // until we start reading the file.  This means that "rtpSink"s "auxSDPLine()" will be NULL initially,
        // and we need to start reading data from our file until this changes.
        fDummyRTPSink = rtpSink;

        // Start reading the file:
        fDummyRTPSink->startPlaying(*inputSource, afterPlayingDummy, this);

        // Check whether the sink's 'auxSDPLine()' is ready:
        checkForAuxSDPLine(this);
    }
    envir().taskScheduler().doEventLoop(&fDoneFlag);
    LOG(info) << "getAuxSDPLine Done for mediaType:" << mediaTypeAsString(m_mediaType)
            << ", m_streamName:" << m_streamName << ", m_sessionId:" << m_sessionId << endl;

    if (fDoneFlag && m_stream_state != DestroyState)
    {
        // Check if this source is created for play mode or not.
        m_stream_state = DescribeState;
        int _delay = CHECK_STALE_MEDIA_SOURCE_INTERVAL_US;
        m_PlayModeCheckTask = envir().taskScheduler().scheduleDelayedTask(_delay,
                (TaskFunc*)checkIfSourceInPlayMode, this);
    }
    return fAuxSDPLine;
}

std::pair<uint64_t, uint64_t> NvFileServerMediaSubsession::getRangeFromUrlParams(const string& url_params)
{
    uint64_t start = 0, end = 0;
    uint64_t file_start_time  = 0;
    double frameRate = 30.0;
    if (m_mediaSource)
    {
        file_start_time = m_mediaSource->getStartTime();
        frameRate = m_mediaSource->getFrameRate();
    }

    vector<string> uri_arr = splitString(url_params, "&");
    for (size_t i = 0; i < uri_arr.size(); i++)
    {
        string param = uri_arr[i];
        if (param.find("startFrameTime") != string::npos)
        {
            string hhmmss_time = param.substr(param.find("=") + 1);
            int64_t requested_time = convertStringToSeconds(hhmmss_time);
            start = file_start_time + requested_time;
        }
        else if (param.find("startFrameId") != string::npos)
        {
            int startFrameId = 0;
            string requested_frame_id = param.substr(param.find("=") + 1);
            startFrameId = stringToInt(requested_frame_id, 0);
            int64_t offset_time_ms = startFrameId * (1000.0/frameRate);
            start = file_start_time + offset_time_ms;
        }
        else if (param.find("endFrameTime") != string::npos)
        {
            string hhmmss_time = param.substr(param.find("=") + 1);
            int64_t requested_time = convertStringToSeconds(hhmmss_time);
            end = file_start_time + requested_time;
        }
        else if (param.find("endFrameId") != string::npos)
        {
            string requested_frame_id = param.substr(param.find("=") + 1);
            int64_t offset_time_ms = stringToInt(requested_frame_id, 0) * (1000.0/frameRate);
            end = file_start_time + offset_time_ms;
        }

        /* Check if any vod time params are present  */
        if (param.find("startTs") != string::npos)
        {
            string start_ts = param.substr(param.find("=") + 1);
            start = getEpocTimeInMS(start_ts);
        }
        if (param.find("endTs") != string::npos)
        {
            string end_ts = param.substr(param.find("=") + 1);
            end = getEpocTimeInMS(end_ts);
        }
    }
    return std::make_pair(start,end);
}

void NvFileServerMediaSubsession::testScaleFactor(float& scale)
{

}
void NvFileServerMediaSubsession::setStreamSourceScale(FramedSource* inputSource, float scale)
{
    LOG(info) << "setStreamSourceScale: scale = "<< scale << endl;
    if (inputSource == nullptr)
    {
        LOG(error) << "input source is null" << endl;
        return;
    }
    FramedFilter* fSource = static_cast<FramedFilter*>(inputSource);
    H264ByteStreamSource* source = static_cast<H264ByteStreamSource *> (fSource->inputSource());
    if (source)
    {
        source->setStreamScale(scale);
    }
}

void NvFileServerMediaSubsession::seekStreamSource(FramedSource* inputSource,
        double& seekNPT, double streamDuration, u_int64_t& numBytes)
{
    LOG(info) << "seekStreamSource: startTime: " <<  seekNPT << " duration: " << streamDuration << " numBytes: " << numBytes << endl;
    numBytes = 0;
    char* p = nullptr;
    seekStreamSource(inputSource, p, p);
}

void NvFileServerMediaSubsession::seekStreamSource(FramedSource* inputSource,
        char*& absStart, char*& absEnd)
{
    if (inputSource == nullptr)
    {
        LOG(error) << "input source is null" << endl;
        return;
    }

    /* Ignore this seek in case of sync_playback, It will be handled while createNewStreamSource */
    if (GET_CONFIG().nv_streamer_sync_playback == true || m_vodEnableOverlay)
    {
        delete[] absStart; absStart = nullptr;
        delete[] absEnd; absEnd = nullptr;
        return;
    }

    FramedFilter* fSource = static_cast<FramedFilter*>(inputSource);
    H264ByteStreamSource* source = static_cast<H264ByteStreamSource *> (fSource->inputSource());
    uint64_t start = 0;
    if (absStart != nullptr)
    {
        start = getEpocTimeInMS(absStart, false);
    }
    uint64_t end = 0;
    if (absEnd != nullptr)
    {
        end = getEpocTimeInMS(absEnd, false);
    }

    start = m_startTime;
    end = m_endTime;
    LOG(info) << "seekStreamSource: absStart: " <<  start << " absEnd: " << end << endl;
    if (source)
    {
        if (m_mediaSource)
        {
            uint64_t file_start_time = m_mediaSource->getStartTime();
            if (start > file_start_time)
            {
                source->setStreamSeek(start, end);
                // Media source starts playing from seek position.
                m_stream_state = PlayState;
            }
            else if (end != 0)
            {
                source->setStreamSeek(start, end);
            }
            m_isSeekStreamDone = true;
        }
    }
    delete[] absStart; absStart = nullptr;
    delete[] absEnd; absEnd = nullptr;
}

static void checkAndSeekToGlobalFrameId(void* clientData)
{
    NvFileServerMediaSubsession* subsess = (NvFileServerMediaSubsession*)clientData;
    if (subsess && subsess->getVideoStreamSource())
    {
        subsess->seekStreamByGlobalFrameId(subsess->getVideoStreamSource());
    }
}

void NvFileServerMediaSubsession::seekStreamByGlobalFrameId(H264ByteStreamSource* source)
{
    uint64_t start = 0;
    uint64_t end = 0;
    uint64_t file_start_time  = 0;
    double frameRate = 30.0;
    if (m_mediaSource)
    {
        file_start_time = m_mediaSource->getStartTime();
        frameRate = m_mediaSource->getFrameRate();
    }

    if (m_syncSourceToGlobalFrameId > 0)
    {
        int64_t offset_time_ms = m_syncSourceToGlobalFrameId * (1000.0/frameRate);
        start = file_start_time + offset_time_ms;
    }
    LOG(info) << "seekStreamByGlobalFrameId:" << m_syncSourceToGlobalFrameId << ", absStart: " <<  start << " absEnd: " << end << endl;
    if (source && m_mediaSource)
    {
        if (start > file_start_time)
        {
            source->setStreamSeek(start, end);
            // Media source starts playing from seek position.
            m_stream_state = PlayState;
        }
        else if (end != 0)
        {
            source->setStreamSeek(start, end);
        }
    }
}

FramedSource* NvFileServerMediaSubsession
::createNewStreamSource(unsigned /*clientSessionId*/, unsigned& estBitrate)
{
    string codec;
    string sourceState;

    LOG(info) << "createNewStreamSource mediaType:" << mediaTypeAsString(m_mediaType)
            << ", m_sessionId:" << m_sessionId << endl;
    if (!m_mediaSource)
    {
        createMediaSource(m_mediaType, m_sourceType);
    }
    /* If the underlying demux reported an error (e.g. unsupported audio codec
     * or pipeline-build failure), do not create a stream source. Returning
     * nullptr keeps live555 from setting up the RTP sink path, which avoids
     * the data-arrival timer + seekToStart cascade that would otherwise run
     * against a half-built pipeline. */
    if (m_mediaSource && m_mediaSource->isError())
    {
        LOG(error) << "createNewStreamSource: media source in error state, mediaType:"
                << mediaTypeAsString(m_mediaType)
                << ", streamName:" << m_streamName
                << ", m_sessionId:" << m_sessionId << endl;
        m_stream_state = InvalidState;
        return nullptr;
    }
    if (m_stream_state == InitialState)
    {
        // Created stream source for first time, start the media source.
        sourceState = "describe";
        m_mediaSource->setSourceState(sourceState);
        m_mediaSource->play();
    }
    else if (m_stream_state == DescribeState)
    {
        // Already in describe state.
        sourceState = "play";
        m_mediaSource->setSourceState(sourceState);
        envir().taskScheduler().unscheduleDelayedTask(m_PlayModeCheckTask);

        std::pair<uint64_t, uint64_t> time_range = getRangeFromUrlParams(m_url_params);
        m_startTime = time_range.first;
        m_endTime = time_range.second;
    }

    m_mediaSource->registerCallback(frameSourceEvent, this);
    if (m_mediaType == MediaTypeVideo)
    {
        // Create a framer for the Video Elementary Stream:
        estBitrate = 500; // kbps, estimate`
        m_videoStreamSource = H264ByteStreamSource::createNew(envir(), m_streamName, m_mediaSource, m_url_params, sourceState, m_sessionId);
        if ((GET_CONFIG().nv_streamer_sync_playback == true ||
            (GET_CONFIG().nv_streamer_sync_file_count > 0 && RtspSyncPlayback::getInstance()->isSyncPlaybackStarted())) &&
            m_syncSourceToGlobalFrameId > 0)
        {
            int timeInDescribeAndPlay = abs(m_syncSourceToGlobalFrameId - RtspSyncPlayback::getInstance()->getGlobalFrameId());
            LOG(info) << "Time (frames) between describe and play:" << timeInDescribeAndPlay << endl;
            if (timeInDescribeAndPlay > 5)
            {
                /* There is much time difference between describe & play, this can happen in case of proxyserver,where
                *  proxyserver just sends describe & seats idle till new client request.
                *  In this case get new seek point from global clock */
                int frames_to_wait = RtspSyncPlayback::getInstance()->framesToWait();
                if (frames_to_wait == 0)
                {
                    m_syncSourceToGlobalFrameId = 0;
                }
                else
                {
                    m_syncSourceToGlobalFrameId = RtspSyncPlayback::getInstance()->getGlobalFrameId();
                    m_syncSourceToGlobalFrameId += frames_to_wait;
                }
                int timeTosync_us = RtspSyncPlayback::getInstance()->timeToSync() * 1000;
                m_stream_state = PlayState;
                nextTask() = envir().taskScheduler().scheduleDelayedTask(timeTosync_us,
                                (TaskFunc*)checkAndSeekToGlobalFrameId, this);
            }
            else
            {
                seekStreamByGlobalFrameId(m_videoStreamSource);
            }
        }
        codec = m_mediaSource->getVideoCodec();
        if (iequals(codec, "h264"))
        {
            return H264VideoStreamDiscreteFramer::createNew(envir(), m_videoStreamSource);
        }
        else if (iequals(codec, "h265"))
        {
            return H265VideoStreamDiscreteFramer::createNew(envir(), m_videoStreamSource);
        }
    }
    else if (m_mediaType == MediaTypeAudio)
    {
        int sampling_freq = 16000, channels = 2;
        if (m_sourceType == SourceTypeLive)
        {
            codec = DEFAULT_AUDIO_CODEC_AAC;
            sampling_freq = DEFAULT_AUDIO_SAMPLING_FREQ;
            channels = DEFAULT_AUDIO_CHANNELS;
        }
        else
        {
            codec = m_mediaSource->getAudioCodec();
            sampling_freq = m_mediaSource->getSampleRate();
            channels = m_mediaSource->getChannels();
        }

        LOG(info) << "codec:"<<codec<<",sampling_freq:"<<sampling_freq<<",channels:"<<channels<<endl;
        // Create a framer for the audio Elementary Stream:
        if (codec.find("AAC") != string::npos)
        {
            if (m_sourceType == SourceTypeFile)
            {
                const AacParams aac = m_mediaSource->getAacParams();
                sampling_freq = aac.sample_rate;
                channels      = aac.channels;
            }
            estBitrate = 96;
            return ADTSByteStreamSource::createNew(
                        envir(), m_streamName, m_mediaSource,
                        m_url_params, 0x29, sampling_freq, channels);
        }
    }
    return nullptr;
}

RTPSink* NvFileServerMediaSubsession
::createNewRTPSink(Groupsock* rtpGroupsock,
		   unsigned char rtpPayloadTypeIfDynamic,
		   FramedSource* inputSource)
{
    LOG(info) << "createNewRTPSink mediaType:" << mediaTypeAsString(m_mediaType)
            << ", m_stream_state:" << m_stream_state << ", m_sessionId:" << m_sessionId << endl;
    string codec = "h264";

    if (m_mediaType == MediaTypeVideo)
    {
        unsigned newBufferSize = GET_CONFIG().tx_socket_buffer_size;
        increaseSendBufferTo(envir(), rtpGroupsock->socketNum(), newBufferSize);
        LOG(info) << "Socket sendbuffer size = " << getSendBufferSize(envir(), rtpGroupsock->socketNum()) << endl;

        codec = m_mediaSource->getVideoCodec();
        if (iequals(codec, "h264"))
        {
            return H264VideoRTPSink::createNew(envir(), rtpGroupsock, rtpPayloadTypeIfDynamic);
        }
        else if (iequals(codec, "h265"))
        {
            return H265VideoRTPSink::createNew(envir(), rtpGroupsock, rtpPayloadTypeIfDynamic);
        }
    }
    else if (m_mediaType == MediaTypeAudio)
    {
        int sampling_freq = 16000, channels = 2;
        string m_audioConfigStr;
        if (m_sourceType == SourceTypeLive)
        {
            codec = DEFAULT_AUDIO_CODEC_AAC;
            sampling_freq = DEFAULT_AUDIO_SAMPLING_FREQ;
            channels = DEFAULT_AUDIO_CHANNELS;
        }
        else
        {
            codec = m_mediaSource->getAudioCodec();
            sampling_freq = m_mediaSource->getSampleRate();
            channels = m_mediaSource->getChannels();
        }

        LOG(info) << "codec:"<<codec<<",sampling_freq:"<<sampling_freq<<",channels:"<<channels<<endl;
        if (codec.find("AAC") != string::npos)
        {
            if (m_sourceType == SourceTypeFile)
            {
                const AacParams aac = m_mediaSource->getAacParams();
                sampling_freq = aac.sample_rate;
                channels      = aac.channels;
                if (m_stream_state == InitialState)
                {
                    m_audioConfigStr = aac.configStr;
                }
            }
            else if (m_stream_state == InitialState)
            {
                m_audioConfigStr = m_mediaSource->getCodecConfigId();
            }
            return MPEG4GenericRTPSink::createNew(envir(), rtpGroupsock,
					rtpPayloadTypeIfDynamic,
					sampling_freq, "audio", "AAC-hbr", m_audioConfigStr.c_str(), channels);
        }
    }
    return nullptr;
}

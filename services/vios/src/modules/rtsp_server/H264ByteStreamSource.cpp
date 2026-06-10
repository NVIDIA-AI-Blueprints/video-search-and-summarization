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

#include "H264ByteStreamSource.hh"
#include "stream_buffer.h"
#include "logger.h"
#include "utils.h"
#include "cmdline_parser.h"
#include "RtspSyncPlayback.h"
#include "mm_utils.h"
#include "AvLoopSyncCoordinator.h"

namespace
{
    /* Returns true iff two timeval values denote the same wall-clock instant. */
    inline bool timevalEqual(const struct timeval &a, const struct timeval &b)
    {
        return a.tv_sec == b.tv_sec && a.tv_usec == b.tv_usec;
    }

    /* Decide whether a coded-slice NAL is a *continuation* slice (slice 2..N
     * of a multi-slice picture) rather than the first slice of a new picture.
     *
     * Per spec, the first slice of a picture has:
     *   - H.264 :  first_mb_in_slice == 0
     *              (first ue(v) of slice_header(); value 0 is encoded as the
     *              single bit '1' -- so the high bit of the byte right after
     *              the NAL header is 1.)
     *   - H.265 :  first_slice_segment_in_pic_flag == 1
     *              (the very first bit of slice_segment_header(), i.e. the
     *              high bit of the byte right after the 2-byte NAL header.)
     *
     * Continuation slices have those bits inverted, which is exactly what we
     * detect here. The check is buffer-safe (size guarded) and operates on
     * the start-code-stripped DiscreteFrame payload that NvMediaSource
     * publishes (NvMediaSource::onFrame() calls removeH264NalStartCodes()
     * before pushing into the queue, so content[0] is the first NAL header
     * byte).
     */
    bool isContinuationSlice(const std::vector<uint8_t> &content,
                             uint8_t nal_type,
                             const std::string &codec)
    {
        if (iequals(codec, "h264"))
        {
            /* Only nal_unit_type 1 (non-IDR slice) and 5 (IDR slice) are
             * coded slices in H.264; anything else cannot be a continuation. */
            if (nal_type != NaluType::kSlice && nal_type != NaluType::kIdr)
            {
                return false;
            }
            /* 1-byte NAL header + at least 1 slice-header byte. */
            if (content.size() < 2)
            {
                return false;
            }
            return (content[1] & 0x80) == 0;
        }
        if (iequals(codec, "h265"))
        {
            /* HEVC VCL NAL units are types 0..31; everything 32+ is non-VCL
             * (VPS/SPS/PPS/AUD/SEI/...). */
            if (nal_type > 31)
            {
                return false;
            }
            /* 2-byte NAL header + at least 1 slice-header byte. */
            if (content.size() < 3)
            {
                return false;
            }
            return (content[2] & 0x80) == 0;
        }
        return false;
    }
} // namespace

constexpr int DATA_ARRIVAL_TIMEOUT_USEC = 30*1000*1000;
constexpr int VOD_DATA_ARRIVAL_TIMEOUT_USEC = 10*1000*1000;
constexpr int RESTART_SOURCE_DELAY_USEC = 5*1000*1000;
constexpr int DEFAULT_VIDEO_FRAME_PLAY_TIME_USEC = 12*1000;

constexpr int NO_DATA_RETRY_INTERVAL_USEC = 2000;       // 2ms retry interval for initial retries
constexpr int NO_DATA_FAST_RETRIES = 10;                // First 10 retries at 2ms
constexpr int IDR_HEAD_START_USEC = 2000;  // 2ms head start for IDR frames to compensate for larger size

H264ByteStreamSource
::H264ByteStreamSource(UsageEnvironment& env, std::string streamName, shared_ptr<NvMediaSource> mediasource,
        string url_params, string sourceState, string sessionId,
		unsigned preferredFrameSize, unsigned playTimePerFrame)
    : FramedSource(env)
    , fPreferredFrameSize(preferredFrameSize)
    , fPlayTimePerFrame(playTimePerFrame)
    , m_streamName(streamName)
    , m_sessionId(sessionId)
    , m_mediaSource(mediasource)
    , m_seekOffset(-1)
    , m_start(0)
    , m_end(0)
    , m_seekRate(1.0)
    , m_loop(false)
    , m_url_params(url_params)
    , m_sourceState(sourceState)
    , m_restartCount(0)
    , m_frameRate(DEFAULT_VIDEO_FRAME_RATE)
    , m_noDataRetryCount(0)
    , m_lastFrameWasHeader(false)
    , m_lastFramePT({0, 0})
    , m_havePrevPT(false)
    , m_DataArrivalCheckTask(nullptr)
    , m_frameCount(0)
  {
      LOG(info) << __METHOD_NAME__ << ", sourceState:" << m_sourceState << ", m_sessionId:" << m_sessionId << endl;

      // Get framerate from media source
      if (m_mediaSource) {
          m_frameRate = m_mediaSource->getFrameRate();
          if (m_frameRate <= 0) {
              m_frameRate = DEFAULT_VIDEO_FRAME_RATE;
          }
      }
      LOG(info) << "Stream:" << m_streamName << ", framerate:" << m_frameRate << endl;

      m_loop = GET_CONFIG().nv_streamer_loop_playback;
      if (m_url_params.find("loop=true") != string::npos)
      {
          m_loop = true;
      }
      else if (m_url_params.find("loop=false") != string::npos)
      {
          m_loop = false;
      }

      bool vod_stream = false;
      if (m_url_params.find("vodStream=true") != string::npos)
      {
          vod_stream = true;
      }

      /* loop playback is not required in case of recorded stream or webcam stream */
      if (m_mediaSource->getSourceType() == SourceTypeLive || vod_stream)
      {
          m_loop = false;
      }
      LOG(info) << "Stream:" << m_streamName << ", m_loop:" << m_loop
                << ", url_params:" << m_url_params << endl;

      /* Register with the AV-loop-sync coordinator iff the media source
       * was paired with one (createNewSMS() only injects when both audio
       * and video subsessions exist for the same file) AND looping is
       * actually enabled for this source. Otherwise leave m_avLoopSync
       * null so closeSource() takes the unchanged immediate-restart
       * path. */
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

      if (m_mediaSource && GET_CONFIG().enable_mega_simulation == false)
      {
          int data_arrival_timeout_us = DATA_ARRIVAL_TIMEOUT_USEC;
          if (vod_stream)
          {
              /* For vod stream, set 10sec timeout. If no data receievd then send bye packet */
              data_arrival_timeout_us = VOD_DATA_ARRIVAL_TIMEOUT_USEC;
          }
          m_DataArrivalCheckTask = envir().taskScheduler().scheduleDelayedTask(data_arrival_timeout_us,
              (TaskFunc*)H264ByteStreamSource::dataArrivalCheck, this);
      }
      setStreamSeek(0, 0);
}

H264ByteStreamSource::~H264ByteStreamSource()
{
    LOG(info) << __METHOD_NAME__ << ", sourceState:" << m_sourceState <<  endl;

    if (m_DataArrivalCheckTask)
    {
        envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalCheckTask);
    }
    /* Unregister from the AV-loop-sync coordinator. If we were parked
     * at EOS waiting on the audio side, this also releases the audio
     * side so it doesn't deadlock waiting for a now-gone video. */
    if (m_avLoopSync)
    {
        m_avLoopSync->unregisterParticipant(this);
        m_avLoopSync.reset();
    }
    LOG(info) << "Exiting ~H264ByteStreamSource(), m_sessionId:" << m_sessionId << endl;
}

void H264ByteStreamSource::setStreamScale(float scaleFactor)
{
    m_seekRate = scaleFactor;
}

void H264ByteStreamSource::setStreamSeek(uint64_t start, uint64_t end)
{
    if (m_mediaSource)
    {
        m_mediaSource->resetActualStartTime();
        uint64_t file_start_time = m_mediaSource->getStartTime();
        LOG(info) << "file start time:"<< file_start_time <<", start:" << start << endl;
        if (start > 0 && start >= file_start_time)
        {
            m_seekOffset = start - file_start_time;

            LOG(info) << "Seek offset " << m_seekOffset << endl;

            m_mediaSource->seek(m_seekOffset, end, m_seekRate);
        }
        else if (end != 0)
        {
            m_mediaSource->seek(m_seekOffset, end, m_seekRate);
        }
    }
    m_start = start;
    m_end = end;
}

H264ByteStreamSource* H264ByteStreamSource
::createNew(UsageEnvironment& env, std::string streamName, shared_ptr<NvMediaSource> mediasource,
      string url_params, string sourceState, string sessionId,
	    unsigned preferredFrameSize, unsigned playTimePerFrame)
{
    H264ByteStreamSource* newSource
      = new H264ByteStreamSource(env, streamName, mediasource, url_params, sourceState, sessionId,
              preferredFrameSize, playTimePerFrame);

    return newSource;
}

void H264ByteStreamSource::doGetNextFrame()
{
    std::shared_ptr<DiscreteFrame> data;
    std::vector<uint8_t> sdpFrames;
    do
    {
        fFrameSize = 0;
        if (m_mediaSource)
        {
            if (m_trunctedBytes.size() > 0)
            {
                struct timeval presentationTime;
                gettimeofday(&presentationTime, nullptr);
                data = std::make_shared<DiscreteFrame>(m_trunctedBytes, presentationTime);
                if (data && data->m_content.size() > 0)
                {
                    fFrameSize = data->m_content.size();
                }
                m_trunctedBytes.clear();
            }
            else
            {
                if (m_mediaSource->getSourceType() == SourceTypeLive && m_sourceState == "describe")
                {
                    sdpFrames = m_mediaSource->getFramesForSdp();
                    if (sdpFrames.empty() && m_mediaSource->m_streamBuf.getQueueSize() >= 2)
                    {
                        data = m_mediaSource->m_streamBuf.pop();
                        if (data && data->m_content.size() > 0)
                        {
                            sdpFrames = data->m_content;
                        }
                    }
                    fFrameSize = sdpFrames.size();
                }
                else
                {
                    data = m_mediaSource->m_streamBuf.pop();
                    if (data && data->m_content.size() > 0)
                    {
                        fFrameSize = data->m_content.size();
                    }
                }
            }
        }
        if (fFrameSize > 0)
        {
            if (m_DataArrivalCheckTask)
            {
                envir().taskScheduler().unscheduleDelayedTask(m_DataArrivalCheckTask);
            }
            if ((fFrameSize == std::string(STREAM_MSG_EOS).size() || fFrameSize == std::string(STREAM_MSG_ERROR).size()) && data)
            {
                std::string msg(data->m_content.begin(), data->m_content.end());
                if (msg == STREAM_MSG_EOS)
                {
                    LOG(info) << "H264ByteStreamSource, received EOS, m_sessionId:" << m_sessionId << endl;
                    m_mediaSource->m_streamBuf.clear();
                    if (!m_loop)
                    {
                        // This is access unit delimeter to stream last NAL unit from the server.
                        uint8_t access_unit_delimeter[6] = { 0x09, 0xf0, 0, 0, 0, 0x1 };
                        vector<uint8_t> vect(access_unit_delimeter, access_unit_delimeter + 6);

                        memmove(fTo, vect.data(), vect.size());
                        FramedSource::afterGetting(this);

                        m_mediaSource->sendSourceEvent(SourceEventEOF);
                    }
                    closeSource();
                    return;
                }
                else if (msg == STREAM_MSG_ERROR)
                {
                    LOG(error) << "H264ByteStreamSource, received ERROR, m_sessionId:" << m_sessionId << endl;
                    m_mediaSource->m_streamBuf.clear();
                    m_mediaSource->m_is_error = true;
                    if (m_loop)
                    {
                        m_restartCount++;
                        if (m_restartCount >= 3)
                        {
                            m_loop = false;
                            if (m_url_params.find("vodStream=true") != string::npos)
                            {
                                m_mediaSource->sendSourceEvent(SourceEventEOF);
                            }
                            else
                            {
                                m_mediaSource->sendSourceEvent(SourceEventError);
                            }
                            closeSource();
                            return;
                        }

                        // Try restarting after given delay.
                        nextTask() = envir().taskScheduler().scheduleDelayedTask(RESTART_SOURCE_DELAY_USEC,
                          (TaskFunc*)H264ByteStreamSource::restartSource, this);
                    }
                    else
                    {
                        m_mediaSource->m_is_error = true;
                        m_mediaSource->sendSourceEvent(SourceEventError);
                        closeSource();
                    }
                    return;
                }
            }

            if (sdpFrames.size() > 0)
            {
                memmove(fTo, sdpFrames.data(), fFrameSize);
            }
            else
            {
                // Set the 'presentation time':
                fPresentationTime = data->m_presentationTime;
                if (fFrameSize > fMaxSize)
                {
                    fNumTruncatedBytes = fFrameSize - fMaxSize;
                    fFrameSize = fMaxSize;
                    fDurationInMicroseconds = 0;
                    m_trunctedBytes.insert(m_trunctedBytes.end(), data->m_content.begin() + fMaxSize, data->m_content.end());
                    data->m_content.erase(data->m_content.begin() + fMaxSize, data->m_content.end());
                }
                else
                {
                    /* Calculate adaptive duration based on queue size for pacing */
                    unsigned adaptiveDuration = calculateAdaptiveDuration();

                    const bool isHeaderNal =
                        !isValidDataNAL(data->m_nalType, data->m_codec);

                    /* Authoritative same-AU signal for slice NALs: the
                     * first_mb_in_slice / first_slice_segment_in_pic_flag bit
                     * tells us per spec whether this slice starts a new
                     * picture or continues the previous one. */
                    const bool isContSlice = !isHeaderNal &&
                        isContinuationSlice(data->m_content,
                                            data->m_nalType,
                                            data->m_codec);

                    /* Defensive cross-check: a NAL whose presentation time
                     * exactly matches the previous NAL's PT belongs to the
                     * same Access Unit. This catches non-spec-conformant
                     * producers (e.g., transcoders that share PT across
                     * slices but don't reset slice-header bits) and also
                     * handles same-AU non-VCL NALs (e.g., a parameter-set
                     * re-emission inside a multi-slice AU, or an SEI
                     * inserted between slices). */
                    const bool sameAuByPt = m_havePrevPT &&
                        timevalEqual(data->m_presentationTime, m_lastFramePT);

                    const bool sameAuAsPrev = isContSlice || sameAuByPt;

                    if (isHeaderNal)
                    {
                        if (m_lastFrameWasHeader || sameAuAsPrev)
                        {
                            /* Tightly-coupled header burst (SPS->PPS->...)
                             * inside one AU, or a non-VCL NAL emitted in
                             * the middle of a multi-slice AU. */
                            fDurationInMicroseconds = 100;
                        }
                        else
                        {
                            /* First header NAL of a new AU after a data
                             * NAL. Take the framerate slot, leaving a small
                             * head start so the IDR slice that follows is
                             * not delayed beyond the AU's natural deadline. */
                            fDurationInMicroseconds = adaptiveDuration > IDR_HEAD_START_USEC
                                ? adaptiveDuration - IDR_HEAD_START_USEC
                                : adaptiveDuration;
                        }
                        m_lastFrameWasHeader = true;
                    }
                    else
                    {
                        if (m_lastFrameWasHeader || sameAuAsPrev)
                        {
                            /* Either:
                             *   - the first slice of a new picture,
                             *     immediately after its parameter sets
                             *     (m_lastFrameWasHeader == true), or
                             *   - a continuation slice (slice 2..N) of the
                             *     SAME picture (sameAuAsPrev == true).
                             * In both cases the framerate slot has already
                             * been (or will be) consumed by the AU's
                             * representative NAL, so deliver this one
                             * back-to-back. */
                            fDurationInMicroseconds = 100;
                        }
                        else
                        {
                            /* First slice of a new picture, with no
                             * preceding headers. Presentation time has
                             * advanced from the previous AU; this NAL
                             * takes the framerate timing slot. */
                            fDurationInMicroseconds = adaptiveDuration;
                        }
                        m_lastFrameWasHeader = false;
                    }

                    /* Remember PT for the same-AU check on the next NAL. */
                    m_lastFramePT = data->m_presentationTime;
                    m_havePrevPT = true;
                }
                m_frameCount++;
                // Reset retry count on successful data retrieval
                m_noDataRetryCount = 0;
                memmove(fTo, data->m_content.data(), fFrameSize);

                // Apply pacing for data frames to control delivery rate
                // Only pace frames with significant duration
                if (fDurationInMicroseconds > 0)
                {
                    // Schedule afterGetting after the calculated duration
                    // This ensures frames are delivered at the correct rate
                    nextTask() = envir().taskScheduler().scheduleDelayedTask(fDurationInMicroseconds,
                        (TaskFunc*)FramedSource::afterGetting, this);
                    return;
                }
            }
        }
        else
        {
            // No data available - implement retry logic with two tiers
            fFrameSize = 0;
            m_noDataRetryCount++;

            if (m_noDataRetryCount <= NO_DATA_FAST_RETRIES)
            {
                // First tier: retry quickly (1ms) to catch data as soon as it arrives
                nextTask() = envir().taskScheduler().scheduleDelayedTask(NO_DATA_RETRY_INTERVAL_USEC,
                    (TaskFunc*)H264ByteStreamSource::retryGetFrame, this);
            }
            else
            {
                // Max retries reached - use short framerate-based delay and reset counter
                m_noDataRetryCount = 0;
                // Use a short delay and continue checking via retryGetFrame
                nextTask() = envir().taskScheduler().scheduleDelayedTask(DEFAULT_VIDEO_FRAME_PLAY_TIME_USEC,
                    (TaskFunc*)H264ByteStreamSource::retryGetFrame, this);
            }
            return;
        }

        // Deliver the frame to downstream client.
        FramedSource::afterGetting(this);
        return;
    } while (0);
}

void H264ByteStreamSource
  ::afterGettingFrame(void* clientData,
		      unsigned frameSize, unsigned numTruncatedBytes,
		      struct timeval presentationTime,
		      unsigned durationInMicroseconds)
{
    H264ByteStreamSource* source
      = (H264ByteStreamSource*)clientData;
    source->fFrameSize = frameSize;
    source->fNumTruncatedBytes = numTruncatedBytes;
    source->fPresentationTime = presentationTime;
    source->fDurationInMicroseconds = durationInMicroseconds;
    FramedSource::afterGetting(source);
}

void H264ByteStreamSource::onSourceClosure(void* clientData)
{
    H264ByteStreamSource* source
      = (H264ByteStreamSource*)clientData;
    source->onSourceClosure1();
}

void H264ByteStreamSource::onSourceClosure1()
{
    // This routine was called because the currently-read source was closed
    // (probably due to EOF).  Close this source down, and move to the
    // next one:
    closeSource();
}

unsigned H264ByteStreamSource::calculateAdaptiveDuration()
{
    // Calculate base duration from framerate
    unsigned baseDuration = (unsigned)(1000000.0 / m_frameRate);

    // Adaptive pacing based on buffer queue size to prevent latency drift
    size_t queueSize = m_mediaSource ? m_mediaSource->m_streamBuf.getQueueSize() : 0;

    if (queueSize >= 3)
    {
        // Multiple frames waiting - we're significantly behind, speed up to catch up
        return (unsigned)(baseDuration * 0.92);
    }
    else if (queueSize >= 1)
    {
        // Some frames waiting - slightly behind, minor speedup
        return (unsigned)(baseDuration * 0.97);
    }
    else
    {
        // Buffer empty - in sync, compensate for scheduling overhead
        return (unsigned)(baseDuration * 0.99);
    }
}

void H264ByteStreamSource::restartSource(H264ByteStreamSource* source)
{
    if (source)
    {
        source->restartForLoop();
    }
    return;
}

void H264ByteStreamSource::dataArrivalCheck(H264ByteStreamSource* source)
{
    if (source)
    {
        if (source->m_frameCount == 0)
        {
            LOG(error) << "No data received for this stream, closing it, m_sessionId:" << source->m_sessionId << endl;
            if (source->m_mediaSource)
            {
                source->m_mediaSource->m_is_error = true;
                if (source->getUrlParams().find("vodStream=true") != string::npos)
                {
                    source->m_mediaSource->sendSourceEvent(SourceEventEOF);
                }
            }
            source->m_loop = false;
            source->closeSource();
        }
    }
    return;
}

void H264ByteStreamSource::retryGetFrame(void* clientData)
{
    H264ByteStreamSource* source = (H264ByteStreamSource*)clientData;
    if (source) {
        // Try to get the next frame again
        source->doGetNextFrame();
    }
}

void H264ByteStreamSource::closeSource()
{
    LOG(info) << "Closing source..., m_sessionId:" << m_sessionId << endl;
    if (m_loop)
    {
        if (m_avLoopSync)
        {
            /* AV-pair loop boundary: don't seek/restart yet. Park here
             * and let the coordinator fire restartForLoop() once the
             * peer subsession has also reached EOS. live555 will stop
             * polling this source until restartForLoop() schedules the
             * retryGetFrame task. */
            LOG(info) << "H264ByteStreamSource parked at EOS for AV-sync, "
                         "m_sessionId:" << m_sessionId << endl;
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

void H264ByteStreamSource::restartForLoop()
{
    LOG(info) << "H264ByteStreamSource restartForLoop, m_sessionId:" << m_sessionId << endl;
    if (GET_CONFIG().nv_streamer_sync_playback == true)
    {
        m_seekOffset = 0;
        m_start = 0;
    }

    if (m_seekOffset > 0)
    {
        setStreamSeek(m_start, m_end);
    }
    else if (m_mediaSource)
    {
        m_mediaSource->seekToStart();
    }
    nextTask() = envir().taskScheduler().scheduleDelayedTask(0,
        (TaskFunc*)H264ByteStreamSource::retryGetFrame, this);
}

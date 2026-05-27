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
#include <memory>
#include <vector>
#include "event_loop.h"
#include "stream_buffer.h"
#include "gstdemux.h"
#include "VodOverlayManager.hh"

class VodOverlayManager;
class AvLoopSyncCoordinator;

using namespace std;

typedef enum
{
    InvalidSource = -1,
    SourceTypeFile,
    SourceTypeLive,
    SourceTypeNative
} eSourceType;

typedef enum
{
    BufferMsgInvalid = -1,
    BufferMsgPlay,
    BufferMsgPause,
    BufferMsgClear,
    BufferMsgEos,
    BufferMsgError
} eBufferMsg;

typedef enum
{
  SourceEventStart,
  SourceEventEOF,
  SourceEventError
} eFrameSourceEvent;

static unsigned const samplingFrequencyTable[16] = {
  96000, 88200, 64000, 48000,
  44100, 32000, 24000, 22050,
  16000, 12000, 11025, 8000,
  7350, 0, 0, 0
};

typedef void (*cb_frameSourceEvent_t)(eFrameSourceEvent sourceEvent, void *owner);

/*
 * AAC parameter bundle returned by NvMediaSource::getAacParams().
 *
 * Single source of truth for the three values that MUST agree across
 * the audio SDP (rtpmap clock-rate, rtpmap channel count, fmtp
 * `config=` AudioSpecificConfig) AND the ADTSByteStreamSource per-frame
 * duration computation. If the underlying probe yields an unsupported
 * sample rate or channel count, the function falls back to a single
 * coherent default (16 kHz / stereo / config="1410") so the SDP can't
 * advertise contradictory rtpmap and fmtp values (which causes VLC's
 * "decoded > 0, played = 0" symptom).
 */
struct AacParams
{
    int         sample_rate = 0;
    int         channels    = 0;
    std::string configStr;
};

class NvMediaSource : public IMediaDataConsumer
{
    public:
        NvMediaSource (const std::string& filename, eMediaType mediaType, eSourceType sourceType, string url_params, string session_id);
        ~NvMediaSource ();

        int create ();
        void destroy ();
        void play ();
        void pause ();
        void resume ();
        bool isError();
        string getFilename() { return m_filename; }
        eMediaType getMediaType() { return m_mediaType; }
        eSourceType getSourceType() { return m_sourceType; }
        int64_t getStartTime();
        int64_t getActualStartTime();
        double getFrameRate();
        string getVideoCodec();
        string getAudioCodec();
        int getSampleRate();
        int getChannels();
        string getUrlParams() { return m_url_params; }
        void setClock(GstClock* global_clock, GstClockTime base_time);
        void seek (int64_t seek_pos , uint64_t end_time, float rate);
        void seekToStart ();
        static void process_source_message(std::shared_ptr<EventLoopData> data, void* parent);
        void setBufferState(eBufferMsg buffer_msg);
        void registerCallback(cb_frameSourceEvent_t callback, void *owner);
        void sendSourceEvent(eFrameSourceEvent sourceEvent);
        string getCodecConfigId();

        /* Return the coherent (sample_rate, channels, configStr) triple
         * to use across rtpmap, fmtp and ADTSByteStreamSource. Prefer
         * this over the individual getSampleRate()/getChannels()/
         * getCodecConfigId() accessors at SDP-build time -- it applies
         * the fallback uniformly so the three values can never disagree. */
        AacParams getAacParams();

        /* AV-loop-sync coordinator: shared between the audio and
         * video subsessions of the same file, used to align EOS-driven
         * loop restarts. Null when not part of an AV pair. Set by
         * NvFileServerMediaSubsession::setAvLoopSync(); read by the
         * byte-stream sources at construction time. */
        void setAvLoopSync(const std::shared_ptr<AvLoopSyncCoordinator>& coord)
        {
            m_avLoopSync = coord;
        }
        std::shared_ptr<AvLoopSyncCoordinator> getAvLoopSync() const
        {
            return m_avLoopSync;
        }

        void insertSeiFrame(int64_t frameId, struct timeval pts, string codec);
        void insertMegaSimSeiFrame(int64_t frameId, struct timeval pts, string codec);
        std::vector<uint8_t> getFramesForSdp();
        void setSourceState(std::string source_state) { m_sourceState = source_state; }
        void resetActualStartTime();
        void playWithOverlay();
        void stopOverlayPipeline();
        void startOverlayPipeline_internal();
        void stopOverlayPipeline_internal();

        //IMediaDataConsumer virtual function
        virtual void onFrame(FrameParams& params);
        virtual eMediaType getConsumerMediaType() { return m_mediaType; }
        virtual void setConsumerMediaType(eMediaType media_type) { m_mediaType = media_type; }

    private:
        std::string             m_filename;
        eMediaType              m_mediaType;
        eSourceType             m_sourceType;
        std::string             m_sourceState;
        shared_ptr<GstDeMux>    m_demux;
        shared_ptr<VodOverlayManager> m_vodOverlayManager = nullptr;
        std::map<void *, cb_frameSourceEvent_t> m_callback;
        bool                    m_includeFrameId = false;
        int64_t                 m_frameId;
        string                  m_uuid;
        std::queue<vector<uint8_t>>    m_videoHeaderFrames;
        string                  m_url_params;
        string                  m_sessionId;
        uint64_t                m_simulationBaseTime;
        std::queue<std::shared_ptr<DiscreteFrame>> m_spsPpsContent;
        std::atomic<bool>       m_isFirstFrame = false;
        std::shared_ptr<AvLoopSyncCoordinator> m_avLoopSync;
    public:
        EventLoop               m_eventLoop;
        StreamBuffer            m_streamBuf;
        bool                    m_is_error;
};
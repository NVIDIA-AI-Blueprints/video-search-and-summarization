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
#include "logger.h"
#include "stream_monitor.h"
#include "pc/local_audio_source.h"
#include "api/audio_codecs/builtin_audio_decoder_factory.h"
#include "gstnvdecoder.h"

#include <string.h>
#include <vector>
#include <mutex>
#include <queue>
#include <condition_variable>
#include <atomic>
#include <glib.h>
#include <gst/gst.h>
#include <condition_variable>

typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;

struct AudioData
{
    int                     m_freq;
    int                     m_channel;
    int                     m_bitsPerSample;
    std::string             m_audioCodec;
    std::atomic<bool>       m_isAudioDataUpdated {false};

    AudioData() :
      m_freq(8000)
    , m_channel(1)
    , m_bitsPerSample(16)
    , m_audioCodec("")
    , m_isAudioDataUpdated(false) {}
};

class GstNvAudioDecoder : public IMediaDataConsumer, public GstNvDecoder
{
    public:
        GstNvAudioDecoder (const std::string& uri, const std::map<std::string, std::string, std::less<>> &opts) :
        IMediaDataConsumer("GstNvAudioDecoder_" + uri),
        m_uri(uri),
        m_pipeline (nullptr),
        m_source (nullptr),
        m_decoder (nullptr),
        m_sink (nullptr)
        {
            m_sinks.clear();
            if (opts.at("capture_type") == "udp")
            {
                m_udpSource = true;
            }
            if (opts.find("sample_rate") != opts.end())
            {
                std::string sample_rate_string = opts.at("sample_rate");
                m_audioData.m_freq = stringToInt(sample_rate_string, 0);
            }
            if (opts.find("bits_per_sample") != opts.end())
            {
                std::string bits_per_sample_string = opts.at("bits_per_sample");
                m_audioData.m_bitsPerSample = stringToInt(bits_per_sample_string, 0);
            }
            if (opts.find("audio_codec") != opts.end())
            {
                m_audioData.m_audioCodec = opts.at("audio_codec");
            }
            setConsumerMediaType(MediaTypeAudio);
        }
        ~GstNvAudioDecoder ()
        {
            LOG(info) << "Audio Decoder instance deleted  for uri = " << m_uri << endl;
        }

        /* GstNvDecoder Interfaces */
        int create(bool blocking = false);
        void destroy(bool expect_result = false);
        bool pause();
        std::string getstate();
        bool isPlaying();
        bool getError() { return m_error; }

        void setError() { m_error = true; };
        void appendWebrtcSink(webrtc::AudioTrackSinkInterface* broadcaster);
        void removeWebrtcSink(webrtc::AudioTrackSinkInterface* broadcaster);
        void updateAudioDataIfRequired();
        std::list<webrtc::AudioTrackSinkInterface *> getWebrtcBroacasterList() { return m_sinks; }

        virtual void onFrame(FrameParams& params);
        
        vector<uint16_t>                             m_audioBuffer;
        std::mutex                                   m_sinkLock;
        std::list<webrtc::AudioTrackSinkInterface *> m_sinks;
        AudioData                                    m_audioData;
        std::atomic<int>                             m_decOutFrames{0};

    private:
        std::string             m_uri;
        GstElement*             m_pipeline = nullptr;
        GstElement*             m_source = nullptr;
        GstElement*             m_decoder = nullptr;
        GstElement*             m_sink = nullptr;
        std::mutex              m_pipelineLock;
        std::atomic<bool>       m_error{false};
        std::atomic<bool>       m_udpSource {false};
};
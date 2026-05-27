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
#include "logger.h"
#include "gstnvaudiodecoder.h"
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <atomic>
#include "libasync++/async++.h"
#include "gstnvaudioudpclient.h"
#include "udpclient.h"
#include "udpclientpool.h"

class AudioDataConsumer : public IMediaDataConsumer
{
    public:
        AudioDataConsumer () 
        : IMediaDataConsumer("AudioDataConsumer")
        , m_freq (8000)
        , m_channel (1)
        , m_bitsPerSample(16)
        { 
            LOG(info) << "AudioDataConsumer" << endl;
            m_audioBuffer.clear();
        }
        void setSinks (webrtc::AudioTrackSinkInterface *sink)
        {
            std::lock_guard<std::mutex> lock(m_audioSinkLock);
            m_sinks.push_back(sink);
            LOG(info) << "Audio Broadcasters size = " << m_sinks.size() << endl;
        }
        void removeSinks (webrtc::AudioTrackSinkInterface *sink)
        {
            std::lock_guard<std::mutex> lock(m_audioSinkLock);
            m_sinks.remove(sink);
            LOG(info) << "Audio Broadcasters size = " << m_sinks.size() << endl;
        }
        size_t getSinksSize ()
        {
            std::lock_guard<std::mutex> lock(m_audioSinkLock);
            size_t sinks_size = m_sinks.size();
            LOG(info) << "Audio Broadcasters size = " << sinks_size << endl;
            return sinks_size;
        }
        void setAudioInfo (int freq, int bitsPerSample)
        {
            m_freq = freq;
            m_bitsPerSample = bitsPerSample;
        }
        void onFrame(FrameParams& params)
        {
            uint16_t segment_length       = m_freq / 100;
            uint16_t total_segment_length = m_channel * segment_length;

            unsigned vec_size = params.m_size / 2 ;
            m_audioBuffer.resize(m_audioBuffer.size() + vec_size);

            if (params.m_buffer == nullptr || params.m_size <= 0)
            {
                LOG(error) << "Audio Decoder: received 0 sized buffer";
                return;
            }
            /* Copy the map.data into vector while keeping existing data untouched */
            std::memcpy(&m_audioBuffer[m_audioBuffer.size() - vec_size], params.m_buffer, params.m_size);

            while (m_audioBuffer.size() > total_segment_length)
            {
                std::lock_guard<std::mutex> lock(m_audioSinkLock);
                for (auto *sink : m_sinks)
                {
                    sink->OnData(m_audioBuffer.data(), m_bitsPerSample, m_freq, m_channel, segment_length);
                }
                /* Erase the processed data from vector
                ** from begin to begin + total_segment_length
                */
                m_audioBuffer.erase(m_audioBuffer.begin(), m_audioBuffer.begin() + total_segment_length);
            }
        }
    private:
        std::list<webrtc::AudioTrackSinkInterface *>   m_sinks;
        vector<uint16_t>                               m_audioBuffer;
        std::mutex                                     m_audioSinkLock;
        int                                            m_freq;
        int                                            m_channel;
        int                                            m_bitsPerSample;
};


class NvGstUDPAudioSource : public webrtc::Notifier<webrtc::AudioSourceInterface>
{
public:
    SourceState state() const override { return kLive; }

    bool remote() const override { return true; }

    virtual void AddSink(webrtc::AudioTrackSinkInterface *sink) override
    {
        LOG(info) << "NvGstUDPAudioSource::AddSink " << endl;
        m_audioDataConsumer->setSinks (sink);
    }

    virtual void RemoveSink(webrtc::AudioTrackSinkInterface *sink) override
    {
        LOG(info) << "NvGstUDPAudioSource::RemoveSink " << endl;
        m_audioDataConsumer->removeSinks(sink);
    }

    NvGstUDPAudioSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts)
    : m_udpAudioClient(nullptr)
    , m_uri(uri)
    , m_freq (8000)
    , m_bitsPerSample(16)
    , m_mediaType("audio")
    , m_peerid ("")
    , m_streamid("")
    {
        if ( opts.find("streamId") != opts.end() )
        {
            // m_streamid = opts.at("sensorId") + string("_") + m_mediaType;
            m_streamid = opts.at("streamId");
        }
        if ( opts.find("peerid") != opts.end() )
        {
            m_peerid = opts.at("peerid");
        }
        if (opts.find("sample_rate") != opts.end())
        {
            std::string sample_rate_string = opts.at("sample_rate");
            m_freq = stringToInt(sample_rate_string, 0);
        }
        if (opts.find("bits_per_sample") != opts.end())
        {
            std::string bits_per_sample_string = opts.at("bits_per_sample");
            m_bitsPerSample = stringToInt(bits_per_sample_string, 0);
        }

        LOG(info) << "NvGstUDPAudioSource peerid: "<< m_peerid << " streamid: "<< m_streamid << " media: " << m_mediaType << endl;
        if (UdpClientPool::getInstance()->isClientExist(m_streamid, m_mediaType) == false)
        {
            // Create new udp client & decoder pipeline.
            setupClient(opts);
            LOG(info) << "Created udpClient:" << m_udpAudioClient << ", Consumer:" << m_audioDataConsumer.get() << endl;
        }
        else
        {
            // Reuse the udp client & decoder pipeline.
            m_udpAudioClient = UdpClientPool::getInstance()->getClient(m_streamid, m_mediaType);
            if (m_udpAudioClient)
            {
                LOG(info) << "Creating audio pipeline " << endl;
                m_udpAudioClient->create_audio();
                m_audioDataConsumer = std::static_pointer_cast<AudioDataConsumer>(m_udpAudioClient->getConsumer(UdpClient::UDP_AUDIO_TYPE));
                if (!m_audioDataConsumer)
                {
                    m_audioDataConsumer.reset(new AudioDataConsumer());
                    m_audioDataConsumer->setAudioInfo (m_freq, m_bitsPerSample);
                    m_udpAudioClient->setConsumer(m_audioDataConsumer, UdpClient::UDP_AUDIO_TYPE);
                }
                else
                {
                    LOG(warning) << "Audio Consumer already exists" << endl;
                }
                LOG(info) << "Reusing udpClient:" << m_udpAudioClient << ", Consumer:" << m_audioDataConsumer.get() << endl;
            }
            else
            {
                LOG(error) << "UdpAudioClient not found for media:" << m_mediaType << endl;
            }
        }
    }

    virtual ~NvGstUDPAudioSource()
    {
        try {
            LOG(info) << __METHOD_NAME__ << endl;
            if (m_audioDataConsumer)
            {
                size_t size = m_audioDataConsumer->getSinksSize();
                LOG(warning) << __METHOD_NAME__ << " sink size = " << size << endl;
            }
            else
            {
                LOG(warning) << __METHOD_NAME__ << " m_audioDataConsumer is null, skipping sink size check" << endl;
            }
        } catch (const std::exception& e) {
            try { LOG(error) << "Exception in ~NvGstUDPAudioSource: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
        } catch (...) {
            try { LOG(error) << "Unknown exception in ~NvGstUDPAudioSource" << endl; } catch (...) { (void)std::current_exception(); }
        }
    }

    void setupClient (const std::map<std::string, std::string, std::less<>> &opts)
    {
        UdpStream stream;
        if ( opts.find("audio_port") != opts.end() )
        {
            stream.m_audioPort = stringToInt(opts.at("audio_port"));
            stream.m_type = UdpClient::UDP_AUDIO_TYPE;
        }
  
        m_udpAudioClient = UdpClientPool::getInstance()->addClient(m_streamid, stream);
        if (m_udpAudioClient)
        {
            m_udpAudioClient->create_audio();
            m_udpAudioClient->setConsumer(m_audioDataConsumer, UdpClient::UDP_AUDIO_TYPE);
            m_udpAudioClient->start();
        }
    }
private:
    shared_ptr<UdpClient>                          m_udpAudioClient;
    shared_ptr<AudioDataConsumer>                  m_audioDataConsumer;
    std::string                                    m_uri;
    int                                            m_freq;
    int                                            m_bitsPerSample;
    std::string                                    m_mediaType;
    std::string                                    m_peerid;
    std::string                                    m_streamid;
};

/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include<iostream>
#include<map>
#include <tuple>
#include<vector>
#include<thread>
#include <condition_variable>
#include<unistd.h>
#include "device_manager.h"
#include "config.h"
#include <curl/curl.h>
#include <algorithm>

#include "media_consumer.h"
#include "media_producer.h"
#include "gstnvaudioencoder.h"
#include "gstnvvideoencoder.h"
#include "rtspserver.h"
#include "modules_apis.h"
#include "storage_management.h"

using namespace std;
using namespace nv_vms;

inline constexpr const char* DEFAULT_WEBRTC_IN_VIDEO_CODEC = "h264";
inline constexpr int DEFAULT_WEBRTC_IN_FRAMERATE = 30;
inline constexpr const char* DEFAULT_WEBRTC_IN_AUDIO_CODEC = "AAC";
inline constexpr int DEFAULT_WEBRTC_IN_SAMPLE_RATE = 16000;
inline constexpr int DEFAULT_WEBRTC_IN_CHANNELS = 2;

class WebrtcStream
{
    public:
        WebrtcStream(std::string deviceId, std::string deviceName, std::string peerId)
        : m_deviceId(deviceId)
        , m_deviceName(deviceName)
        , m_peerId(peerId)
        {
            updateStreamMetadata();
            m_gstencoder.reset(new GstNvAudioEncoder());
            if (m_gstencoder->create(peerId) == -1)
            {
                LOG(error) << "Error in Creating Audio Encoder Pipeline" << endl;
            }
            m_gstvideoencoder.reset(new GstNvVideoEncoder(deviceName, peerId));
            if (GET_CONFIG().webrtc_in_passthrough)
            {
                m_gstvideoencoder->setPassThrough(true);
            }
            else if (m_gstvideoencoder->create() == -1)
            {
                LOG(error) << "Error in Creating Video Encoder Pipeline" << endl;
            }
        }

        ~WebrtcStream() 
        {
            try {
                LOG(info) << "~WebrtcStream, m_deviceId:" << m_deviceId << endl;
                m_gstencoder->destroy ();
                m_gstvideoencoder->destroy ();
                vst_rtsp::removeServerMediaSession(m_deviceId);
            } catch (const std::exception& e) {
                try { LOG(error) << "Exception in ~WebrtcStream: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
            } catch (...) {
                try { LOG(error) << "Unknown exception in ~WebrtcStream" << endl; } catch (...) { (void)std::current_exception(); }
            }
        }

        void updateStreamMetadata()
        {
            Json::Value video_metadata = getVideoMetadata(m_deviceId);
            m_videoCodec = video_metadata.get("Codec", DEFAULT_WEBRTC_IN_VIDEO_CODEC).asString();
            m_frameRate = stringToDouble(video_metadata.get("Framerate", "30").asString());

            Json::Value audio_metadata = getAudioMetadata(m_deviceId);
            m_audioCodec = audio_metadata.get("Codec", DEFAULT_WEBRTC_IN_AUDIO_CODEC).asString();
            m_sampleRate = audio_metadata.get("SampleRate", DEFAULT_WEBRTC_IN_SAMPLE_RATE).asInt();
            m_channels = audio_metadata.get("Channels", DEFAULT_WEBRTC_IN_CHANNELS).asInt();
        }

        int addFrame (const string& media, void *buffer, unsigned int size, int sample_rate = 0,
                        size_t num_channels = 0, int codec_type = 1, int64_t latencyStartTime = 0)
        {
            LOG(verbose) << "media = " << media << " Peer ID = " << endl;
            string codec = "";
            codec = (media == "video" && codec_type == 1) ? "h264" : ((media == "video") ? "h265" : "pcm");

            std::lock_guard<std::mutex> recordLock(m_webRTCConsumerLock);
            if (media == "audio" && m_consumersList.size())
            {
                m_gstencoder->onFrame(media, codec, (const unsigned char*)buffer, size, sample_rate, num_channels);
            }
            if (media == "video")
            {
                struct timeval currTime;
                gettimeofday(&currTime, nullptr);
                FrameParams frame_params;
                frame_params.m_media            = "video";
                frame_params.m_buffer = (unsigned char*)buffer;
                frame_params.m_size = size;
                if (latencyStartTime == 0)
                {
                    frame_params.m_latencyStartTime = currTime;
                }
                else
                {
                    frame_params.m_latencyStartTime.tv_sec  = latencyStartTime / 1000000;
                    frame_params.m_latencyStartTime.tv_usec = latencyStartTime % 1000000;
                }

                if (m_consumersList.size() || m_gstvideoencoder->checkIfSpsPpsHeadersAvailable() == false ||
                    GET_CONFIG().enable_frameid_in_webrtc_stream == true)
                {
                    return m_gstvideoencoder->onFrame(frame_params, codec, sample_rate);
                }
            }
            return 0;
        }

        void addConsumer (shared_ptr<IMediaDataConsumer> consumer)
        {
            std::lock_guard<std::mutex> lock(m_webRTCConsumerLock);
            LOG(info) << "Adding consumer for " << m_deviceId << endl;
            if (std::find(m_consumersList.begin(), m_consumersList.end(), consumer) == m_consumersList.end())
            {
                m_consumersList.push_back(consumer);
            }

            if (consumer->getConsumerMediaType() == MediaTypeAudio)
            {
                m_gstencoder->addConsumer(consumer);
            }
            else if(consumer->getConsumerMediaType() == MediaTypeVideo)
            {
                m_gstvideoencoder->addConsumer(consumer);
            }
            else if(consumer->getConsumerMediaType() == MediaTypeAudioVideo)
            {
                m_gstvideoencoder->addConsumer(consumer);
                m_gstencoder->addConsumer(consumer);
            }
        }

        void removeConsumer (shared_ptr<IMediaDataConsumer> consumer)
        {
            std::lock_guard<std::mutex> lock(m_webRTCConsumerLock);
            LOG(info) << "Removing consumer for " << m_deviceId << endl;
            m_consumersList.erase(std::remove(m_consumersList.begin(), m_consumersList.end(), consumer), m_consumersList.end());
            if (consumer->getConsumerMediaType() == MediaTypeAudio)
            {
                m_gstencoder->removeConsumer(consumer);
            }
            else if(consumer->getConsumerMediaType() == MediaTypeVideo)
            {
                m_gstvideoencoder->removeConsumer(consumer);
            }
            else if(consumer->getConsumerMediaType() == MediaTypeAudioVideo)
            {
                m_gstvideoencoder->removeConsumer(consumer);
                m_gstencoder->removeConsumer(consumer);
            }
        }

        bool isVideoTrackEnabled() { return m_isVideoTrackEnabled; }
        bool isAudioTrackEnabled() { return m_isAudioTrackEnabled; }
        void setVideoTrackEnabled(bool enable) { m_isVideoTrackEnabled = enable; }
        void setAudioTrackEnabled(bool enable) { m_isAudioTrackEnabled = enable; }

        std::map<string, media_info, std::less<>> getAudioInfo()
        {
            return m_gstencoder->getAudioInfo();
        }

        std::queue<vector<uint8_t>> getVideoHeaders()
        {
            return m_gstvideoencoder->getSpsPpsHeaders();
        }

        string getVideoCodec() { return m_videoCodec; }
        string getAudioCodec() { return m_audioCodec; }
        double getFramerate() { return m_frameRate; }
        int getSampleRate() { return m_sampleRate; }
        int getChannels() { return m_channels; }

    private:
        string                                      m_deviceId;
        string                                      m_deviceName;
        string                                      m_peerId;
        std::mutex                                  m_webRTCConsumerLock;
        std::vector<shared_ptr<IMediaDataConsumer>> m_consumersList;
        shared_ptr<GstNvAudioEncoder>               m_gstencoder = nullptr;
        shared_ptr<GstNvVideoEncoder>               m_gstvideoencoder = nullptr;
        std::atomic<bool>                           m_isVideoTrackEnabled {false};
        std::atomic<bool>                           m_isAudioTrackEnabled {false};
        string                                      m_videoCodec = DEFAULT_WEBRTC_IN_VIDEO_CODEC;
        string                                      m_audioCodec = DEFAULT_WEBRTC_IN_AUDIO_CODEC;
        double                                      m_frameRate = DEFAULT_WEBRTC_IN_FRAMERATE;
        int                                         m_sampleRate = DEFAULT_WEBRTC_IN_SAMPLE_RATE;
        int                                         m_channels = DEFAULT_WEBRTC_IN_CHANNELS;
};

class WebrtcStreamProducer : public IMediaDataProducer
{
    private:
        WebrtcStreamProducer () {}

    public:
        ~WebrtcStreamProducer ()  {}

        static WebrtcStreamProducer* getInstance()
        {
            static WebrtcStreamProducer instance;
            return &instance;
        }

        void addStreamProducer (string deviceId, std::shared_ptr<WebrtcStream> ptr)
        {
            std::lock_guard<std::mutex> lock(m_webrtcConsumerLock);
            m_webrtcStreams[deviceId] = ptr;
        }

        void removeStreamProducer (string deviceId)
        {
            std::lock_guard<std::mutex> lock(m_webrtcConsumerLock);
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                m_webrtcStreams.erase(deviceId);
            }
        }

        void registerDataCallback(std::string deviceId, shared_ptr<IMediaDataConsumer> consumer)
        {
            LOG(info) << "add consumer for deviceId: " << deviceId << endl;
            if (consumer == nullptr)
            {
                LOG(error) << "Consumer is null" << endl;
                return;
            }

            {
                std::lock_guard<std::mutex> lock(m_webrtcConsumerLock);
                std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
                if (it != m_webrtcStreams.end())
                {
                    it->second->addConsumer(consumer);
                }
            }
        }

        void deregisterDataCallback(shared_ptr<IMediaDataConsumer> consumer, std::string& deviceId)
        {
            LOG(info) << "removing consumer for deviceId: " << deviceId << endl;
            if (consumer == nullptr)
            {
                LOG(error) << "Consumer is null" << endl;
                return;
            }
            {
                std::lock_guard<std::mutex> lock(m_webrtcConsumerLock);
                std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
                if (it != m_webrtcStreams.end())
                {
                    it->second->removeConsumer(consumer);
                }
            }
        }

        bool isVideoTrackEnabled(const string& deviceId)
        {
            bool video_track_enabled = false;
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                video_track_enabled = it->second->isVideoTrackEnabled();
            }
            return video_track_enabled;
        }
        bool isAudioTrackEnabled(const string& deviceId)
        {
            bool audio_track_enabled = false;
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                audio_track_enabled = it->second->isAudioTrackEnabled();
            }
            return audio_track_enabled;
        }

        void setVideoTrackEnabled(const string& deviceId, bool enable)
        {
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                it->second->setVideoTrackEnabled(enable);
            }
            return;
        }
        void setAudioTrackEnabled(const string& deviceId, bool enable)
        {
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                it->second->setAudioTrackEnabled(enable);
            }
            return;
        }

        std::map<string, media_info, std::less<>> getAudioInfo (std::string& deviceId)
        {
            std::map<string, media_info, std::less<>> supported_map;
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                if (it->second->isAudioTrackEnabled())
                {
                    supported_map = it->second->getAudioInfo();
                }
            }
            return supported_map;
        }

        std::queue<vector<uint8_t>> getVideoHeaders (std::string& deviceId)
        {
            std::queue<vector<uint8_t>> video_headers;
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                video_headers = it->second->getVideoHeaders();
            }
            return video_headers;
        }

        string getVideoCodec(std::string& deviceId)
        {
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                return it->second->getVideoCodec();
            }
            return DEFAULT_WEBRTC_IN_VIDEO_CODEC;
        }

        string getAudioCodec(std::string& deviceId)
        {
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                return it->second->getAudioCodec();
            }
            return DEFAULT_WEBRTC_IN_AUDIO_CODEC;
        }

        double getFramerate(std::string& deviceId)
        {
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                return it->second->getFramerate();
            }
            return DEFAULT_WEBRTC_IN_FRAMERATE;
        }

        int getSampleRate(std::string& deviceId)
        {
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                return it->second->getSampleRate();
            }
            return DEFAULT_WEBRTC_IN_SAMPLE_RATE;
        }

        int getChannels(std::string& deviceId)
        {
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                return it->second->getChannels();
            }
            return DEFAULT_WEBRTC_IN_CHANNELS;
        }

        void updateStreamProperties(std::string& deviceId)
        {
            std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>::iterator it = m_webrtcStreams.find(deviceId);
            if (it != m_webrtcStreams.end())
            {
                return it->second->updateStreamMetadata();
            }
        }

        // IMediaDataProducer interface implementation
        bool start() override
        {
            LOG(info) << "WebrtcStreamProducer::start() - Starting WebrtcStreamProducer" << endl;
            // WebrtcStreamProducer is a singleton and is always running
            return true;
        }

        void stop() override
        {
            LOG(info) << "WebrtcStreamProducer::stop() - Stopping WebrtcStreamProducer" << endl;
            // Note: WebrtcStreamProducer is a singleton and should not be stopped
            // This method is provided for interface compliance
            LOG(warning) << "WebrtcStreamProducer::stop() called - WebrtcStreamProducer is a singleton and should not be stopped" << endl;
        }

        bool isRunning() const override
        {
            // WebrtcStreamProducer singleton is always running
            return true;
        }

        eMediaType getProducerMediaType() const override
        {
            // WebrtcStreamProducer handles both video and audio
            return MediaTypeAudioVideo;
        }

        std::string getSourceIdentifier() const override
        {
            // Return a generic identifier for the WebRTC producer
            return "webrtc_producer";
        }

        size_t getConsumerCount() const override
        {
            std::lock_guard<std::mutex> lock(m_webrtcConsumerLock);
            size_t totalConsumers = 0;
            for (const auto& pair : m_webrtcStreams)
            {
                // Note: WebrtcStream doesn't expose consumer count directly
                // This is an approximation - count each stream as having at least one consumer
                (void)pair; // Suppress unused variable warning
                totalConsumers += 1; // At least one consumer per stream
            }
            return totalConsumers;
        }

        bool hasConsumers() const override
        {
            return getConsumerCount() > 0;
        }

        void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const std::string& identifier = "") override
        {
            // Call the existing registerDataCallback function
            registerDataCallback(identifier, consumer);
        }

        void unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer, const std::string& identifier = "", bool doNotRemoveClient = false) override
        {
            // Call the existing deregisterDataCallback function
            std::string deviceId = identifier;
            deregisterDataCallback(consumer, deviceId);
        }

        void distributeToConsumers(std::shared_ptr<RawFrameParams> frameData) override
        {
            // WebrtcStreamProducer doesn't directly distribute frames
            // This is handled by individual WebrtcStream instances
            LOG(verbose) << "WebrtcStreamProducer::distributeToConsumers - RawFrameParams distribution not implemented" << endl;
        }

        void distributeToConsumers(FrameParams& frameParams) override
        {
            // WebrtcStreamProducer doesn't directly distribute frames
            // This is handled by individual WebrtcStream instances
            LOG(verbose) << "WebrtcStreamProducer::distributeToConsumers - FrameParams distribution not implemented" << endl;
        }

    private:
        mutable std::mutex                                    m_webrtcConsumerLock;
        std::map<std::string, std::shared_ptr<WebrtcStream>, std::less<>>  m_webrtcStreams;
};
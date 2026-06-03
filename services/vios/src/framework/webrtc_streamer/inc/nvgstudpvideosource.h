/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "videosinkinfo.h"
#include "gstnvvideoudpclient.h"
#include <mutex>
#include <condition_variable>
#include <list>
#include <chrono>
#include <atomic>
#include "libasync++/async++.h"
#include "decoderpool.h"
#include "garbagecollector.h"
#include "udpclient.h"
#include "udpclientpool.h"
#include "media_consumer.h"
#include "nvbufwrapper.h"
#include "gstnvvstmeta.h"
#include "webrtc_sink_consumer.h"
#include "video_resolution.h"

static std::array<FrameSize, 7> g_resolutions = { FrameSize(WIDTH_2160p, HEIGHT_2160p),
                                                  FrameSize(WIDTH_1080p, HEIGHT_1080p),
                                                  FrameSize(WIDTH_720p, HEIGHT_720p),
                                                  FrameSize(WIDTH_480p, HEIGHT_480p),
                                                  FrameSize(WIDTH_360p, HEIGHT_360p),
                                                  FrameSize(WIDTH_240p, HEIGHT_240p),
                                                  FrameSize(WIDTH_144p, HEIGHT_144p)
                                                 };

class VideoDataConsumer : public IMediaDataConsumer
{
    public:
        VideoDataConsumer () : IMediaDataConsumer("VideoDataConsumer")
        {
        }
        ~VideoDataConsumer () {}
        FrameSize qualityToFrameSize(const string& quality)
        {
            FrameSize size;
            if (quality == "high")
            {
                size.m_width = WIDTH_1080p;
                size.m_height = HEIGHT_1080p;
            }
            else if (quality == "medium")
            {
                size.m_width = WIDTH_720p;
                size.m_height = HEIGHT_720p;
            }
            else if (quality == "low")
            {
                size.m_width = WIDTH_480p;
                size.m_height = HEIGHT_480p;
            }
            else
            {
                size.m_width = m_sourceWidth;
                size.m_height = m_sourceHeight;
            }
            return size;
        }

        size_t getBroadcasterSize ()
        {
            std::lock_guard<std::mutex> lock(m_videoSinkLock);
            LOG(info) << "Video Broadcaster size = " << m_videoSinkList.size() << endl;
            return m_videoSinkList.size();
        }

        void onFrame(FrameParams& params)
        {
            std::lock_guard<std::mutex> lock(m_videoSinkLock);
            m_sourceWidth  = params.m_width;
            m_sourceHeight = params.m_height;
            std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it;
            for( it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
            {
                shared_ptr<VideoSinkInfo> sink = it->second;
                if (!sink->m_isSinkReady)
                {
                    continue;
                }
                /* Create a YUV Buffer */
                uint32_t target_width = 0;
                uint32_t target_height = 0;
                /* if DRC is disabled use the source resolution or if frameSize is not yet set */
                if (GET_CONFIG().enable_drc == false || (sink->m_frameSize.m_width == 0 && sink->m_frameSize.m_height == 0))
                {
                    target_width  = m_sourceWidth;
                    target_height = m_sourceHeight;
                }
                else
                {
                    target_width = sink->m_frameSize.m_width;
                    target_height = sink->m_frameSize.m_height;
                }
                if(target_width == 0 || target_height == 0)
                {
                    LOG(error) << "target_width/target_height of frame is zero" << endl;
                    continue;
                }

                if (target_width == m_sourceWidth && target_height == m_sourceHeight)
                {
                    target_width = m_sourceWidth;
                    target_height = m_sourceHeight;
                }

                /* HW Path, i.e. NV Encoder */
                if (NvHwDetection::getInstance()->m_useNvV4l2Enc)
                {
                    std::shared_ptr<RawFrameParams> enc_frame_data = std::make_shared<RawFrameParams>();
                    NvEncoderVideoConsumer* encoder = (NvEncoderVideoConsumer*)sink->m_consumer.get();
                    std::shared_ptr<GstNvVstMeta> meta = std::make_shared<GstNvVstMeta>();
                    if (!encoder)
                    {
                        LOG(error) << "Encoder is NULL" << endl;
                        continue;
                    }
                    enc_frame_data->m_sample       = (GstSample*)params.m_extdata;
                    enc_frame_data->m_sourceWidth  = m_sourceWidth;
                    enc_frame_data->m_sourceHeight = m_sourceHeight;
                    enc_frame_data->m_targetWidth  = target_width;
                    enc_frame_data->m_targetHeight = target_height;
                    if (GET_CONFIG().enable_latency_logging)
                    {
                        enc_frame_data->meta           = meta.get();
                        ((GstNvVstMeta*)enc_frame_data->meta)->ts       = (params.m_latencyStartTime.tv_sec * 1000000) + params.m_latencyStartTime.tv_usec;
                    }
                    sink->m_consumer->onFrame (enc_frame_data);
                }
                /* SW Path, i.e. Inbuilt webRTC Encoder */
                else
                {
                    rtc::scoped_refptr<webrtc::I420Buffer> yuv_buffer    (new rtc::RefCountedObject<webrtc::I420Buffer>(target_width,  target_height ));
                    rtc::scoped_refptr<webrtc::I420Buffer> decoded_buffer(new rtc::RefCountedObject<webrtc::I420Buffer>(m_sourceWidth, m_sourceHeight));
                    /* Create Y, U, V data pointers */
                    uint8_t *buffer_y = params.m_buffer;
                    uint8_t *buffer_u = buffer_y + m_sourceWidth * m_sourceHeight;
                    uint8_t *buffer_v = buffer_u + (m_sourceWidth/2) * (m_sourceHeight/2);
                    /* Copy Y, U, V data in YUV Buffer */
                    memcpy((char *)decoded_buffer->MutableDataY(), buffer_y, m_sourceWidth * m_sourceHeight);
                    memcpy((char *)decoded_buffer->MutableDataU(), buffer_u, (m_sourceWidth * m_sourceHeight) / 4);
                    memcpy((char *)decoded_buffer->MutableDataV(), buffer_v, (m_sourceWidth * m_sourceHeight) / 4);
                    yuv_buffer->ScaleFrom(*decoded_buffer->GetI420());

                    // here send yuv_buffer to webrtc_sink_consumer
                    std::shared_ptr<RawFrameParams> frame_data = std::make_shared<RawFrameParams>();
                    frame_data->m_isYuvBuffer = true;
                    frame_data->m_buffer      = static_cast<unsigned char*>(static_cast<void*>(yuv_buffer.get()));
                    if (sink->m_consumer)
                    {
                        sink->m_consumer->onFrame(frame_data);
                    }
                }
            }
        }

        bool isDRCAllowed ()
        {
            bool ret = true;
            std::time_t now = std::time(nullptr);
            if (m_lastDRCTime != 0) // This is initial condition
            {
                now = std::time(nullptr);
                // ignore drc request less than interval set in config
                ret = (now - m_lastDRCTime >= GET_CONFIG().webrtc_out_min_drc_interval);
            }
            m_lastDRCTime = now;
            return ret;
        }

        void handleDRC(const string& peerid, int targetPixels, int targetFPS)
        {
            if (GET_CONFIG().enable_drc == false || !isDRCAllowed ())
            {
                LOG(warning) << "Either DRC disabled or DRC Request too early, in less than " << GET_CONFIG().webrtc_out_min_drc_interval << " secs" << endl;
                return;
            }
            std::lock_guard<std::mutex> lock(m_videoSinkLock);
            std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
            FrameSize prev_size = g_resolutions[m_resolutionIndex];
            if(it != m_videoSinkList.end())
            {
                shared_ptr<VideoSinkInfo> sink = it->second;
                if(sink->m_quality == "auto")
                {
                    if (m_sourceWidth != 0 && m_sourceHeight != 0)
                    {
                        size_t res_index = 0;
                        FrameSize source_size(m_sourceWidth, m_sourceHeight);
                        while(true)
                        {
                            res_index = std::min(res_index, g_resolutions.size() - 1 );
                            FrameSize size = g_resolutions[res_index];
                            if((size.getPixels() <= targetPixels) || (res_index == (g_resolutions.size() - 1)))
                            {
                                sink->m_frameSize = size.minimum(source_size);
                                m_resolutionIndex = res_index;
                                break;
                            }
                            ++ res_index;
                        }
                    }
                }
                LOG(warning) << "Changing resolution for peerid : " << peerid << " from : " << prev_size.m_width << "x" << prev_size.m_height << " ==> " << sink->m_frameSize.m_width << "x" << sink->m_frameSize.m_height << endl;
            }
        }
        void setConsumer(const std::string& peerid, rtc::VideoBroadcaster* broadcaster,
                                                    const std::string& quality, const std::string& framerate)
        {
            std::lock_guard<std::mutex> lock(m_videoSinkLock);
            std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
            if(it == m_videoSinkList.end())
            {
                shared_ptr<VideoSinkInfo> sink (new VideoSinkInfo);
                sink->m_quality = quality;
                sink->m_frameSize = qualityToFrameSize(quality);
                sink->m_decoderStats.clear();
                /* This is intentionally setting 0.0 to avoid checkEarlyFramesAndSynchronize */
                string consumer_name = "video_encoder_udp_" + peerid;
                shared_ptr<NvEncoderVideoConsumer> nvEncoder = std::make_shared<NvEncoderVideoConsumer>(consumer_name, 0.0, m_peerIdStreamId);
                std::map<std::string, std::string, std::less<>> opts;
                string webrtc_consumer_name = "webrtc_sink_udp_" + peerid;
                shared_ptr<WebrtcSinkConsumer> webrtcSinkConsumer = std::make_shared<WebrtcSinkConsumer>(webrtc_consumer_name, m_peerIdStreamId, 0.0, opts);
                nvEncoder->setConsumer(webrtcSinkConsumer);
                sink->m_consumer = nvEncoder;
                webrtcSinkConsumer->setWebrtcBroadcaster(broadcaster);
                m_videoSinkList[peerid] = sink;
            }
            LOG(info) << "Video Broadcaster size = " << m_videoSinkList.size() << endl;
        }
        void removeConsumer(const std::string& peerid)
        {
            std::lock_guard<std::mutex> lock(m_videoSinkLock);
            std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
            if(it != m_videoSinkList.end())
            {
                m_videoSinkList.erase(it);
            }
            LOG(info) << "Video Broadcaster size = " << m_videoSinkList.size() << endl;
        }
        void setConsumerReady(const string& peerid)
        {
            /* search peer in map to set start play flag */
            std::lock_guard<std::mutex> lock(m_videoSinkLock);
            std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
            if(it != m_videoSinkList.end())
            {
                std::shared_ptr<VideoSinkInfo> sink = it->second;
                sink->m_isSinkReady = true;
            }
        }
        void setPeerIdStreamId(const string& peerid_streamid)
        {
            m_peerIdStreamId = peerid_streamid;
        }
    private:
        std::mutex                           m_videoSinkLock;
        std::size_t                          m_resolutionIndex = 0;
        uint32_t                             m_targetWidth  = WIDTH_1080p;
        uint32_t                             m_targetHeight = HEIGHT_1080p;
        uint32_t                             m_sourceWidth  = 0;
        uint32_t                             m_sourceHeight = 0;
        std::string                          m_peerIdStreamId;
        std::time_t                          m_lastDRCTime {0};
        std::map<std::string, std::shared_ptr<VideoSinkInfo>> m_videoSinkList;
};

class NvGstUDPVideoSource : public rtc::VideoSourceInterface<webrtc::VideoFrame>
{
public:
    void getDecodeStats(LatencyStats& stats)
    {

    }
    NvGstUDPVideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts)
    : m_udpVideoClient(nullptr)
    , m_audioFreq (8000)
    , m_uri(uri)
    , m_mediaType("video")
    , m_streamid("")
    {
        if ( opts.find("streamId") != opts.end() )
        {
            m_streamid = opts.at("streamId");
            m_peerIdStreamId = m_streamid;
        }
        if ( opts.find("peerid") != opts.end() )
        {
            m_peerid = opts.at("peerid");
            m_peerIdStreamId = m_peerid + ":" + m_streamid;
        }
        if (opts.find("sample_rate") != opts.end())
        {
            std::string sample_rate_string = opts.at("sample_rate");
            m_audioFreq = stringToInt(sample_rate_string, 0);
        }
        if ( opts.find("quality") != opts.end() )
        {
            m_quality = opts.at("quality");
        }
        if ( opts.find("framerate") != opts.end() )
        {
            m_frameRate = opts.at("framerate");
        }
        LOG(info) << "NvGstUDPVideoSource peerid: "<< m_peerid << " streamId: "<< m_streamid << " Video Quality: " << m_quality << endl;

        if (UdpClientPool::getInstance()->isClientExist(m_streamid, m_mediaType) == false)
        {
            // Create new udp client & decoder pipeline.
            LOG(info) << "Setting up Client for streamId: " << m_streamid << " media: " << m_mediaType << endl;
            m_videoDataConsumer.reset(new VideoDataConsumer());
            setupClient(opts);
            LOG(info) << "Created udpClient:" << m_udpVideoClient << ", Consumer:" << m_videoDataConsumer.get() << endl;
        }
        else
        {
            // Reuse the udp client & decoder pipeline.
            m_udpVideoClient = UdpClientPool::getInstance()->getClient(m_streamid, m_mediaType);
            if (m_udpVideoClient)
            {
                m_videoDataConsumer = static_pointer_cast<VideoDataConsumer>(m_udpVideoClient->getConsumer(UdpClient::UDP_VIDEO_TYPE));
                if (!m_videoDataConsumer)
                {
                    LOG(info) << "Creating new Video Data Consumer and setting it" << endl;
                    m_videoDataConsumer.reset(new VideoDataConsumer());
                    m_udpVideoClient->setConsumer(m_videoDataConsumer, UdpClient::UDP_VIDEO_TYPE);
                }
                else
                {
                    LOG(warning) << "Video Consumer already exists" << endl;
                }
                LOG(info) << "Reusing udpClient:" << m_udpVideoClient << ", for Consumer:" << m_videoDataConsumer.get() << endl;
            }
            else
            {
                LOG(error) << "UdpVideoClient not found for media:" << m_mediaType << endl;
            }
        }
        if (m_videoDataConsumer)
        {
            m_videoDataConsumer->setPeerIdStreamId (m_peerIdStreamId);
        }
    }
    virtual ~NvGstUDPVideoSource()
    {
        try {
            LOG(info) << "Enter ~NvGstUDPVideoSource peerId:" << m_peerid << endl;
            size_t size = 0;
            if (m_videoDataConsumer)
            {
                size = m_videoDataConsumer->getBroadcasterSize();
            }
            else
            {
                LOG(warning) << "~NvGstUDPVideoSource: m_videoDataConsumer is null, treating broadcaster size as 0. peerId:" << m_peerid << endl;
            }
            if(size == 0)
            {
                if (m_udpVideoClient)
                {
                    m_udpVideoClient->destroy(false);
                    EventLoop *eventLoop = m_udpVideoClient->getEventLoop();
                    if (eventLoop && eventLoop->testEventLoopRunning() == false)
                    {
                        LOG(info) << "UDPVideoClient Event loop is not running. Pending messages: "
                                << eventLoop->getPendingMessages() << " peerid: " << m_peerid <<  endl;
                        GarbageCollector::getInstace()->insert(move(m_udpVideoClient), false);
                    }
                }
                UdpClientPool::getInstance()->removeClient(m_streamid);
            }
            LOG(info) << "Exit ~NvGstUDPVideoSource peerId:" << m_peerid << endl;
        } catch (const std::exception& e) {
            try { LOG(error) << "Exception in ~NvGstUDPVideoSource: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
        } catch (...) {
            try { LOG(error) << "Unknown exception in ~NvGstUDPVideoSource" << endl; } catch (...) { (void)std::current_exception(); }
        }
    }

    void controlStreamFileVideoSource(const std::string& action, const std::string& seek_value)
    {

    }

    void setupClient (const std::map<std::string, std::string, std::less<>> &opts)
    {
        UdpStream stream;
        if ( opts.find("video_port") != opts.end() )
        {
            stream.m_videoPort = stringToInt(opts.at("video_port"));
            stream.m_type = UdpClient::UDP_VIDEO_TYPE;
        }
        if ( opts.find("audio_port") != opts.end() )
        {
            stream.m_audioPort = stringToInt(opts.at("audio_port"));
            stream.m_type = UdpClient::UDP_VIDEO_AUDIO_TYPE;
            stream.m_audioFreq = m_audioFreq;
        }
        if ( opts.find("codec") != opts.end() )
        {
            stream.m_videoCodec = opts.at("codec");
        }
        LOG(info) << "stream_type: " << stream.m_type << endl;
        m_udpVideoClient = UdpClientPool::getInstance()->addClient(m_streamid, stream);
        if (m_udpVideoClient)
        {
            m_udpVideoClient->create();
            m_udpVideoClient->setConsumer(m_videoDataConsumer, UdpClient::UDP_VIDEO_TYPE);
        }
    }

    gint64 getPositionFileVideoSource()
    {
        return 0;
    }
    virtual string getStreamState()
    {
        return "NOT_PLAYING";
    }
    virtual bool isStreamError()
    {
        return false;
    }
    void startStream()
    {
        LOG(info) << "startStream: " << m_peerid << endl;
        if(m_udpVideoClient && m_videoDataConsumer)
        {
            m_videoDataConsumer->setConsumerReady(m_peerid);
            m_udpVideoClient->start();
        }
        else
        {
            LOG(warning) << "Data Consumer or Video Client Instance is NULL" << endl;
        }
    }
    // overide rtc::VideoSourceInterface<webrtc::VideoFrame>
    void AddOrUpdateSink(rtc::VideoSinkInterface<webrtc::VideoFrame> *sink, const rtc::VideoSinkWants &wants)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        m_broadcaster.AddOrUpdateSink(sink, wants);
        m_videoDataConsumer->setConsumer(m_peerid, &m_broadcaster, m_quality, m_frameRate);
        OnSinkWantsChanged(m_broadcaster.wants());
        m_videoDataConsumer->setConsumerReady(m_peerid);
    }

    void RemoveSink(rtc::VideoSinkInterface<webrtc::VideoFrame> *sink)
    {
        LOG(info) << __METHOD_NAME__ << endl;
        m_videoDataConsumer->removeConsumer(m_peerid);
        OnSinkWantsChanged(m_broadcaster.wants());
        m_broadcaster.RemoveSink(sink);
    }

    void OnSinkWantsChanged(const rtc::VideoSinkWants& wants)
    {
        unsigned int targetPixelCount = wants.target_pixel_count.value_or(wants.max_pixel_count);
        LOG (info) << "WebRTC asked targetPixel = " << targetPixelCount << " maxPixel = " << wants.max_pixel_count
                   << " maxFps = " << wants.max_framerate_fps << " resAlignment = " << wants.resolution_alignment << endl;
        m_videoDataConsumer->handleDRC (m_peerid, wants.max_pixel_count, wants.max_framerate_fps);
    }

private:
    shared_ptr<UdpClient>               m_udpVideoClient;
    shared_ptr<VideoDataConsumer>       m_videoDataConsumer;
    int                                 m_audioFreq;
    rtc::VideoBroadcaster               m_broadcaster;
    std::string                         m_uri;
    std::string                         m_mediaType;
    std::string                         m_peerid;
    std::string                         m_streamid;
    std::string                         m_quality;
    std::string                         m_frameRate;
    std::string                         m_peerIdStreamId;
};

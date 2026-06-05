/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <string.h>
#include <vector>
#include <queue>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <future>

#include "environment.h"
#include "logger.h"

#include "libyuv/video_common.h"
#include "libyuv/convert.h"

#include "media/base/codec.h"
#include "media/base/video_common.h"
#include "media/base/video_broadcaster.h"
#include "media/engine/internal_decoder_factory.h"

#include "common_video/h264/h264_common.h"
#include "common_video/h264/sps_parser.h"
#include "modules/video_coding/h264_sprop_parameter_sets.h"

#include "api/video_codecs/video_decoder.h"

#include "media_consumer.h"
#include "media/video_source/decoders/gstnvvideodecoder.h"
#include "media/video_source/encoders/nvvideoencoder.h"
#include "garbagecollector.h"
#include "media/overlays/ll_overlay.h"
#include "media/video_source/processors/transforms/ll_transform.h"
#include "media/video_source/senders/webrtc_sink_consumer.h"

#define H265_NAL_IDR_RADL     19
#define H265_NAL_IDR_LP       20
#define H265_NAL_VPS          32
#define H265_NAL_SPS          33
#define H265_NAL_PPS          34
#define H265_NAL_PREFIX_SEI   39

template <typename T>
class VideoSource : public T::Callback
{
public:
    VideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts, IMediaDataConsumer* consumer) :
        m_resume_time_in_epoch(0),
        m_env(m_stop),
	    m_liveclient(m_env, this, uri.c_str(), opts, 0),
        m_consumer(consumer)
    {
        LOG(info) << __func__ << endl;
        if ( opts.find("sensor_type") != opts.end() )
        {
            m_sensorType = opts.at("sensor_type");
        }
        this->Start();
    }

    VideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts, bool wait) :
        m_resume_time_in_epoch(0),
        m_env(m_stop),
	    m_liveclient(m_env, this, uri.c_str(), opts, 0),
        m_consumer(nullptr),
        m_frameId(-1),
        m_ptsFromServer(0)
    {
        LOG(info) << __func__ << endl;
        if ( opts.find("sensor_type") != opts.end() )
        {
            m_sensorType = opts.at("sensor_type");
        }
    }

    VideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts) :
        m_resume_time_in_epoch(0),
        m_env(m_stop),
	    m_liveclient(m_env, this, uri.c_str(), opts, 0),
        m_consumer(nullptr)
    {
        m_capturethread = std::thread(&VideoSource::CaptureThread, this);
    }

    virtual ~VideoSource()
    {
        this->Stop();
        LOG(info) << __func__ << endl;
    }
    void Start()
    {
        LOG(info) << "VideoSource::Start" << endl;
        m_frameId = -1;
        m_ptsFromServer = 0;
        m_capturethread = std::thread(&VideoSource::CaptureThread, this);
    }
    void Start(std::shared_ptr<IMediaDataConsumer> consumer)
    {
        LOG(info) << "VideoSource::Start" << endl;
        if (m_consumer == nullptr)
        {
            m_consumer = consumer;
            Start();
        }
    }
    void Stop()
    {
        LOG(info) << "VideoSource::stop" << endl;
        m_env.stop();
        if (m_capturethread.joinable())
        {
            m_capturethread.join();
        }
        if (m_consumer)
        {
            FrameParams frame_params;
            m_consumer->onFrame(frame_params);

        }
        LOG(info) << "VideoSource::stop : done" << endl;
    }
    bool IsRunning() { return (m_stop == 0); }

    void CaptureThread()
    {
        if (GET_CONFIG().rtsp_server_use_socket_poll == true)
        {
            m_env.useSocketPoll(true);
        }
        m_env.mainloop();
    }

    void controlStreamLiveVideoSource(const std::string& action, const std::string& seek_value)
    {
        m_liveclient.controlStreamRtspConnection(&m_resume_time_in_epoch, action, seek_value);
    }

    std::vector< std::vector<uint8_t> > getInitFrames(const std::string & codec, const char* sdp)
    {
        if (m_initFrames.empty() == false)
        {
            LOG(info) << "Returning - InitFrames already present" << endl;
            return m_initFrames;
        }
        std::vector< std::vector<uint8_t> > frames;
        if (codec == "H264")
        {
            const char* pattern = "sprop-parameter-sets=";
            const char* sprop = strstr(sdp, pattern);
            if (sprop)
            {
                std::string sdpstr(sprop + strlen(pattern));
                size_t pos = sdpstr.find_first_of(" ;\r\n");
                if (pos != std::string::npos)
                {
                    sdpstr.erase(pos);
                }

                vector<uint8_t> h264marker = getDefaultH26xMarker();
                vector<string> sprop_vector = splitString(sdpstr, ",");
                if (sprop_vector[0].empty() == false)
                {
                    std::vector<uint8_t> sps;
                    std::string sps_decoded_string = base64_decode(sprop_vector[0]);
                    sps = toBytes(sps_decoded_string);
                    sps.insert(sps.begin(), h264marker.begin(), h264marker.end());
                    frames.push_back(sps);
                }

                if (sprop_vector[1].empty() == false)
                {
                    std::vector<uint8_t> pps;
                    std::string pps_decoded_string = base64_decode(sprop_vector[1]);
                    pps = toBytes(pps_decoded_string);
                    pps.insert(pps.begin(), h264marker.begin(), h264marker.end());
                    frames.push_back(pps);
                }
            }
        }
        else if (codec == "H265")
        {
            uint8_t init_code[4] = { 0, 0, 0, 0x1 };
            vector<uint8_t> init_code_vect(init_code, init_code + 4);

            const char* pattern = "sprop-vps=";
            const char* sprop = strstr(sdp, pattern);
            if (sprop)
            {
                std::string vpsStr(sprop + strlen(pattern));
                size_t pos = vpsStr.find_first_of(" ;\r\n");
                if (pos != std::string::npos)
                {
                    vpsStr.erase(pos);
                }
                std::string vps_decoded_string = base64_decode(vpsStr);
                std::vector<uint8_t> vps_bytes = toBytes(vps_decoded_string);
                vps_bytes.insert(vps_bytes.begin(), init_code_vect.begin(), init_code_vect.end());
                frames.push_back(vps_bytes);
            }

            pattern = "sprop-sps=";
            sprop = strstr(sdp, pattern);
            if (sprop)
            {
                std::string spsStr(sprop + strlen(pattern));
                size_t pos = spsStr.find_first_of(" ;\r\n");
                if (pos != std::string::npos)
                {
                    spsStr.erase(pos);
                }
                std::string sps_decoded_string = base64_decode(spsStr);
                std::vector<uint8_t> sps_bytes = toBytes(sps_decoded_string);
                sps_bytes.insert(sps_bytes.begin(), init_code_vect.begin(), init_code_vect.end());
                frames.push_back(sps_bytes);
            }

            pattern = "sprop-pps=";
            sprop = strstr(sdp, pattern);
            if (sprop)
            {
                std::string ppsStr(sprop + strlen(pattern));
                size_t pos = ppsStr.find_first_of(" ;\r\n");
                if (pos != std::string::npos)
                {
                    ppsStr.erase(pos);
                }
                std::string pps_decoded_string = base64_decode(ppsStr);
                std::vector<uint8_t> pps_bytes = toBytes(pps_decoded_string);
                pps_bytes.insert(pps_bytes.begin(), init_code_vect.begin(), init_code_vect.end());
                frames.push_back(pps_bytes);
            }
        }
        return frames;
    }

    // overide T::Callback
    virtual bool onNewSession(const char *id, const char *media, const char *codec, const char *sdp)
    {
        bool success = false;
        if (strcmp(media, "video") == 0)
        {
            LOG(info) << "LiveVideoSource::onNewSession " << media << "/" << codec << " " << sdp << endl;

            if ( (strcmp(codec, "H264") == 0)
               || (strcmp(codec, "H265") == 0)
               || (strcmp(codec, "JPEG") == 0)
               || (strcmp(codec, "VP9") == 0) )
            {
                m_codec[id] = codec;
                success = true;
            }
            m_media[id] = "video";

            m_sdpVideo = sdp;
            if (success)
            {
                struct timeval presentationTime;
                timerclear(&presentationTime);

                m_initFrames = getInitFrames(codec, m_sdpVideo.data());
                for (auto frame : m_initFrames)
                {
                    onData(id, frame.data(), frame.size(), presentationTime);
                }
            }
        }
        else if (strcmp(media, "audio") == 0)
        {
            LOG(info) << "LiveVideoSource::onNewSession " << media << "/" << codec << endl;
            m_media[id] = "audio";
            success = true;
        }
        return success;
    }
    virtual bool onData(const char *id, unsigned char *buffer, ssize_t size, struct timeval presentationTime)
    {
        int64_t ts = presentationTime.tv_sec;
        ts = ts * 1000 + presentationTime.tv_usec / 1000;
        LOG(verbose2) << "LiveVideoSource:onData id:" << id << " size:" << size << " ts:" << ts << endl;
        int res = 0;

	    if (strcmp(id, "record_time") == 0)
        {
             m_resume_time_in_epoch = *((uint64_t*)buffer);
             return 1;
        }

        if(m_consumer)
        {
            std::string codec = m_codec[id];
            std::string media = m_media[id];
            if (m_sensorType == SENSOR_TYPE_NVSTREAM)
            {
                uint8_t nalu_type = NaluType::kNalUnknown;
                if (codec == "H265")
                {
                    nalu_type = parseH265NaluType(buffer, size);
                }
                else
                {
                    nalu_type = parseH264NaluType(buffer, size);
                }

                if ((codec == "H264" && nalu_type == NaluType::kSei) ||
                    (codec == "H265" && nalu_type == H265NaluType::PREFIX_SEI_NUT))
                {
                    /* Parse the frameId from SEI frame & update to consumer */
                    m_frameId = parseSeiFrameId(buffer, size, m_ptsFromServer, codec);
                }
                else
                {
                    FrameParams frame_params;
                    frame_params.m_media   = media;
                    frame_params.m_codec   = codec;
                    frame_params.m_serverFrameId = m_frameId;
                    frame_params.m_presentationTime = presentationTime;
                    frame_params.m_serverPts = m_ptsFromServer;

                    // Attach sps-pps nals with first IDR-frame.
                    if (m_consumer->m_startConsuming == false && isIDRFrame(nalu_type, codec))
                    {
                        if (m_consumer->isSpsPpsAvailable() == false)
                        {
                            std::vector<std::vector<uint8_t>> initFrames = getInitFrames(codec, m_sdpVideo.data());
                            for (auto frame : initFrames)
                            {
                                frame_params.m_buffer  = frame.data();
                                frame_params.m_size    = frame.size();
                                m_consumer->onFrame(frame_params);
                            }
                        }
                    }
                    frame_params.m_buffer  = buffer;
                    frame_params.m_size    = size;
                    m_consumer->onFrame(frame_params);
                }
            }
            else
            {
                FrameParams frame_params;
                frame_params.m_media   = media;
                frame_params.m_codec   = codec;
                frame_params.m_buffer  = buffer;
                frame_params.m_size    = size;
                frame_params.m_presentationTime = presentationTime;
                m_consumer->onFrame(frame_params);
            }
        }
        return (res == 0);
    }

    string getPlaybackState()
    {
        return m_liveclient.getPlaybackState();
    }

    uint64_t m_resume_time_in_epoch;
private:
    char        m_stop = 0;
    Environment m_env;

protected:
    T m_liveclient;

private:
    std::thread                        m_capturethread;
    std::vector<uint8_t>               m_cfg;
    std::map<std::string, std::string, std::less<>> m_media;
    std::map<std::string, std::string, std::less<>> m_codec;
    std::shared_ptr<IMediaDataConsumer>  m_consumer;
    bool                               m_startDecLoop = false;
    string                             m_sensorType;
    int64_t                            m_frameId = -1;
    int64_t                            m_ptsFromServer = 0;
    std::vector<std::vector<uint8_t>>  m_initFrames;
    string                             m_sdpVideo;
};

template <typename T>
class LiveVideoSource : public rtc::VideoSourceInterface<webrtc::VideoFrame>, public VideoSource<T>
{
public:
    LiveVideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts) :
        VideoSource<T> (uri, opts, false)
    {
        LOG(info) << __func__ << endl;

        if ( opts.find("peerid") != opts.end() )
        {
            m_peerid = opts.at("peerid");
            m_peerIdStreamId = m_peerid;
        }
        if ( opts.find("quality") != opts.end() )
        {
            m_quality = opts.at("quality");
        }
        if ( opts.find("sensorID") != opts.end() )
        {
            m_sensorName = opts.at("sensorID");
        }
        if ( opts.find("sensorId") != opts.end() )
        {
            m_sensorId = opts.at("sensorId");
            m_peerIdStreamId = m_peerIdStreamId + ":" + m_sensorId;
        }
        if ( opts.find("framerate") != opts.end() )
        {
            m_frameRate = opts.at("framerate");
        }

        if(m_quality == "pass_through")
        {
            m_passThrough = true;
        }

        if(m_passThrough)
        {
            LOG(info) << "Alert: Pass Through Mode Enabled.. ! for uri = " << uri << endl;
            if ( opts.find("codec") != opts.end() )
            {
                string codec = opts.at("codec");
                /* Only H264/H265 codec is supported in pass through mode */
                if(!iequals(codec, "H264") && !iequals(codec, "H265"))
                {
                    string unsupported_error = codec + " codec not supported in pass through mode";
                    LOG(error) << unsupported_error << endl;
                    throw std::invalid_argument( unsupported_error );
                }
            }
            m_peerIdStreamId = m_peerIdStreamId + "_pass-through";
            VideoSenderPool* pool = VideoSenderPool::getInstance();
            m_videowebRTCSender = pool->getVideoSender(uri);
            if (m_videowebRTCSender == nullptr)
            {
                pool->addStream(uri);
                m_videowebRTCSender = pool->getVideoSender(uri);
            }
            this->template Start(m_videowebRTCSender);
        }
        else
        {
            string decoder_consumer_name = "video_decoder_live_" + m_peerid;
            m_gstdecoder.reset(new GstNvVideoDecoder(decoder_consumer_name, uri, opts));
            if (m_gstdecoder->create(true) == -1)
            {
                LOG(error) << "Error in Creating Pipeline" << endl;
                throw std::invalid_argument( "Error in Creating Pipeline" );
            }
            LOG(info) << "Creating Video Encoder object: " << m_peerid << endl;
            double frame_rate = stringToDouble(m_frameRate, 30.0);
            string consumer_name = "video_encoder_" + m_peerid;
            m_nvEncoder.reset(new NvEncoderVideoConsumer(consumer_name, frame_rate, m_peerIdStreamId));
            if (!m_nvLLOverlay && !GET_OSD_INSTANCE()->isError())
            {
                //m_nvLLOverlay.reset(new NvLLOverlay(uri, opts));
            }
            if (!m_nvLLTransform)
            {
                string consumer_name = "transform_" + m_peerid;
                m_nvLLTransform.reset(new NvLLTransform(consumer_name));
            }
            if (!m_nvLLTransformSink)
            {
                string consumer_name = "transform_sink_" + m_peerid;
                m_nvLLTransformSink.reset(new NvLLTransform(consumer_name));
            }
            if (!m_webrtcSinkConsumer)
            {
                std::map<std::string, std::string, std::less<>> opts;
                string consumer_name = "webrtc_sink_" + m_peerid;
                m_webrtcSinkConsumer.reset(new WebrtcSinkConsumer(consumer_name, m_peerIdStreamId, frame_rate, opts));
            }
        }
    }

    void startStream()
    {
        if(m_gstdecoder)
        {
            m_gstdecoder->setConsumerReady(m_peerid);
            m_gstdecoder->play();
            this->template Start(m_gstdecoder);
        }
    }

    void setDecoderConsumerPipeline()
    {
        if (m_nvLLOverlay && !GET_OSD_INSTANCE()->isError() && m_nvLLOverlay->isOverlayEnabled())
        {
            // Decoder -> Transform -> overlay -> HW / SW encoder
            m_gstdecoder->setConsumer(m_peerid, m_nvLLTransform);
            m_nvLLTransform->setConsumer(m_nvLLOverlay);
            if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
            {
                m_nvLLOverlay->setConsumer (m_nvEncoder);
                m_nvEncoder->setConsumer (m_webrtcSinkConsumer);
            }
            else
            {
                m_nvLLOverlay->setConsumer (m_nvLLTransformSink);
                m_nvLLTransformSink->setConsumer (m_webrtcSinkConsumer);
            }
            m_gstdecoder->setConsumerReady(m_peerid);
        }
        else
        {
            // Decoder -> Transform -> HW / SW encoder
            if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
            {
                m_gstdecoder->setConsumer(m_peerid, m_nvLLTransform);
                m_nvLLTransform->setConsumer(m_nvEncoder);
                m_nvEncoder->setConsumer (m_webrtcSinkConsumer);
            }
            else
            {
                m_gstdecoder->setConsumer(m_peerid, m_nvLLTransformSink);
                m_nvLLTransformSink->setConsumer (m_webrtcSinkConsumer);
            }
            m_gstdecoder->setConsumerReady(m_peerid);
        }
    }

    LiveVideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts, bool wait) :
        VideoSource<T> (uri, opts, wait)
    {
        LOG(info) << __func__ << endl;
    }

    virtual ~LiveVideoSource()
    {
        try {
            LOG(info) << __func__ << endl;
            m_nvLLOverlay.reset();
            m_nvEncoder.reset ();
            m_nvLLTransform.reset();
            m_nvLLTransformSink.reset();
            m_webrtcSinkConsumer.reset();
            if (m_gstdecoder)
            {
                bool is_event_loop_running = m_gstdecoder->getEventLoop().testEventLoopRunning();
                if(is_event_loop_running == false)
                {
                    LOG(info) << "Event loop is running: " << is_event_loop_running << " pending messages: "
                            << m_gstdecoder->getEventLoop().getPendingMessages() << " peerid: " << m_peerid <<  endl;
                    m_gstdecoder->destroy();
                    GarbageCollector::getInstace()->insert(move(m_gstdecoder), false);
                }
            }
        } catch (const std::exception& e) {
            try { LOG(error) << "Exception in ~LiveVideoSource: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
        } catch (...) {
            try { LOG(error) << "Unknown exception in ~LiveVideoSource" << endl; } catch (...) { (void)std::current_exception(); }
        }
    }

    virtual string getStreamState()
    {
        if(m_gstdecoder)
        {
            return m_gstdecoder->getstate();
        }
        else if(m_passThrough)
        {
            return  this->template getPlaybackState();
        }
        return "NOT_PLAYING";
    }
    virtual bool isStreamError()
    {
        if(m_gstdecoder)
        {
            return m_gstdecoder->getError();
        }
        return false;
    }

    uint64_t getLastTS()
    {
        if(m_gstdecoder)
        {
            return m_gstdecoder->getLastTS();
        }
        return 0;
    }

    int64_t getFileStartTime()
    {
        if(m_gstdecoder)
        {
            return m_gstdecoder->getFileStartTime();
        }
        return 0;
    }

    uint32_t getDurationStream()
    {
        if(m_gstdecoder)
        {
            return m_gstdecoder->getDurationStream();
        }
        return 0;
    }

    string getSensorName()
    {
        return m_sensorName;
    }
    string getSensorId()
    {
        return m_sensorId;
    }

    // overide rtc::VideoSourceInterface<webrtc::VideoFrame>
    void AddOrUpdateSink(rtc::VideoSinkInterface<webrtc::VideoFrame> *sink, const rtc::VideoSinkWants &wants)
    {
        LOG(info) << "AddOrUpdateSink" << endl;
        m_broadcaster.AddOrUpdateSink(sink, wants);
        if(m_passThrough)
        {
            m_videowebRTCSender->appendWebrtcBroacaster(m_peerIdStreamId, &m_broadcaster);
        }
        else
        {
            if(m_gstdecoder)
            {
                if (m_nvLLOverlay && !GET_OSD_INSTANCE()->isError() && m_nvLLOverlay->isOverlayEnabled())
                {
                    m_gstdecoder->setConsumer(m_peerid, m_nvEncoder);
                    m_nvEncoder->setConsumer (m_webrtcSinkConsumer);
                    m_nvLLTransform->setConsumer(m_nvLLOverlay);
                    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
                    {
                        m_nvLLOverlay->setConsumer (m_nvEncoder);
                    }
                    else
                    {
                        m_nvLLOverlay->setConsumer (m_nvLLTransformSink);
                        m_nvLLTransformSink->setConsumer (m_webrtcSinkConsumer);
                    }
                    m_gstdecoder->setQuality(m_peerid, m_quality);
                    m_gstdecoder->setConsumerReady(m_peerid);
                }
                else
                {
                    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
                    {
                        m_gstdecoder->setConsumer(m_peerid, m_nvLLTransform);
                        m_nvLLTransform->setConsumer(m_nvEncoder);
                        m_nvEncoder->setConsumer (m_webrtcSinkConsumer);
                    }
                    else
                    {
                        m_gstdecoder->setConsumer(m_peerid, m_nvLLTransformSink);
                        m_nvLLTransformSink->setConsumer (m_webrtcSinkConsumer);
                    }
                    m_gstdecoder->setQuality(m_peerid, m_quality);
                    m_gstdecoder->setConsumerReady(m_peerid);
                    if (GET_OSD_INSTANCE()->isError() && m_gstdecoder->isOverlay())
                    {
                        LOG(error) << "Overlay cuda libs not found, Disabling overlay" << endl;
                    }
                }
            }
            if (m_nvLLOverlay)
            {
                m_nvLLOverlay->setOriginalFrameSize ();
            }
            if (m_nvLLTransform)
            {
                m_nvLLTransform->setOriginalFrameSize ();
            }
            if (m_nvLLTransformSink)
            {
                m_nvLLTransformSink->setOriginalFrameSize ();
            }
            if (m_webrtcSinkConsumer)
            {
                m_webrtcSinkConsumer->setWebrtcBroadcaster ((void*)&m_broadcaster);
            }
        }
        m_broadcaster.AddOrUpdateSink(sink, wants);
    }

    void RemoveSink(rtc::VideoSinkInterface<webrtc::VideoFrame> *sink)
    {
        LOG(info) << "RemoveSink" << endl;
        if (m_passThrough)
        {
            m_videowebRTCSender->removeWebrtcBroacaster(m_peerIdStreamId);
        }
        else
        {
            if(m_gstdecoder)
            {
                m_gstdecoder->removeConsumer(m_peerid);
            }
            m_broadcaster.RemoveSink(sink);
        }
    }

    void OnSinkWantsChanged(const rtc::VideoSinkWants& wants)
    {
        if (!m_passThrough)
        {
            unsigned int targetPixelCount = wants.target_pixel_count.value_or(wants.max_pixel_count);
            LOG (info) << "WebRTC asked targetPixel = " << targetPixelCount << " maxPixel = " << wants.max_pixel_count
                    << " maxFps = " << wants.max_framerate_fps << " resAlignment = " << wants.resolution_alignment << endl;

            if(m_gstdecoder)
            {
                m_gstdecoder->handleDRC (m_peerid, wants.max_pixel_count, wants.max_framerate_fps);
            }
        }
    }

    std::string getBuffer()
    {
        std::string buffer;
        if (m_gstdecoder)
        {
            buffer = m_gstdecoder->getImageBuffer();
        }
        return buffer;
    }
    rtc::VideoBroadcaster          m_broadcaster;
    shared_ptr<GstNvVideoDecoder>  m_gstdecoder = nullptr;
    std::string                    m_peerid;
    std::string                    m_peerIdStreamId {""};
    std::string                    m_quality;
    std::string                    m_sensorName;
    std::string                    m_sensorId;
    std::string                    m_frameRate;
    shared_ptr<NvEncoderVideoConsumer>  m_nvEncoder = nullptr;
    shared_ptr<NvLLOverlay>             m_nvLLOverlay = nullptr;
    shared_ptr<NvLLTransform>           m_nvLLTransform = nullptr;
    // Transform sink connects to SW encoder and acts as sink element
    shared_ptr<NvLLTransform>           m_nvLLTransformSink = nullptr;
    shared_ptr<WebrtcSinkConsumer>      m_webrtcSinkConsumer = nullptr;
    bool                               m_passThrough {false};
    shared_ptr<VideoWebRTCSender>      m_videowebRTCSender = nullptr;
};

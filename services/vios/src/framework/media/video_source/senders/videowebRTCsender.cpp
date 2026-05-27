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

#include "videowebRTCsender.h"
#include "modules/video_coding/codecs/nvidia/NvVideoFrameBuffer.h"
#include "rtc_base/ref_counted_object.h"
using namespace std;

void VideoWebRTCSender::unRefDataStructure(void *ptr)
{
    if (ptr)
    {
        std::lock_guard<std::mutex> lock(m_encParamsLock);
        encoder_params* params = (encoder_params*) ptr;
        /* Copy encoder feedback params as member variables */
        m_qp        = params->m_qp;
        m_targetBps = params->m_targetBps;
        m_fps       = params->m_frameRate;
        free (params);
    }
}

VideoWebRTCSender::VideoWebRTCSender (const std::string& consumer_name, const std::string& uri) :
                    IMediaDataConsumer(consumer_name), m_uri(uri)
{
    m_fpsDisplay = std::make_unique<FPSDisplay>();
    setConsumerMediaType(MediaTypeVideo);
}

VideoWebRTCSender::VideoWebRTCSender (const std::string& consumer_name, double frame_rate, bool enable_frame_sync)
    : IMediaDataConsumer(consumer_name), m_frameRate (frame_rate)
{
    LOG(info) << "VideoWebRTCSender::VideoWebRTCSender m_frameRate:" << m_frameRate << endl;
    m_fpsDisplay = std::make_unique<FPSDisplay>();
    setConsumerMediaType(MediaTypeVideo);
    if (frame_rate != 0)
    {
        m_idealFrameSendInterval = (1000/m_frameRate);
    }
    m_enableFrameSync = enable_frame_sync;
}

VideoWebRTCSender::VideoWebRTCSender (const std::string& consumer_name) : IMediaDataConsumer(consumer_name)
{
    m_fpsDisplay = std::make_unique<FPSDisplay>();
    setConsumerMediaType(MediaTypeVideo);
}

int VideoWebRTCSender::createPassThroughMode(std::string& device_id)
{
    LOG (info) << "Creating bypass pipeline for : "  << m_uri << " and device = " << device_id << endl;
    if (m_uri.find("webrtc/") != std::string::npos)
    {
        m_deviceId = device_id;
        WebrtcStreamProducer::getInstance()->registerDataCallback(device_id, getself());
    }
    else
    {
        StreamMonitor::getInstance()->registerDataCallback(m_uri, getself());
    }

    return 1;
}

void VideoWebRTCSender::appendWebrtcBroacaster(const std::string& peerid, rtc::VideoBroadcaster* broadcaster)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSink>>::iterator it = m_videoSinkList.find(peerid);
    if(it == m_videoSinkList.end())
    {
        shared_ptr<VideoSink> sink (new VideoSink);
        sink->m_broadcaster = broadcaster;
        sink->m_state = "PLAYING";
        m_videoSinkList[peerid] = sink;
    }
}

void VideoWebRTCSender::removeWebrtcBroacaster(const std::string& peerid)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    LOG(info) << "Removing webrtc broadcaster for peer: " << peerid << endl;
    std::map<std::string, std::shared_ptr<VideoSink>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        if (it->second) {
            LOG(info) << "Setting broadcaster to nullptr for peer: " << peerid << endl;
            it->second->m_broadcaster = nullptr;  // Set broadcaster to null - prevents crash
            it->second->m_state = "STOPPED";      // Update state to stopped
            LOG(info) << "Broadcaster safely nullified for peer: " << peerid << endl;
        }
        // Keep the VideoSink object in the map but with null broadcaster
        // This prevents race conditions where onFrame() tries to access deleted object
    }
    
    // Check if all sinks are stopped (not removed) to determine if we should deregister
    bool allStopped = true;
    for (const auto& pair : m_videoSinkList) {
        if (pair.second && pair.second->m_broadcaster != nullptr && pair.second->m_state == "PLAYING") {
            allStopped = false;
            break;
        }
    }
    
    if (allStopped)
    {
        LOG(info) << "All sinks stopped, deregistering data callbacks" << endl;
        if (m_uri.find("webrtc/") != std::string::npos)
        {
            WebrtcStreamProducer::getInstance()->deregisterDataCallback(getself(), m_deviceId);
        }
        else
        {
            StreamMonitor::getInstance()->deregisterDataCallback(getself(), m_uri);
        }
        
        // Now safe to set shutdown flag after all callbacks are deregistered
        m_isShuttingDown = true;
    }
}

/* Send the feedback received to caller */
void VideoWebRTCSender::getwebRTCFeedback(int* qp, int* bitrate, double* frame_rate)
{
    std::lock_guard<std::mutex> lock(m_encParamsLock);
    *qp = m_qp;
    *bitrate = m_targetBps;
    *frame_rate = m_fps;
}

#ifdef ENABLE_FRAMEID_SUPPORT_IN_WEBRTC
std::vector<uint8_t> VideoWebRTCSender::getSeiFrame(std::vector<uint8_t>& content, FrameParams& params)
{
    uint8_t nal_type = NaluType::kNalUnknown;
    nal_type = parseH264NaluType(content.data(), content.size());
    std::vector<uint8_t> sei_frame;
    if (!isValidDataNAL(nal_type, params.m_codec) && nal_type != NaluType::kSps)
    {
        return sei_frame;
    }

    int64_t timestamp = params.m_presentationTime.tv_sec;
    timestamp = timestamp * 1000*1000 + params.m_presentationTime.tv_usec;
    ++m_frameId;

    string uuid = stringToHex("VST_WEBRTC_META") + "00";
    FrameInfoSeiPayload frameInfo;
    frameInfo.frameId = m_frameId;
    frameInfo.timestamp = timestamp;
    sei_frame = getUserDefinedSeiFrame(frameInfo, uuid, params.m_codec);

    return sei_frame;
}
#endif

void VideoWebRTCSender::onFrame(FrameParams& frame_params)
{
    // Early exit if shutting down to prevent race conditions
    if (m_isShuttingDown.load()) {
        return;
    }

    std::vector<uint8_t> content;
    LOG(verbose2) << "onFrame media:"<< frame_params.m_media << ", codec:" << frame_params.m_codec
                  << ", size:" << frame_params.m_size << endl;
    if (frame_params.m_media == "audio")
    {
        return;
    }
    if (frame_params.m_needParsing)
    {
        content = parseAndCreateFrame(frame_params);
        if(content.size() == 0)
        {
            return;
        }
    }
    else
    {
        content.insert(content.end(), frame_params.m_buffer, frame_params.m_buffer + frame_params.m_size);
    }

    if (m_idealFrameSendInterval && m_enableFrameSync)
    {
        /* Check if Frames are received earlier than expected, synchronize in that case */
        checkEarlyFramesAndSynchronize();
    }

    if (GET_CONFIG().enable_frameid_in_webrtc_stream)
    {
        std::vector<uint8_t> sei_frame = getSeiFrame(content, frame_params);
        if (!sei_frame.empty())
        {
            int index_for_sei = getSeiIndex(content);
            if (index_for_sei >= 0)
            {
                content.insert(content.begin() + index_for_sei, sei_frame.begin(), sei_frame.end());
            }
            else
            {
                LOG(error) << "index invalid" << endl;
            }
        }
        else
        {
            LOG(error) << "Empty sei frame" << endl;
        }
    }

#ifdef DUMP_BITSTREAM
    dump_input_stream(content.data(), content.size());
#endif

    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSink>>::iterator it;
    for( it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
    {
        shared_ptr<VideoSink> sink = it->second;
        if(sink == nullptr || sink->m_state != "PLAYING" || m_isShuttingDown.load())
        {
            continue;
        }
        string unique_id = it->first + string("_out");
        m_fpsDisplay->displayFPS(getCurrentUnixTimestampInMs(), unique_id);
        rtc::scoped_refptr<NvVideoFrameBuffer> nv_video_frame_buffer(new rtc::RefCountedObject<NvVideoFrameBuffer>((int)frame_params.m_width, (int)frame_params.m_height));
        NvVideoFrameBuffer* nv_video_frame_buffer_ptr = nv_video_frame_buffer.get();

        /* This is being freed in webRTC stack */
        nv_video_frame_buffer_ptr->m_encodedData = (uint8_t* )malloc( content.size());
        nv_video_frame_buffer_ptr->m_encodedSize =  content.size();
        memcpy(nv_video_frame_buffer_ptr->m_encodedData,  content.data(),  content.size());
        nv_video_frame_buffer->codecName = frame_params.m_codec;

        encoder_params* params = (encoder_params*)malloc(sizeof(encoder_params));
        memset(params, 0, sizeof(encoder_params));
        nv_video_frame_buffer_ptr->m_clientBuffer = (void*)params;
        nv_video_frame_buffer_ptr->setPassThrough(true);
        nv_video_frame_buffer_ptr->registerCB([this](void* params) { unRefDataStructure(params); });
        if (frame_params.m_latencyStartTime.tv_sec != std::numeric_limits<time_t>::max() && GET_CONFIG().enable_latency_logging)
        {
            nv_video_frame_buffer->rtspToWebrtcStartTime = frame_params.m_latencyStartTime;
        }
        else
        {
            nv_video_frame_buffer->rtspToWebrtcStartTime.tv_sec = std::numeric_limits<time_t>::max();
        }

        webrtc::VideoFrame decodedImage  = webrtc::VideoFrame::Builder()
                                        .set_video_frame_buffer(nv_video_frame_buffer)
                                        .set_rotation(webrtc::kVideoRotation_0)
                                        .set_timestamp_rtp(0)
                                        .build();
        
        // Validate broadcaster and state before calling OnFrame to prevent crashes
        if (sink->m_broadcaster != nullptr && sink->m_state == "PLAYING") {
            sink->m_broadcaster->OnFrame(decodedImage);
        } else {
            LOG(verbose) << "Skipping frame for peer " << it->first 
                         << " - broadcaster: " << (sink->m_broadcaster ? "valid" : "null")
                         << ", state: " << sink->m_state << endl;
        }
    }
}

void VideoWebRTCSender::checkEarlyFramesAndSynchronize()
{
    int64_t currentFrameTs = getCurrentUnixTimestampInMs();
    if (m_prevFrameTimestamp != 0)
    {
        int64_t frameDiff = currentFrameTs - m_prevFrameTimestamp;
        m_prevFrameTimestamp = currentFrameTs;
        if (frameDiff < m_idealFrameSendInterval)
        {
            std::unique_lock<std::mutex> earlyFrame_lock(m_earlyFrameMutex);
            int64_t early_time_ms = m_idealFrameSendInterval - frameDiff;
            auto wait_time = std::chrono::system_clock::now() + chrono::milliseconds(early_time_ms);
            m_earlyFrameCv.wait_until(earlyFrame_lock, wait_time);
            m_prevFrameTimestamp = getCurrentUnixTimestampInMs();
        }
    }
    else
    {
        m_prevFrameTimestamp = currentFrameTs;
    }
}

#ifdef DUMP_BITSTREAM
void VideoWebRTCSender::dump_input_stream(const unsigned char *buffer, ssize_t size)
{
    
    if (m_frameCount > 100)
    {
        return;
    }
    if (m_frameCount == 0)
    {
        auto myid = this_thread::get_id();
        stringstream ss;
        ss << myid;
        string myThread = ss.str();
        string filename = string("encoder_out") + myThread + string(".h264");
        LOG(info) << "Opening out.h264" << endl;

        m_dumpFile.open (filename, ios::out | ios::binary);
        if(!m_dumpFile.is_open())
        {
            m_frameCount = 101;
        }
    }
    if (m_dumpFile.is_open() && (m_frameCount == 100))
    {
        LOG(info) << "Closing out.h264" << endl;
        m_dumpFile.close();
        m_frameCount ++;
    }
    else if (m_dumpFile.is_open())
    {
        m_dumpFile.write((char*)buffer, size);
        LOG(info) << "contant data: " << size << "frame count : " << m_frameCount << endl;
        m_frameCount ++;
    }
}
#endif

void VideoWebRTCSender::resume(const std::string& peerid)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSink>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSink> sink = it->second;
        sink->m_state = "PLAYING";
    }
}

void VideoWebRTCSender::pause(const std::string& peerid)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSink>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSink> sink = it->second;
        sink->m_state = "PAUSED";
    }
}

string VideoWebRTCSender::getPlaybackState(const std::string& peerid)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    string state =  "NOT_PLAYING";
    std::map<std::string, std::shared_ptr<VideoSink>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSink> sink = it->second;
        state = sink->m_state;
    }
    return state;
}



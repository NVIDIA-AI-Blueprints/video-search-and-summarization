/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <string.h>
#include <iostream>
#include <string.h>
#include <algorithm>
#include <glib/gstdio.h>
#include <chrono>
#include <errno.h>
#include <stdexcept>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include "gstnvvideoencoder.h"
#include "NvMediaSource.hh"
#include "stats.h"
#include "pc/local_audio_source.h"
#include "api/audio_codecs/builtin_audio_decoder_factory.h"
#include "nvhwdetection.h"
#include "nvbufsurface.h"

using namespace std;
using namespace nv_vms;

#define DEFAULT_ENCODER_WIDTH 1280
#define DEFAULT_ENCODER_HEIGHT 720

#ifdef ENABLE_FRAMEID_SUPPORT_IN_WEBRTC
#define WEBRTC_INPUT_LATENCY_DUMP_INTERVAL_SEC 60
#endif

GstNvVideoEncoder::GstNvVideoEncoder (const string& device_name, const string& peer_id) :
    m_pipeline (nullptr),
    m_source (nullptr),
    m_conv (nullptr),
    m_scale (nullptr),
    m_videoParseOrCapsFilter (nullptr),
    m_capsfilterA (nullptr),
    m_encoder (nullptr),
    m_parser (nullptr),
    m_sink (nullptr),
    m_width (DEFAULT_ENCODER_WIDTH),
    m_height (DEFAULT_ENCODER_HEIGHT),
    m_busWatchId(G_MAXUINT),
    m_deviceName(device_name),
    m_peerId(peer_id)
{
    LOG(info) << "Created Video Encoder peerId:" << peer_id << ", deviceName:" << device_name << endl;

    if (GET_CONFIG().enable_frameid_in_webrtc_stream)
    {
        string filename = GET_CONFIG().vst_data_path + "/webrtc_input_frames_info_" + m_deviceName + "_" + m_peerId + ".csv";
        m_csvFile.open(filename, std::ofstream::out | std::ofstream::app);
        if (m_csvFile.is_open())
        {
            m_csvFile << "peerId,  current_time, frameId,  frameSize,  latency(ms),  missing_frames" << endl;
        }
    }
    std::lock_guard<std::mutex> lock(m_videoFrameDataMapLock);
    m_videoFrameDataMap.clear();
}

GstNvVideoEncoder::~GstNvVideoEncoder ()
{
    if (m_csvFile.is_open())
    {
        m_csvFile.close();
    }
    std::lock_guard<std::mutex> lock(m_videoFrameDataMapLock);
    m_videoFrameDataMap.clear();
    LOG(info) << "Video Encoder instance deleted" << endl;
}

/* called when the appsink notifies us that there is a new buffer ready for
 * processing */
static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, GstNvVideoEncoder* nvVideoEncoder)
{
    if (nvVideoEncoder)
    {
        return nvVideoEncoder->processNewSampleFromSink(appsink);
    }
    return GST_FLOW_ERROR;
}

GstFlowReturn GstNvVideoEncoder::processNewSampleFromSink (GstElement * appsink)
{
    GstSample *sample = nullptr;
    GstBuffer *gstBuffer = nullptr;
    GstMapInfo map;
    NaluType nal_type = NaluType::kSps;

    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));
    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG (info) << "EOS Received on app sink element" << endl;
            return GST_FLOW_EOS;
        }
        else
        {
            LOG (warning) << "Received NULL sample in Audio Decoder Pipeline" << endl;
            return GST_FLOW_ERROR;
        }
    }

    /* Get the buffer from sample */
    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG (warning) << "No more buffers available from app sink element" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG (warning) << "Map the gst buffer Failed" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    if (map.size <= 0)
    {
        LOG(error) << "Video Encoder: received 0 sized buffer";
        /* Unmap the gst buffer */
        gst_buffer_unmap (gstBuffer, &map);

        /* Unref the sample */
        gst_sample_unref (sample);
        return GST_FLOW_OK;
    }

    if (m_spsPpsFrames.size() < 2 || GET_CONFIG().enable_latency_logging == true)
    {
        nal_type = parseH264NaluType(map.data, map.size);
    }

    if (m_spsPpsFrames.size() < 2)
    {
        if (nal_type == kSps || nal_type == kPps)
        {
            std::vector<uint8_t> content;
            content.insert(content.end(), map.data, map.data + map.size);
            removeH264NalStartCodes(content);
            m_spsPpsFrames.push(content);
        }
    }

    /* Retrieve the start latency time from the unique caps - reference timestamp meta added */
    GstReferenceTimestampMeta *tsmeta;
    int64_t latencyStartTime = 0;
    GstCaps *reference = gst_caps_from_string("timestamp/x-test-stream");
    tsmeta = gst_buffer_get_reference_timestamp_meta(gstBuffer, reference);
    if (tsmeta && (nal_type == NaluType::kIdr || nal_type == NaluType::kSlice))
    {
        latencyStartTime = tsmeta->timestamp;
    }
    gst_caps_unref (reference);

    if (m_consumersList.size() > 0)
    {
        struct timeval presentationTime;
        gettimeofday(&presentationTime, nullptr);
        std::lock_guard<std::mutex> guard(m_consumerLock);
        for (shared_ptr<IMediaDataConsumer> consumer : m_consumersList)
        {
            if (consumer->getConsumerMediaType() == MediaTypeVideo || 
                consumer->getConsumerMediaType() == MediaTypeAudioVideo)
            {
                FrameParams frame_params;
                frame_params.m_media            = "video";
                frame_params.m_codec            = "h264";
                frame_params.m_buffer           = map.data;
                frame_params.m_size             = map.size;
                frame_params.m_needParsing      = true;
                frame_params.m_presentationTime = presentationTime;
                if (latencyStartTime != 0)
                {
                    frame_params.m_latencyStartTime.tv_sec  = latencyStartTime / 1000000;
                    frame_params.m_latencyStartTime.tv_usec = latencyStartTime % 1000000;
                }
                else
                {
                    frame_params.m_latencyStartTime.tv_sec = std::numeric_limits<time_t>::max();
                    frame_params.m_latencyStartTime.tv_usec = std::numeric_limits<time_t>::max();
                }
                consumer->onFrame(frame_params);
            }
        }
    }

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return GST_FLOW_OK;
}

void GstNvVideoEncoder::saveSpsPps(const unsigned char *buffer, ssize_t size)
{
    NaluType nal_type = parseH264NaluType(buffer, size);
    if (nal_type == kSps)
    {
        std::vector<uint8_t> content;
        content.insert(content.end(), buffer, buffer + size);
        vector<std::pair<NaluType, int>> list = getListOfNalUnits(content);

        // Assuming data is arranged as sps | pps | idr
        for (size_t index = 0; index < list.size(); index++)
        {
            std::vector<uint8_t> data;
            NaluType nal_unit = list[index].first;
            if (nal_unit == NaluType::kSps || nal_unit == NaluType::kPps)
            {
                if (index == list.size() - 1)
                {
                    data.insert(data.end(), buffer + list[index].second, buffer + size);
                }
                else
                {
                    data.insert(data.end(), buffer + list[index].second, buffer + list[index+1].second);
                }
            }
            else if (nal_unit == NaluType::kIdr || nal_unit == NaluType::kSlice)
            {
                break;
            }
            removeH264NalStartCodes(data);
            m_spsPpsFrames.push(data);
        }
    }
}

void GstNvVideoEncoder::sendToConsumer(FrameParams& frame_params, string codec)
{
    const unsigned char* buffer = frame_params.m_buffer;
    ssize_t size = frame_params.m_size;
    std::vector<uint8_t> content;
    uint8_t nal_type = NaluType::kNalUnknown;
    vector<std::pair<NaluType, int>> list;
    vector<std::pair<H265NaluType, int>> h265_list;
    struct timeval presentationTime;

    if (m_spsPpsFrames.size() < 2)
    {
        saveSpsPps(buffer, size);
    }

    if (m_consumersList.size() <= 0 && GET_CONFIG().enable_frameid_in_webrtc_stream == false)
    {
        return;
    }

    content.insert(content.end(), buffer, buffer + size);
    if (iequals(codec, "h264"))
    {
        nal_type = parseH264NaluType(content.data(), content.size());
        bool use_edge_device_time = false;

        /* Check if SEI frames are present from edge device */
        {
            int sei_frame_number = -1;
            list = getListOfNalUnits(content);
            for (size_t index = 0; index < list.size(); index++)
            {
                if (list[index].first == NaluType::kSei)
                {
                    sei_frame_number = index;
                    if (GET_CONFIG().enable_frameid_in_webrtc_stream)
                    {
                        std::vector<uint8_t> sei_frame;
                        sei_frame.insert(sei_frame.end(), buffer + list[index].second, buffer + list[index + 1].second);
                        m_currentFrameId = parseSeiFrameId(sei_frame.data(), sei_frame.size(), m_ptsFromServer, "h264");
                        int64_t current_time_ms = getCurrentUnixTimestampInMs();
                        int time_diff = current_time_ms - m_ptsFromServer/1000;
                        m_latencyVector.push_back(time_diff);

                        if (m_currentFrameId < 0 || m_ptsFromServer <= 0)
                        {
                            /* Remove sei frame from the data buffer */
                            content.erase(content.begin() + list[index].second, content.begin() + list[index + 1].second);
                            if (m_ptsFromServer <= 0)
                            {
                                gettimeofday(&presentationTime, nullptr);
                            }
                            break;
                        }

                        /* Use the time from edge device for rtsp server consumer */
                        presentationTime.tv_sec = m_ptsFromServer / 1000000;
                        presentationTime.tv_usec = m_ptsFromServer % 1000000;
                        use_edge_device_time = true;

                        if (m_csvFile.is_open())
                        {
                            struct timeval timeNow;
                            gettimeofday(&timeNow, nullptr);
                            long elapsed_time = timevaldiff(m_prevDumpTime, timeNow);
                            if (elapsed_time >= WEBRTC_INPUT_LATENCY_DUMP_INTERVAL_SEC * 1000 * 1000)
                            {
                                int latency_sum = 0;
                                for (size_t j = 0; j < m_latencyVector.size(); j++)
                                {
                                    latency_sum += m_latencyVector[j];
                                }
                                int avg_latency = latency_sum / m_latencyVector.size();

                                m_csvFile <<m_peerId<<", "<<getCurrentTimeMS()<<", "<<m_currentFrameId<<", "<<(size-sei_frame.size())<<", "<<avg_latency;
                                if (m_prevFrameId != -1 && (m_currentFrameId - m_prevFrameId) != 1)
                                {
                                    LOG(verbose) << "#### [Receiver] Alert Missed Frames for:" << m_peerId << ", currentFrameId: " << m_currentFrameId << ", PrevFrameId:"<<m_prevFrameId<<endl;
                                    m_csvFile<<", "<<"Missed frames:"<<(m_currentFrameId - m_prevFrameId) - 1;
                                }
                                m_csvFile<<endl;
                                m_prevDumpTime = timeNow;
                                m_latencyVector.clear();
                            }
                            else if (m_prevFrameId != -1 && (m_currentFrameId - m_prevFrameId) != 1)
                            {
                                LOG(verbose) << "#### [Receiver] Alert Missed Frames for:" << m_peerId << ", currentFrameId: " << m_currentFrameId << ", PrevFrameId:"<<m_prevFrameId<<endl;
                                m_csvFile <<m_peerId<<", "<<getCurrentTimeMS()<<", "<<m_currentFrameId<<", "<<(size-sei_frame.size())<<", "<<time_diff;
                                m_csvFile<<", "<<"Missed frames:"<<(m_currentFrameId - m_prevFrameId) - 1;
                                m_csvFile<<endl;
                            }
                        }
                        m_prevFrameId = m_currentFrameId;
                    }

                    /* Remove sei frame from the data buffer */
                    content.erase(content.begin() + list[index].second, content.begin() + list[index + 1].second);
                    break;
                }
            }
            if (sei_frame_number != -1)
            {
                list[sei_frame_number + 1].second = list[sei_frame_number].second;
                list.erase(list.begin() + sei_frame_number);
            }
            if (m_consumersList.size() <= 0)
            {
                return;
            }
        }
        if (use_edge_device_time == false)
        {
            gettimeofday(&presentationTime, nullptr);
        }
    }
    else if (iequals(codec, "h265"))
    {
        nal_type = parseH265NaluType(content.data(), content.size());
        if (nal_type == H265NaluType::SPS_NUT)
        {
            h265_list = getListOfH265NalUnits(content);
        }
        gettimeofday(&presentationTime, nullptr);
    }

    std::lock_guard<std::mutex> guard(m_consumerLock);
    for (shared_ptr<IMediaDataConsumer> consumer : m_consumersList)
    {
        if (consumer->getConsumerMediaType() == MediaTypeVideo || 
            consumer->getConsumerMediaType() == MediaTypeAudioVideo)
        {
            frame_params.m_media            = "video";
            frame_params.m_codec            = codec;
            frame_params.m_needParsing      = true;
            frame_params.m_presentationTime = presentationTime;

            if (iequals(frame_params.m_codec, "h264") && nal_type == NaluType::kSps)
            {
                // Send sps, pps and idr as separate frame.
                // Assuming data is arranged as sps | pps | idr
                for (size_t index = 0; index < list.size(); index++)
                {
                    frame_params.m_buffer = content.data() + list[index].second;
                    if (index == list.size() - 1)
                    {
                        frame_params.m_size = content.size() - list[index].second;
                    }
                    else
                    {
                        frame_params.m_size = list[index + 1].second - list[index].second;
                    }
                    consumer->onFrame(frame_params);
                }
            }
            else if (iequals(frame_params.m_codec, "h265") && nal_type == H265NaluType::SPS_NUT)
            {
                // Send sps, vps, pps and idr as separate frame.
                // Assuming data is arranged as sps | vps | pps | idr
                for (size_t index = 0; index < h265_list.size(); index++)
                {
                    frame_params.m_buffer = content.data() + h265_list[index].second;
                    if (index == h265_list.size() - 1)
                    {
                        frame_params.m_size = content.size() - h265_list[index].second;
                    }
                    else
                    {
                        frame_params.m_size = h265_list[index + 1].second - h265_list[index].second;
                    }
                    consumer->onFrame(frame_params);
                }
            }
            else
            {
                frame_params.m_buffer           = content.data();
                frame_params.m_size             = content.size();
                consumer->onFrame(frame_params);
            }
        }
    }
}

typedef struct WebrtcVideoDecoderBufferData
{
    void* m_videoEncInstance;
    int m_decoderFd;
} WebrtcVideoDecoderBufferData;

static void gst_buffer_object_free_cb(gpointer data, GstMiniObject *obj)
{
    WebrtcVideoDecoderBufferData* buffer_data  = (WebrtcVideoDecoderBufferData*) data;

    if (!buffer_data)
    {
        LOG(error) << "gst_buffer_object_free_cb: buffer data is null" << endl;
        return;
    }

    GstNvVideoEncoder* encoder = (GstNvVideoEncoder*)buffer_data->m_videoEncInstance;
    if (!encoder)
    {
        LOG(error) << "gst_buffer_object_free_cb: consumer is null" << endl;
        free(buffer_data);
        return;
    }

    encoder->freeVideoFrameData(buffer_data->m_decoderFd);

    free (buffer_data);
}

void GstNvVideoEncoder::freeVideoFrameData(int fd)
{
    std::lock_guard<std::mutex> lock(m_videoFrameDataMapLock);
    std::map<int, rtc::scoped_refptr<NvVideoFrameBuffer>>::iterator it_map = m_videoFrameDataMap.find(fd);
    if (it_map != m_videoFrameDataMap.end())
    {
        m_videoFrameDataMap.erase(it_map);
    }
    else
    {
        LOG(error) << "FD: " << fd << " not found in the map" << endl;
    }
}

int GstNvVideoEncoder::onFrame(FrameParams& frame_params, const string& codec, int fps)
{
    const unsigned char* buffer = frame_params.m_buffer;
    ssize_t size = frame_params.m_size;
    bool is_zero_copy = false;
    NvBufSurface* buf_surf = nullptr;
    GstCaps* capsSrc = nullptr;
    std::string caps_string;
    int width, height, max_framerate;
    rtc::scoped_refptr<NvVideoFrameBuffer> frame_buffer = nullptr;
    rtc::scoped_refptr<webrtc::I420BufferInterface> i420_buffer = nullptr;

    if (buffer == nullptr || size == 0)
    {
        return 0;
    }
    if (m_isFatalError == true)
    {
        return FATAL_ERROR_CODE;
    }

    if (m_passThrough)
    {
        sendToConsumer(frame_params, codec);
        return 0;
    }
    {
        std::lock_guard<std::mutex> guard(m_pipelineLock);
        if (m_pipeline == nullptr || m_source == nullptr)
        {
            LOG(error) << "Pipeline is in null state" << endl;
            return FATAL_ERROR_CODE;
        }
    }

    max_framerate = GET_CONFIG().webrtc_in_max_framerate;
    if (size == sizeof(NvBufSurface))
    {
        frame_buffer = rtc::scoped_refptr<NvVideoFrameBuffer>(*((rtc::scoped_refptr<NvVideoFrameBuffer>*)buffer));
        buf_surf = (NvBufSurface *)frame_buffer->m_decodedData;
        width = buf_surf->surfaceList[0].width;
        height = buf_surf->surfaceList[0].height;
        is_zero_copy = true;

        if (width != m_width || height != m_height)
        {
            caps_string = "video/x-raw(memory:NVMM), format=(string)NV12";
            caps_string = caps_string + ", width=(int)" + to_string(width) +
                                        ", height=(int)" + to_string(height) +
                                        ", framerate=(fraction)" + to_string(max_framerate) + "/1";
            capsSrc = gst_caps_from_string (caps_string.c_str());
            g_object_set (G_OBJECT (m_videoParseOrCapsFilter),   "caps"        , capsSrc, nullptr);
            gst_caps_unref (capsSrc);
        }
    }
    else
    {
        i420_buffer = (webrtc::I420BufferInterface*)buffer;
        width  = i420_buffer->width ();
        height = i420_buffer->height();
    }

    if (width != m_width || height != m_height)
    {
        LOG(warning) << "Resolution changed, Old:" << m_width << "x" << m_height
                << ", New:" << width << "x" << height << endl;
        m_width = width;
        m_height = height;
        if (m_resetInProgress == false)
        {
            reset_pipeline();
        }
        else
        {
            /* Ignoring the frames as pipeline reset is in progress */
            LOG(warning) << "Ignoring the frames as pipeline reset is in progress device_name:" << m_deviceName << endl;
            return 0;
        }
    }

    GstBuffer *gstbuffer = nullptr;
    GstMapInfo map;
    GstMemory *mem = nullptr;

    /* Allocate a new Gst Buffer */
    gstbuffer = gst_buffer_new_allocate (nullptr, size, nullptr);

    /* Map the Gst Buffer to write the data */
    gst_buffer_map (gstbuffer, &map, GST_MAP_WRITE);

    /* Copy buffer to map, i.e. to gstbuffer */
    if (is_zero_copy)
    {
        memcpy (map.data,                                           buf_surf, sizeof(NvBufSurface));
    }
    else
    {
        /* Set height and width dynamically */
        g_object_set (G_OBJECT (m_videoParseOrCapsFilter), "width",  width , nullptr);
        g_object_set (G_OBJECT (m_videoParseOrCapsFilter), "height", height, nullptr);
        g_object_set (G_OBJECT (m_videoParseOrCapsFilter), "framerate", fps, 1, nullptr);

        /* Copy planes
        ** Y at 0th index,
        ** U at (Y + size of Y)
        ** V at (Y + size of Y + size of V)
        */
        memcpy (map.data,                                           i420_buffer->DataY(), width * height    );
        memcpy (map.data + (width * height),                        i420_buffer->DataU(), width * height / 4);
        memcpy (map.data + (width * height) + (width * height) / 4, i420_buffer->DataV(), width * height / 4);
    }

    /* Add reference timestamp meta and load the latency start time for this unique caps */
    GstCaps *caps = gst_caps_from_string("timestamp/x-test-stream");
    gst_buffer_add_reference_timestamp_meta(gstbuffer, caps,
                    (frame_params.m_latencyStartTime.tv_sec * 1000000) + frame_params.m_latencyStartTime.tv_usec,
                    GST_CLOCK_TIME_NONE);
    gst_caps_unref (caps);

    /* Unmap the Gst Buffer */
    gst_buffer_unmap (gstbuffer, &map);

    if (is_zero_copy)
    {
        mem = gst_buffer_peek_memory (gstbuffer, 0);
        struct WebrtcVideoDecoderBufferData* buffer_data = (WebrtcVideoDecoderBufferData*) malloc(sizeof (WebrtcVideoDecoderBufferData));
        buffer_data->m_videoEncInstance = this;
        buffer_data->m_decoderFd = buf_surf->surfaceList[0].bufferDesc;
        gst_mini_object_weak_ref(GST_MINI_OBJECT(mem), (GstMiniObjectNotify)gst_buffer_object_free_cb, (void*)buffer_data);

        std::lock_guard<std::mutex> lock(m_videoFrameDataMapLock);
        m_videoFrameDataMap[buffer_data->m_decoderFd] = frame_buffer;
    }

    /* Push gstBuffer to appsrc element */
    {
        std::lock_guard<std::mutex> guard(m_pipelineLock);
        if (m_source)
        {
            gst_app_src_push_buffer((GstAppSrc*)m_source, gstbuffer);
        }
    }
    return 0;
}

void GstNvVideoEncoder::addConsumer (shared_ptr<IMediaDataConsumer> consumer)
{
    std::lock_guard<std::mutex> guard(m_consumerLock);
    if (std::find(m_consumersList.begin(), m_consumersList.end(), consumer) == m_consumersList.end())
    {
        LOG(warning) << "======== Video Consumer Added ==========" << endl;
        m_consumersList.push_back(consumer);
    }
    /* To force IDR once new consumer is connected to avoid hang issue */
    intraRefreshEncoder();
}

void GstNvVideoEncoder::removeConsumer (shared_ptr<IMediaDataConsumer> consumer)
{
    std::lock_guard<std::mutex> guard(m_consumerLock);
    m_consumersList.erase(std::remove(m_consumersList.begin(), m_consumersList.end(), consumer), m_consumersList.end());
    LOG(warning) << "======== Video Consumer Removed ==========" << endl;
}

void GstNvVideoEncoder::reset_pipeline()
{
    std::lock_guard<std::mutex> guard(m_pipelineLock);
    if (m_pipeline)
    {
        LOG(info) << "Resetting Video encoder pipeline" << endl;
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
        /* pipeline will be put into PLAYING state from other thread */
        if (gstStateChangeRet == GST_STATE_CHANGE_ASYNC)
        {
            /* watching pipeline bus for below messages type */
            LOG (info) << "GST_STATE_CHANGE_ASYNC. " << endl;
            m_resetInProgress = true;
        }
        else
        {
            LOG (info) << "State change success " << endl;
        }
    }
}

void GstNvVideoEncoder::intraRefreshEncoder()
{
    std::lock_guard<std::mutex> guard(m_pipelineLock);
    if (m_pipeline && m_encoder)
    {
        g_object_set (G_OBJECT (m_encoder), "force-idr", true, nullptr);
    }
}

gboolean busWatchEncoder (GstBus *bus, GstMessage *message, gpointer decoder_data)
{
    GError *error = nullptr;
    gchar *name, *debug = nullptr;
    GstNvVideoEncoder* nvVideoEncoder = (GstNvVideoEncoder*)decoder_data;
    if (nvVideoEncoder == nullptr)
    {
        LOG(error) << "Encoder object is NULL" << endl;
        goto exit;
    }
    {
        if (message)
        {
            if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ERROR)
            {
                /* get element name from which error was triggered */
                name = gst_object_get_path_string (message->src);

                /* get actual error message and debug info */
                gst_message_parse_error (message, &error, &debug);
                if(error != nullptr && name != nullptr)
                {
                    LOG(error) << "ERROR : " <<  name << error->message << endl;
                    if (error->domain == GST_RESOURCE_ERROR || error->domain == GST_LIBRARY_ERROR)
                    {
                        LOG(error) << "######## Fatal resource/library error, Terminating the service... ###########" << endl;
                        nvVideoEncoder->m_isFatalError = true;
                        if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
                        {
                            detectGPU();
                            if (g_isGpuPresent == false)
                            {
                                LOG(error) << "---#--- /dev/nvidia node not present, Non-recoverable error ---#---" << endl;
                                std::exit(EXIT_GPU_NOT_FOUND);
                            }
                        }
                    }
                    else if (error->domain == GST_STREAM_ERROR)
                    {
                        LOG(error) << "Fatal stream error" << endl;
                        nvVideoEncoder->m_isFatalError = true;
                    }
                    else
                    {
                        // Handle non-fatal error, attempt to recover
                    }
                    g_error_free (error);
                    g_free (name);
                }
                if (debug != nullptr)
                {
                    LOG (error) << "Additional debug info: " << debug;
                    g_free (debug);
                }
                LOG (error) << "Gstreamer error occured: " <<  endl;
                goto exit;
            }
            else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_EOS)
            {
                LOG(warning) << "****** Received EOS: " << " ********"<<endl;
            }
            else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ASYNC_DONE ||
                     GST_MESSAGE_TYPE (message) == GST_MESSAGE_STATE_CHANGED)
            {
                if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ASYNC_DONE)
                {
                    LOG(info) << "Received ASYNC_DONE" <<  endl;
                    nvVideoEncoder->m_resetInProgress = false;
                }
            }
        }
        else
        {
            LOG (info) << "No message on Gstreamer Bus" << endl;
        }
    }
exit:
    return TRUE;
}

int GstNvVideoEncoder::create ()
{
    LOG(info) << "Creating GstNvVideoEncoder pipeline" << endl;
    GstBus* bus = nullptr;
    GstCaps* capsSrc = nullptr;
    GstCaps *caps_after_enc = nullptr;
    GstElement* m_capsfilterAfterEnc = nullptr;
    std::string caps_string;
    bool is_hw_accelerated = NvHwDetection::getInstance()->m_useNvV4l2Dec && GET_CONFIG().use_webrtc_hw_dec
            && NvHwDetection::getInstance()->m_useNvV4l2Enc;
    /*
        HW Pipeline
        appsrc -> videoparse -> video_conv ----------------> capsfilter -> encoder -> h264parse -> capsfilter -> appsink

        HW Pipeline that uses webrtc - NvDecoder
        appsrc -> capsfilter -> video_conv ----------------> capsfilter -> encoder -> h264parse -> capsfilter -> appsink

        SW Pipeline
        appsrc -> videoparse -> video_conv -> video_scale -> capsfilter -> encoder -> h264parse -> capsfilter -> appsink

    */
    std::lock_guard<std::mutex> guard(m_pipelineLock);
    m_pipeline       = gst_pipeline_new         ("video_encoder_pipeline");
    m_source         = gst_element_factory_make ("appsrc"       , nullptr);
    if (is_hw_accelerated)
    {
        m_videoParseOrCapsFilter = gst_element_factory_make ("capsfilter"   , nullptr);
    }
    else
    {
        m_videoParseOrCapsFilter  = gst_element_factory_make ("videoparse"   , nullptr);
    }
    m_capsfilterA    = gst_element_factory_make ("capsfilter"   , nullptr);
    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == false)
    {
        m_conv       = gst_element_factory_make ("videoconvert"    , nullptr);
        m_scale      = gst_element_factory_make ("videoscale"    , nullptr);
        m_encoder    = gst_element_factory_make ("x264enc", nullptr);
        if (!m_scale)
        {
            LOG (error) << "Gstreamer Video Encoder videoscale element creation failed" << endl;
            return -1;
        }
    }
    else
    {
        m_conv       = gst_element_factory_make ("nvvideoconvert"    , nullptr);
        // If nvvideoconvert element creation failed, use nvvidconv instead eg: in T26x platforms
        if (!m_conv)
        {
            LOG (info) << "Gstreamer Video Encoder nvvideoconvert element creation failed, using nvvidconv instead" << endl;
            m_conv = gst_element_factory_make ("nvvidconv"    , nullptr);
        }
        m_encoder    = gst_element_factory_make ("nvv4l2h264enc", nullptr);
    }
    m_parser         = gst_element_factory_make ("h264parse"    , nullptr);
    m_capsfilterAfterEnc  = gst_element_factory_make ("capsfilter" , nullptr);
    m_sink           = gst_element_factory_make ("appsink"      , nullptr);

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_conv || !m_videoParseOrCapsFilter || !m_capsfilterA
        || !m_encoder || !m_capsfilterAfterEnc || !m_parser || !m_sink)
    {
        LOG (error) << "Gstreamer Video Encoder element creation failed" << endl;
        return -1;
    }

    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_videoParseOrCapsFilter,
            m_conv, m_capsfilterA, m_encoder, m_parser,
            m_capsfilterAfterEnc, m_sink, nullptr);
    if (!gst_element_link_many (m_source, m_videoParseOrCapsFilter, m_conv, nullptr))
    {
        LOG (error) << "Video Encoder: elements could not be linked" << endl;
        return -1;
    }

    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
    {
        g_object_set (G_OBJECT (m_encoder), "gpu-id"   , g_gpuIndex, nullptr);
        g_object_set (G_OBJECT (m_conv), "gpu-id"   , g_gpuIndex, nullptr);
        g_object_set (G_OBJECT (m_conv), "compute-hw"  , 1      , nullptr);
    #ifdef AARCH64_PLATFORM
        g_object_set (G_OBJECT (m_encoder), "insert-sps-pps", true, nullptr);
    #endif
        if (!gst_element_link_many (m_conv, m_capsfilterA, nullptr))
        {
            LOG (error) << "Video Encoder: elements could not be linked" << endl;
            return -1;
        }
    }
    else
    {
        gst_bin_add_many (GST_BIN (m_pipeline), m_scale, nullptr);
        if (!gst_element_link_many (m_conv, m_scale, m_capsfilterA, nullptr))
        {
            LOG (error) << "Video Encoder: elements could not be linked" << endl;
            return -1;
        }
    }

    if (!gst_element_link_many (m_capsfilterA, m_encoder, m_parser, m_capsfilterAfterEnc, m_sink, nullptr))
    {
        LOG (error) << "Video Encoder: elements could not be linked" << endl;
        return -1;
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
#endif
    
    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this))
    {
        LOG(error) << "Video Encoder: Error in g_signal_connect of new-sample" << endl;
    }

    int max_framerate = GET_CONFIG().webrtc_in_max_framerate;
    if (is_hw_accelerated)
    {
        caps_string = "video/x-raw(memory:NVMM), format=(string)NV12";
        caps_string = caps_string + ", width=(int)" + to_string(WIDTH_720p) +
                                    ", height=(int)" + to_string(HEIGHT_720p) +
                                    ", framerate=(fraction)" + to_string(max_framerate) + "/1";
        LOG(info) << "App src caps = " << caps_string << endl;
        capsSrc = gst_caps_from_string (caps_string.c_str());
        g_object_set (G_OBJECT (m_videoParseOrCapsFilter), "caps"        , capsSrc, nullptr);
    }
    else
    {
        /* Set properties for video-parse element */
        g_object_set (G_OBJECT (m_videoParseOrCapsFilter), "width"       ,  WIDTH_720p  , nullptr);
        g_object_set (G_OBJECT (m_videoParseOrCapsFilter), "height"      ,  HEIGHT_720p  , nullptr);
        g_object_set (G_OBJECT (m_videoParseOrCapsFilter), "framerate"   ,  max_framerate  , 1, nullptr);
    }

    /* Set caps on output of nvvidconv */
    Resolution resolution;
    resolution = GET_CONFIG().webrtc_in_fixed_resolution;

    caps_string = "video/x-raw(memory:NVMM), format=(string)NV12";
    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == false)
    {
        caps_string = "video/x-raw, format=(string)NV12";
    }
    if (!resolution.empty())
    {
        m_width = stringToInt(resolution.width, DEFAULT_ENCODER_WIDTH);
        m_height = stringToInt(resolution.height, DEFAULT_ENCODER_HEIGHT);
        caps_string = caps_string + ", width=(int)" + to_string(m_width) +
                                    ", height=(int)" + to_string(m_height);
    }
    LOG(info) << "Encoder Input Caps = " << caps_string << endl;
    capsSrc = gst_caps_from_string (caps_string.c_str());
    g_object_set (G_OBJECT (m_capsfilterA),   "caps"        , capsSrc, nullptr);

    /* set properties on sink element */
    g_object_set (G_OBJECT (m_sink),          "emit-signals", TRUE   , nullptr);
    g_object_set (G_OBJECT (m_sink),          "sync"        , FALSE  , nullptr);

    caps_after_enc = gst_caps_new_simple ("video/x-h264",
                    "stream-format", G_TYPE_STRING, "byte-stream",
                    "alignment", G_TYPE_STRING, "nal",
                    nullptr);
    g_object_set (G_OBJECT (m_capsfilterAfterEnc), "caps", caps_after_enc, nullptr);
    gst_caps_unref (caps_after_enc);    
    gst_caps_unref (capsSrc);

    bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!bus)
    {
        LOG(error) << "Failed to get BUS of Encoder pipeline" << endl;
        return -1;
    }
    m_busWatchId = gst_bus_add_watch (bus, busWatchEncoder, (void*)this);
    gst_object_unref(bus);

    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    LOG(info) << "Created GstNvVideoEncoder pipeline" << endl;
    return 0;
}

void GstNvVideoEncoder::destroy(bool expect_result)
{
    LOG(info) << "Destroying Video Encoder Pipeline" << endl;

    std::lock_guard<std::mutex> guard(m_pipelineLock);

    if (m_busWatchId != G_MAXUINT)
    {
        g_source_remove (m_busWatchId);
        m_busWatchId = G_MAXUINT;
    }

    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }
    /* Notify EOS to media source */
    if (m_consumersList.size() > 0)
    {
        std::lock_guard<std::mutex> guard(m_consumerLock);
        for (shared_ptr<IMediaDataConsumer> consumer : m_consumersList)
        {
            if (consumer->getConsumerMediaType() == MediaTypeVideo)
            {
                LOG(warning) << "Sending EOS for Video" << endl;
                FrameParams frame_params;
                frame_params.m_buffer  = nullptr;
                frame_params.m_size    = 0;
                consumer->onFrame(frame_params);
            }
        }
    }
    m_consumersList.clear();
    LOG(info) << "Destroyed Video Encoder Pipeline" << endl;
}
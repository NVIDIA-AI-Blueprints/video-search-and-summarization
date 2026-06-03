/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <cmath>
#include <glib/gstdio.h>
#include <chrono>
#include <errno.h>
#include <stdexcept>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include "gstnvvideodecoder.h"
#include "modules/video_coding/include/video_error_codes.h"
#include "config.h"
#include "storage_management.h"
#include "unified_storage_reader.h"
#include "unified_storage_reader_factory.h"
#include "unified_storage_types.h"
#include "CivetServer.h"
#include "profiler.h"
#include "nvbufwrapper.h"
#include "gst_utils.h"
#include "webrtcstreamproducer.h"
#include "gstnvvstmeta.h"
#include "redis/redis_subscriber.h"
#include "vst_common.h"
#include "Websocket.h"
#include "unified_storage_reader_utils.h"
#include "fs_utils.h"
#include "ReplayPeerConnection.h"
#include "s3stream_producer.h"  // For CloudStreamProducer::seek()

using namespace std;
using namespace nv_vms;
using namespace std::chrono_literals;

constexpr int OSD_PROCESS_MODE = 1;
constexpr int OSD_DISPLAY_TEXT = 1;
constexpr int MUXER_BATCH_TIMEOUT_USEC = 40000;
constexpr double DEFAULT_FRAME_RATE = 30.0;
constexpr int DEFAULT_GOV_LENGTH = 60;
constexpr int DEFAULT_FRAME_WIDTH = 1920;
constexpr int DEFAULT_FRAME_HEIGHT = 1080;
constexpr auto MAX_BUFFER_WAIT_TIMEOUT = 15s;
constexpr int MAX_FRAMES_FOR_LATENCY = 30;
constexpr auto DEFAULT_FRAME_LATENCY = (1000 / DEFAULT_FRAME_RATE) * MAX_FRAMES_FOR_LATENCY;

constexpr const char* GST_CAPS_FEATURES_NVMM = "memory:NVMM";
constexpr int CONFIRM_DEC_OUT_FRAMES = 5;
constexpr int GST_DEBUG_PROBE_BUFFER_COUNT = 10;
const auto MAX_SEGMENT_DURATION = 60 * GST_SECOND;

#define SET_FILE_SOURCE(src , inFile) { \
                                        LOG(info) << "Set file location: " << inFile << endl; \
                                        g_object_set (G_OBJECT (src), "location", inFile.c_str(), nullptr); \
                                        vst_storage::addOrRemoveFileInProtectList(inFile, true); \
                                      }

constexpr auto SCHEDULER_WD_INTERVAL = 5s;

constexpr int STATE_PLAYING = 1;
constexpr int STATE_NOT_PLAYING = 0;

bool GstNvVideoDecoder::m_debug_logging_live = false;
bool GstNvVideoDecoder::m_debug_logging_vod = false;

static std::array<FrameSize, 7> g_resolutions = { FrameSize(WIDTH_2160p, HEIGHT_2160p),
                                                  FrameSize(WIDTH_1080p, HEIGHT_1080p),
                                                  FrameSize(WIDTH_720p, HEIGHT_720p),
                                                  FrameSize(WIDTH_480p, HEIGHT_480p),
                                                  FrameSize(WIDTH_360p, HEIGHT_360p),
                                                  FrameSize(WIDTH_240p, HEIGHT_240p),
                                                  FrameSize(WIDTH_144p, HEIGHT_144p)
                                                 };



static void on_pad_added (GstElement *element, GstPad *pad, void *data)
{
    GstElement *element2 = (GstElement *)data;
    GstPad *sink_pad = nullptr;
    GstCaps *caps = nullptr;
    gchar *capsString = nullptr;

    caps = gst_pad_get_current_caps (pad);
    capsString = gst_caps_to_string (caps);
    sink_pad = gst_element_get_static_pad (element2, "sink");
    LOG (info) << "Caps = " << capsString << endl;

    /* Try to link pads only if format is video */
    if (g_strrstr(capsString, "video"))
    {
        /* Check if sink_pad exists */
        if (!sink_pad)
        {
            LOG(error) << "Failed to get sink pad of element." << endl;
            GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to get sink pad of element."), (nullptr));
            if (caps != nullptr)
            {
                gst_caps_unref (caps);
            }
            goto _exit;
        }
        /* Check if pads can be linked */
        if (gst_pad_link (pad, sink_pad) != GST_PAD_LINK_OK)
        {
            LOG(error) << "Failed to link elements in pad-added callback. = " << sink_pad << endl;
            GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to link elements in pad-added callback"), (nullptr));
        }
    }
    /* Unref the data structure */
    if (sink_pad)
    {
        gst_object_unref (sink_pad);
    }
    if (caps != nullptr)
    {
        gst_caps_unref (caps);
    }
_exit:
    g_free(capsString);
}

void on_pad_added2 (GstElement *element1, GstPad *pad, gpointer data)
{
    GstElement *element2 = (GstElement *)data;
    GstPad *sink_pad = gst_element_get_static_pad (element2, "sink");
    if (sink_pad)
    {
        if (GST_PAD_LINK_OK != gst_pad_link (pad, sink_pad))
        {
            LOG(error) << "Could not link pads \n" << endl;
        }
        /* Unref the data structure */
        gst_object_unref (sink_pad);
    }
    else
    {
        LOG(error) << "sink pad is NULL" << endl;
    }
}

static GstElement* make_floor_map_nv_converter()
{
    GstElement* nv = gst_element_factory_make("nvvideoconvert", "nvconverter");
    if (!nv) {
        nv = gst_element_factory_make("nvvidconv", "nvconverter");
    }
    if (!nv) {
        LOG(error) << "make_floor_map_nv_converter: failed to create nvvideoconvert/nvvidconv" << endl;
        return nullptr;
    }
    // Set compute-hw=GPU for nvvideoconvert/nvvidconv
    g_object_set(G_OBJECT(nv), "compute-hw", 1, NULL);
    LOG(info) << "Floor map pipeline: video converter compute-hw=GPU" << endl;
    return nv;
}

/* called when the appsink notifies us that there is a new buffer ready for
 * processing */
static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, GstNvVideoDecoder* nvVideoDecoder)
{
   if (nvVideoDecoder)
    {
        if (nvVideoDecoder->m_isImageCapture)
        {
            return nvVideoDecoder->processJpegImageFromSink(appsink);
        }
        else
        {
            return nvVideoDecoder->processNewSampleFromSink(appsink);
        }
    }
   return GST_FLOW_ERROR;
}

static GstPadProbeReturn pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    gboolean res = false;
    GstEvent *event = nullptr;
    event = GST_PAD_PROBE_INFO_EVENT(info);
    if (event)
    {
        if (GST_EVENT_CAPS == GST_EVENT_TYPE(event))
        {
            GstCaps * caps;
            int width, height;
            gst_event_parse_caps(event, &caps);

            GstStructure *gstStruct = gst_caps_get_structure(caps, 0);
            if (gstStruct)
            {
                res = gst_structure_get_int (gstStruct, "width", &width);
                res |= gst_structure_get_int (gstStruct, "height", &height);
                if (!res)
                {
                    LOG (error) << "No resolution information received";
                }
                GstNvVideoDecoder* decoder = (GstNvVideoDecoder*) user_data;
                LOG (info) << "Resolution information received: Frame Size: "<< width << "x" << height << endl;
                decoder->setSourceFrameSize(width, height);
            }
            else
            {
                LOG (error) << "gst_caps_get_structure failed" << endl;
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

static bool compare(const VideoFileInfo &a, const VideoFileInfo &b)
{
    return a < b;
}

#ifdef DUMP_INPUT_NALS
static void dump_input_stream(std::vector<uint8_t> content)
{
    static int frame_count = 0;
    static ofstream dump_file;
    if (frame_count > 100)
    {
        return;
    }
    if (frame_count == 0)
    {
        LOG(info) << "Opening out.h264" << endl;
        dump_file.open ("out.h264", ios::out | ios::binary);
        if(!dump_file.is_open())
        {
            frame_count = 101;
        }
    }
    if (dump_file.is_open() && (frame_count == 100))
    {
        LOG(info) << "Closing out.h264" << endl;
        dump_file.close();
        frame_count ++;
    }
    else if (dump_file.is_open())
    {
        dump_file.write((char*)content.data(), content.size());
        LOG(info) << "contant data: " << content.size() << "frame count : " << frame_count << endl;
        frame_count ++;
    }
}
#endif
void GstNvVideoDecoder::setConsumer(const string& peerid, std::shared_ptr<IMediaDataConsumer> consumer)
{
    /* Add Consumer to map with consumer_id = "VIDEO_TYPE" + peerID */
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if(it == m_videoSinkList.end())
    {
        shared_ptr<VideoSinkInfo> sink (new VideoSinkInfo);
        sink->m_consumer = consumer;
        sink->m_consumer->setOriginalFrameSize(m_decoderWidth, m_decoderHeight);
        m_videoSinkList[peerid] = sink;
    }
    else
    {
        LOG(error) << "Consumer already exists for " << peerid << endl;
    }

    if (peerid == "image_capture")
    {
        std::shared_ptr<VideoSinkInfo> sink = m_videoSinkList[peerid];
        FrameSize size;
        size.m_width = m_resizeWidth ? m_resizeWidth : m_decoderWidth;
        size.m_height = m_resizeHeight ? m_resizeHeight : m_decoderHeight;
        sink->m_frameSize = size;
        sink->m_decoderStats.clear();
        m_videoSinkList[peerid] = sink;
    }
    LOG(info) << "Sink list size = " << m_videoSinkList.size() << " for " << m_uri << endl;
}

void GstNvVideoDecoder::setConsumerReady(const string& peerid, bool is_ready)
{
    /* search peer in map to set start play flag */
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSinkInfo> sink = it->second;
        sink->m_isSinkReady = is_ready;
        sendStateChangeWebSocketMessage(peerid, is_ready);
    }
}

void GstNvVideoDecoder::onFrame(FrameParams& params)
{
    std::vector<uint8_t> content;
    guint64 current_level;

    LOG(verbose2) << "onFrame media:"<< params.m_media << ", codec:"
                  << params.m_codec << ", size:" << params.m_size << endl;

    if (params.m_media == "audio")
    {
        return;
    }
    if (params.m_needParsing)
    {
        content = parseAndCreateFrame(params);
        if(m_stop || content.size() == 0 || m_startConsuming == false)
        {
            return;
        }
    }
    else
    {
        content.insert(content.end(), params.m_buffer, params.m_buffer + params.m_size);
    }
    m_codec = params.m_codec;

    uint64_t ts;
    if (params.m_serverFrameId != -1)
    {
        ts = params.m_serverPts / 1000;
    }
    else
    {
        ts = params.m_presentationTime.tv_sec;
        ts = ts * 1000 + params.m_presentationTime.tv_usec / 1000;
    }

    if (!m_firstFrameTS && ts)
    {
        m_firstFrameTS = ts;
        LOG(warning) << "firstFrameTS: " << m_firstFrameTS << ", size:" << params.m_size << endl;
    }

    g_object_get ((GstAppSrc*)m_source, "current-level-bytes", &current_level, nullptr);
    LOG(verbose2) << "appsrc : current_level = " << current_level << endl;

    /* Push the Gst Buffer in pipeline */
    pushBufferToDecoder(content.data(), content.size(), params.m_serverFrameId, ts);
    return;
}

/**
 * Handle frames from ClipReaderProducer (for local file image capture).
 * ClipReaderProducer outputs parsed byte-stream H.264/H.265 data.
 * This method pushes the data to the appsrc for decoding.
 */
void GstNvVideoDecoder::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    if (!frame_data || m_stop)
    {
        return;
    }

    // Get the buffer from the frame data
    GstBuffer* buffer = frame_data->m_gstBuffer;
    if (!buffer)
    {
        LOG(warning) << "onFrame(RawFrameParams): No buffer in frame data" << endl;
        return;
    }

    // Get buffer info
    GstMapInfo map;
    if (!gst_buffer_map(buffer, &map, GST_MAP_READ))
    {
        LOG(error) << "onFrame(RawFrameParams): Failed to map buffer" << endl;
        return;
    }

    // Get PTS from frame data (already in milliseconds from ClipReaderProducer)
    uint64_t pts_ms = frame_data->pts;
    
    LOG(verbose) << "onFrame: Received frame, size=" << map.size 
                 << ", pts=" << pts_ms << " ms" << endl;

    // Push to decoder using existing method
    // For producer-based sources, PTS is already in absolute time (ms)
    pushBufferToDecoder(map.data, map.size, 0, pts_ms);

    gst_buffer_unmap(buffer, &map);
}

void GstNvVideoDecoder::pushBufferToDecoder(const unsigned char *buffer, ssize_t size, int64_t id, uint64_t ts)
{
    MEASURE_FUNCTION_EXECUTION_TIME
    GstBuffer *gstbuffer = nullptr;
    GstMapInfo map;

    /* Allocate a new Gst Buffer */
    gstbuffer = gst_buffer_new_allocate (nullptr, size, nullptr);

    /* Map the Gst Buffer to write the data */
    gst_buffer_map (gstbuffer, &map, GST_MAP_WRITE);

    memcpy (map.data, (uint8_t*)buffer, size);
    map.size = size;

    /* Unmap the Gst Buffer */
    gst_buffer_unmap (gstbuffer, &map);

    /* Assign PTS and DTS to Gst Buffer */
    if (m_isCloudStream)
    {
        GST_BUFFER_PTS (gstbuffer) = ts * 1000;
        GST_BUFFER_DTS (gstbuffer) = ts * 1000;
    }
    else
    {
        GST_BUFFER_PTS (gstbuffer) = getTimestampInNanoSecond(ts);
        GST_BUFFER_DTS (gstbuffer) = GST_BUFFER_PTS (gstbuffer);
    }

    /* Add VST metadata to Gst Buffer */
    GstNvVstMeta *meta = GST_NV_VST_META_ADD (gstbuffer);
    if (meta)
    {
        meta->pts = getTimestampInNanoSecond(ts);
        meta->id = id;
        meta->ts = std::chrono::duration_cast<std::chrono::microseconds>
            (std::chrono::system_clock::now().time_since_epoch()).count();
    }
    if (m_perfLogging)
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);

        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
        for(it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
        {
            shared_ptr<VideoSinkInfo> sink = it->second;
            sink->m_consumer->startStatsProcessing();
        }
    }

#ifdef DUMP_INPUT_NALS
    dump_input_stream(content);
#endif

    /* Push the Gst Buffer in pipeline */
    gst_app_src_push_buffer((GstAppSrc*)m_source, gstbuffer);
    return;
}

VmsErrorCode GstNvVideoDecoder::controlStream (const std::string& action, const std::string& seek_value)
{
    if (!m_isSeeking)
    {
        std::shared_ptr<DecoderData> data(new DecoderData);
        std::shared_ptr<DecoderOutData> out_data (new DecoderOutData);
        data->m_outResult = out_data;
        data->m_expectResult = true;
        data->m_taskName = "control";
        data->m_msgId = m_peerid;
        data->actions["action"] = action;
        data->actions["seek_value"] = seek_value;
        data->actions["eos"] = "false";
        m_isSeeking = true;
        m_eventLoop.postMsg(data);

        VmsErrorCode ret = getCameraErrorCode(out_data->data["error"]);
        return ret;
    }
    else
    {
        string msg = "Pipeline is currently seeking, ignoring this seek operation";
        LOG(error) << msg << endl;
        return VmsErrorCode::VMSInternalError;
    }
}

gboolean busWatch (GstBus *bus, GstMessage *message, gpointer decoder_data)
{
    GError *error = nullptr;
    gchar *name, *debug = nullptr;
    GstNvVideoDecoder* nvVideoDecoder = (GstNvVideoDecoder*)decoder_data;
    if (nvVideoDecoder == nullptr)
    {
        LOG(error) << "Decoder object is NULL" << endl;
        goto exit;
    }
    {
        if (message)
        {
            if (nvVideoDecoder->m_stop)
            {
                goto exit;
            }
            else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ERROR)
            {
                /* get element name from which error was triggered */
                name = gst_object_get_path_string (message->src);

                /* get actual error message and debug info */
                gst_message_parse_error (message, &error, &debug);
                if(error != nullptr && name != nullptr)
                {
                    LOG(error) << "ERROR : " <<  name << error->message << " : " << nvVideoDecoder->m_peerid << endl;
                }
                if (debug != nullptr)
                {
                    LOG (error) << "Additional debug info: " << debug;
                    g_free (debug);
                }
                LOG (error) << "Gstreamer error occured: " <<  endl;

                bool isFatalError = false;
                /* Check for fatal resource/library errors before attempting recovery */
                if (error != nullptr && (error->domain == GST_RESOURCE_ERROR || error->domain == GST_LIBRARY_ERROR))
                {
                    LOG(error) << "######## Fatal resource/library error, Terminating the service... ###########" << endl;
                    isFatalError = true;
                    if (NvHwDetection::getInstance()->m_useNvV4l2Dec)
                    {
                        detectGPU();
                        if (!g_isGpuPresent)
                        {
                            LOG(error) << "---#--- /dev/nvidia node not present, Non-recoverable error ---#---" << endl;
                            std::exit(EXIT_GPU_NOT_FOUND);
                        }
                    }
                }

                if (error != nullptr)
                {
                    g_error_free (error);
                }
                if (name != nullptr)
                {
                    g_free (name);
                }

                /* TODO: This will handle any errors, this WAR was added to handle matroskademux error */
                if (!isFatalError)
                {
                    if (nvVideoDecoder->m_resetAttempts > 0)
                    {
                        nvVideoDecoder->m_resetAttempts--;
                        LOG(error) << "Resetting pipeline due to non-fatal error, attempts remaining: " << nvVideoDecoder->m_resetAttempts << endl;
                        nvVideoDecoder->controlStream("reset_pipeline", "");
                    }
                    else
                    {
                        LOG(error) << "Max reset attempts reached, not resetting pipeline" << endl;
                    }
                }
                else
                {
                    LOG(error) << "Terminating the service due to fatal error" << endl;
                }
                goto exit;
            }
            else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_EOS)
            {
                LOG(warning) << "****** Received EOS: " <<  nvVideoDecoder->m_peerid << " ********"<<endl;
                if(nvVideoDecoder->m_recordedPlayback)
                {
                        std::shared_ptr<DecoderData> data(new DecoderData);
                        data->m_taskName = "control";
                        data->m_msgId = nvVideoDecoder->m_peerid;
                        data->actions["eos"] = "true";
                        EventLoop& loop = nvVideoDecoder->getEventLoop();
                        loop.postMsg(data);
                }
                else
                {
                    std::shared_ptr<DecoderData> data(new DecoderData);
                    data->m_taskName = "stop";
                    data->m_msgId = nvVideoDecoder->m_peerid;
                    EventLoop& loop = nvVideoDecoder->getEventLoop();
                    loop.postMsg(data);
                    goto exit;
                }
            }
            else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ASYNC_DONE ||
                     GST_MESSAGE_TYPE (message) == GST_MESSAGE_STATE_CHANGED)
            {
                if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ASYNC_DONE)
                {
                    LOG(info) << "Received ASYNC_DONE" <<  nvVideoDecoder->m_peerid << endl;
                    std::shared_ptr<DecoderData> data(new DecoderData);
                    data->m_taskName = "get_state";
                    data->m_msgId = nvVideoDecoder->m_peerid;
                    EventLoop& loop = nvVideoDecoder->getEventLoop();
                    loop.postMsg(data);
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

void process_dec_message(std::shared_ptr<EventLoopData> data, void* parent)
{
    shared_ptr<DecoderData> dec_data = std::static_pointer_cast<DecoderData>(data);
    GstNvVideoDecoder* dec = static_cast <GstNvVideoDecoder*>(parent);
    if (dec_data == nullptr || dec == nullptr)
    {
        LOG(error) << "Received null data" << endl;
        return;
    }
    LOG(verbose) << dec_data->m_taskName << endl;
    if (dec_data->m_taskName == "create")
    {
        int ret = 0;
        if (dec->m_recordedPlayback)
        {
            ret = dec->create_recorded_internal();
        }
        else if (dec->m_hlsPlayback)
        {
            ret = dec->create_hls_internal();
        }
        else if (dec->m_godsEyeView)
        {
            ret = dec->createJpegDecoderPipeline(dec->m_uri);
        }
        else
        {
            ret = dec->create_internal();
        }
        shared_ptr<DecoderOutData> out_data = std::static_pointer_cast<DecoderOutData>(dec_data->m_outResult);
        if (out_data)
        {
            out_data->result = ret;
        }
    }
    else if (dec_data->m_taskName == "play")
    {
        dec->play_internal();
    }
    else if (dec_data->m_taskName == "pause")
    {
        dec->pause_internal();
    }
    else if (dec_data->m_taskName == "control")
    {
        dec->m_isSeeking = true;
        dec->m_action = dec_data->actions["action"];
        dec->m_seekValue = dec_data->actions["seek_value"];
        bool eos = dec_data->actions["eos"] == "true" ? true : false;
        if ((dec->m_action == "seek_forward" || dec->m_action == "seek_backward")
            && dec->m_seekValue != "unknown" && dec->m_seekValue != "")
        {
            dec->m_action = dec->m_action + "_custom";
        }
        VmsErrorCode ret = dec->update(dec->m_action, dec->m_seekValue, eos);
        if (ret == VmsErrorCode::VMSInternalError)
        {
            dec->destroy();
        }
        shared_ptr<DecoderOutData> out_data = std::static_pointer_cast<DecoderOutData>(data->m_outResult);
        if (out_data.get())
        {
            out_data->data["error"] = getCameraErrorCodeString(ret).first;
        }
    }
    else if (dec_data->m_taskName == "get_state")
    {
        dec->getstate_internal();
    }
    else if (dec_data->m_taskName == "stop")
    {
        dec->stop_internal();
    }
    else if (dec_data->m_taskName == "destroy")
    {
        dec->destroy_internal();
    }
    else if (dec_data->m_taskName == "get_position")
    {
        std::shared_ptr<gint64> position = std::make_shared<gint64> (0);
        bool ret = dec->getPosition_internal(position.get());
        if (ret == false)
        {
            *position = 0;
        }
        dec_data->m_outResult->m_outData = std::static_pointer_cast<void>(position);
    }
    else if (dec_data->m_taskName == "get_duration")
    {
        std::shared_ptr<gint64> duration = std::make_shared<gint64> (0);
        bool ret = dec->getDuration_internal(duration.get());
        if (ret == false)
        {
            *duration = 0;
        }
        dec_data->m_outResult->m_outData = std::static_pointer_cast<void>(duration);
    }
    else if (dec_data->m_taskName == "get_playlist_path")
    {
        std::pair <std::string, std::string> url_path = dec->getUrlPath_internal();
        shared_ptr<DecoderOutData> out_data = std::static_pointer_cast<DecoderOutData>(data->m_outResult);
        out_data->data["first"] = url_path.first;
        out_data->data["second"] = url_path.second;
    }
    else
    {
        LOG(warning) << "Invalid action" << endl;
    }
}

static GstPadProbeReturn appsrc_src_pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    GstNvVideoDecoder* nvVideoDecoder = (GstNvVideoDecoder*)user_data;
    if (nvVideoDecoder)
    {
        nvVideoDecoder->m_appsrc_out_probe_count++;
        if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
        {
            if (nvVideoDecoder->m_appsrc_out_probe_count <= GST_DEBUG_PROBE_BUFFER_COUNT)
            {
                LOG(info) << " [appsrc-output-probe] Received buffer:" << nvVideoDecoder->m_appsrc_out_probe_count << endl;
            }
            else
            {
                /* Remove probe, as we don't need it anymore */
                return GST_PAD_PROBE_REMOVE;
            }
        }
    }
    return GST_PAD_PROBE_OK;
}


static GstElement* createParseElement(const string& codec )
{
    GstElement* parser = nullptr;
    if (iequals(codec, "H264"))
    {
        parser         = gst_element_factory_make ("h264parse", nullptr);
        LOG(info) << "Selecting h264parse" << endl;
    }
    else if (iequals(codec, "H265"))
    {
        parser         = gst_element_factory_make ("h265parse", nullptr);
        LOG(info) << "Selecting h265parse" << endl;
    }
    return parser;
}

static bool link_decoder(GstElement* decoder, GstElement* element)
{
    if (!gst_element_link_many (decoder, element, nullptr))
    {
        if (!g_signal_connect (decoder, "pad-added", G_CALLBACK (on_pad_added), element))
        {
            return false;
        }
    }
    return true;
}

int GstNvVideoDecoder::create(bool blocking)
{
    int ret = -1;
    m_eventLoop.setParent(this);
    m_decOutFrames = 0;
    std::shared_ptr<DecoderData> in_data(new DecoderData);
    std::shared_ptr<DecoderOutData> out_data (new DecoderOutData);
    out_data->result = 0;
    in_data->m_taskName = "create";
    in_data->m_msgId = m_peerid;
    if (blocking)
    {
        in_data->m_outResult = out_data;
        in_data->m_expectResult = true;
    }
    if (m_eventLoop.postMsg(in_data))
    {
        ret = out_data->result;
    }
    return ret;
}

int GstNvVideoDecoder::create_hls_internal()
{
    MEASURE_FUNCTION_EXECUTION_TIME_WITH_TAG(m_peerid)
    GstBus* bus = nullptr;
    m_error = false;
    GstElement* parser     = nullptr;
    GstElement* mpegtsmux  = nullptr;
    GstElement* sink       = nullptr;

    LOG (info) << "Creating Gstreamer HLS pipeline"  << m_uri << endl;
    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    m_pipeline     = gst_pipeline_new ("pipeline");
    m_source       = gst_element_factory_make ("appsrc", nullptr);
    parser         = createParseElement(m_codec);
    mpegtsmux      = gst_element_factory_make ("mpegtsmux", nullptr);
    sink           = gst_element_factory_make ("hlssink", nullptr);

    if (!m_pipeline || !m_source || !parser || !mpegtsmux || !sink)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return -1;
    }

    /* Add Elements in pipeline */
    gst_bin_add_many (GST_BIN (m_pipeline), m_source, parser, mpegtsmux, sink, nullptr);

    if (!gst_element_link_many (m_source, parser, mpegtsmux, sink, nullptr))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return -1;
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
#endif
    string webroot_path = "webroot/";
    string hls_dir_path = string("hls/") + m_sensorName +  string("/");
    m_urlPath.first     = webroot_path + hls_dir_path;

    if (createDir(m_urlPath.first) == false)
    {
        LOG(error) << "Cannot create directory, using default directory of webroot" << endl;
        m_urlPath.first = "webroot/";
    }

    m_urlPath.second = hls_dir_path + m_sensorName + ".m3u8";
    string playlist_location = webroot_path + m_urlPath.second;
    std::string location = m_urlPath.first + m_sensorName + "_segment_%05d.ts";

    /* Setting properties of elements */
    g_object_set (m_source, "format", 3, nullptr);
    g_object_set (sink, "playlist-location", playlist_location.c_str(), nullptr);
    g_object_set (sink, "location", location.c_str(), nullptr);
    g_object_set (sink, "max-files", 20, nullptr);
    g_object_set (sink, "target-duration", 5, nullptr);
    g_object_set (sink, "playlist-length", 20, nullptr);


    bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!bus)
    {
        LOG(error) << "Failed to get BUS of Decoder pipeline" << endl;
        goto failure;
    }
    m_bus_watch_id = gst_bus_add_watch (bus, busWatch, (void*)this);
    gst_object_unref(bus);

    LOG (info) << "Gstreamer HLS pipeline is created: " << m_uri << endl;
    return 0;

failure:
    m_error = true;
    return -1;
}

std::pair <std::string, std::string> GstNvVideoDecoder::getUrlPath_internal()
{
    return m_urlPath;
}

std::pair <std::string, std::string> GstNvVideoDecoder::getUrlPath()
{
    std::shared_ptr<DecoderData> in_data(new DecoderData);
    in_data->m_taskName = "get_playlist_path";
    in_data->m_msgId = m_peerid;
    std::shared_ptr<DecoderOutData> out_data (new DecoderOutData);
    in_data->m_outResult = out_data;
    in_data->m_expectResult = true;
    m_eventLoop.postMsg(in_data);
    std::pair <std::string, std::string> out_url;
    const string first = out_data->data["first"];
    const string second = out_data->data["second"];
    out_url.first = first;
    out_url.second = second;
    return out_url;
}

/*
======================================================================================
   Live playback without IPC

          appsrc -> decodebin -> appsink

======================================================================================
   Live playback with IPC
                                        -> queue_appsink ------------------> app_sink
                                        |
          appsrc -> decodebin -> tee ->
                                        |
                                        -> queue_ipcsink ------------------> ipc_sink
======================================================================================
*/

int GstNvVideoDecoder::create_internal()
{
    MEASURE_FUNCTION_EXECUTION_TIME_WITH_TAG(m_peerid)
    GstPad* sinkpad = nullptr;
    GstBus* bus = nullptr;
    m_error = false;
    m_frameNum  = 0;
    string stream_id = getStreamIdFromUrl(m_uri, "/live/");

#ifdef DUMP_FILE
    GstElement* videnc_bin = nullptr;
#endif

    LOG (info) << "Creating Gstreamer decode pipeline"  << m_uri << endl;
    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    m_pipeline  = gst_pipeline_new ("pipeline");
    m_source       = gst_element_factory_make ("appsrc", nullptr);
    m_sink         = gst_element_factory_make ("appsink", nullptr);
    if (!m_pipeline || !m_source || !m_sink)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return -1;
    }

    m_nvDecodeBin.reset(new NvDecodeBin(this, m_codec));
    m_decodeBin = m_nvDecodeBin->create(m_isImageCapture);
    if (!m_decodeBin)
    {
        LOG (error) << "Gstreamer element m_decodeBin creation failed" << endl;
        return -1;
    }

    if (GET_CONFIG().enable_ipc_path)
    {
        m_tee          = gst_element_factory_make ("tee", nullptr);
        m_queueAppSink = gst_element_factory_make ("queue", nullptr);
        m_queueIPCSink = gst_element_factory_make ("queue", nullptr);
        m_ipcSink      = gst_element_factory_make ("nvunixfdsink", nullptr);

        if (!m_tee || !m_ipcSink || !m_queueAppSink || !m_queueIPCSink)
        {
            LOG (error) << "Gstreamer element creation failed" << endl;
            return -1;
        }
        std::string socket_name = GET_CONFIG().ipc_socket_path + stream_id;
        g_object_set (m_ipcSink, "socket-path"          , socket_name.c_str()                         , nullptr);
        g_object_set (m_ipcSink, "buffer-copy"          , GET_CONFIG().ipc_sink_buffer_copy           , nullptr);
        g_object_set (m_ipcSink, "buffer-timestamp-copy", GET_CONFIG().ipc_sink_buffer_timestamp_copy , nullptr);
        g_object_set (m_ipcSink, "compute-hw"           , 1                                           , nullptr);
        g_object_set (m_ipcSink, "async"                , false                                       , nullptr);
        g_object_set (m_ipcSink, "sync"                 , false                                       , nullptr);

        gst_bin_add_many (GST_BIN (m_pipeline), m_tee, m_queueAppSink, m_queueIPCSink, m_ipcSink, nullptr);
    }
    /* Add Elements in pipeline */
    {
        gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_decodeBin, m_sink, nullptr);
    }
    if (!gst_element_link_many (m_source, m_decodeBin, nullptr))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return -1;
    }

    if (GET_CONFIG().enable_ipc_path)
    {
        if (!gst_element_link_many (m_queueAppSink, m_sink, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
        if (!gst_element_link_many (m_queueIPCSink, m_ipcSink, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
        m_teeSrcAppsink = gst_element_request_pad_simple(m_tee, "src_%u");
        GstPad *queue_appsink_pad  = gst_element_get_static_pad(m_queueAppSink, "sink");

        m_teeSrcIpcsink = gst_element_request_pad_simple(m_tee, "src_%u");
        GstPad *queue_ipcsink_pad  = gst_element_get_static_pad(m_queueIPCSink, "sink");

        if (m_teeSrcAppsink && queue_appsink_pad)
        {
            if (gst_pad_link(m_teeSrcAppsink, queue_appsink_pad) != GST_PAD_LINK_OK)
            {
                LOG (error) <<"Tee could not be linked to Appsink branch." << endl;
                gst_element_release_request_pad (m_tee, m_teeSrcAppsink);
                gst_object_unref (m_teeSrcAppsink);
                gst_object_unref (queue_appsink_pad);
                return -1;
            }
            gst_object_unref (queue_appsink_pad);
        }
        if (m_teeSrcIpcsink && queue_ipcsink_pad)
        {
            if (gst_pad_link(m_teeSrcIpcsink, queue_ipcsink_pad) != GST_PAD_LINK_OK)
            {
                LOG (error) <<"Tee could not be linked to IPC Sink branch." << endl;
                gst_element_release_request_pad (m_tee, m_teeSrcIpcsink);
                gst_object_unref (m_teeSrcIpcsink);
                gst_object_unref (queue_ipcsink_pad);
                return -1;
            }
            gst_object_unref (queue_ipcsink_pad);
        }
    }

#ifdef DUMP_FILE
    string dump_file_suffix = m_peerid.empty() ? getCurrentUtcTime() : m_peerid;
    string dump_file_name = "out_" + dump_file_suffix + ".mkv";
    m_videoEncodeOut.reset(new NvVideoEncodeOut);
    videnc_bin = m_videoEncodeOut->create(dump_file_name);
    if(!videnc_bin)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return -1;
    }
    gst_bin_add (GST_BIN (m_pipeline), videnc_bin);
    if (!link_decoder(m_decodeBin, videnc_bin))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return -1;
    }
    if (!gst_element_link_many (videnc_bin, m_sink, nullptr))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return -1;
    }
#else

    if (GET_CONFIG().enable_ipc_path)
    {
        if (!link_decoder(m_decodeBin, m_tee))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
    }
    else
    {
        if (!link_decoder(m_decodeBin, m_sink))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
    }
#endif

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
#endif

    /* Setting properties of elements */
    g_object_set (m_source, "format", 3, nullptr);
    g_object_set (m_source, "is-live", true, nullptr);
    if (GET_CONFIG().enable_gst_debug_probes)
    {
        GstPad* appsrc_src_pad = nullptr;
        appsrc_src_pad = gst_element_get_static_pad (GST_ELEMENT(m_source), "src");
        if (!appsrc_src_pad)
        {
            LOG(error) << "Failed to get sink pad of appsrc_src_pad" << endl;
        }
        gst_pad_add_probe(appsrc_src_pad, GST_PAD_PROBE_TYPE_BUFFER, appsrc_src_pad_cb, this, nullptr);
        gst_object_unref(appsrc_src_pad);
    }

    g_object_set (G_OBJECT (m_sink), "emit-signals", TRUE, "sync", FALSE, nullptr);

    bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!bus)
    {
        LOG(error) << "Failed to get BUS of Decoder pipeline" << endl;
        goto failure;
    }
    m_bus_watch_id = gst_bus_add_watch (bus, busWatch, (void*)this);
    gst_object_unref(bus);

    sinkpad = gst_element_get_static_pad (m_sink, "sink");
    /* Check if sink_pad exists */
    if (!sinkpad)
    {
        LOG(error) << "Failed to get sink pad of m_sink." << endl;
        goto failure;
    }

    /* Add probe to query width and height of video stream */
    gst_pad_add_probe(sinkpad, GST_PAD_PROBE_TYPE_EVENT_BOTH, pad_cb, (void*)this, nullptr);
    gst_object_unref(sinkpad);

    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this))
    {
        LOG(error) << "Error in g_signal_connect of new-sample" << endl;
        goto failure;
    }

    m_stop = false;
    LOG (info) << "Gstreamer decode pipeline is created: " << m_uri << endl;
    return 0;

failure:
    m_error = true;
    return -1;
}

int GstNvVideoDecoder::create_recorded_internal()
{
    MEASURE_FUNCTION_EXECUTION_TIME_WITH_TAG(m_peerid)
    GstPad* sinkpad = nullptr;
    GstBus* bus = nullptr;
    GstElement* demuxer    = nullptr;
    m_error = false;

    LOG (info) << "Creating Gstreamer decode pipeline"  << m_uri << endl;
    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    m_pipeline  = gst_pipeline_new         ("pipeline");
    string sourceType = (m_isCloudStream || m_isImageCapture) ? "appsrc" : "filesrc";
    m_source = gst_element_factory_make (sourceType.c_str(), nullptr);
    m_sink         = gst_element_factory_make ("appsink", nullptr);
    /* Check if any of element failed to create */
    LOG (info) << "Creating Hardware Decode pipeline " << endl;
    string container;
    if ( m_opts.find("container") != m_opts.end() )
    {
        container = m_opts.at("container");
        LOG(info) << "Container format specified: " << container << endl;
    }

    if (container.empty())
    {
        LOG(warning) << "Container format not specified, inferring from file extension" << endl;
        // Remove query parameters (startTime, endTime) from URI
        size_t query_pos = m_uri.find('?');
        string clean_uri = (query_pos != string::npos) ? m_uri.substr(0, query_pos) : m_uri;
        if (m_sensorType != SENSOR_TYPE_FILE && m_fileNameArray.size() > 0) {
            clean_uri = m_fileNameArray[0].m_filePath;
        }
        
        // Extract file extension using existing utility function
        string extension = getFileExtension(clean_uri);
        if (!extension.empty()) {
            // Remove the dot if present
            if (extension[0] == '.') {
                extension = extension.substr(1);
            }
            std::transform(extension.begin(), extension.end(), extension.begin(), ::tolower);
            
            if (extension == "mp4" || extension == "mov" || extension == "m4v") {
                container = CONTAINER_FORMAT_QUICKTIME;
            } else if (extension == "mkv" || extension == "webm") {
                container = CONTAINER_FORMAT_MATROSKA;
            }
            // Default to MATROSKA for other extensions
            LOG(info) << "File extension: " << extension << ", container format: " << container << endl;
        } else {
            LOG(warning) << "No file extension found, using default container format: " << container << endl;
        }
    }

    // For remote/cloud streams, we get demuxed bitstream from producer
    if (m_isCloudStream || m_isImageCapture)
    {
        demuxer = gst_element_factory_make("identity", nullptr);
        // Configure appsrc for image capture
        g_object_set(m_source,
                     "format", 3,
                     "is-live", false,
                     nullptr);
        LOG(info) << "CloudStream/ClipReader appsrc configured" << endl;
    }
    else
    {
        // Use the common utility API for demuxer creation
        demuxer = createDemuxerForFile(m_uri, container);
        if (demuxer == nullptr)
        {
            LOG(warning) << "Failed to create demuxer for container: " << container << ", falling back to matroskademux" << endl;
            container = CONTAINER_FORMAT_MATROSKA;
            demuxer = createDemuxerForContainer(container);
        }
    }
    m_nvDecodeBin.reset(new NvDecodeBin(this, m_codec));
    m_decodeBin = m_nvDecodeBin->create(m_isImageCapture);
    if (!m_decodeBin)
    {
        LOG (error) << "Gstreamer element m_decodeBin creation failed" << endl;
        return -1;
    }

    if (!m_pipeline || !m_source || !demuxer || !m_decodeBin || !m_sink)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        goto failure;
    }
    /* Add Elements in pipeline */
    gst_bin_add_many (GST_BIN (m_pipeline), m_source, demuxer, m_decodeBin, m_sink, nullptr);

    if (!gst_element_link_many (m_decodeBin, m_sink, nullptr))
    {
        LOG (error) << "Elements could not be linked" << endl;
        goto failure;
    }

    if(m_isImageCapture || m_isCloudStream)
    {
        g_object_set (G_OBJECT (m_sink), "emit-signals", TRUE, "sync", FALSE, nullptr);
    }
    else
    {
        g_object_set (G_OBJECT (m_sink), "emit-signals", TRUE, "sync", TRUE, nullptr);
    }

    if (!gst_element_link_many (m_source, demuxer , nullptr))
    {
        LOG(error) << "Error in linking source and decodebin in hardware decode pipeline" << endl;
        goto failure;
    }

    if (m_isCloudStream || m_isImageCapture)
    {
        if (!gst_element_link_many (demuxer, m_decodeBin, nullptr))
        {
            LOG(error) << "Error in linking demuxer and decodebin in hardware decode pipeline" << endl;
            goto failure;
        }
    }
    else
    {
        if (!g_signal_connect (G_OBJECT (demuxer), "pad-added", G_CALLBACK (on_pad_added), m_decodeBin))
        {
            LOG(error) << "Error in g_signal_connect of pad-added" << endl;
            goto failure;
        }
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
#endif

    bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!bus)
    {
        LOG(error) << "Failed to get BUS of Decoder pipeline" << endl;
        goto failure;
    }
    m_bus_watch_id = gst_bus_add_watch (bus, busWatch, (void*)this);
    gst_object_unref(bus);
    sinkpad = gst_element_get_static_pad (m_sink, "sink");
    /* Check if sink_pad exists */
    if (!sinkpad)
    {
        LOG(error) << "Failed to get sink pad of m_sink_rw." << endl;
        goto failure;
    }
    /* Add probe to query width and height of video stream */
    gst_pad_add_probe(sinkpad, GST_PAD_PROBE_TYPE_EVENT_BOTH, pad_cb, (void*)this, nullptr);
    gst_object_unref(sinkpad);
    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this))
    {
        LOG(error) << "Error in g_signal_connect of new-sample" << endl;
        goto failure;
    }
    m_nvBufferMode = NvBufWrapper::getInstance()->getNvBufferMode();
    initializeRecordParams();
    initial_seek();
    LOG (info) << "Gstreamer Playback decode pipeline is created: " << m_uri << endl;
    return 0;
failure:
    m_error = true;
    return -1;
}

void GstNvVideoDecoder::setResolution(int width, int height)
{
    m_decoderWidth = width;
    m_decoderHeight = height;

    // For image capture mode, update the sink's frame size when resolution is received
    if (m_isImageCapture)
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        auto it = m_videoSinkList.find("image_capture");
        if (it != m_videoSinkList.end())
        {
            std::shared_ptr<VideoSinkInfo> sink = it->second;
            // Update frame size with actual decoder resolution (or resize dimensions if set)
            sink->m_frameSize.m_width = m_resizeWidth ? m_resizeWidth : width;
            sink->m_frameSize.m_height = m_resizeHeight ? m_resizeHeight : height;
        }
    }
}

bool GstNvVideoDecoder::play()
{
    LOG(info) << "Calling play..." << endl;
    m_startConsuming = false;
    /* Decoder will be always in playing state in case of IPC
    ** so no need to reset this in case of IPC enabled
    */
    if (GET_CONFIG().enable_ipc_path == false)
    {
        m_decOutFrames = 0;
    }
    std::shared_ptr<DecoderData> data(new DecoderData);
    data->m_taskName = "play";
    data->m_msgId = m_peerid;
    m_eventLoop.postMsg(data);
    return true;
}

bool GstNvVideoDecoder::play_internal()
{
    LOG(info) << "Calling play from peer = " << m_peerid << endl;
    bool ret = true;
    /* Setting Pipeline to playing state*/
    GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    /* handling returns of above API */
    if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
    {
        LOG (error) << "gst_element_set_state failed. " << endl;
        m_error = true;
        ret = false;
    }
    /* pipeline will be put into PLAYING state from other thread */
    else if (gstStateChangeRet == GST_STATE_CHANGE_ASYNC)
    {
        /* watching pipeline bus for below messages type */
        LOG (info) << "GST_STATE_CHANGE_ASYNC. " << endl;
    }
    /* success case */
    else
    {
        LOG (info) << "State change success " << endl;
    }

    bool isReplayPicture = m_isImageCapture && m_uri.find("file://") == 0;
    if(m_needSharedStream || m_isCloudStream || isReplayPicture)
    {
        // Use the producer that was passed via setProducer() if available
        if (m_producer)
        {
            if (isReplayPicture)
            {
                // Register finished callback to flush decoder when producer completes
                m_producer->onFinished([this]() {
                    LOG(info) << "ClipReaderProducer finished callback - flushing decoder pipeline for peer: " << m_peerid << endl;
                    flushDecoderPipeline();
                });
            }

            // Register this decoder as a consumer with the producer
            if (m_sensorType == SENSOR_TYPE_MMS_ONVIF && m_isImageCapture && !m_startTime.empty())
            {
                m_producer->registerConsumer(getSelf(), m_uri, m_startTime, m_endTime);
            }
            else
            {
                m_producer->registerConsumer(getSelf(), m_uri);
            }
            LOG(info) << "Registered decoder with producer for peer: " << m_peerid << endl;
        }
        else
        {
            LOG(warning) << "No producer available for shared stream, peer: " << m_peerid << endl;
        }
        
        m_appsrc_out_probe_count = m_decoder_in_probe_count = m_decoder_out_probe_count = 0;
    }
    return ret;
}

bool GstNvVideoDecoder::pause()
{
    bool isReplayPicture = m_isImageCapture && m_uri.find("file://") == 0;
    if(m_needSharedStream || m_isCloudStream || isReplayPicture)
    {
        // Deregister from the producer
        if (m_producer)
        {
            m_producer->unregisterConsumer(getSelf(), m_uri, true);
        }
        m_state = GST_STATE_PAUSED;
        return true;
    }
    std::shared_ptr<DecoderData> data(new DecoderData);
    data->m_msgId = m_peerid;
    data->m_taskName = "pause";
    m_eventLoop.postMsg(data);
    return true;
}

void GstNvVideoDecoder::initial_seek()
{
    if (m_isImageCapture)
    {
        m_nvDecodeBin->m_monitoFramesInProbe = true;
    }
    if (m_isCloudStream || m_isImageCapture)
    {
        // Seek will be handled by Producer pipeline
        return;
    }
    gst_element_set_state (m_pipeline, GST_STATE_PAUSED);
    GstSeekFlags gstSeekFlags;
    gint64 startTime = m_startTimeFirstFile;
    if (m_isImageCapture == true)
    {
        gstSeekFlags = (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT);
        GstState current, pending;
        gst_element_get_state(m_pipeline, &current, &pending, GST_SECOND);
        if (current != GST_STATE_PAUSED)
        {
            gst_element_get_state(m_pipeline, &current, &pending, 2*GST_SECOND);
        }
    }
    else
    {
        gstSeekFlags = (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE | GST_SEEK_FLAG_TRICKMODE);
        getstate_internal();
    }
    gint64 position = 0;
    position = (startTime * GST_SECOND) / 1000;
    m_nvDecodeBin->waitForAllPadsCreation();
    if (m_fileNameArray.size() == 1 && m_endTimeLastFile)
    {
        gint64 stopTime = m_endTimeLastFile;
        if (position > stopTime * GST_MSECOND)
        {
            LOG(info) << "Trying to seek after stop time, seeking to end" << endl;
            position = (m_endTimeLastFile * GST_SECOND) / 1000;
        }
        gst_element_seek (m_pipeline, m_playBackSpeed, GST_FORMAT_TIME,
            gstSeekFlags, GST_SEEK_TYPE_SET, position, GST_SEEK_TYPE_SET, stopTime * GST_MSECOND);
    }
    else
    {
        gst_element_seek (m_pipeline, m_playBackSpeed, GST_FORMAT_TIME,
                        gstSeekFlags, GST_SEEK_TYPE_SET, position, GST_SEEK_TYPE_END, 0);
    }
}

bool GstNvVideoDecoder::pause_internal()
{
    bool ret = true;
    if (m_pipeline)
    {
        LOG (info) << "Pausing the pipeline" << endl;
        GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, GST_STATE_PAUSED);
        if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
        {
            LOG (error) << "gst_element_set_state failed. " << endl;
            m_error = true;
            ret = false;
        }
        else if (gstStateChangeRet == GST_STATE_CHANGE_ASYNC)
        {
            LOG (info) << "GST_STATE_CHANGE_ASYNC. " << endl;
        }
        else
        {
            gst_element_get_state (m_pipeline, nullptr, nullptr, GST_SECOND);
            LOG (info) << "State change success " << endl;
        }
    }
    getstate_internal();
    return ret;
}

void GstNvVideoDecoder::stop()
{
    bool isReplayPicture = m_isImageCapture && m_uri.find("file://") == 0;
    if(!m_recordedPlayback || m_isCloudStream || isReplayPicture)
    {
        // Deregister from the producer
        if (m_producer)
        {
            m_producer->unregisterConsumer(getSelf());
            m_producer = nullptr;
        }
    }
    std::shared_ptr<DecoderData> data(new DecoderData);
    data->m_taskName = "stop";
    data->m_msgId = m_peerid;
    m_eventLoop.postMsg(data);
}

void GstNvVideoDecoder::stop_internal()
{
    LOG(info) << "Send EOS to encoder and put decoder in null state" << endl;
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(m_peerid);
        if (m_error == false && it != m_videoSinkList.end())
        {
            shared_ptr<VideoSinkInfo> sink = it->second;
            sink->m_consumer->onLastFrame();
        }
    }
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
    }
    getstate_internal();
}

bool GstNvVideoDecoder::isPlaying()
{
    bool result = false;
    std::unique_lock<std::mutex> lk(m_playStateLock);
    if (m_playbackState == false)
    {
        auto until = std::chrono::system_clock::now() + chrono::milliseconds(1000);
        result = m_playStateWait.wait_until(lk, until, [this]{ return (m_playbackState.load()); });
    }
    if (result)
    {
        result =  (m_decOutFrames == CONFIRM_DEC_OUT_FRAMES) && m_playbackState;
    }
    return result;
}

void GstNvVideoDecoder::pre_destroy()
{
    if(m_recordedPlayback && !m_prevFileName.empty())
    {
        vst_storage::addOrRemoveFileInProtectList(m_prevFileName, false);
    }
}

void GstNvVideoDecoder::setEOS()
{
    GstFlowReturn ret = gst_app_src_end_of_stream (GST_APP_SRC (m_source));
    if (ret != GST_FLOW_OK)
    {
        LOG(error) << "gst_app_src_end_of_stream failed" << endl;
    }
    m_state = GST_STATE_NULL;
}

void GstNvVideoDecoder::destroy(bool expect_result)
{
    pre_destroy();
    std::shared_ptr<DecoderData> in_data(new DecoderData);
    in_data->m_msgId = m_peerid;
    std::shared_ptr<EventLoopOutData> out_data(new EventLoopOutData);
    in_data->m_outResult = out_data;
    in_data->m_expectResult = expect_result;
    in_data->m_taskName = "destroy";
    m_eventLoop.postMsg(in_data);
}

void GstNvVideoDecoder::destroy_internal()
{
    LOG(info) << "destroy_internal() called for peer: " << m_peerid << ", uri: " << m_uri << endl;
    m_stop = true;
    if (m_pipeline == nullptr)
    {
        LOG(info) << "Pipeline is already destroyed" << endl;
        return;
    }

    bool isReplayPicture = m_isImageCapture && m_uri.find("file://") == 0;
    if(m_needSharedStream || m_isCloudStream || isReplayPicture)
    {
        // Deregister from the producer
        if (m_producer)
        {
            m_producer->unregisterConsumer(getSelf(), m_uri);
            m_producer = nullptr;
        }
    }

#ifdef DUMP_PIPELINE
    GST_DEBUG_BIN_TO_DOT_FILE_WITH_TS(GST_BIN(m_pipeline), GST_DEBUG_GRAPH_SHOW_ALL, "pipeline");
#endif

    LOG(info) << "Terminating gstreamer Decoder pipeline peerid:" << m_peerid << ", uri:" << m_uri << endl;
    if (m_hlsPlayback)
    {
        std::string command = "rm -rf " + m_urlPath.first;
        int result = system(command.c_str());
        if (!result)
        {
            LOG(info) << "Directory deleted = " << m_urlPath.first << endl;
        }
    }
    MEASURE_FUNCTION_EXECUTION_TIME_WITH_TAG(m_peerid)
    if (m_bus_watch_id != G_MAXUINT)
    {
        g_source_remove (m_bus_watch_id);
        m_bus_watch_id = G_MAXUINT;
    }

    if (m_tee)
    {
        if (m_teeSrcIpcsink)
        {
            gst_element_release_request_pad (m_tee, m_teeSrcIpcsink);
            gst_object_unref (m_teeSrcIpcsink);
        }
        if (m_teeSrcAppsink)
        {
            gst_element_release_request_pad (m_tee, m_teeSrcAppsink);
            gst_object_unref (m_teeSrcAppsink);
        }
    }

    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
#ifdef UNIT_TEST
    getstate_internal();
#endif
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }

    if (m_recordedPlayback)
    {
        m_vodDebugFile.close();
    }
    else
    {
        m_liveDebugFile.close();
    }

    m_decoderCreated = false;
    LOG(info) << "Terminated gstreamer Decoder pipeline" << endl;
}

void GstNvVideoDecoder::getstate_internal()
{
    GstState previous_state = m_state;
    if (m_pipeline)
    {
        GstStateChangeReturn state_change;
        GstState  current, pending;
        state_change = gst_element_get_state(m_pipeline, &current, &pending, GST_SECOND);
        if (state_change == GST_STATE_CHANGE_FAILURE)
        {
            m_state = GST_STATE_NULL;
        }
        m_state = current;
    }
    LOG(info) << "Current state: " << m_state << endl;

    // Send WebSocket status update if state changed
    if (previous_state != m_state)
    {
        sendStateChangeWebSocketMessage(m_peerid, m_state == GST_STATE_PLAYING);
    }
}

void GstNvVideoDecoder::sendStateChangeWebSocketMessage(const string& peerid, bool is_ready, const std::string& updated_state)
{
    string state_str = updated_state.empty() ? (is_ready ? "PLAYING" : "PAUSED") : updated_state; // Use explicit state if provided, otherwise derive from ready status

    Json::Value wsResponse;
    wsResponse["apiKey"] = m_recordedPlayback ? "api/v1/replay/stream/status" : "api/v1/live/stream/status";
    wsResponse["peerId"] = peerid;
    wsResponse["data"]["error"] = static_cast<bool>(m_error);
    wsResponse["data"]["state"] = state_str;

    GET_WEBSOCKET_INSTANCE()->sendMessage(peerid, jsonToString(wsResponse), MG_WEBSOCKET_OPCODE_TEXT);

    LOG(info) << "Sent WebSocket state change message: " << state_str << " for peer: " << peerid << endl;
}

#ifdef JETSON_PLATFORM
void GstNvVideoDecoder::registerDecoderPlayingStatusListener(IStreamStatusEvent *listener)
{
    std::lock_guard<std::mutex> listerner_lock(m_listenerMutex);
    m_listeners.insert(listener);
}

void GstNvVideoDecoder::deregisterDecoderPlayingStatusListener(IStreamStatusEvent *listener)
{
    std::lock_guard<std::mutex> listerner_lock(m_listenerMutex);
    m_listeners.erase(listener);
}
#endif

void GstNvVideoDecoder::getstate_async()
{
    std::shared_ptr<DecoderData> in_data(new DecoderData);
    in_data->m_msgId = m_peerid;
    std::shared_ptr<EventLoopOutData> out_data(new EventLoopOutData);
    in_data->m_outResult = out_data;
    in_data->m_expectResult = true;
    in_data->m_taskName = "get_state";
    m_eventLoop.postMsg(in_data);
}

std::string GstNvVideoDecoder::getstate()
{
    string state_str = gst_element_state_get_name(m_state);
    if (state_str != "PLAYING" && state_str != "PAUSED")
    {
        state_str = "NOT_PLAYING";
    }
    return state_str;
}

std::string GstNvVideoDecoder::getstate(const std::string& peerid)
{
    string state_str = getstate();

    if (!m_recordedPlayback && state_str != "NOT_PLAYING")
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
        if(it != m_videoSinkList.end())
        {
            std::shared_ptr<VideoSinkInfo> sink = it->second;
            state_str = sink->m_isSinkReady ? "PLAYING" : "PAUSED";
        }
        else
        {
            state_str = "NOT_PLAYING";
        }
    }
    return state_str;
}

/* get Decoder stats */
void GstNvVideoDecoder::getStats (const std::string& peerid, LatencyStats& stats)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if (it != m_videoSinkList.end())
    {
        shared_ptr<VideoSinkInfo> sink = it->second;
        stats.m_totalFrames = sink->m_decoderStats.m_totalFrames;
        stats.m_totalLatency = 0;
        stats.m_minLatency = 0;
        stats.m_maxLatency = 0;
    }
}

GstNvVideoDecoder::GstNvVideoDecoder (const std::string& consumer_name, const std::string& uri, const std::map<std::string, std::string, std::less<>> &opts) :
                    IMediaDataConsumer(consumer_name),
                    m_uri(uri)
                  , m_port(0)
                  , m_recordedPlayback (false)
                  , m_hlsPlayback (false)
                  , m_decodeBin (nullptr)
                  , m_source(nullptr)
                  , m_sink(nullptr)
                  , m_bus_watch_id(G_MAXUINT)
                  , m_frameNum(0)
                  , m_frameRate(DEFAULT_FRAME_RATE)
                  , m_maxDecLatency (DEFAULT_FRAME_LATENCY)
                  , m_currentFileIndex(0)
                  , m_startTimeFirstFile(0)
                  , m_endTimeLastFile(0)
                  , m_seekValue("1")
                  , m_action("")
                  , m_playBackSpeed(1.0)
                  , m_position_forward(0)
                  , m_position_rewind(0)
                  , m_position(0)
                  , m_sourceWidth (WIDTH_1080p)
                  , m_sourceHeight (HEIGHT_1080p)
                  , m_gpuExist(false)
                  , m_eventLoop("dec_event_loop", process_dec_message)
                  , m_nvBufferMode (NvBufferModeInvalid)
                  , m_appsrc_out_probe_count(0)
                  , m_decoder_in_probe_count(0)
                  , m_decoder_out_probe_count(0)

{
    if(GET_CONFIG().enable_cloud_storage)
    {
        initUnifiedStorageReader();
    }

    m_state = GST_STATE_NULL;
    m_gpuExist = g_isGpuPresent;
    setOptions(opts);
    if (m_recordedPlayback && m_debug_logging_vod && !m_peerid.empty())
    {
        string file_name = "vod_" + m_peerid + "_timestamp.txt";
        m_vodDebugFile.open(file_name, ios::out | ios::app);
    }
    m_playbackWD = make_unique<Bosma::Scheduler>(1);
    m_playbackWD->interval(SCHEDULER_WD_INTERVAL, [=]()
    {
        /* Reset playstate to not playing, it will be set again in appsink callback*/
        m_playbackState.store(STATE_NOT_PLAYING);
    });
    setConsumerMediaType(MediaTypeAudioVideo);

    if (m_perfLogging)
    {
        m_decStats.clear();
        m_decStats.setElementName("Video Decode");
        if (m_isImageCapture)
        {
            // Performance tracking element name is now set in base constructor
            m_transcodeStats.startProcessing();
        }
    }
}

void GstNvVideoDecoder::setOptions(const std::map<std::string, std::string, std::less<>> &opts)
{
    m_opts = opts;
    if ( m_opts.find("peerid") != m_opts.end() )
    {
        m_peerid = m_opts.at("peerid");
    }
    if ( opts.find("image_capture") != opts.end() )
    {
        m_isImageCapture = opts.at("image_capture") == "true" ? true: false;
    }
    if ( opts.find("sensor_type") != opts.end() )
    {
        m_sensorType = opts.at("sensor_type");
    }
    if ( opts.find("sensorId") != opts.end() )
    {
        m_deviceId = opts.at("sensorId");
        // Read B-frame flag from database using stream ID (stored in m_deviceId from sensorId opt)
        auto dbHelper = GET_DB_INSTANCE();
        if (dbHelper)
        {
            // Get stream info from database using the stream ID
            SensorStreamsDBColumns stream_row = dbHelper->readSensorStreams(m_deviceId);
            if (!stream_row.stream_id_value.empty())
            {
                m_hasBframes = (stream_row.isBframesPresent_value == 1);
                LOG(info) << "NvVideoDecoder: B-frame flag read from DB for stream " << m_deviceId
                          << ": " << (m_hasBframes ? "true" : "false")
                          << " (DB value: " << stream_row.isBframesPresent_value << " [0=false, 1=true])" << endl;
            }
            else
            {
                LOG(verbose) << "NvVideoDecoder: Could not find stream in database for streamId: " << m_deviceId << endl;
            }
        }
        else
        {
            LOG(error) << "NvVideoDecoder: Database helper is null, cannot read B-frame flag" << endl;
        }
    }
    if ( opts.find("framerate") != opts.end() )
    {
        m_frameRate = stringToDouble(opts.at("framerate"), DEFAULT_FRAME_RATE);
        if (m_frameRate == 0)
        {
            m_frameRate = DEFAULT_FRAME_RATE;
        }
        /* Maximum number of miliseconds that a buffer can be late before it is dropped */
        m_maxDecLatency = (1000 / m_frameRate) * MAX_FRAMES_FOR_LATENCY;
        m_maxDecLatency = std::max(m_maxDecLatency, GET_CONFIG().webrtc_latency_ms);
    }
    if ( opts.find("do_composition") != opts.end() )
    {
        m_compositePlayback = true;
    }
    m_perfLogging = GET_CONFIG().enable_perf_logging;
    LOG(info) << "GstNvVideoDecoder: uri: " << m_uri << endl;
    LOG(info) << "GstNvVideoDecoder: m_sensorType: " << m_sensorType << endl;

    if (m_uri.find("s3://") != std::string::npos ||
        (opts.find("storageLocation") != opts.end() && opts.at("storageLocation") == "cloud"))
    {
        LOG(info) << "Cloud storage stream detected" << endl;
        m_isCloudStream = true;
    }
    if ((m_uri.find("file://") == 0) || (m_uri.find("s3://") == 0))
    {
        m_recordedPlayback = true;
        vector<string> uri_arr = splitString(m_uri, "?");
        if (uri_arr.size() > 1)
        {
            string params = uri_arr[1];
            CivetServer::getParam(params, "startTime", m_startTime);
            CivetServer::getParam(params, "endTime",   m_endTime);
        }
        LOG(info) << "start time: " << m_startTime << " & end time: " << m_endTime << endl;

        if ( opts.find("startTime") != opts.end() )
        {
            m_isoStartTime = opts.at("startTime");
        }
        if ( opts.find("endTime") != opts.end() )
        {
            m_isoEndTime = opts.at("endTime");
        }

        if (m_deviceId.empty())
        {
            string token("vod/");
            m_deviceId = m_uri.substr( m_uri.find(token) + token.size(),
                                m_uri.find("?") - (m_uri.find(token) + token.size()) );
        }
        LOG(verbose) << "Device Id: " << m_deviceId << endl;

        if (!m_startTime.empty())
        {
            m_epochStartTime = getEpocTimeInMS(m_startTime);
            LOG(info) << "epoch start time: " << m_epochStartTime << endl;
        }
        if (!m_endTime.empty())
        {
            m_epochEndTime = getEpocTimeInMS(m_endTime);
            LOG(info) << "epoch end time: " << m_epochEndTime << endl;
        }
        else
        {
            m_continuosPlayback = true;
        }

        if (m_isImageCapture && !m_startTime.empty())
        {
            /* Use 3-frame offset, in case exact time is not present */
            m_epochEndTime = m_epochStartTime + ((1000 / m_frameRate) * 3);
            LOG(info) << "epochTime: " << m_epochStartTime << ", IsoStartTime:" << m_startTime << ", m_epochEndTime:" << m_epochEndTime <<  endl;
        }

        /* Get list of files alongwith its associated timestamps */
        auto dbHelper = GET_DB_INSTANCE();
        if (m_sensorType == SENSOR_TYPE_NVSTREAM)
        {
            m_fileNameArray = vst_common::getStreamerFileName(m_uri);
        }
        else if (m_sensorType == SENSOR_TYPE_FILE)
        {
            if ( opts.find("streamId") != opts.end() )
            {
                m_deviceId = opts.at("streamId");
            }

            m_fileNameArray  = dbHelper->getFileListStreamIdBased(m_deviceId, m_epochStartTime, m_epochEndTime);
            LOG(info) << "m_fileNameArray size: " << m_fileNameArray.size() << endl;

            if (!m_fileNameArray.empty())
            {
                m_fileStartTime = m_fileNameArray[0].m_startTime;
                m_objectId = m_fileNameArray[0].m_objectId;
                LOG(info) << "m_fileStartTime: " << m_fileStartTime << endl;
            }
        }
        else if (m_sensorType == SENSOR_TYPE_MMS_ONVIF)
        {
            m_recordedPlayback = false;
        }
        else
        {
            m_fileNameArray  = dbHelper->getFileList(m_deviceId, m_epochStartTime, m_epochEndTime);
            LOG(info) << "m_fileNameArray size: " << m_fileNameArray.size() << endl;
        }
        if (m_fileNameArray.size() == 0 && m_sensorType != SENSOR_TYPE_MMS_ONVIF)
        {
            VideoFileInfo receivedFile = dbHelper->getInProgressRecordFile(m_deviceId, m_epochStartTime);
            if (!receivedFile.m_filePath.empty())
            {
                m_fileNameArray.push_back(receivedFile);
            }
            else
            {
                LOG(error) << "No streams found" << endl;
                throw std::invalid_argument( "no valid stream found for given timestamps, please check timelines using /api/v1/storage/timelines" );
            }
        }
    }
    else if ((m_uri.find("rtsp://") == 0))
    {
        if (m_sensorType == SENSOR_TYPE_MMS_ONVIF)
        {
            vector<string> uri_arr = splitString(m_uri, "?");
            if (uri_arr.size() > 1)
            {
                string params = uri_arr[1];
                CivetServer::getParam(params, "startTime", m_startTime);
                CivetServer::getParam(params, "endTime",   m_endTime);
            }
            LOG(info) << "start time: " << m_startTime << " & end time: " << m_endTime << endl;

            if (!m_startTime.empty())
            {
                m_epochStartTime = getEpocTimeInMS(m_startTime);
                LOG(info) << "epoch start time: " << m_epochStartTime << endl;
            }
            if (!m_endTime.empty())
            {
                m_epochEndTime = getEpocTimeInMS(m_endTime);
                LOG(info) << "epoch end time: " << m_epochEndTime << endl;
            }
            if (m_isImageCapture && !m_startTime.empty())
            {
                /* Use 3-frame offset, in case exact time is not present */
                m_epochEndTime = m_epochStartTime + ((1000 / m_frameRate) * 3);
                LOG(info) << "epochTime: " << m_epochStartTime << ", IsoTime:" << m_startTime << endl;
            }
        }
        m_recordedPlayback = false;
    }
    else if ((m_uri.find("udp:") == 0))
    {
        if (opts.find("capture_type") != opts.end() && opts.at("capture_type") == "udp")
        {
            if ( opts.find("port_video") != opts.end() )
            {
                std::string port_string = opts.at("port_video");
                m_port = stringToInt(port_string, 0);
            }
        }
    }
    else if (opts.find("gods_eye_view") != opts.end() && opts.at("gods_eye_view") == "true" &&
            isFileExist(GET_CONFIG().floor_map_file_path))
    {
        LOG(info) << "Creating JPEG decode pipeline for file: " << m_uri << endl;
        m_godsEyeView = true;
    }
    else if (opts.find("sensor_type") != opts.end() && opts.at("sensor_type") == SENSOR_TYPE_FILE)
    {
        m_sensorType = SENSOR_TYPE_FILE;
    }
    else
    {
        LOG(error) << "Invalid URL" << endl;
        throw std::invalid_argument( "Invalid URL" );
    }
    if ( opts.find("overlay") != opts.end() )
    {
        m_isOverlay = opts.at("overlay") == "true" ? true: false;
    }
    if ( opts.find("sensorID") != opts.end() )
    {
        m_sensorName = opts.at("sensorID");
    }
    if ( opts.find("peerid") != opts.end() )
    {
        m_peerid = opts.at("peerid");
    }
    if ( opts.find("govLength") != opts.end() )
    {
        m_govLength = stringToInt(opts.at("govLength"), DEFAULT_GOV_LENGTH);
        if (m_govLength == 0)
        {
            m_govLength = DEFAULT_GOV_LENGTH;
        }
    }
    if ( opts.find("source_width") != opts.end() )
    {
        m_sourceWidth = stringToInt(opts.at("source_width"), WIDTH_1080p);
    }
    if ( opts.find("source_height") != opts.end() )
    {
        m_sourceHeight = stringToInt(opts.at("source_height"), HEIGHT_1080p);
    }
    if ( opts.find("resize_width") != opts.end() )
    {
        m_resizeWidth = stringToInt(opts.at("resize_width"), 0);
    }
    if ( opts.find("resize_height") != opts.end() )
    {
        m_resizeHeight = stringToInt(opts.at("resize_height"), 0);
    }
    if ( opts.find("codec") != opts.end() )
    {
        m_codec = opts.at("codec");
    }
    LOG(info) << "Is this recorded playback? " << m_recordedPlayback << endl;
    LOG(info) << "Is this HLS playback? " << m_hlsPlayback << endl;
    LOG(info) << "Is this Composite playback? " << m_compositePlayback << endl;

    if (!m_recordedPlayback && m_debug_logging_live && !m_deviceId.empty())
    {
        string file_name = "live_" + m_deviceId + "_timestamp.txt";
        m_liveDebugFile.open(file_name, ios::out | ios::app);
    }
}

FrameSize GstNvVideoDecoder::qualityToFrameSize(const string& quality)
{
    FrameSize size;
    if (quality == "high" || quality == "pass_through")
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

void GstNvVideoDecoder::setQuality(const std::string& peerid, const std::string& quality)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSinkInfo> sink = it->second;
        sink->m_quality = quality;
        sink->m_frameSize = qualityToFrameSize(quality);
        sink->m_decoderStats.clear();
    }
    else
    {
        LOG(error) << "Consumer does not exist for " << peerid << endl;
    }
    LOG(info) << "Sink list size = " << m_videoSinkList.size() << " for " << m_uri << endl;
}

FrameSize GstNvVideoDecoder::qualityToFrameSize(const string& quality, int width, int height)
{
    FrameSize size;
    if (quality == "high" || quality == "pass_through")
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
    else if (quality == "custom")
    {
        size.m_width = width;
        size.m_height = height;
    }
    else
    {
        size.m_width = m_sourceWidth;
        size.m_height = m_sourceHeight;
    }
    return size;
}

void GstNvVideoDecoder::setQuality(const std::string& peerid, const std::string& quality, int width, int height)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSinkInfo> sink = it->second;
        sink->m_quality = quality;
        sink->m_frameSize = qualityToFrameSize(quality, width, height);
        sink->m_decoderStats.clear();
    }
    else
    {
        LOG(error) << "Consumer does not exist for " << peerid << endl;
    }
    LOG(info) << "Sink list size = " << m_videoSinkList.size() << " for " << m_uri << endl;
}

void GstNvVideoDecoder::removeConsumer(const std::string& peerid)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        m_videoSinkList.erase(it);
    }
#ifdef JETSON_PLATFORM
    if (m_videoSinkList.size() == 0 && (m_recordedPlayback || GET_CONFIG().enable_ipc_path == false))
    {
        m_stop = true;
    }
#else
    if (m_videoSinkList.size() == 0)
    {
        m_stop = true;
    }
#endif

    // When the last consumer is removed, send EOS to the appsrc to unblock any
    // ClipReaderProducer streaming thread stuck in gst_app_src_push_buffer
    // (configured with block=TRUE). Without this, ClipReaderProducer::stop()
    // hangs in gst_element_set_state(NULL) because its streaming thread can't
    // be stopped while blocked on the decoder's appsrc.
    if (m_stop && m_isImageCapture && m_source)
    {
        LOG(info) << "Sending EOS to the appsrc to unblock any ClipReaderProducer streaming thread stuck in gst_app_src_push_buffer" << endl;
        gst_app_src_end_of_stream(GST_APP_SRC(m_source));
    }
}

void GstNvVideoDecoder::initializeRecordParams()
{
    bool useFileSrc = m_isImageCapture || m_isCloudStream ? false : true;
    if (m_recordedPlayback && useFileSrc)
    {
        setFileAndUpdatePipelineState (true);
    }
    if (!m_startTime.empty())
    {
        gint64 epochStartTime = 0;
        epochStartTime = getEpocTimeInMS(m_startTime);
        m_startTimeFirstFile = epochStartTime - m_fileNameArray[0].m_startTime;
        m_startTimeFirstFile = m_startTimeFirstFile < 0 ? 0 : m_startTimeFirstFile;
        LOG(info) << "m_startTimeFirstFile = " << m_startTimeFirstFile << " epochStartTime: "
        << epochStartTime << "m_fileNameArray[0].m_startTime: " << m_fileNameArray[0].m_startTime << endl;
    }
    if (!m_endTime.empty())
    {
        gint64 epochEndTime = 0;
        epochEndTime = getEpocTimeInMS(m_endTime);
        m_endTimeLastFile = epochEndTime - m_fileNameArray[m_fileNameArray.size()-1].m_startTime;
        LOG(info) << "m_endTimeLastFile = " << m_endTimeLastFile << endl;
    }
}

void GstNvVideoDecoder::updateDecoderElement ()
{
    LOG(info) << "Updating Decoder Element" << endl;
    /* Update the decoder element in NvDecodeBin Class */
    m_forceResetEnc = true;
    m_nvDecodeBin->updateDecoderElement (m_playBackSpeed);
}

gint64 GstNvVideoDecoder::getNextFile ()
{
    string file_name;
    gint64 ret = -1;
    auto dbHelper = GET_DB_INSTANCE();
    if(m_continuosPlayback && (m_currentFileIndex == m_fileNameArray.size()))
    {
        LOG(info) << "Reached at end of file, try to fetch next file(S) if available ..." << endl;
        VideoFileInfo last_file = m_fileNameArray[m_currentFileIndex - 1 ];
        if (last_file.m_fileFPS == 0) // File FPS is not updated in DB
        {
            last_file = dbHelper->getRecordFileInfo(m_deviceId, last_file.m_startTime);
            if (last_file.m_fileFPS != 0) // File FPS is now updated in DB
            {
                m_fileNameArray[m_currentFileIndex - 1 ] = last_file;
            }
        }
        LOG(info) << "Duration of last file : " << last_file.m_duration << endl;
        if (last_file.m_duration != 1) // check if the last file is being written
        {
            uint64_t last_file_start_time = last_file.m_startTime;
            std::vector <VideoFileInfo> next_files  = dbHelper->getNextFileList(m_deviceId, last_file_start_time);
            if (next_files.size() > 0)
            {
                for (auto file : next_files)
                {
                    if (std::find(m_fileNameArray.begin(), m_fileNameArray.end(), file) != m_fileNameArray.end())
                    {
                        continue;
                    }
                    else
                    {
                        // push file which has start time > last file in current list
                        if(last_file < file)
                        {
                            m_fileNameArray.push_back(file);
                        }
                    }
                }
            }
            else
            {
                LOG(warning) << "No next files are available" << endl;
                return ret;
            }
        }
        if (m_currentFileIndex == m_fileNameArray.size())
        {
            --m_currentFileIndex; // play the same file again with some back offset
            gint64 position;
            if (gst_element_query_position  (m_pipeline, GST_FORMAT_TIME, &position))
            {
                LOG(info) << "Current position when eos triggered: " << position << endl;
                gint64 current_pos_ms = position / GST_MSECOND;
                gint64 current_abs_position = m_fileNameArray[m_currentFileIndex].m_startTime + current_pos_ms;
                size_t current_time_ms = std::chrono::duration_cast
                                        <std::chrono::milliseconds>(std::chrono::system_clock::now()
                                        .time_since_epoch()).count();
                if ((current_time_ms - current_abs_position) < (GST_SECOND * 5) / GST_MSECOND)
                {
                    LOG(info) << "Wait for 5 secs as eos is triggered when file length is < 5 " << position << endl;
                    sleep(5);
                    ret = 0;
                }
                else
                {
                    if (current_pos_ms < 5000)
                    {
                        ret = 0; // start the current file from begining
                    }
                    else
                    {
                        ret = position - (GST_SECOND * 5); // Go back 5 secs from current postion.
                    }
                    LOG(info) << "Go back 5 secs from current postion: " << ret << endl;
                }
            }
            else
            {
                LOG(warning) << "Query postion is failed, start the file from begining" << endl;
                ret = 0;
            }
            LOG(info) <<"Last file is not completely played, play same file again from position: " << ret << endl;
        }
        else
        {
            ret = 0;
        }
    }
    else
    {
        ret = 0;
    }

    if (m_currentFileIndex < m_fileNameArray.size())
    {
        file_name = m_fileNameArray[m_currentFileIndex].m_filePath;
        std::string object_id = m_fileNameArray[m_currentFileIndex].m_objectId;
        VideoFileInfo currentFile = m_fileNameArray[m_currentFileIndex];

        //If file exists in recorded_video_root, we don't need to download it from cloud
        if (isFileExist(file_name))
        {
            LOG(info) << "File = " << file_name << " is already in the recorded video root path, skipping downloading it from cloud" << endl;
            return ret;
        }

        bool file_exists = false;
        if (m_unifiedStorageReader && !object_id.empty())
        {
            nv_vms::FileResult file_result = m_unifiedStorageReader->checkFileExists(object_id);
            file_exists = file_result.success;

            LOG(info) << "File exists check result: " << (file_exists ? "true" : "false") << std::endl;
            if (!file_exists)
            {
                LOG(warning) << "File check error: " << file_result.message << std::endl;
            }
        }
        else
        {
            LOG(warning) << "Unified storage reader is not initialized, assuming file does not exist" << std::endl;
        }

        /* File doesn't exist locally or in cloud storage, try next file */
        LOG(warning) << "File = " << file_name << " not available, checking next files in list" << endl;

        /* Move to next/previous file based on playback direction */
        if (m_playBackSpeed > 0 && m_currentFileIndex < m_fileNameArray.size() - 1)
        {
            m_currentFileIndex++;
            ret = getNextFile();
        }
        else if (m_playBackSpeed < 0 && m_currentFileIndex > 0)
        {
            m_currentFileIndex--;
            ret = getNextFile();
        }
        /* If we can't move further in the requested direction, return current ret */
        else
        {
            LOG(warning) << " Reached at end of playlist, retry after 1sec" << endl;
        }
    }
    return ret;
}

void GstNvVideoDecoder::resetPipeline ()
{
    {
        /* Need to return buffers from encoder (if any) to decoder as pipeline
        ** is transitioning to NULL state */
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
        for( it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
        {
            if (it != m_videoSinkList.end())
            {
                shared_ptr<VideoSinkInfo> sink = it->second;
                LOG(info) << "Reset dec consumer" << endl;
                sink->m_consumer->reset();
            }
        }
    }
    LOG(info) <<"=== Setting pipeline to NULL ===" << endl;
    gst_element_set_state (m_pipeline, GST_STATE_NULL);
    gst_element_get_state (m_pipeline, nullptr, nullptr, 10 * GST_SECOND);
    LOG(info) << "=== Setting pipeline to NULL Done ===" << endl;

    LOG(info) <<"=== Setting pipeline to PLAYING ===" << endl;
    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
}

bool GstNvVideoDecoder::setFileAndUpdatePipelineState (bool first_time)
{
    string filename;
    gint64 ret = getNextFile ();

    if (ret != -1)
    {
        // Clean up previous file if it was downloaded from cloud storage
        if (!m_prevFileName.empty())
        {
            vst_storage::addOrRemoveFileInProtectList(m_prevFileName, false);
        }

        if (!first_time)
        {
            {
                /* Need to return buffers from encoder (if any) to decoder as pipeline
                ** is transitioning to NULL state */
                std::lock_guard<std::mutex> lock(m_videoSinkLock);
                std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(m_peerid);
                {
                    if (it != m_videoSinkList.end())
                    {
                        shared_ptr<VideoSinkInfo> sink = it->second;
                        LOG(info) << "Reset dec consumer" << endl;
                        sink->m_consumer->reset();
                    }
                }
            }
            LOG(info) <<"=== Setting pipeline to NULL ===" << endl;
            gst_element_set_state (m_pipeline, GST_STATE_NULL);
            gst_element_get_state (m_pipeline, nullptr, nullptr, 10 * GST_SECOND);
            LOG(info) << "=== Setting pipeline to NULL Done ===" << endl;
        }

        // Get the current file info
        VideoFileInfo currentFile = m_fileNameArray[m_currentFileIndex];
        filename = currentFile.m_filePath;
        /* store current file name to unset it in next iteration file */
        m_prevFileName = filename;

        // Check if using producer-based pipeline (e.g., ClipReaderProducer for image capture)
        // In this case, m_source is appsrc which doesn't have 'location' property
        if (m_producer)
        {
            LOG(warning) << "Cannot continue playback with producer-based pipeline (appsrc has no location property)" << endl;
            if (m_isImageCapture)
            {
                LOG(warning) << "Image capture: seek position may be near end of file, not enough frames available" << endl;
            }
            return 0;  // Signal that we can't continue playback
        }

        SET_FILE_SOURCE (m_source, filename);
        if (!first_time)
        {
            LOG(info) <<"=== Setting pipeline to PAUSE ===" << endl;
            gst_element_set_state (m_pipeline, GST_STATE_PAUSED);
            gst_element_get_state (m_pipeline, nullptr, nullptr, 10 * GST_SECOND);
            LOG(info) << "=== Setting pipeline to PAUSED Done ===" << endl;
        }

        if (ret > 0)
        {
            LOG(info) << "Seek the file to postion: " << ret << endl;
            GstSeekFlags gstSeekFlags;
            if (m_isImageCapture == true)
            {
                gstSeekFlags = (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT);
            }
            else
            {
                gstSeekFlags = (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_ACCURATE | GST_SEEK_FLAG_TRICKMODE);
            }
            gst_element_seek (m_pipeline, 1, GST_FORMAT_TIME, gstSeekFlags, GST_SEEK_TYPE_SET, ret, GST_SEEK_TYPE_END, 0);
        }
        return 1;
    }
    else
    {
        LOG(warning) << "Need to stop the pipeline as no files are available for playback" << endl;
        return 0;
    }
}

VmsErrorCode GstNvVideoDecoder::update (std::string action, std::string seek_value, bool eos)
{
    gint64 duration = 0, position = 0;
    int64_t seek = 0;
    string overlay_new_start = "";
    VmsErrorCode return_err = VmsErrorCode::NoError;
    GstSeekFlags gstSeekFlags = (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT);

    LOG(info) << "Update for peerID = " << m_peerid << ", action = " << action << ", seek_value = " << seek_value << ", eos = " << eos  << endl;

    if (action == "reset_pipeline")
    {
        if (m_recordedPlayback)
        {
            LOG(warning) << "Resetting pipeline with position = " << m_position << endl;
            m_isSeeking = true;
            if (setFileAndUpdatePipelineState ())
            {
                position = m_position;
                goto seek_now;
            }
        }
        else
        {
            resetPipeline();
            goto exit;
        }
    }

    if (action == "rewind" && m_isOverlay == true)
    {
        LOG(info) << "Rewind not supported with overlay enabled" << endl;
        goto exit;
    }
    if (!eos && !gst_element_query_position  (m_pipeline, GST_FORMAT_TIME, &position))
    {
        LOG(error) << "Failed to query position of pipeline" << endl;
        position = 0;
    }
    if (position > MAX_SEGMENT_DURATION + 1 * GST_SECOND)
    {
        LOG(error) << "Fetched incorrect position of pipeline, position(nanosec) = " << position << ", resetting position to 0" << endl;
        position = 0;
    }
    m_position = position;
    LOG(info) << "position = " << position << endl;
    if (action == "first_seek")
    {
        seek = m_startTimeFirstFile;
        position = position + (seek * GST_SECOND) / 1000;
        this->m_action = "";
    }
    else if (action == "seek_backward")
    {
        if (m_recordedPlayback == false)
        {
            goto exit;
        }
        seek = -10;
        if (m_isOverlay)
        {
            /* new absolute position is current file start time
             *                         + current relative positon
             *                         + seek requested
             */
            gint64 current_pos_ms = position / GST_MSECOND;
            gint64 current_abs_position = m_fileNameArray[m_currentFileIndex].m_startTime + current_pos_ms;
            gint64 new_abs_position = current_abs_position + (seek * 1000);
            overlay_new_start = convertEpocToISO8601_2(new_abs_position * 1000);
        }
        /* If abs(seek) is greater the position then we can't
        ** seek in current file, set eos.
        ** eg: position = 6 and seek = -10, so we can't seek
        ** in current file
        */
        if (abs(seek) * GST_SECOND > position)
        {
            m_position_rewind = abs(seek) * GST_SECOND - position;
            eos = true;
        }
        else
        {
            position = position + seek * GST_SECOND;
            this->m_action = "";
        }

        if (eos)
        {
            m_action = "";
#ifdef UNIT_TEST
            m_currentFileIndex = (m_fileNameArray.size() + m_currentFileIndex - 1) % m_fileNameArray.size();
            {
#else
            if (m_currentFileIndex == 0)
            {
                /* Seek to m_startTimeFirstFile in case of first file */
                LOG(info) << "Reached start of the playback time" << endl;
                position = (m_startTimeFirstFile * GST_SECOND) / 1000;
            }
            else
            {
                m_currentFileIndex--;
#endif
                if (setFileAndUpdatePipelineState ())
                {
                    position = -m_position_rewind;
                    if (!gst_element_query_duration  (m_pipeline, GST_FORMAT_TIME, &duration))
                    {
                        LOG(error) << "Failed to query duration of pipeline, duration fetched = " << duration << endl;
                        return_err = VmsErrorCode::VMSInternalError;
                        goto exit;
                    }
                    position = duration - m_position_rewind;
                    m_position_rewind = 0;
                }
                else
                {
                    LOG(info) << "No file found for playback, returning" << endl;
                    goto exit;
                }
            }
        }
    }
    else if (action == "seek_forward")
    {
        if (m_recordedPlayback == false)
        {
            goto exit;
        }
        seek = 10;
        if (!gst_element_query_duration  (m_pipeline, GST_FORMAT_TIME, &duration))
        {
            LOG(error) << "Failed to query duration of pipeline, duration fetched = " << duration << endl;
            return_err = VmsErrorCode::VMSInternalError;
            goto exit;
        }
        /* If seek is greater the (duration - position) then we can't
        ** seek in current file, set eos.
        ** eg: position = 56, duration = 60 and seek = 10, so we can't seek
        ** in current file
        */
        if (seek * GST_SECOND > (duration - position))
        {
            m_position_forward = seek * GST_SECOND - (duration - position);
            eos = true;
        }
        else
        {
            position = position + seek * GST_SECOND;
            m_action = "";
        }
        if (eos)
        {
            m_action = "";
#ifdef UNIT_TEST
            m_currentFileIndex = (m_currentFileIndex + 1) % m_fileNameArray.size();
#else
            if (m_currentFileIndex == m_fileNameArray.size() - 1)
            {
                LOG(info) << "Reached end of the playback time" << endl;
                sendEosToSink();
                if(!m_continuosPlayback){
                        m_state = GST_STATE_NULL;
                        sendStateChangeWebSocketMessage(m_peerid, m_state == GST_STATE_PLAYING, "NOT_PLAYING");
                }
                goto exit;
            }
            m_currentFileIndex++;
#endif
            if (setFileAndUpdatePipelineState ())
            {
                position = m_position_forward;
                m_position_forward = 0;
            }
            else
            {
                LOG(info) << "No file found for playback, returning" << endl;
                goto exit;
            }
        }
    }
    else if (action == "rewind" || action == "fast_forward")
    {
        if (action == "rewind" && m_currentFileIndex == 0 && position == 0)
        {
            LOG(warning) << "Already reached start, cannot rewind more" << endl;
            goto exit;
        }

        bool update_decoder_element = true;
#if 0   /* TBD, Workaround for rewind use-case.
        Using sw decoder for rewind. Needs to be fixed. */
        if (m_govLength <= DEFAULT_GOV_LENGTH)
        {
            update_decoder_element = false;
        }
#endif
        gdouble prev_speed = m_playBackSpeed;
        if (m_recordedPlayback == false)
        {
            goto exit;
        }
        m_playBackSpeed = stringToInt(seek_value, 1);
        m_playBackSpeed = (action=="rewind") ? (-m_playBackSpeed) : m_playBackSpeed;
        /* Check if below product is -ve, this will be
        ** -ve only if one of the value is -ve and other is +ve
        ** this means we need to switch the decoder
        */
        if (prev_speed * m_playBackSpeed < 0 && update_decoder_element)
        {
            updateDecoderElement ();
        }

        if (eos)
        {
            if (action == "fast_forward")
            {
#ifdef UNIT_TEST
                m_currentFileIndex = (m_currentFileIndex + 1) % m_fileNameArray.size();
#else
                if (m_currentFileIndex == m_fileNameArray.size() - 1)
                {
                    LOG(info) << "Reached end of the playback time" << endl;
                    sendEosToSink();
                    if(!m_continuosPlayback){
                        m_state = GST_STATE_NULL;
                        sendStateChangeWebSocketMessage(m_peerid, m_state == GST_STATE_PLAYING, "NOT_PLAYING");
                    }
                    goto exit;
                }
                m_currentFileIndex++;
#endif
                if(setFileAndUpdatePipelineState ())
                {
                    position = 0;
                }
                else
                {
                    LOG(info) << "No file found for playback, returning" << endl;
                    goto exit;
                }
            }
            else if (action == "rewind")
            {
#ifdef UNIT_TEST
                m_currentFileIndex = (m_fileNameArray.size() + m_currentFileIndex - 1) % m_fileNameArray.size();
#else
                if (m_currentFileIndex == 0)
                {
                    LOG(info) << "Reached start of the playback time" << endl;
                    goto exit;
                }
                m_currentFileIndex--;
#endif
                if(setFileAndUpdatePipelineState ())
                {
                    if (!gst_element_query_duration  (m_pipeline, GST_FORMAT_TIME, &duration))
                    {
                        LOG(error) << "Failed to query duration of pipeline, duration fetched = " << duration << endl;
                        return_err = VmsErrorCode::VMSInternalError;
                        goto exit;
                    }
                    position = duration;
                }
                else
                {
                    LOG(info) << "No file found for playback, returning" << endl;
                    goto exit;
                }
            }

        }
    }
    else if (m_action == "" && m_playBackSpeed > 0 && eos)
    {
#ifdef UNIT_TEST
        m_currentFileIndex = (m_currentFileIndex + 1) % m_fileNameArray.size();
#else
        if (m_continuosPlayback == false)
        {
            if (m_currentFileIndex == m_fileNameArray.size() - 1)
            {
                LOG(info) << "Reached end of the playback time" << endl;
                sendEosToSink();
                m_state = GST_STATE_NULL;
                sendStateChangeWebSocketMessage(m_peerid, m_state == GST_STATE_PLAYING, "NOT_PLAYING");
                goto exit;
            }
        }
        m_currentFileIndex++;
#endif
        if(setFileAndUpdatePipelineState ())
        {
            position = 0;
        }
        else
        {
            LOG(info) << "No file found for playback, returning" << endl;
            goto exit;
        }
    }
    else if (action == "seek_forward_custom" || action == "seek_backward_custom")
    {
        if (m_recordedPlayback == false)
        {
            goto exit;
        }
        time_t epochs;
        if (!isNumber(seek_value))
        {
            epochs = getEpocTimeInMS(seek_value);
            if (epochs == 0)
            {
                LOG(error) << "Invalid time to seek\n";
                return_err = VmsErrorCode::InvalidParameterError;
                goto exit;
            }
            if (m_isOverlay)
            {
                overlay_new_start = seek_value;
            }
        }
        else
        {
            gint64 seek = stringToInt(seek_value, 0);
            if (seek == 0)
            {
                LOG(error) << "Invalid time to seek\n";
                return_err = VmsErrorCode::InvalidParameterError;
                goto exit;
            }
            // convert seek in seconds to milliseconds
            seek *= action == "seek_forward_custom" ? 1000 : -1000;
            gint64 current_pos_ms = position / GST_MSECOND;
            gint64 current_abs_position = m_fileNameArray[m_currentFileIndex].m_startTime + current_pos_ms;
            epochs = current_abs_position + seek;
            if (m_isOverlay)
            {
                overlay_new_start = convertEpocToISO8601_2(epochs * 1000);
            }

        }
        position = seekToEpoch(epochs) * GST_MSECOND;
        m_action = "";
    }
    else if (m_action == "" && m_playBackSpeed < 0 && eos)
    {
#ifdef UNIT_TEST
        m_currentFileIndex = (m_fileNameArray.size() + m_currentFileIndex - 1) % m_fileNameArray.size();
#else
        if (m_currentFileIndex == 0)
        {
            LOG(info) << "Reached start of the playback time" << endl;
            goto exit;
        }
        m_currentFileIndex--;
#endif
        if(setFileAndUpdatePipelineState ())
        {
            if (!gst_element_query_duration  (m_pipeline, GST_FORMAT_TIME, &duration))
            {
                LOG(error) << "Failed to query duration of pipeline, duration fetched = " << duration << endl;
                return_err = VmsErrorCode::VMSInternalError;
                goto exit;
            }
            position = duration;
        }
        else
        {
            LOG(info) << "No file found for playback, returning" << endl;
            goto exit;
        }
    }

seek_now:
    if (m_isImageCapture == false)
    {
        gstSeekFlags = (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT | GST_SEEK_FLAG_TRICKMODE);
    }
    if (m_playBackSpeed > 0)
    {
        gint64 stopTime = 0;
        if (m_isImageCapture == true)
        {
            position = m_startTimeFirstFile;
            gst_element_seek (m_pipeline, m_playBackSpeed, GST_FORMAT_TIME,
                            gstSeekFlags, GST_SEEK_TYPE_SET, position, GST_SEEK_TYPE_END, 0);
        }
        else if (m_currentFileIndex == 0 && position < (m_startTimeFirstFile * GST_SECOND) / 1000)
        {
            LOG(info) << "Trying to seek before start time, seeking to start" << endl;
            position = (m_startTimeFirstFile * GST_SECOND) / 1000;
            /* Currently m_currentFileIndex = (m_fileNameArray.size() - 1) = 0
            ** then stop time has to be the m_endTimeLastFile if it is non zero
            */
            if (m_fileNameArray.size() - 1  == m_currentFileIndex && m_endTimeLastFile)
            {
                stopTime = m_endTimeLastFile;
                gst_element_seek (m_pipeline, m_playBackSpeed, GST_FORMAT_TIME,
                    gstSeekFlags, GST_SEEK_TYPE_SET, position, GST_SEEK_TYPE_SET, (stopTime * GST_SECOND) / 1000);
            }
            else
            {
                gst_element_seek (m_pipeline, m_playBackSpeed, GST_FORMAT_TIME,
                    gstSeekFlags, GST_SEEK_TYPE_SET, position, GST_SEEK_TYPE_END, 0);
            }
        }
        else if (m_fileNameArray.size() - 1  == m_currentFileIndex && m_endTimeLastFile)
        {
            stopTime = m_endTimeLastFile;
            if (position > stopTime * GST_MSECOND)
            {
                LOG(info) << "Trying to seek after stop time, seeking to end" << endl;
                position = (m_endTimeLastFile * GST_SECOND) / 1000;
            }
            gst_element_seek (m_pipeline, m_playBackSpeed, GST_FORMAT_TIME,
                gstSeekFlags, GST_SEEK_TYPE_SET, position, GST_SEEK_TYPE_SET, (stopTime * GST_SECOND) / 1000);
        }
        else
        {
            gst_element_seek (m_pipeline, m_playBackSpeed, GST_FORMAT_TIME,
                            gstSeekFlags, GST_SEEK_TYPE_SET, position, GST_SEEK_TYPE_END, 0);
        }
    }
    else
    {
        int startTime = 0;
        if (m_currentFileIndex == 0)
        {
            startTime = m_startTimeFirstFile;
        }
        if (m_currentFileIndex == 0 && position < (m_startTimeFirstFile * GST_SECOND) / 1000)
        {
            LOG(info) << "Trying to seek before start time, seeking to start" << endl;
            position = (m_startTimeFirstFile * GST_SECOND) / 1000;
        }
        if (m_fileNameArray.size() - 1  == m_currentFileIndex && m_endTimeLastFile)
        {
            if (position > (m_endTimeLastFile * GST_SECOND) / 1000)
            {
                LOG(info) << "Trying to seek after stop time, seeking to end" << endl;
                position = (m_endTimeLastFile * GST_SECOND) / 1000;
            }
        }
        gst_element_seek (m_pipeline, m_playBackSpeed, GST_FORMAT_TIME,
                        gstSeekFlags, GST_SEEK_TYPE_SET, (startTime * GST_SECOND) / 1000, GST_SEEK_TYPE_SET, position);
    }

    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        m_isSwDecoder = m_playBackSpeed < 0 ? true : false;
    }
    gst_element_get_state (m_pipeline, nullptr, nullptr, 10 * GST_SECOND);
    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);

    /* Fetch new metadata after seek is completed to avoid stalling pipeline and
    ** confirm flushing of frames.
    **/
    if (m_isOverlay && !overlay_new_start.empty())
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(m_peerid);
        if(it != m_videoSinkList.end())
        {
            std::shared_ptr<VideoSinkInfo> sink = it->second;
            // Assumption: data flow is either decoder -> overlay
            // or decoder -> transform -> overlay
            sink->m_consumer->updateStartTime(overlay_new_start);
        }
    }
exit:
    m_isSeeking = false;
    return return_err;
}

bool GstNvVideoDecoder::checkSinksStatus ()
{
    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true && m_stop == false)
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
        for( it = m_videoSinkList.begin(); it != m_videoSinkList.end();)
        {
            shared_ptr<VideoSinkInfo> sink = it->second;
            if (sink->m_consumer.get() == nullptr)
            {
                LOG(error) << "consumer is NULL for " << m_peerid << " uri = " << m_uri << endl;
                it = m_videoSinkList.erase(it);
            }
            else
            {
                it++;
            }
        }
        return m_videoSinkList.size() == 0 ? true : false;
    }
    else
    {
        return false;
    }
}
GstFlowReturn GstNvVideoDecoder::processNewSampleFromSink(GstElement * appsink)
{
    GstSample *sample = nullptr;
    GstBuffer *gstBuffer = nullptr;
    GstMapInfo map;
    int targetWidth  = 0;
    int targetHeight = 0;
    uint64_t pts = 0;
    GstFlowReturn gst_flow_ret = GST_FLOW_OK;
    std::pair< int64_t, uint64_t > pair_frameid_ts;
    int64_t id = 0;

    /* Check if all sinks are null */
    bool are_all_sinks_null = checkSinksStatus ();
#ifdef JETSON_PLATFORM
    if (are_all_sinks_null == true && (m_recordedPlayback || GET_CONFIG().enable_ipc_path == false))
    {
        gst_flow_ret = GST_FLOW_ERROR;
        return gst_flow_ret;
    }
#else
    if (are_all_sinks_null == true)
    {
        gst_flow_ret = GST_FLOW_ERROR;
        return gst_flow_ret;
    }
#endif
    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));
    if (sample == nullptr)
    {
        LOG (info) << "Sample NULL received on app sink element" << endl;
        return GST_FLOW_OK;
    }

    if (gst_app_sink_is_eos((GstAppSink *)appsink))
    {
        LOG (info) << "EOS Received on app sink element" << endl;
        return GST_FLOW_EOS;
    }

    /* Get the buffer from sample */
    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG (warning) << "No more buffers available from app sink element" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    if (m_godsEyeView)
    {
        LOG(error) << "Creating cached frame" << endl;
        std::lock_guard<std::mutex> lock(m_frameMutex);
        // Store only the first frame
        if (m_cachedSample == nullptr) {
            m_cachedSample = gst_sample_copy(sample);
            // Stop the pipeline after getting the first frame
            // gst_element_set_state(m_pipeline, GST_STATE_READY);
            LOG(error) << "Cached frame created" << endl;
            m_cachedSampleCreated = true;
        }
    }

  // Frame rate control
    uint64_t current_time = GST_BUFFER_PTS (gstBuffer) / 100000;
    if (m_lastFrameTime > 0)
    {
        uint64_t time_diff = (current_time - m_lastFrameTime) / 10;
        double min_frame_interval = (1000.0 / m_frameRate); // Convert fps to milliseconds
        if (time_diff < (uint64_t)floor(min_frame_interval))
        {
            // Skip frame to maintain target frame rate
            /* Unref the sample */
            gst_sample_unref (sample);
            return GST_FLOW_OK;
        }
    }
    m_lastFrameTime = current_time;

    /* Get vst metadata of the buffer */
    GstNvVstMeta *meta = GST_NV_VST_META_GET (gstBuffer);
    int64_t meta_ts = 0;
    if (meta)
    {
        /* ns for live playback from onFrame */
        pts = meta->pts;
        id = meta->id;
        meta_ts = meta->ts;
        if (m_perfLogging)
        {
            int64_t currentTs = std::chrono::duration_cast<std::chrono::microseconds>
                (std::chrono::system_clock::now().time_since_epoch()).count();
            int64_t latency = currentTs - meta_ts;
            m_decStats.setLatency(latency);
        }
    }
    else
    {
        /* ns for recorded playback */
        pts = GST_BUFFER_PTS (gstBuffer);
    }

    pts = (meta_ts == 0 || m_recordedPlayback) ? getTimestampInMicroSecond(pts) : meta_ts;
    if (m_sensorType == SENSOR_TYPE_FILE)
    {
        bool isEpochTimestamp = ((GST_BUFFER_PTS (gstBuffer)/1000000) > 946684800000); // Jan 1, 2000 in epoch ms
        if (isEpochTimestamp)
        {
            pts = GST_BUFFER_PTS (gstBuffer)/1000;
        }
        else
        {
            pts = GST_BUFFER_PTS (gstBuffer) == 0 ? m_fileStartTime * 1000 : m_fileStartTime * 1000 + GST_BUFFER_PTS (gstBuffer) / 1000;
        }
        GST_BUFFER_PTS (gstBuffer) = pts * 1000;
    }

    if(m_recordedPlayback == false && m_sensorType != SENSOR_TYPE_NVSTREAM)
    {
        /* Live playback case */
        if (GET_CONFIG().enable_frame_drop && GET_CONFIG().enable_mega_simulation == false && m_godsEyeView == false)
        {
            uint64_t pts_millisec = pts/1000;
            uint64_t current_time = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
            if (pts_millisec < current_time)
            {
                uint64_t diff = current_time - pts_millisec;
                if (diff > m_maxDecLatency)
                {
                    string stream_id                     = getStreamIdFromUrl(m_uri, "/live/");
                    LOG(warning) << "appsink: Dropping frame Device Id = " << m_deviceId << " Stream Id = " << stream_id << " difference = " << diff << " PTS = " << pts_millisec << " and current Time = " << current_time << endl;
                    gst_sample_unref (sample);
                    return GST_FLOW_OK;
                }
            }
        }
        if (pts == 0)
        {
            gst_sample_unref (sample);
            return GST_FLOW_OK;
        }
    }

    // Get frame PTS for VST recorded playback
    if(m_recordedPlayback == true)
    {
        if (m_debug_logging_vod == true)
        {
            if (!m_vodDebugFile.fail())
            {
                m_vodDebugFile << pts << endl;
            }
        }
    }

    if (m_sensorType == SENSOR_TYPE_NVSTREAM)
    {
        if (GET_CONFIG().enable_rtsp_server_sei_metadata == false)
        {
            m_startTime.clear();
            m_endTime.clear();
        }
    }

    if (!m_startTime.empty())
    {
        uint64_t start_epochTime = (uint64_t) (getEpocTimeInMS(m_startTime) * 1000);
        /* Check if we want to drop frames that are received before start time */
        if (pts < start_epochTime)
        {
            LOG (info) << "Frames received before start time" << endl;
            /* Unref the sample */
            gst_sample_unref (sample);
            return GST_FLOW_OK;
        }
    }
    if (!m_endTime.empty())
    {
        uint64_t epochTime = (uint64_t) getEpocTimeInMS(m_endTime);
        /* Convert ms to us */
        epochTime = epochTime * 1000;
        /* Check if we want to drop frames that are received after end time */
        if (pts > epochTime)
        {
            LOG (info) << "Frames received after end time" << endl;
            /* Unref the sample */
            gst_sample_unref (sample);
            return GST_FLOW_EOS;
        }
    }

    if(m_recordedPlayback == false && m_debug_logging_live == true)
    {
        if (!m_liveDebugFile.fail())
        {
            m_liveDebugFile << pts << endl;
        }
    }

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG (warning) << "Map the gst buffer Failed" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }
    if (m_recordedPlayback && m_isSeeking == true)
    {
        LOG(warning) << "Dropping any incoming buffer as pipeline is currently seeking" << endl;
        goto exit;
    }
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);

        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
        for( it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
        {
            shared_ptr<VideoSinkInfo> sink = it->second;
            if (m_recordedPlayback)
            {
                sink->m_decoderStats.m_totalFrames++;
                /* Frame skip logic using Modulo operator */
                if (sink->m_decoderStats.m_totalFrames != 1 &&  (sink->m_decoderStats.m_totalFrames % m_playBackSpeed) != 0 )
                {
                    continue;
                }
            }
            /* Avoid sending frames to encoder
            ** till PLAY is not received for that peer id*/
            if (!sink->m_isSinkReady)
            {
                continue;
            }
            /* Create a YUV Buffer */
            targetWidth = sink->m_frameSize.m_width;
            targetHeight = sink->m_frameSize.m_height;
            if(targetWidth == 0 || targetHeight == 0)
            {
                LOG(error) << "targetWidth/targetHeight of frame is zero" << endl;
                continue;
            }

            if (m_recordedPlayback)
            {
                // TODO - Workaround for Rewind case, using software buffer mode.
                if (m_nvBufferMode != NvBufferModeSoftware && m_isSwDecoder)
                {
                    if (map.size == sizeof(NvBufSurface))
                    {
                        LOG(warning) << "Expecting software buffer but received hw buffer, skipping it" << endl;
                        gst_sample_unref (sample);
                        return GST_FLOW_OK;
                    }
                }
            }

            // SW encoder is handled in transform consumer class
            {
                string stream_id                     = getStreamIdFromUrl(m_uri, "/live/");
                if (sink->m_consumer.get() == nullptr)
                {
                    LOG(error) << "consumer is NULL for " << m_peerid << " uri = " << m_uri << endl;
                    continue;
                }
                std::shared_ptr<RawFrameParams> consumer_frame_data = std::make_shared<RawFrameParams>();
                consumer_frame_data->m_streamId     = stream_id;
                consumer_frame_data->m_targetWidth  = targetWidth;
                consumer_frame_data->m_targetHeight = targetHeight;
                consumer_frame_data->m_fd           = 0;
                consumer_frame_data->m_index        = 0;
                consumer_frame_data->m_sourceWidth  = m_sourceWidth;
                consumer_frame_data->m_sourceHeight = m_sourceHeight;
                consumer_frame_data->m_srcStrideY    = m_decoderStrideY;
                consumer_frame_data->m_srcStrideU    = m_decoderStrideU;
                consumer_frame_data->m_srcStrideV    = m_decoderStrideV;
                consumer_frame_data->m_sample       = sample;
                sink->m_consumer->onFrame (consumer_frame_data);
            }
        }
        if (m_decOutFrames < CONFIRM_DEC_OUT_FRAMES)
        {
            m_decOutFrames ++;
            if (m_decOutFrames == CONFIRM_DEC_OUT_FRAMES)
            {
#ifdef JETSON_PLATFORM
                if (GET_CONFIG().enable_ipc_path)
                {
                    std::lock_guard<std::mutex> listerner_lock(m_listenerMutex);
                    for (auto listener: m_listeners)
                    {
                        if (listener != nullptr)
                        {
                            listener->onDecoderPlayingStatus(m_uri);
                        }
                    }
                }
#endif
                if (m_state != GST_STATE_PLAYING )
                {
                    getstate_async();
                }
                LOG(info) << "Sending frames to webrtc ...: " <<  m_peerid << endl;
            }
        }
    }

    {
        std::unique_lock<std::mutex> lk(m_playStateLock);
        m_playbackState = STATE_PLAYING;
        m_playStateWait.notify_all();
    }

    // Store last served frame pts
    if (m_sensorType == SENSOR_TYPE_NVSTREAM &&
        GET_CONFIG().enable_rtsp_server_sei_metadata == true)
    {
        m_lastTS = id;
    }
    else
    {
        m_lastTS = pts;
    }

exit:
    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return gst_flow_ret;
}

GstFlowReturn GstNvVideoDecoder::processJpegImageFromSink(GstElement *appsink)
{
    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;
    GstFlowReturn ret = GST_FLOW_OK;
    int targetWidth  = 0;
    int targetHeight = 0;

    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));

    if (m_stop)
    {
        gst_sample_unref (sample);
        return GST_FLOW_EOS;
    }

    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG (info) << "EOS Received on app sink element" << endl;
            return GST_FLOW_OK;
        }
        return GST_FLOW_ERROR;
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

    if (m_recordedPlayback || m_sensorType == SENSOR_TYPE_MMS_ONVIF)
    {
        if (m_sensorType == SENSOR_TYPE_NVSTREAM)
        {
            int64_t gstBuffer_time = 0;
            if (GST_BUFFER_PTS (gstBuffer) != GST_CLOCK_TIME_NONE)
            {
                gstBuffer_time = (GST_BUFFER_PTS (gstBuffer) / 1000000);
            }
            LOG(info) << "GstBuffer time: " << gstBuffer_time << " m_startTime: " << m_startTime << endl;
            if (gstBuffer_time < stringToInt(m_startTime))
            {
                goto exit_func;
            }
        }
        else
        {
            int64_t buf_time = (GST_BUFFER_PTS (gstBuffer)/1000000);
            if (m_sensorType == SENSOR_TYPE_FILE)
            {
                bool isEpochTimestamp = buf_time > 946684800000; // Jan 1, 2000 in epoch ms
                if (!isEpochTimestamp)
                {
                    buf_time = GST_BUFFER_PTS (gstBuffer) == 0 ? m_fileStartTime : m_fileStartTime + GST_BUFFER_PTS (gstBuffer) / 1000000;
                }
                GST_BUFFER_PTS (gstBuffer) = buf_time * 1000 * 1000;
	        }
            // Direct PTS matching: the picture is the first decoded frame
            // whose PTS is at or after the requested epoch time.
            LOG(info) << "onFrame buf_time: " << buf_time << " epochStartTime: " << m_epochStartTime << endl;
            if (buf_time < m_epochStartTime)
            {
                goto exit_func;
            }
        }
    }

    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);

        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
        for( it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
        {
            shared_ptr<VideoSinkInfo> sink = it->second;
            if (m_recordedPlayback)
            {
                sink->m_decoderStats.m_totalFrames++;
                /* Frame skip logic using Modulo operator */
                if (sink->m_decoderStats.m_totalFrames != 1 &&  (sink->m_decoderStats.m_totalFrames % m_playBackSpeed) != 0 )
                {
                    continue;
                }
            }
            /* Create a YUV Buffer */
            targetWidth = sink->m_frameSize.m_width;
            targetHeight = sink->m_frameSize.m_height;
            if(targetWidth == 0 || targetHeight == 0)
            {
                LOG(error) << "targetWidth/targetHeight of frame is zero" << endl;
            }
            // For sw case, scaling is done by converter in image_enc class
            if (NvHwDetection::getInstance()->m_useNvV4l2Dec == false)
            {
                targetWidth = m_decoderWidth;
                targetHeight = m_decoderHeight;
            }

            if (m_recordedPlayback || m_sensorType == SENSOR_TYPE_MMS_ONVIF)
            {
                // TODO - Workaround for Rewind case, using software buffer mode.
                if (m_nvBufferMode != NvBufferModeSoftware && m_isSwDecoder)
                {
                    if (map.size == sizeof(NvBufSurface))
                    {
                        LOG(warning) << "Expecting software buffer but received hw buffer, skipping it" << endl;
                        gst_sample_unref (sample);
                        return GST_FLOW_OK;
                    }
                }
            }

            // /* Send a frame for image encoding */
            // SW encoder is handled in transform consumer class
            {
                string stream_id                     = getStreamIdFromUrl(m_uri, "/live/");
                if (sink->m_consumer.get() == nullptr)
                {
                    LOG(error) << "consumer is NULL for " << m_peerid << " uri = " << m_uri << endl;
                    continue;
                }
                std::shared_ptr<RawFrameParams> consumer_frame_data = std::make_shared<RawFrameParams>();
                consumer_frame_data->m_streamId     = stream_id;
                consumer_frame_data->m_targetWidth  = targetWidth;
                consumer_frame_data->m_targetHeight = targetHeight;
                consumer_frame_data->m_fd           = -1;
                consumer_frame_data->m_index        = 0;
                consumer_frame_data->m_sourceWidth  = m_decoderWidth;
                consumer_frame_data->m_sourceHeight = m_decoderHeight;
                consumer_frame_data->m_srcStrideY    = m_decoderStrideY;
                consumer_frame_data->m_srcStrideU    = m_decoderStrideU;
                consumer_frame_data->m_srcStrideV    = m_decoderStrideV;
                consumer_frame_data->m_sample       = sample;
                if (!m_isOverlay && NvHwDetection::getInstance()->m_useNvV4l2Dec)
                {
                    consumer_frame_data->m_targetColorFormat = NVBUF_COLOR_FORMAT_YUV420;
                }
                if(m_perfLogging)
                {
                    m_transcodeStats.finishProcessing();
                }
                sink->m_consumer->onFrame (consumer_frame_data);
            }
        }
    }
    m_stop = true;

exit_func:
    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return ret;
}

void GstNvVideoDecoder::setSourceFrameSize(uint32_t w, uint32_t h)
{
    m_sourceWidth = w;
    m_sourceHeight = h;
}

void GstNvVideoDecoder::setDecoderStride(int stride_y, int stride_u, int stride_v)
{
    m_decoderStrideY = stride_y;
    m_decoderStrideU = stride_u;
    m_decoderStrideV = stride_v;
}

bool GstNvVideoDecoder::isDRCAllowed ()
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

FrameSize GstNvVideoDecoder::handleDRC(const string& peerid, int targetPixels, int targetFPS)
{
    FrameSize frame_size;
    if (!isDRCAllowed ())
    {
        LOG(warning) << "DRC Request too early, in less than " << GET_CONFIG().webrtc_out_min_drc_interval << " secs" << endl;
        return frame_size;
    }

    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    FrameSize prev_size = g_resolutions[m_resolutionIndex];
    if(it != m_videoSinkList.end())
    {
        shared_ptr<VideoSinkInfo> sink = it->second;
        FrameSize source_frame_size;
#ifdef JETSON_PLATFORM
        // For any quality respect the webrtc out default resolution specified in config
        Resolution resolution;
        resolution = GET_CONFIG().webrtc_out_default_resolution;
        if (!resolution.empty() || NvHwDetection::getInstance()->m_useNvV4l2Enc == false)
        {
            // For Orin Nano scale-down the video
            int width = WIDTH_480p, height = HEIGHT_480p;

            if (!resolution.empty())
            {
                width = stringToInt(resolution.width, WIDTH_480p);
                // Replace with nearest multiple of 8 - Bug 4561987
                width = ((width + 7) >> 3) << 3;
                height = stringToInt(resolution.height, HEIGHT_480p);
            }
            source_frame_size.m_width = std::min(width, (int)m_sourceWidth);
            source_frame_size.m_height = std::min(height, (int)m_sourceHeight);

            // DRC not supported in Orin Nano. Bug 4619232
            sink->m_frameSize = source_frame_size;
            if (prev_size.m_width != sink->m_frameSize.m_width || prev_size.m_height != sink->m_frameSize.m_height)
            {
                LOG(warning) << "Peer id = " << peerid << " Changing resolution from : " << prev_size.m_width << "x" << prev_size.m_height << " ==> " << sink->m_frameSize.m_width << "x" << sink->m_frameSize.m_height << endl;
            }
            return sink->m_frameSize;
        }
#endif
        if(sink->m_quality == "auto")
        {
            size_t res_index = 0;
            source_frame_size.m_width = m_sourceWidth;
            source_frame_size.m_height = m_sourceHeight;
            while(true)
            {
                res_index = std::min(res_index, g_resolutions.size() - 1 );
                FrameSize size = g_resolutions[res_index];
                if((size.getPixels() <= targetPixels) || (res_index == (g_resolutions.size() - 1)))
                {
                    /* sink's framesize should be less than or equal to source frame size */
                    sink->m_frameSize = size.minimum(source_frame_size);
                    m_resolutionIndex = res_index;
                    break;
                }
                ++ res_index;
            }
        }
        LOG(warning) << "Peer id = " << peerid << " Changing resolution from : " << prev_size.m_width << "x" << prev_size.m_height << " ==> " << sink->m_frameSize.m_width << "x" << sink->m_frameSize.m_height << endl;
        return sink->m_frameSize;
    }
    return frame_size;
}

std::string GstNvVideoDecoder::getImageBuffer()
{
    std::unique_lock<std::mutex> lk(m_imgBufferLock);
    if (m_stop == false)
    {
        auto until = std::chrono::system_clock::now() + MAX_BUFFER_WAIT_TIMEOUT;
        if (m_imgBufferWait.wait_until(lk, until, [this]{ return (m_stop.load()); }) == false)
        {
            LOG(error) << "Image Buffer wait timeout occured" << endl;
            return std::string();
        }
    }
    return m_imgBuffer;
}

bool GstNvVideoDecoder::getPosition_internal (gint64* position)
{
    if (m_pipeline)
    {
        return gst_element_query_position (m_pipeline, GST_FORMAT_TIME, position);
    }
    return false;
}

bool GstNvVideoDecoder::getDuration_internal (gint64* duration)
{
    if (m_pipeline)
    {
        return gst_element_query_duration  (m_pipeline, GST_FORMAT_TIME, duration);
    }
    return false;
}

bool GstNvVideoDecoder::getPosition(gint64& position)
{
    std::shared_ptr<DecoderData> in_data(new DecoderData);
    in_data->m_msgId = m_peerid;
    std::shared_ptr<EventLoopOutData> out_data(new EventLoopOutData);
    in_data->m_outResult = out_data;
    in_data->m_expectResult = true;
    in_data->m_taskName = "get_position";
    m_eventLoop.postMsg(in_data);

    position = *(gint64*)out_data->m_outData.get();
    return position;
}

bool GstNvVideoDecoder::getDuration (gint64& duration)
{
    std::shared_ptr<DecoderData> in_data(new DecoderData);
    in_data->m_msgId = m_peerid;
    std::shared_ptr<EventLoopOutData> out_data(new EventLoopOutData);
    in_data->m_outResult = out_data;
    in_data->m_expectResult = true;
    in_data->m_taskName = "get_duration";
    m_eventLoop.postMsg(in_data);

    duration = *(gint64*)out_data->m_outData.get();
    return duration;
}

gint64 GstNvVideoDecoder::getAbsPosition()
{
    gint64 abs_position = 0;
    if (getPosition(abs_position) == false)
    {
        LOG(error) << "Unable to get position" << endl;
    }
    else if (m_fileNameArray.size() > m_currentFileIndex)
    {
        uint64_t file_start_time = m_fileNameArray[m_currentFileIndex].m_startTime;
        abs_position = file_start_time + abs_position/GST_MSECOND;
    }
    else
    {
        abs_position /= GST_MSECOND;
    }
    return abs_position;
}

gint64 GstNvVideoDecoder::seekToEpoch (time_t final_epoch)
{
    gint64 seek_in_file;
    LOG(info) << "Seek to epoch: " << final_epoch << endl;

    if (m_sensorType == SENSOR_TYPE_NVSTREAM)
    {
        seek_in_file = final_epoch/1000;
        return seek_in_file;
    }

    // For CloudStream: Forward seek to producer, don't reset decoder/encoder
    if (m_isCloudStream && m_producer)
    {
        LOG(info) << "CloudStream: Forwarding seek to producer instead of resetting decoder" << endl;

        // Calculate seek position in seconds from first file
        if (!m_fileNameArray.empty()) {
            double seekPositionSeconds = (final_epoch - m_fileNameArray[0].m_startTime) / 1000.0;

            LOG(info) << "CloudStream: Seeking producer to " << seekPositionSeconds << "s "
                      << "(epoch: " << final_epoch << ")" << endl;

            // Flush appsrc to clear old buffers
            gst_element_send_event(m_source, gst_event_new_flush_start());
            gst_element_send_event(m_source, gst_event_new_flush_stop(TRUE));

            // Cast to CloudStreamProducer to access seek() method
            auto cloudProducer = std::dynamic_pointer_cast<CloudStreamProducer>(m_producer);
            if (cloudProducer) {
                // Forward seek to producer (CloudStreamProducer handles filesrc/concat seeking)
                bool seekSuccess = cloudProducer->seek(seekPositionSeconds);
                if (seekSuccess) {
                    LOG(info) << "CloudStream: Producer seek successful, new data will flow" << endl;
                }
            }
        }
        return 0;
    }

    VideoFileInfo search_epoch;
    search_epoch.m_startTime = final_epoch;
    vector<VideoFileInfo>::iterator it = upper_bound(m_fileNameArray.begin(),
                                        m_fileNameArray.end(), search_epoch, compare);
    if (it != m_fileNameArray.begin())
    {
        --it;
    }
    uint32_t next_file_num = it - m_fileNameArray.begin();

    // Change file only if required
    if (next_file_num != m_currentFileIndex)
    {
        m_currentFileIndex = next_file_num;
        setFileAndUpdatePipelineState();
    }
    uint64_t file_start_time = m_fileNameArray[m_currentFileIndex].m_startTime;
    seek_in_file = final_epoch - file_start_time;
    return seek_in_file;
}

// Return last served frame TS in microseconds
uint64_t GstNvVideoDecoder::getLastTS()
{
    return m_lastTS;
}

int64_t GstNvVideoDecoder::getFileStartTime()
{
    return m_fileStartTime;
}

uint32_t GstNvVideoDecoder::getDurationStream()
{
    if (m_currentFileIndex < m_fileNameArray.size())
    {
        return m_fileNameArray[m_currentFileIndex].m_duration;
    }
    return 0;
}

string GstNvVideoDecoder::getSensorName()
{
    return m_sensorName;
}

string GstNvVideoDecoder::getSensorId()
{
    return m_deviceId;
}

void GstNvVideoDecoder::sendEosToSink()
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
    for(it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
    {
        shared_ptr<VideoSinkInfo> sink = it->second;
        sink->m_consumer->onLastFrame();
    }
}

void GstNvVideoDecoder::flushDecoderPipeline()
{
    LOG(info) << "flushDecoderPipeline() called for peer: " << m_peerid << endl;
    if (!m_pipeline)
    {
        LOG(warning) << "flushDecoderPipeline: Pipeline is NULL" << endl;
        return;
    }
    // Send EOS to appsrc - this will propagate through the pipeline naturally,
    // allowing all pending buffers to be processed before the EOS reaches the sink.
    if (m_source)
    {
        LOG(info) << "flushDecoderPipeline: Sending EOS to appsrc (will drain pipeline)" << endl;
        GstFlowReturn ret = gst_app_src_end_of_stream(GST_APP_SRC(m_source));
        if (ret != GST_FLOW_OK)
        {
            LOG(warning) << "flushDecoderPipeline: gst_app_src_end_of_stream returned " << ret << endl;
        }
    }
    LOG(info) << "flushDecoderPipeline() completed for peer: " << m_peerid << endl;
}

#ifdef UNIT_TEST
void GstNvVideoDecoder::setPeerid(const string& peer_id)
{
    m_peerid = peer_id;
}

uint32_t GstNvVideoDecoder::getCurrentFileNumber()
{
    return m_currentFileIndex;
}

std::vector<VideoFileInfo> GstNvVideoDecoder::getFileInfo()
{
    return m_fileNameArray;
}
#endif

void GstNvVideoDecoder::addFrameTs(int64_t ts)
{
    std::lock_guard<std::mutex> lock(m_frameTsQueueLock);
    std::pair <int64_t, uint64_t> pair_frameid_ts = std::make_pair(-1, ts);
    m_frameTsQueue.push(pair_frameid_ts);
    m_frameTsQueueCond.notify_all();
}

int GstNvVideoDecoder::createJpegDecoderPipeline(const std::string& filepath) {
    GstBus* bus = nullptr;
    GstCaps* convert_caps = nullptr;
    GstAppSinkCallbacks callbacks = {nullptr};
    GstElement* filesrc = nullptr;
    GstElement* decodebin = nullptr;
    GstElement* jpegdec = nullptr;
    GstElement* sw_videoconvert = nullptr;
    GstElement* nv_converter = nullptr;
    GstElement* capsfilter = nullptr;
    bool pipeline_owns_elements = false;

    m_error = false;
    m_frameNum = 0;

    // filesrc reads floor_map_file_path; extension and access must use the same path (not filepath / m_uri).
    const std::string& floor_map_path = GET_CONFIG().floor_map_file_path;
    if (floor_map_path.empty()) {
        LOG(error) << "Floor map file path is not configured" << endl;
        return -1;
    }
    if (access(floor_map_path.c_str(), R_OK) != 0) {
        LOG(error) << "Cannot access floor map file: " << floor_map_path << endl;
        return -1;
    }

    std::string ext = getFileExtension(floor_map_path);
    if (!ext.empty() && ext[0] == '.') {
        ext = ext.substr(1);
    }
    std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
    const bool svg_floor_map = (ext == "svg");
#if defined(JETSON_PLATFORM)
    const bool floor_map_sw_jpegdec = false;
#else
    // x86 / SBSA: avoid decodebin autoplugging to nvjpegdec for floor map JPEG
    const bool floor_map_sw_jpegdec =
        (ext == "jpg" || ext == "jpeg" || ext == "jpe");
#endif

    LOG(info) << "Creating floor map decode pipeline for: " << floor_map_path
              << (filepath != floor_map_path ? (" (request context: " + filepath + ")") : std::string())
              << (svg_floor_map ? " (SVG: decodebin ! videoconvert ! nvvideoconvert)" :
                  floor_map_sw_jpegdec ? " (JPEG: jpegdec ! nvvideoconvert)" :
                                  " (decodebin ! nvvideoconvert)") << endl;

    if (gst_is_initialized() == false) {
        gst_init(nullptr, nullptr);
    }

    // Create pipeline elements
    m_pipeline = gst_pipeline_new("converter-pipeline");
    filesrc = gst_element_factory_make("filesrc", "file-source");
    if (svg_floor_map) {
        decodebin = gst_element_factory_make("decodebin", "floor-map-decodebin");
        sw_videoconvert = gst_element_factory_make("videoconvert", "floor-map-sw-convert");
    } else if (floor_map_sw_jpegdec) {
        jpegdec = gst_element_factory_make("jpegdec", "floor-map-jpegdec");
    } else {
        decodebin = gst_element_factory_make("decodebin", "floor-map-decodebin");
    }
    nv_converter = make_floor_map_nv_converter();
    capsfilter = gst_element_factory_make("capsfilter", "capsfilter");
    m_sink = gst_element_factory_make("appsink", "floor-map-sink");

    if (!m_pipeline || !filesrc || !nv_converter || !capsfilter || !m_sink) {
        LOG(error) << "Failed to create floor map pipeline elements" << endl;
        goto error;
    }
    if (svg_floor_map && (!decodebin || !sw_videoconvert)) {
        LOG(error) << "Failed to create floor map SVG pipeline elements" << endl;
        goto error;
    }
    if (floor_map_sw_jpegdec && !jpegdec) {
        LOG(error) << "Failed to create jpegdec for floor map pipeline" << endl;
        goto error;
    }
    if (!svg_floor_map && !floor_map_sw_jpegdec && !decodebin) {
        LOG(error) << "Failed to create decodebin for floor map pipeline" << endl;
        goto error;
    }

    g_object_set(G_OBJECT(filesrc),
                "location", GET_CONFIG().floor_map_file_path.c_str(),
                "start-index", 0,
                "stop-index", 0,
                nullptr);

    // Configure appsink
    g_object_set(G_OBJECT(m_sink),
        "sync", FALSE,
        "drop", FALSE,
        "max-buffers", 1,
        "emit-signals", TRUE,
        nullptr);

    // Configure capsfilter
    {
        const std::string caps_string =
            "video/x-raw(memory:NVMM), format=(string)NV12, width=(int)1920, height=(int)1080";
        convert_caps = gst_caps_from_string(caps_string.c_str());
        if (!convert_caps) {
            LOG(error) << "Failed to build caps for floor map pipeline" << endl;
            goto error;
        }
        g_object_set(G_OBJECT(capsfilter), "caps", convert_caps, NULL);
        g_object_set(G_OBJECT(m_sink), "caps", convert_caps, NULL);
        gst_caps_unref(convert_caps);
        convert_caps = nullptr;
    }

    if (svg_floor_map) {
        gst_bin_add_many(GST_BIN(m_pipeline),
            filesrc, decodebin, sw_videoconvert, nv_converter, capsfilter, m_sink,
            NULL);
        pipeline_owns_elements = true;
        g_signal_connect(decodebin, "pad-added", G_CALLBACK(on_pad_added), sw_videoconvert);
        if (!gst_element_link(filesrc, decodebin)
            || !gst_element_link_many(sw_videoconvert, nv_converter, capsfilter, m_sink, NULL)) {
            LOG(error) << "Failed to link floor map SVG pipeline" << endl;
            goto error;
        }
    } else if (floor_map_sw_jpegdec) {
        gst_bin_add_many(GST_BIN(m_pipeline),
            filesrc, jpegdec, nv_converter, capsfilter, m_sink,
            NULL);
        pipeline_owns_elements = true;
        if (!gst_element_link_many(filesrc, jpegdec, nv_converter, capsfilter, m_sink, NULL)) {
            LOG(error) << "Failed to link floor map JPEG (jpegdec) pipeline" << endl;
            goto error;
        }
    } else {
        gst_bin_add_many(GST_BIN(m_pipeline),
            filesrc, decodebin, nv_converter, capsfilter, m_sink,
            NULL);
        pipeline_owns_elements = true;
        g_signal_connect(decodebin, "pad-added", G_CALLBACK(on_pad_added), nv_converter);
        if (!gst_element_link(filesrc, decodebin)
            || !gst_element_link_many(nv_converter, capsfilter, m_sink, NULL)) {
            LOG(error) << "Failed to link floor map raster pipeline" << endl;
            goto error;
        }
    }
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );

    bus = gst_pipeline_get_bus(GST_PIPELINE(m_pipeline));
    if (!bus) {
        LOG(error) << "Failed to get BUS of Decoder pipeline" << endl;
        goto error;
    }
    m_bus_watch_id = gst_bus_add_watch (bus, busWatch, (void*)this);
    gst_object_unref(bus);
    bus = nullptr;

    // Set up new sample callback
    callbacks.new_sample = (GstFlowReturn (*)(GstAppSink*, gpointer))on_new_sample_from_sink;
    gst_app_sink_set_callbacks(GST_APP_SINK(m_sink), &callbacks, this, nullptr);

    m_running = true;
    m_frameThread = std::thread(&GstNvVideoDecoder::sendCachedFrameLoop, this);

    return 0;

error:
    if (pipeline_owns_elements) {
        if (m_pipeline) {
            gst_object_unref(GST_OBJECT(m_pipeline));
            m_pipeline = nullptr;
            m_sink = nullptr;
        }
    } else {
        if (filesrc) {
            gst_object_unref(GST_OBJECT(filesrc));
            filesrc = nullptr;
        }
        if (decodebin) {
            gst_object_unref(GST_OBJECT(decodebin));
            decodebin = nullptr;
        }
        if (jpegdec) {
            gst_object_unref(GST_OBJECT(jpegdec));
            jpegdec = nullptr;
        }
        if (sw_videoconvert) {
            gst_object_unref(GST_OBJECT(sw_videoconvert));
            sw_videoconvert = nullptr;
        }
        if (nv_converter) {
            gst_object_unref(GST_OBJECT(nv_converter));
            nv_converter = nullptr;
        }
        if (capsfilter) {
            gst_object_unref(GST_OBJECT(capsfilter));
            capsfilter = nullptr;
        }
        if (m_sink) {
            gst_object_unref(GST_OBJECT(m_sink));
            m_sink = nullptr;
        }
        if (m_pipeline) {
            gst_object_unref(GST_OBJECT(m_pipeline));
            m_pipeline = nullptr;
        }
    }
    return -1;
}


void GstNvVideoDecoder::sendCachedFrameLoop() {
    while (m_stop == false) {
        {
            if (m_cachedSample && m_cachedSampleCreated)
            {
                for( auto it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
                {
                    shared_ptr<VideoSinkInfo> sink = it->second;
                    std::lock_guard<std::mutex> lock(m_frameMutex);
                    if (m_cachedSample && sink && sink->m_consumer) {
                        std::shared_ptr<RawFrameParams> consumer_frame_data = std::make_shared<RawFrameParams>();
                        consumer_frame_data->m_streamId = m_peerid;
                        consumer_frame_data->m_targetWidth = m_sourceWidth;
                        consumer_frame_data->m_targetHeight = m_sourceHeight;
                        consumer_frame_data->m_sourceWidth = m_sourceWidth;
                        consumer_frame_data->m_sourceHeight = m_sourceHeight;
                        consumer_frame_data->m_sample = gst_sample_copy(m_cachedSample);

                        sink->m_consumer->onFrame(consumer_frame_data);
                    }
                }
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(33));  // ~30fps
    }
}

#if 0
    /* Add VST metadata to Gst Buffer */
    GstNvVstMeta *meta = GST_NV_VST_META_ADD (gstbuffer);
    if (meta)
    {
        meta->pts = getTimestampInNanoSecond(ts);
        meta->id = id;
        meta->ts = std::chrono::duration_cast<std::chrono::microseconds>
            (std::chrono::system_clock::now().time_since_epoch()).count();
        if (m_perfLogging)
        {
            {
                std::lock_guard<std::mutex> lock(m_videoSinkLock);

                std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
                for(it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
                {
                    shared_ptr<VideoSinkInfo> sink = it->second;
                    sink->m_consumer->startStatsProcessing();
                }
            }
        }
    }


#endif

// Unified storage configuration and management methods
bool GstNvVideoDecoder::initUnifiedStorageReader()
{
    ReplayPeerConnection* replayPeerConnection = GET_PEERCONNECTION_REPLAY_MNGR();
    if (replayPeerConnection == nullptr)
    {
        LOG(error) << "Replay Peer Connection module is not loaded" << endl;
        return false;
    }

    m_unifiedStorageReader = replayPeerConnection->getUnifiedStorageReader();
    if (!m_unifiedStorageReader)
    {
        LOG(error) << "Failed to get unified storage reader" << endl;
        return false;
    }
    return true;
}



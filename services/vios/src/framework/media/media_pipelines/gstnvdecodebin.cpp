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

#include "gstnvdecodebin.h"
#include <gst/gst.h>
#include <gst/video/video.h>
#include "mm_utils.h"
#include "nvhwdetection.h"
#include "gstnvvideodecoder.h"

#define GST_DEBUG_PROBE_BUFFER_COUNT 10
#define DECODER_EXTRA_SURFACES 6
#define CUDA_DEC_MEM_TYPE_DEVICE 0
#define MAX_PADS_WAIT_TIMEOUT 5s
#define MAX_IMAGE_WIDTH  3840
#define MAX_IMAGE_HEIGHT 2160

namespace
{
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

    /* called when decodebin has created all pads */
    static void cb_no_more_pads(GstElement *element, gpointer user_data)
    {
        LOG(info) << "All decodebin pads are created" << endl;
        NvDecodeBin *video_decoder = (NvDecodeBin *)user_data;
        std::lock_guard<std::mutex> lk(video_decoder->m_padsCreatedLock);
        video_decoder->m_padsCreated = true;
        video_decoder->m_padsCreatedCv.notify_all();
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

    static GstPadProbeReturn
    pad_probe_cb (GstPad * pad, GstPadProbeInfo * info, gpointer user_data)
    {
        NvDecodeBin* video_decoder = (NvDecodeBin*) user_data;
        return video_decoder->padProbeCB (pad, info);
    }


    static GstPadProbeReturn decodebin_src_pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
    {
        NvDecodeBin* nvDecodeBin = (NvDecodeBin*)user_data;
        if (nvDecodeBin)
        {
            if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
            {
                if (nvDecodeBin->m_imageCapture)
                {
                    GstBuffer* gstBuffer = GST_PAD_PROBE_INFO_BUFFER(info);
                    if (gstBuffer != nullptr && nvDecodeBin->m_monitoFramesInProbe)
                    {
                        int64_t buf_time = (GST_BUFFER_PTS (gstBuffer)/1000000);
                        LOG(info) << "[decoder-out-Probe] gstbuffer_time: " << buf_time << ", requested time:" << nvDecodeBin->m_imageEpochTime << endl;
                        if (buf_time >= nvDecodeBin->m_imageEpochTime)
                        {
                            /* Received the needed frame, remove the probe now */
                            nvDecodeBin->m_monitoFramesInProbe = false;
                            return GST_PAD_PROBE_REMOVE;
                        }
                        else
                        {
                            return GST_PAD_PROBE_DROP;
                        }
                    }
                }
                else
                {
                    nvDecodeBin->m_decoder_out_probe_count++;
                    /* On first buffer, extract stride and pass to parent for frame_data */
                    if (nvDecodeBin->m_decoder_out_probe_count == 1 && nvDecodeBin->m_parent)
                    {
                        GstBuffer* buf = GST_PAD_PROBE_INFO_BUFFER(info);
                        GstCaps* caps = gst_pad_get_current_caps(pad);
                        if (!caps)
                            caps = gst_pad_query_caps(pad, nullptr);
                        if (caps && buf)
                        {
                            GstStructure* s = gst_caps_get_structure(caps, 0);
                            const gchar* struct_name = s ? gst_structure_get_name(s) : nullptr;
                            bool is_video = false;
                            if (struct_name != nullptr)
                            {
                                is_video = g_str_has_prefix(struct_name, "video/");
                            }
                            if (is_video)
                            {
                                GstVideoInfo video_info;
                                if (gst_video_info_from_caps(&video_info, caps))
                                {
                                    GstVideoFrame frame;
                                    if (gst_video_frame_map(&frame, &video_info, buf, GST_MAP_READ))
                                    {
                                        int stride_y = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, 0);
                                        int stride_u;
                                        int stride_v;
                                        if (GST_VIDEO_INFO_N_PLANES(&video_info) >= 3)
                                        {
                                            /* I420/YV12 etc.: separate U and V planes */
                                            stride_u = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, 1);
                                            stride_v = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, 2);
                                        }
                                        else
                                        {
                                            /* NV12 etc.: single interleaved UV plane; use plane 1 stride for both */
                                            stride_u = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, 1);
                                            stride_v = stride_u;
                                        }
                                        nvDecodeBin->m_parent->setDecoderStride(stride_y, stride_u, stride_v);
                                        LOG(info) << "[decoder-src-stride] Y=" << stride_y << " U=" << stride_u << " V=" << stride_v << endl;
                                        gst_video_frame_unmap(&frame);
                                    }
                                }
                            }
                            gst_caps_unref(caps);
                        }
                        else if (caps)
                            gst_caps_unref(caps);
                    }
                    if (nvDecodeBin->m_decoder_out_probe_count <= GST_DEBUG_PROBE_BUFFER_COUNT)
                    {
                        LOG(info) << "[decodebin-output-probe] Received buffer:" << nvDecodeBin->m_decoder_out_probe_count << endl;
                    }
                    else
                    {
                        /* Remove probe, as we don't need it anymore */
                        return GST_PAD_PROBE_REMOVE;
                    }
                }
            }
        }
        return GST_PAD_PROBE_OK;
    }

    static GstPadProbeReturn decodebin_src_pad_event_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
    {
        NvDecodeBin* nvDecodeBin = (NvDecodeBin*)user_data;
        gboolean res = false;
        GstEvent *event = nullptr;
        if (nvDecodeBin)
        {
            event = GST_PAD_PROBE_INFO_EVENT(info);
            if (event && !nvDecodeBin->m_decoderSrcWidth && !nvDecodeBin->m_decoderSrcHeight)
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
                        LOG (info) << "Resolution information of decoder output: "<< width << "x" << height << endl;
                        nvDecodeBin->m_decoderSrcWidth = width;
                        nvDecodeBin->m_decoderSrcHeight = height;
                        nvDecodeBin->setResolution(width, height);
                    }
                    else
                    {
                        LOG (error) << "gst_caps_get_structure failed" << endl;
                    }
                }
            }
        }
        return GST_PAD_PROBE_OK;
    }

    static void on_pad_added_decodebin (GstElement *element1, GstPad *pad, gpointer data)
    {
        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, decodebin_src_pad_cb, data, nullptr);
        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_EVENT_BOTH, decodebin_src_pad_event_cb, data, nullptr);
    }

    static GstPadProbeReturn decodebin_sink_pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
    {
        NvDecodeBin* nvDecodeBin = (NvDecodeBin*)user_data;
        if (nvDecodeBin)
        {
            nvDecodeBin->m_decoder_in_probe_count++;
            if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
            {
                if (nvDecodeBin->m_decoder_in_probe_count <= GST_DEBUG_PROBE_BUFFER_COUNT)
                {
                    LOG(info) << "[decodebin-input-probe] Received buffer:" << nvDecodeBin->m_decoder_in_probe_count << endl;
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

}   // unnamed namespace

void NvDecodeBin::setResolution(int width, int height)
{
    if (m_parent)
    {
        m_parent->setResolution(width, height);
    }
}

int NvDecodeBin::waitForAllPadsCreation()
{
    std::unique_lock<std::mutex> lk(m_padsCreatedLock);
    LOG(info) << "waiting for decodebin pads creation" << endl;
    if (m_padsCreated == false)
    {
        auto until = std::chrono::system_clock::now() + MAX_PADS_WAIT_TIMEOUT;
        if (m_padsCreatedCv.wait_until(lk, until, [this] { return m_padsCreated == true; }) == false)
        {
            LOG(error) << "Pads wait timeout occured" << endl;
        }
    }
    return 0;
}

GstPadProbeReturn NvDecodeBin::padProbeCB (GstPad * pad, GstPadProbeInfo * info)
{

    /* remove the probe first */
    gst_pad_remove_probe (pad, GST_PAD_PROBE_INFO_ID (info));

    gst_element_set_state (m_decoder, GST_STATE_NULL);
    gst_element_get_state (m_decoder, nullptr, nullptr, 10 * GST_SECOND);

    /* remove unlinks automatically */
    gst_bin_remove (GST_BIN (m_decodeBin), m_decoder);
    m_decoder = nullptr;
    gchar *element_name;
    if (m_useNvV4l2Dec == false || m_playBackSpeed < 0)
    {
        element_name = (gchar *)SW_AV_DECODER;
    }
    else
    {
        element_name = (gchar *)NV_V4L2_DECODER;
    }
    m_decoder = gst_element_factory_make (element_name, nullptr);
    LOG(info) << "Selecting decoder element: " << element_name << endl;
    if (m_decoder == nullptr)
    {
        /* if creating NV_V4L2_DECODER fails, then select SW_AV_DECODER */
        m_decoder = gst_element_factory_make (SW_AV_DECODER, nullptr);
        LOG(info) << "Selecting decoder element: " << SW_AV_DECODER << endl;
    }
    gst_bin_add (GST_BIN (m_decodeBin), m_decoder);
    link_decoder(m_decoder, m_queue);

    /* Setting caps on capsfilter after decoder */
    GstElementFactory *factory = GST_ELEMENT_GET_CLASS(m_decoder)->elementfactory;
    if (m_useNvV4l2Dec && m_playBackSpeed > 0 && !g_strcmp0(GST_OBJECT_NAME(factory), NV_V4L2_DECODER))
    {
        /* Setting below properties for NV_V4L2_DECODER
        ** The value 6 is set to match the buffers allocated in Encoder class
        */
        g_object_set (G_OBJECT (m_decoder), "num-extra-surfaces", DECODER_EXTRA_SURFACES  , nullptr);
#ifndef JETSON_PLATFORM 
        g_object_set (G_OBJECT (m_decoder), "cudadec-memtype"   , CUDA_DEC_MEM_TYPE_DEVICE, nullptr);
        g_object_set (G_OBJECT (m_decoder), "gpu-id"   , g_gpuIndex, nullptr);
#endif
    }

    gst_element_set_state (m_decoder, GST_STATE_PLAYING);
    gst_element_get_state (m_decoder, nullptr, nullptr, 10 * GST_SECOND);

    return GST_PAD_PROBE_OK;
}

static NvGstAutoplugSelectResult
autoplug_select_sw (GstElement * dbin, GstPad * pad, GstCaps * caps,
     GstElementFactory * factory, gpointer data)
{
    NvGstAutoplugSelectResult ret = NVGST_AUTOPLUG_SELECT_TRY;
    const gchar *klass = gst_element_factory_get_klass (factory);
    /* Check only Decode Klass */
    if (strstr (klass, "Decode"))
    {
        /* Check only Decode Video Klass */
        if (strstr (klass, "Video"))
        {
            /* Return SKIP value for Hardware Decoders */
            if (!strcmp ((GST_OBJECT_NAME (factory)), NV_V4L2_DECODER) || !strcmp ((GST_OBJECT_NAME (factory)), OMX_DECODER) ||
                !strcmp ((GST_OBJECT_NAME (factory)), NVCODEC_H264_DECODER) || !strcmp ((GST_OBJECT_NAME (factory)), NVCODEC_H265_DECODER))
            {
                LOG(info) << "Return SKIP value for Hardware Decoders" << endl;
                ret = NVGST_AUTOPLUG_SELECT_SKIP;
            }
        }
    }
    return ret;
}

static void
dbin_element_added (GstElement * dbin, GstElement * element, gpointer data)
{
    NvDecodeBin* nvDecodeBin = (NvDecodeBin*)data;
    GstElementFactory* factory = gst_element_get_factory(element);
    if (factory)
    {
        if (!strcmp ((GST_OBJECT_NAME (factory)), NV_V4L2_DECODER))
        {
    #ifndef JETSON_PLATFORM 
            g_object_set (G_OBJECT (element), "num-extra-surfaces", DECODER_EXTRA_SURFACES  , nullptr);
            g_object_set (G_OBJECT (element), "cudadec-memtype"   , CUDA_DEC_MEM_TYPE_DEVICE, nullptr);
            g_object_set (G_OBJECT (element), "gpu-id"   , g_gpuIndex, nullptr);
    #else
            g_object_set (G_OBJECT (element), "num-extra-surfaces", 10, nullptr);
    #endif
            // Apply low-latency mode based on B-frame presence from stream config
            if (GET_CONFIG().enable_dec_low_latency_mode)
            {
                bool hasBframes = false;

                // Get B-frame presence from parent decoder's stream configuration
                if (nvDecodeBin && nvDecodeBin->m_parent)
                {
                    hasBframes = nvDecodeBin->m_parent->hasBframes();
                }

                if (!hasBframes)
                {
                    LOG(info) << "B-frames NOT present (from stream config), enabling low-latency decoder mode" << endl;
#ifndef JETSON_PLATFORM
                    g_object_set(G_OBJECT(element), "low-latency-mode", true, nullptr);
#else
                    g_object_set(G_OBJECT(element), "disable-dpb", true, nullptr);
#endif
                }
                else
                {
                    LOG(info) << "B-frames present (from stream config), keeping default decoder settings" << endl;
                }
            }
        }
    }
    return;

}

void NvDecodeBin::updateDecoderElement (gdouble playback_speed)
{
    static GstPad *blockpad;
    /* Below code is intentionally under if 0 as we need to handle rewind case later */
#if 0
    blockpad = gst_element_get_static_pad (m_filterBeforeDec, "src");
#endif
    if (blockpad == nullptr)
    {
        LOG(error) << "Unable to switch decoder" << endl;
        return;
    }
    m_playBackSpeed = playback_speed;
    gst_pad_add_probe (blockpad, GST_PAD_PROBE_TYPE_BLOCK_DOWNSTREAM, pad_probe_cb, this, nullptr);
    gst_object_unref (blockpad);
}

NvDecodeBin::NvDecodeBin(DecoderBase* parent, const string codec)
{
    m_parent = parent;
    m_codec = codec;
    if (codec.empty())
    {
        m_codec = "h264";
    }
    m_useNvV4l2Dec = NvHwDetection::getInstance()->m_useNvV4l2Dec;
}

NvDecodeBin::~NvDecodeBin()
{

}

GstElement* NvDecodeBin::create(bool is_image_capture)
{
    LOG (info) << "Creating Gstreamer Decodebin pipeline" << endl;

    string   bitstream_type;
    GstPad *sink_pad = nullptr, *source_pad = nullptr, *ghost_sourcepad = nullptr, *ghost_sinkpad = nullptr;

    m_decodeBin          = gst_bin_new              ("NvDecodeBin");
    m_decoder            = gst_element_factory_make ("decodebin", nullptr);
    m_queue              = gst_element_factory_make ("queue", nullptr);

    if (!m_decodeBin || !m_decoder || !m_queue)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return nullptr;
    }

    m_imageCapture = is_image_capture;
    gst_bin_add_many (GST_BIN (m_decodeBin), m_decoder, m_queue, nullptr);

    if (!link_decoder(m_decoder, m_queue))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return nullptr;
    }

    if (!g_signal_connect(m_decoder, "no-more-pads", G_CALLBACK(cb_no_more_pads), this))
    {
        LOG (error) << "g_signal_connect failed for decodebin" << endl;
        return nullptr;
    }

    source_pad = gst_element_get_static_pad (m_queue, "src");
    if (source_pad)
    {
        ghost_sourcepad = gst_ghost_pad_new ("src", source_pad);
        gst_pad_set_active (ghost_sourcepad, TRUE);
        gst_element_add_pad (m_decodeBin, ghost_sourcepad);
        gst_object_unref (source_pad);
    }
    else
    {
        LOG(error) << "Failed to get src pad from filter after Decoder in Nv Decode Bin" << endl;
        return nullptr;
    }

    sink_pad = gst_element_get_static_pad (m_decoder, "sink");
    if (sink_pad)
    {
        ghost_sinkpad = gst_ghost_pad_new ("sink", sink_pad);
        gst_pad_set_active (ghost_sinkpad, TRUE);
        gst_element_add_pad (m_decodeBin, ghost_sinkpad);
        gst_object_unref (sink_pad);
    }
    else
    {
        LOG(error) << "Failed to get sink pad from parser in Nv Decode Bin" << endl;
        return nullptr;
    }

    if (GET_CONFIG().enable_gst_debug_probes)
    {
        g_signal_connect (G_OBJECT(m_decoder), "pad-added", G_CALLBACK (on_pad_added_decodebin), this);

        GstPad* decodebin_sink_pad = nullptr;
        decodebin_sink_pad = gst_element_get_static_pad (GST_ELEMENT(m_decoder), "sink");
        if (!decodebin_sink_pad)
        {
            LOG(error) << "Failed to get sink pad of decoderbin sink_pad." << endl;
        }
        else
        {
            gst_pad_add_probe(decodebin_sink_pad, GST_PAD_PROBE_TYPE_BUFFER, decodebin_sink_pad_cb, this, nullptr);
            gst_object_unref(decodebin_sink_pad);
        }
    }

    if (m_useNvV4l2Dec == true)
    {
#ifdef JETSON_PLATFORM
        Resolution resolution;
        resolution = GET_CONFIG().webrtc_out_default_resolution;
#endif
        /* Adding seperate CB for HW mode, as we need to set properties of HW decoder */
        g_signal_connect (G_OBJECT(m_decoder), "element-added", G_CALLBACK (dbin_element_added), this);
    }
    else
    {
        g_signal_connect (G_OBJECT(m_decoder), "autoplug-select", G_CALLBACK (autoplug_select_sw), nullptr);
    }

    return m_decodeBin;
}
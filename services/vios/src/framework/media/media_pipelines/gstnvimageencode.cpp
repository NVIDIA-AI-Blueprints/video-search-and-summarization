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

#include "gstnvimageencode.h"

#define MAX_BUFFER_WAIT_TIMEOUT 10s

NvImageEncode::NvImageEncode()
{
    LOG(info) << "Creating NvImageEncode()" << endl;
}

NvImageEncode::~NvImageEncode()
{
    LOG(info) << "~NvImageEncode()" << endl;
}

gboolean encoder_busWatch (GstBus *bus, GstMessage *message, gpointer data)
{
    GError *error = nullptr;
    gchar *name, *debug = nullptr;
    NvImageEncode* nvImageEncoder = (NvImageEncode*)data;
    if (nvImageEncoder == nullptr)
    {
        LOG(error) << "Image encoder object is NULL" << endl;
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
            else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ASYNC_DONE ||
                     GST_MESSAGE_TYPE (message) == GST_MESSAGE_STATE_CHANGED)
            {
                if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ASYNC_DONE)
                {
                    LOG(info) << "Received ASYNC_DONE" << endl;
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

GstFlowReturn NvImageEncode::processNewSampleFromSink(GstElement *appsink)
{
    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;
    GstFlowReturn ret = GST_FLOW_OK;

    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));
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

    LOG(info) << "Received image buffer with size: " << map.size << endl;
    std::string image_buf(map.size, 1);
    memmove (&image_buf[0], map.data, map.size);
    std::lock_guard<std::mutex> lock(m_imgBufferLock);
    m_imgBuffer = image_buf;
    std::string().swap(image_buf);
    m_imgBufferWait.notify_all();

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return ret;
}

std::string NvImageEncode::getImageBuffer()
{
    std::unique_lock<std::mutex> lk(m_imgBufferLock);
    auto until = std::chrono::system_clock::now() + MAX_BUFFER_WAIT_TIMEOUT;
    if (m_imgBufferWait.wait_until(lk, until, [this]{ return !m_imgBuffer.empty(); }) == false)
    {
        LOG(error) << "Image Buffer wait timeout occured" << endl;
        return std::string();
    }
    return m_imgBuffer;
}

static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, NvImageEncode* nvImageEncoder)
{
   if (nvImageEncoder)
    {
        return nvImageEncoder->processNewSampleFromSink(appsink);
    }
   return GST_FLOW_ERROR;
}

int NvImageEncode::create(int width, int height)
{
    LOG (info) << "Creating Gstreamer image encoder pipeline " << width << " " << height << endl;
    GstBus* bus = nullptr;

    m_pipeline  = gst_pipeline_new         ("imageenc_pipeline");
    m_source    = gst_element_factory_make ("appsrc", nullptr);
    m_filtersrc       = gst_element_factory_make ("capsfilter"    , nullptr);
    m_filter    = gst_element_factory_make ("capsfilter", nullptr);
    m_scale    = gst_element_factory_make ("videoscale", nullptr);
#ifndef JETSON_PLATFORM
    m_image_encoder   = gst_element_factory_make ("jpegenc", nullptr);
#else
    /* Found crash with nvjpegenc on below Rosie version. So using jpegenc
    * Rosie Version  : 64,  L4T BSP Version: R35.1.0,  JetPack Version: 5.0.2 */

    //m_image_encoder   = gst_element_factory_make ("nvjpegenc", nullptr);
    m_image_encoder   = gst_element_factory_make ("jpegenc", nullptr);
#endif
    m_sink      = gst_element_factory_make ("appsink", nullptr);

    if (!m_source || !m_filtersrc || !m_scale || !m_filter || !m_image_encoder || !m_sink)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return -1;
    }

    GstCaps* filtercaps = gst_caps_new_simple ("video/x-raw",
        "format", G_TYPE_STRING, "I420",
        "width",  G_TYPE_INT,    width,
        "height", G_TYPE_INT,    height,
        "framerate", GST_TYPE_FRACTION, 30, 1, nullptr);
    g_object_set (G_OBJECT (m_filtersrc), "caps", filtercaps, nullptr);
    g_object_set (G_OBJECT (m_filter), "caps", filtercaps, nullptr);
    gst_caps_unref (filtercaps);

    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_filtersrc, m_scale, m_filter, m_image_encoder, m_sink, nullptr);

    if (!gst_element_link_many (m_source, m_filtersrc, m_scale, m_filter, m_image_encoder, m_sink, nullptr))
    {
        LOG (error) << "Gst Elements could not be linked" << endl;
        return -1;
    }
    g_object_set (G_OBJECT (m_sink), "emit-signals", TRUE, "sync", TRUE, nullptr);
    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this))
    {
        LOG(error) << "Error in g_signal_connect of new-sample" << endl;
        return -1;
    }

    bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!bus)
    {
        LOG(error) << "Failed to get BUS of Decoder pipeline" << endl;
        return -1;
    }
    m_bus_watch_id = gst_bus_add_watch (bus, encoder_busWatch, (void*)this);
    gst_object_unref(bus);

    /* Setting Pipeline to playing state*/
    GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
    {
        LOG (error) << "gst_element_set_state failed. " << endl;
    }
    else if (gstStateChangeRet == GST_STATE_CHANGE_ASYNC)
    {
        LOG (info) << "GST_STATE_CHANGE_ASYNC. " << endl;
    }
    else
    {
        LOG (info) << "State change success " << endl;
    }
    LOG (info) << "Created Gstreamer image encoder pipeline" << endl;
    return 0;
}

void NvImageEncode::destroy()
{
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }

    if (m_bus_watch_id != G_MAXUINT)
    {
        g_source_remove (m_bus_watch_id);
        m_bus_watch_id = G_MAXUINT;
    }
    LOG (info) << "Terminated Gstreamer image encoder pipeline" << endl;
}

void NvImageEncode::setCaps(int width, int height)
{
    if (width != 0 && height != 0)
    {
        GstCaps* filtercaps = gst_caps_new_simple ("video/x-raw",
        "format", G_TYPE_STRING, "I420",
        "width",  G_TYPE_INT,    width,
        "height", G_TYPE_INT,    height,
        "framerate", GST_TYPE_FRACTION, 30, 1, nullptr);
        g_object_set (G_OBJECT (m_filter), "caps", filtercaps, nullptr);
        gst_caps_unref (filtercaps);
    }
    LOG(info) << "Resize scale width x height: " << width << "x" << height << endl;
}

void NvImageEncode::setSourceCaps(int width, int height)
{
    if (width != 0 && height != 0)
    {
        GstCaps* filtercaps = gst_caps_new_simple ("video/x-raw",
        "format", G_TYPE_STRING, "I420",
        "width",  G_TYPE_INT,    width,
        "height", G_TYPE_INT,    height,
        "framerate", GST_TYPE_FRACTION, 30, 1, nullptr);
        g_object_set (G_OBJECT (m_filtersrc), "caps", filtercaps, nullptr);
        gst_caps_unref (filtercaps);
    }
    LOG(info) << "Resize source width x height: " << width << "x" << height << endl;
}

void NvImageEncode::onFrame(const unsigned char *buffer, ssize_t size)
{
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

    /* Push the Gst Buffer in pipeline */
    LOG(info) << "Pushing buffer to encoder size: " << size << endl;
    gst_app_src_push_buffer((GstAppSrc*)m_source, gstbuffer);
    return;
}

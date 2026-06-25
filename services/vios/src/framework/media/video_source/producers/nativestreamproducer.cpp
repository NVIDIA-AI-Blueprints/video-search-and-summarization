/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "media_consumer.h"
#include "nativestreamproducer.h"
#include "native_stream_monitor.h"
#include "mm_utils.h"

static std::array<FrameSize, 7> g_resolutions = { FrameSize(WIDTH_2160p, HEIGHT_2160p),
                                                  FrameSize(WIDTH_1080p, HEIGHT_1080p),
                                                  FrameSize(WIDTH_720p, HEIGHT_720p),
                                                  FrameSize(WIDTH_480p, HEIGHT_480p),
                                                  FrameSize(WIDTH_360p, HEIGHT_360p),
                                                  FrameSize(WIDTH_240p, HEIGHT_240p),
                                                  FrameSize(WIDTH_144p, HEIGHT_144p)
                                                 };

static const std::vector<PropertyDescription> tnrModeDescriptions = {
    {GST_NVCAM_NR_OFF, "NoiseReduction_Off"},
    {GST_NVCAM_NR_FAST, "NoiseReduction_Fast"},
    {GST_NVCAM_NR_HIGHQUALITY, "NoiseReduction_HighQuality"}
};

static const std::vector<PropertyDescription> wbModeDescriptions = {
    {GST_NVCAM_WB_MODE_OFF, "off"},
    {GST_NVCAM_WB_MODE_AUTO, "auto"},
    {GST_NVCAM_WB_MODE_INCANDESCENT, "incandescent"},
    {GST_NVCAM_WB_MODE_FLUORESCENT, "fluorescent"},
    {GST_NVCAM_WB_MODE_WARM_FLUORESCENT, "warm-fluorescent"},
    {GST_NVCAM_WB_MODE_DAYLIGHT, "daylight"},
    {GST_NVCAM_WB_MODE_CLOUDY_DAYLIGHT, "cloudy-daylight"},
    {GST_NVCAM_WB_MODE_TWILIGHT, "twilight"},
    {GST_NVCAM_WB_MODE_SHADE, "shade"},
    {GST_NVCAM_WB_MODE_MANUAL, "manual"}
};

static const std::vector<PropertyDescription> aeAntiBandingDescriptions = {
    {GST_NVCAM_AEANTIBANDING_OFF, "AeAntibandingMode_Off"},
    {GST_NVCAM_AEANTIBANDING_AUTO, "AeAntibandingMode_Auto"},
    {GST_NVCAM_AEANTIBANDING_50HZ, "AeAntibandingMode_50HZ"},
    {GST_NVCAM_AEANTIBANDING_60HZ, "AeAntibandingMode_60HZ"}
};

static const std::vector<PropertyDescription> eeModeDescriptions = {
    {GST_NVCAM_EE_OFF, "EdgeEnhancement_Off"},
    {GST_NVCAM_EE_FAST, "EdgeEnhancement_Fast"},
    {GST_NVCAM_EE_HIGHQUALITY, "EdgeEnhancement_HighQuality"}
};

NativeStreamProducer::NativeStreamProducer(std::string streamId, std::string sensorName, const string location)
    : m_streamId (streamId)
    , m_sensorName (sensorName)
    , m_location (location)
    , m_resetAttempts (0)
{
    m_state = GST_STATE_NULL;
    updateEncoderConfig();
#ifdef DUMP_FRAMES
    // Path to the output file
    m_yuvFilePath = string("./") + m_sensorName + string(".yuv");
    m_yuvFile.open(m_yuvFilePath, std::ios::out | std::ios::binary | std::ios::app);
    if (!m_yuvFile.is_open())
    {
        LOG(error) << "Failed to open yuv file: " << m_yuvFilePath << endl;
    }
    else
    {
        LOG(info) << "Successfully opened yuv file: " << m_yuvFilePath << endl;
    }

    m_bitstreamFilePath = string("./") + m_sensorName + string(".") + m_videoCodec;
    m_bitstreamFile.open(m_bitstreamFilePath, std::ios::out | std::ios::binary | std::ios::app);
    if (!m_bitstreamFile.is_open())
    {
        LOG(error) << "Failed to open bitstream file: " << m_bitstreamFilePath << endl;
    }
    else
    {
        LOG(info) << "Successfully opened bitstream file: " << m_bitstreamFilePath << endl;
    }
#endif
}

NativeStreamProducer::~NativeStreamProducer()
{
  try
  {
    {
        std::lock_guard<std::mutex> lock(m_pipelineLock);
        if (m_pipeline != nullptr)
        {
            stopPipeline();
        }
    }

    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        m_videoSinkList.clear();
    }
    m_stop = true;

#ifdef DUMP_FRAMES
    if (m_yuvFile.is_open())
    {
        m_yuvFile.close();
        LOG(info) << "Closed yuv file:" << m_yuvFilePath << std::endl;
    }

    if (m_bitstreamFile.is_open())
    {
        m_bitstreamFile.close();
        LOG(info) << "Closed bitstream file:" << m_bitstreamFilePath << std::endl;
    }
#endif

    LOG(info) << "~NativeStreamProducer called for streamId:" << m_streamId << " sensorName:" << m_sensorName << " location:" << m_location << endl;
  } catch (const std::exception& e) {
    try { LOG(error) << "Exception in ~NativeStreamProducer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
  } catch (...) {
    try { LOG(error) << "Unknown exception in ~NativeStreamProducer" << endl; } catch (...) { (void)std::current_exception(); }
  }
}

void NativeStreamProducer::setOptions(const std::map<std::string, std::string, std::less<>> &opts)
{
    if ( opts.find("peerid") != opts.end() )
    {
        m_peerid = opts.at("peerid");
    }
    if ( opts.find("sensor_type") != opts.end() )
    {
        m_sensorType = opts.at("sensor_type");
    }
    if ( opts.find("source_width") != opts.end() )
    {
        m_sourceWidth = stringToInt(opts.at("source_width"), WIDTH_1080p);
    }
    if ( opts.find("source_height") != opts.end() )
    {
        m_sourceHeight = stringToInt(opts.at("source_height"), HEIGHT_1080p);
    }
}

int NativeStreamProducer::getVideoDeviceIndex(const std::string& devicePath)
{
    // Check if the device path starts with "/dev/video"
    const std::string prefix = "/dev/video";
    if (devicePath.substr(0, prefix.size()) != prefix)
    {
        throw std::invalid_argument("Invalid device path");
    }

    // Extract the index part
    std::string indexStr = devicePath.substr(prefix.size());
    for (char c : indexStr)
    {
        if (!isdigit(c))
        {
            throw std::invalid_argument("Invalid device index");
        }
    }

    // Convert the extracted index part to an integer
    return std::stoi(indexStr);
}

static GstFlowReturn on_new_sample_from_sink(GstElement * appsink, NativeStreamProducer* source)
{
    if (source)
    {
        return source->onNewSampleYUV(appsink);
    }
    return GST_FLOW_ERROR;
}

GstFlowReturn NativeStreamProducer::onNewSampleYUV(GstElement * appsink)
{
    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;
    int targetWidth  = 0;
    int targetHeight = 0;
    GstFlowReturn gst_flow_ret = GST_FLOW_OK;

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

#ifdef DUMP_FRAMES
    /* Write YUV data to file */
    if (m_yuvFile.is_open())
    {
        LOG (info) << "Writing into the yuv file size:" << map.size << " sensorName:" << m_sensorName << endl;
        m_yuvFile.write(reinterpret_cast<char*>(map.data), map.size);
        if (!m_yuvFile.good())
        {
            LOG (warning) << "Failed to write YUV data to file" << endl;
        }
    }
    else
    {
        LOG (error) << "Failed to open YUV file for writing" << endl;
    }
#endif

    /* Check if all sinks are null */
    bool is_sinkPresent = isSinkPresent ();
    if (is_sinkPresent)
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
        for( it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
        {
            shared_ptr<VideoSinkInfo> sink = it->second;
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

            if (sink->m_consumer.get() == nullptr)
            {
                LOG(error) << "consumer is NULL for " << m_peerid << ", streamId = " << m_streamId << endl;
                continue;
            }
            std::shared_ptr<RawFrameParams> consumer_frame_data = std::make_shared<RawFrameParams>();
            consumer_frame_data->m_streamId     = m_streamId;
            consumer_frame_data->m_targetWidth  = targetWidth;
            consumer_frame_data->m_targetHeight = targetHeight;
            consumer_frame_data->m_fd           = 0;
            consumer_frame_data->m_index        = 0;
            consumer_frame_data->m_sourceWidth  = m_sourceWidth;
            consumer_frame_data->m_sourceHeight = m_sourceHeight;
#ifndef JETSON_PLATFORM
            consumer_frame_data->m_sourceLayout = NVBUF_LAYOUT_BLOCK_LINEAR;
            consumer_frame_data->m_targetLayout = NVBUF_LAYOUT_PITCH;
#endif
            consumer_frame_data->m_sample       = sample;
            sink->m_consumer->onFrame (consumer_frame_data);
        }
    }

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);
    return gst_flow_ret;
}

GstFlowReturn NativeStreamProducer::onNewSampleBitstream(GstElement * appsink, NativeStreamProducer* source)
{
    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;
    if (source)
    {
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

        H265NaluType nal_type = parseH265NaluType(map.data, map.size);
        if (nal_type == H265NaluType::AUD_NUT)
        {
            gst_sample_unref (sample);
            gst_buffer_unmap (gstBuffer, &map);
            return GST_FLOW_OK;
        }

#ifdef DUMP_FRAMES
        /* Write bitstream data to file */
        if (source->m_bitstreamFile.is_open())
        {
            LOG (info) << "Writing into the bitstream file size:" << map.size << " nal_type:" << (int)nal_type << " sensorName:" << source->m_sensorName << endl;
            source->m_bitstreamFile.write(reinterpret_cast<char*>(map.data), map.size);
            if (!source->m_bitstreamFile.good())
            {
                LOG(warning) << "Failed to write bitstream data to file" << endl;
            }
        }
        else
        {
            LOG(error) << "Failed to open bitstream file for writing" << endl;
        }
#endif

        // Call Onframe for all bitstream consumers
        struct timeval presentationTime;
        gettimeofday(&presentationTime, nullptr);
        std::lock_guard<std::mutex> guard(source->m_consumerLock);
        for (shared_ptr<IMediaDataConsumer> consumer : source->m_bitstreamConsumersList)
        {
            if (consumer && consumer->getConsumerMediaType() == MediaTypeVideo)
            {
                FrameParams frame_params;
                frame_params.m_media            = "video";
                frame_params.m_codec            = "h265";
                frame_params.m_buffer           = map.data;
                frame_params.m_size             = map.size;
                frame_params.m_needParsing      = true;
                frame_params.m_presentationTime = presentationTime;
                consumer->onFrame(frame_params);
            }
        }

        /* Unmap the gst buffer */
        gst_buffer_unmap (gstBuffer, &map);

        /* Unref the sample */
        gst_sample_unref (sample);
    }
    return GST_FLOW_OK;
}

gboolean NativeStreamProducer::busWatch(GstBus *bus, GstMessage *msg, gpointer data)
{
    NativeStreamProducer *streamProducer = static_cast<NativeStreamProducer*>(data);
    switch (GST_MESSAGE_TYPE(msg))
    {
        case GST_MESSAGE_EOS:
            LOG (info) << "End of stream for sensorName:" << streamProducer->m_sensorName << " streamId:" << streamProducer->m_streamId << endl;
            streamProducer->resetPipeline();
            break;

        case GST_MESSAGE_ERROR:
            GError *err;
            gchar *debug_info;
            gst_message_parse_error(msg, &err, &debug_info);
            LOG (error) << "Error received from element for sensorName:" << streamProducer->m_sensorName << " streamId:" << streamProducer->m_streamId << endl;
            LOG (error) << "Error received from element " << GST_OBJECT_NAME(msg->src) << ": " << err->message << endl;
            LOG (error) << "Debugging information: " << (debug_info ? debug_info : "none") << endl;
            g_clear_error(&err);
            g_free(debug_info);
            streamProducer->resetPipeline();
            break;

        case GST_MESSAGE_ASYNC_DONE:
        {
            LOG(info) << "GST_MESSAGE_ASYNC_DONE for sensorName:" << streamProducer->m_sensorName << " streamId:" << streamProducer->m_streamId << endl;
        }
        default:
            break;
    }
    return true; // Continue receiving messages
}

void NativeStreamProducer::resetPipeline()
{
    if (m_resetAttempts >= MAX_RESET_ATTEMPTS)
    {
        stopPipeline();
        LOG(info) << "Max reset attempts reached. Not resetting pipeline for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        return;
    }

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
                LOG(info) << "Reset dec consumer for streamId:" << m_streamId << " name:" << m_sensorName << endl;;
                sink->m_consumer->reset();
            }
        }
    }

    {
        std::lock_guard<std::mutex> lock(m_pipelineLock);
        if (m_pipeline)
        {
            LOG(info) <<"Setting pipeline to NULL for streamId:" << m_streamId << " name:" << m_sensorName << " attempts:" << m_resetAttempts << endl;
            gst_element_set_state (m_pipeline, GST_STATE_NULL);
            gst_element_get_state (m_pipeline, nullptr, nullptr, 10 * GST_SECOND);
            LOG(info) << "Setting pipeline to NULL Done for streamId:" << m_streamId << " name:" << m_sensorName << " attempts:" << m_resetAttempts << endl;

            LOG(info) <<"Setting pipeline to PLAYING for streamId:" << m_streamId << " name:" << m_sensorName << " attempts:" << m_resetAttempts << endl;
            gst_element_set_state (m_pipeline, GST_STATE_PLAYING);

            m_resetAttempts++;
        }
    }
}

void NativeStreamProducer::stopPipeline()
{
    m_stop = true;
    std::lock_guard<std::mutex> lock(m_pipelineLock);
    if (m_pipeline != nullptr)
    {
        LOG(info) << "Sending EOS to gst pipeline for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        gboolean res = gst_element_send_event(m_pipeline, gst_event_new_eos());
        if (!res)
        {
            LOG(info) << "Error occurred! EOS signal cannot be sent!" << endl;
        }

        // Send EOS to encoder
        {
            std::lock_guard<std::mutex> lock(m_videoSinkLock);
            std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(m_peerid);
            if (it != m_videoSinkList.end())
            {
                shared_ptr<VideoSinkInfo> sink = it->second;
                sink->m_consumer->onLastFrame();
            }
        }

        for (shared_ptr<IMediaDataConsumer> consumer : m_bitstreamConsumersList)
        {
            if (consumer && consumer->getConsumerMediaType() == MediaTypeVideo)
            {
                LOG(warning) << "Sending EOS for streamID:" << m_streamId << " name:" << m_sensorName << endl;
                FrameParams frame_params;
                frame_params.m_buffer  = nullptr;
                frame_params.m_size    = 0;
                consumer->onFrame(frame_params);
            }
        }

        if (m_busWatchId != G_MAXUINT)
        {
            g_source_remove(m_busWatchId);
            m_busWatchId = G_MAXUINT;
        }

        gst_element_set_state(m_pipeline, GST_STATE_NULL);
        gst_element_get_state(m_pipeline, nullptr, nullptr, GST_SECOND * 5);
        gst_object_unref(m_pipeline);
        m_pipeline = nullptr;
        m_state = GST_STATE_NULL;

        NativeStreamMonitor::getInstance()->updateSensorStatus(m_streamId);
    }
    else
    {
        LOG(error) << "Pipeline is not started for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    }
}

void NativeStreamProducer::addConsumer(shared_ptr<IMediaDataConsumer> consumer, ConsumerStreamType consumerStreamType)
{
    if (m_state != GST_STATE_PLAYING)
    {
        if (startPipeline())
        {
            LOG(info) << "Started NativeStreamProducer pipeline for streamId:" << m_streamId << " sensorName:" << m_sensorName << " location:" << m_location << endl;
        }
        else
        {
            LOG(error) << "Failed to start NativeStreamProducer pipeline for streamId:" << m_streamId << " sensorName:" << m_sensorName << " location:" << m_location << endl;
        }
    }

    {
        std::lock_guard<std::mutex> lock(m_consumerLock);
        if(consumer && consumer->getConsumerMediaType() == MediaTypeVideo)
        {
            if (consumerStreamType == BITSTREAM_H265)
            {
                if (std::find(m_bitstreamConsumersList.begin(), m_bitstreamConsumersList.end(), consumer) == m_bitstreamConsumersList.end())
                {
                    m_bitstreamConsumersList.push_back(consumer);
                    LOG(info) << "Added consumer for " << m_streamId << " into h265 list" << " count:" << m_bitstreamConsumersList.size() << endl;
                }
            }
            else if (consumerStreamType == RAW_NV12)
            {
                if (std::find(m_yuvConsumersList.begin(), m_yuvConsumersList.end(), consumer) == m_yuvConsumersList.end())
                {
                    m_yuvConsumersList.push_back(consumer);
                    LOG(info) << "Added consumer for " << m_streamId << " into yuv list" << " count:" << m_yuvConsumersList.size() << endl;
                }
            }
        }
        else
        {
            LOG(error) << "Failed to add consumer for media Type:" << consumer->getConsumerMediaType() << endl;
        }
    }
}

void NativeStreamProducer::removeConsumer (shared_ptr<IMediaDataConsumer> consumer, ConsumerStreamType consumerStreamType)
{
    std::lock_guard<std::mutex> lock(m_consumerLock);
    if (consumerStreamType == BITSTREAM_H265)
    {
        m_bitstreamConsumersList.erase(std::remove(m_bitstreamConsumersList.begin(), m_bitstreamConsumersList.end(), consumer), m_bitstreamConsumersList.end());
        LOG(info) << "Removed consumer for " << m_streamId << " from the h265 list" << " count:" << m_bitstreamConsumersList.size() << endl;
    }
    else if (consumerStreamType == RAW_NV12)
    {
        m_yuvConsumersList.erase(std::remove(m_yuvConsumersList.begin(), m_yuvConsumersList.end(), consumer), m_yuvConsumersList.end());
        LOG(info) << "Removed consumer for " << m_streamId << " from the yuv list" << " count:" << m_yuvConsumersList.size() << endl;
    }
}

void NativeStreamProducer::setConsumer(const string& peerid, std::shared_ptr<IMediaDataConsumer> consumer)
{
    /* Add Consumer to map with consumer_id = "VIDEO_TYPE" + peerID */
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if(it == m_videoSinkList.end())
    {
        shared_ptr<VideoSinkInfo> sink (new VideoSinkInfo);
        sink->m_consumer = consumer;
        sink->m_consumer->setOriginalFrameSize(m_sourceWidth, m_sourceHeight);
        m_videoSinkList[peerid] = sink;
    }
    LOG(info) << "Sink list size = " << m_videoSinkList.size() << " for " << m_streamId << endl;
}

void NativeStreamProducer::setConsumerReady(const string& peerid, bool isReady)
{
    /* search peer in map to set start play flag */
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSinkInfo> sink = it->second;
        sink->m_isSinkReady = isReady;
    }
}

FrameSize NativeStreamProducer::qualityToFrameSize(const string& quality)
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

void NativeStreamProducer::setQuality(const std::string& peerid, const std::string& quality)
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
    m_stop = false;
    LOG(info) << "Sink list size = " << m_videoSinkList.size() << " for " << m_streamId << endl;
}

void NativeStreamProducer::removeConsumer(const std::string& peerid)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        m_videoSinkList.erase(it);
    }
#ifdef JETSON_PLATFORM
    if (m_videoSinkList.size() == 0 && GET_CONFIG().enable_ipc_path == false)
    {
        m_stop = true;
    }
#else
    if (m_videoSinkList.size() == 0)
    {
        m_stop = true;
    }
#endif
}

bool NativeStreamProducer::isSinkPresent ()
{
    if (m_stop == false)
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>, std::less<>>::iterator it;
        for( it = m_videoSinkList.begin(); it != m_videoSinkList.end();)
        {
            shared_ptr<VideoSinkInfo> sink = it->second;
            if (sink->m_consumer.get() == nullptr)
            {
                LOG(error) << "consumer is NULL for " << m_peerid << " uri = " << m_streamId << endl;
                it = m_videoSinkList.erase(it);
            }
            else
            {
                it++;
            }
        }
        return m_videoSinkList.size() == 0 ? false : true;
    }
    else
    {
        return false;
    }
}

bool NativeStreamProducer::isDRCAllowed ()
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

FrameSize NativeStreamProducer::handleDRC(const string& peerid, int targetPixels, int targetFPS)
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

std::string NativeStreamProducer::getstate()
{
    string state_str = gst_element_state_get_name(m_state);
    if (state_str != "PLAYING" && state_str != "PAUSED")
    {
        state_str = "NOT_PLAYING";
    }
    return state_str;
}

void NativeStreamProducer::getEnumSupportedProperty(const std::string& propertyName, const std::vector<PropertyDescription>& descriptions,
std::vector<std::string>& outOptions)
{
    if (!m_source)
    {
        LOG(error) << "m_source is not initialized." << std::endl;
        return;
    }

    GParamSpec *pspec = g_object_class_find_property(G_OBJECT_GET_CLASS(m_source), propertyName.c_str());

    if (!pspec)
    {
        LOG(error) << "Failed to find property '" << propertyName << "'." << std::endl;
        return;
    }

    if (G_IS_PARAM_SPEC_ENUM(pspec))
    {
        GParamSpecEnum *enum_pspec = G_PARAM_SPEC_ENUM(pspec);
        GEnumClass *enum_class = enum_pspec->enum_class;

        LOG(verbose) << "Supported " << propertyName << " values: ";
        for (uint32_t i = 0; i < enum_class->n_values; ++i)
        {
            GEnumValue *enum_value = &enum_class->values[i];
            std::string enum_value_nick(enum_value->value_nick);

            for (const auto& desc : descriptions)
            {
                if (desc.description == enum_value_nick)
                {
                    outOptions.push_back(desc.description);
                    LOG(verbose) << desc.description << " ";
                    break; // Exit inner loop once a match is found
                }
            }
        }
        LOG(verbose) << std::endl;
    }
    else
    {
        LOG(error) << propertyName << " is not an enum property." << std::endl;
    }
}

void NativeStreamProducer::getEnumPropertyValue(const std::string& propertyName,
const std::vector<PropertyDescription>& descriptions, std::string& outValue)
{
    if (!m_source)
    {
        LOG(error) << "m_source is not initialized." << std::endl;
        return;
    }

    int value;
    g_object_get(G_OBJECT(m_source), propertyName.c_str(), &value, NULL);

    LOG(verbose) << "Current " << propertyName << " value: " << value << std::endl;
    for (const auto& desc : descriptions)
    {
        if (desc.propertyValue == value)
        {
            LOG(verbose) << "Current " << propertyName << " description: " << desc.description << std::endl;
            outValue = desc.description;
            break;
        }
    }
}

void NativeStreamProducer::getFloatProperty(const std::string& propertyName, std::string& minString,
std::string& maxString, std::string& currentString)
{
    if (!m_source)
    {
        LOG(error) << "m_source is not initialized." << std::endl;
        return;
    }

    GParamSpec *pspec = g_object_class_find_property(G_OBJECT_GET_CLASS(m_source), propertyName.c_str());
    bool isFloatParam = pspec ? G_IS_PARAM_SPEC_FLOAT(pspec) : false;

    if (pspec && isFloatParam)
    {
        GParamSpecFloat *float_pspec = G_PARAM_SPEC_FLOAT(pspec);
        float minValue = float_pspec->minimum;
        float maxValue = float_pspec->maximum;
        float currentValue;
        g_object_get(G_OBJECT(m_source), propertyName.c_str(), &currentValue, NULL);

        std::ostringstream infoStream;
        infoStream << "Supported " << propertyName << " values: min=" << minValue << ", max=" << maxValue << "; "
                   << "Current " << propertyName << " value: " << currentValue;
        LOG(verbose) << infoStream.str() << std::endl;

        std::ostringstream minStream;
        minStream << minValue;
        minString = minStream.str();

        std::ostringstream maxStream;
        maxStream << maxValue;
        maxString = maxStream.str();

        std::ostringstream currentStream;
        currentStream << currentValue;
        currentString = currentStream.str();
    }
    else
    {
        LOG(error) << propertyName << " is not a float property or failed to retrieve " << propertyName << " information." << std::endl;
    }
}

void NativeStreamProducer::getEncodeSettings(SensorVideoEncoderSettingsValues& encoderValues, SensorEncoderSettingsOptions& encoderOptions)
{
    if (!m_source || !m_pipeline)
    {
        LOG(error) << "m_source|m_pipeline is not initialized." << std::endl;
        return;
    }

    /* TODO: Min and max value currently we have setted as current value, But once we add support of set
    encode settings then we need to get properly min and max value */

    /* Set all encoding parameters */
    if (getVideoCodec() == "h265")
    {
        encoderValues.encoding = "H265";
    }
    else
    {
        encoderValues.encoding = "H264";
    }

    encoderValues.bitrate = std::to_string(getBitrate()/1000);
    encoderValues.frameRate = std::to_string(getFramerate());

    int profile = getProfile();
    if (profile == BASELINE)
    {
        encoderValues.encodingProfile = "Baseline";
    }
    else if (profile == MAIN)
    {
        encoderValues.encodingProfile = "Main";
    }
    else if (profile == HIGH)
    {
        encoderValues.encodingProfile = "High";
    }

    string width, height;
    getResolution(width, height);
    encoderValues.resolution.width = width;
    encoderValues.resolution.height = height;

    encoderValues.encodingInterval = std::to_string(1);

    /* Set all the encoder options(min,max) as current value as we are not supporting set encode settings for the native sensors */
    VideoEncoderConfigurationsOptions options;
    encoderOptions.videoEncodingSupported.push_back(encoderValues.encoding);

    options.BitrateRange.max = encoderValues.bitrate;
    options.BitrateRange.min = encoderValues.bitrate;
    options.encoding = encoderValues.encoding;
    options.EncodingIntervalRange.max = encoderValues.encodingInterval;
    options.EncodingIntervalRange.min = encoderValues.encodingInterval;
    options.FrameRateSupported = encoderValues.frameRate;
    options.profilesSupported.push_back(encoderValues.encodingProfile);

    options.ResolutionsAvailable.push_back(encoderValues.resolution);

    encoderOptions.encoderSettingsOptions.push_back(options);
}

bool NativeStreamProducer::getImageSettings(SensorImageSettingsValues& imageValues, SensorImageSettingsOptions& imageOptions)
{
    if (!m_source || !m_pipeline)
    {
        LOG(error) << "m_source|m_pipeline is not initialized." << std::endl;
        return false;
    }

    /* TODO: Min and max value currently we have setted as current value, But once we add support of set
    image settings then we need to get properly min and max value */

    // Get tnr-mode
    //getEnumSupportedProperty("tnr-mode", tnrModeDescriptions, imageOptions.TemporalNoiseReductionModes);
    getEnumPropertyValue("tnr-mode", tnrModeDescriptions, imageValues.TemporalNoiseReductionMode);
    imageOptions.TemporalNoiseReductionModes.push_back(imageValues.TemporalNoiseReductionMode);
    LOG(info) << "Current tnr-mode value: " << imageValues.TemporalNoiseReductionMode << std::endl;

    // Get wbmode
    //getEnumSupportedProperty("wbmode", wbModeDescriptions, imageOptions.WhiteBalanceModes);
    getEnumPropertyValue("wbmode", wbModeDescriptions, imageValues.WhiteBalanceMode);
    imageOptions.WhiteBalanceModes.push_back(imageValues.WhiteBalanceMode);
    LOG(info) << "Current wbmode value: " << imageValues.WhiteBalanceMode << std::endl;

    // Get aeantibanding
    //getEnumSupportedProperty("aeantibanding", aeAntiBandingDescriptions, imageOptions.AeAntibandingModes);
    getEnumPropertyValue("aeantibanding", aeAntiBandingDescriptions, imageValues.AeAntibandingMode);
    imageOptions.AeAntibandingModes.push_back(imageValues.AeAntibandingMode);
    LOG(info) << "Current wbmode value: " << imageValues.AeAntibandingMode << std::endl;

    // Get ee-mode
    //getEnumSupportedProperty("ee-mode", eeModeDescriptions, imageOptions.EdgeEnhancementModes);
    getEnumPropertyValue("ee-mode", eeModeDescriptions, imageValues.EdgeEnhancementMode);
    imageOptions.EdgeEnhancementModes.push_back(imageValues.EdgeEnhancementMode);
    LOG(info) << "Current wbmode value: " << imageValues.EdgeEnhancementMode << std::endl;

    // Get ee-strength
    getFloatProperty("ee-strength", imageOptions.EdgeEnhancementStrength.min, imageOptions.EdgeEnhancementStrength.max, imageValues.EdgeEnhancementStrength);
    imageOptions.EdgeEnhancementStrength.min = imageValues.EdgeEnhancementStrength;
    imageOptions.EdgeEnhancementStrength.max = imageValues.EdgeEnhancementStrength;
    LOG(info) << "Current EdgeEnhancementStrength value: " << imageValues.EdgeEnhancementStrength << " min:" <<
    imageOptions.EdgeEnhancementStrength.min << " max:" << imageOptions.EdgeEnhancementStrength.max << std::endl;

    // Get exposurecompensation
    getFloatProperty("exposurecompensation", imageOptions.ExposureCompensation.min, imageOptions.ExposureCompensation.max, imageValues.ExposureCompensation);
    imageOptions.ExposureCompensation.min = imageValues.ExposureCompensation;
    imageOptions.ExposureCompensation.max = imageValues.ExposureCompensation;
    LOG(info) << "Current ExposureCompensation value: " << imageValues.ExposureCompensation << " min:" <<
    imageOptions.ExposureCompensation.min << " max:" << imageOptions.ExposureCompensation.max << std::endl;

    // Get saturation
    getFloatProperty("saturation", imageOptions.ColorSaturation.min, imageOptions.ColorSaturation.max, imageValues.ColorSaturation);
    imageOptions.ColorSaturation.min = imageValues.ColorSaturation;
    imageOptions.ColorSaturation.max = imageValues.ColorSaturation;
    LOG(info) << "Current ColorSaturation value: " << imageValues.ColorSaturation << " min:" <<
    imageOptions.ColorSaturation.min << " max:" << imageOptions.ColorSaturation.max << std::endl;

    return true;
}

/*
Native Stream Producer Pipeline:
                                                |--> queue --> appsink
                                                |
    nvarguscamerasrc --> capsfilter --> tee --> |
                                                |
                                                |--> queue --> capsfilter --> nvv4l2h265enc --> h265parse --> capsfilter --> appsink
*/
bool NativeStreamProducer::startPipeline()
{
    bool ret = true;
    string capsAfterNvArgus;
    GstCaps* filterCapsAfterNvArgus = nullptr;
    GstBus *bus = nullptr;
    GstPad* tee_yuv_pad = nullptr;
    GstPad* queue_yuv_pad = nullptr;
    GstPad* tee_bitstream_pad = nullptr;
    GstPad* queue_bitstream_pad = nullptr;
    string capsBeforeEncoder;
    string capsAfterEncoder;
    GstCaps* filterCapsBeforeEncoder = nullptr;
    string nvvidconv_caps_string;
    GstCaps* filterCapsAfterEncoder = nullptr;
    int sensorId = 0;
    string capsString;

    if (m_resetAttempts >= MAX_RESET_ATTEMPTS)
    {
        LOG(error) << "Pipeline not going to start because reset attempts reached for the streamId:" << m_streamId << " name:" << m_sensorName << " attempts:" << m_resetAttempts << endl;
        ret = false;
        return ret;
    }

    {
        std::lock_guard<std::mutex> lock(m_pipelineLock);
        if (m_pipeline)
        {
            LOG(error) << "Pipeline is already started for the streamId:" << m_streamId << " name:" << m_sensorName << endl;
            ret = true;
            return ret;
        }
    }

    if (m_location.empty())
    {
        LOG(error) << "Location of the sensor is invalid" << endl;
        ret = false;
        goto error;
    }

    try
    {
        sensorId = getVideoDeviceIndex(m_location);

        LOG(info)  << "Index of " << m_location << " is " << sensorId << " for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    } catch (const std::exception& e)
    {
        LOG(error) << "Error: " << e.what() << " for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        ret = false;
        goto error;
    }

    if(gst_is_initialized() == false)
    {
        m_pipeline = nullptr;
        gst_init (nullptr, nullptr);
    }

    m_pipeline                  = gst_pipeline_new ("pipeline");
    m_source                    = gst_element_factory_make ("nvarguscamerasrc", nullptr);
    m_capsFilterArgusCameraSrc  = gst_element_factory_make ("capsfilter", nullptr);
    m_tee                       = gst_element_factory_make ("tee", nullptr);
    m_queueYuvSink              = gst_element_factory_make ("queue", nullptr);
    m_queueBitstreamSink        = gst_element_factory_make ("queue", nullptr);
    if (m_videoCodec == DEFAULT_NATIVE_STREAM_VIDEO_CODEC)
    {
        m_encoder               = gst_element_factory_make ("nvv4l2h265enc", nullptr);
    }
    m_yuvSink                   = gst_element_factory_make ("appsink", nullptr);
    m_bitstreamSink             = gst_element_factory_make ("appsink", nullptr);
    m_capsFilterBeforeEncoder   = gst_element_factory_make ("capsfilter", nullptr);
    m_capsFilterAfterEncoder    = gst_element_factory_make ("capsfilter", nullptr);
    m_h265parse                 = gst_element_factory_make ("h265parse", nullptr);
    m_nvvidconv                 = gst_element_factory_make ("nvvidconv", nullptr);
    m_capsFilterNvvidconv       = gst_element_factory_make ("capsfilter", nullptr);

    #ifdef DEBUG
        g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
    #endif

    if (!m_pipeline || !m_source || !m_capsFilterArgusCameraSrc || !m_yuvSink || !m_tee ||
    !m_queueYuvSink || !m_queueBitstreamSink || !m_encoder || !m_bitstreamSink || !m_capsFilterBeforeEncoder ||
    !m_h265parse || !m_nvvidconv|| !m_capsFilterNvvidconv || !m_capsFilterAfterEncoder)
    {
        LOG(error) << "Not all elements could be created, returning for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        ret = false;
        goto error;
    }

    capsString = "video/x-raw(memory:NVMM), format=(string)NV12, width=(int)" +
    std::to_string(m_sourceWidth) + ", height=(int)" + std::to_string(m_sourceHeight);
    capsAfterNvArgus = capsString;
    capsBeforeEncoder = capsString;
    capsAfterEncoder = "video/x-h265, stream-format=(string)byte-stream, alignment=(string)nal, framerate=" +
    std::to_string(m_frameRate) + "/" + "1";

    filterCapsAfterNvArgus = gst_caps_from_string (capsAfterNvArgus.c_str());
    filterCapsBeforeEncoder = gst_caps_from_string (capsBeforeEncoder.c_str());
    filterCapsAfterEncoder = gst_caps_from_string (capsAfterEncoder.c_str());
    g_object_set (G_OBJECT (m_capsFilterArgusCameraSrc), "caps", filterCapsAfterNvArgus, NULL);
    if (filterCapsAfterEncoder)
    {
        gst_caps_unref (filterCapsAfterNvArgus);
        filterCapsAfterNvArgus = nullptr;
    }
    g_object_set (G_OBJECT (m_capsFilterBeforeEncoder), "caps", filterCapsBeforeEncoder, NULL);
    if (filterCapsBeforeEncoder)
    {
        gst_caps_unref (filterCapsBeforeEncoder);
        filterCapsBeforeEncoder = nullptr;
    }
    g_object_set (G_OBJECT (m_capsFilterAfterEncoder), "caps", filterCapsAfterEncoder, NULL);
    if (filterCapsAfterEncoder)
    {
        gst_caps_unref (filterCapsAfterEncoder);
        filterCapsAfterEncoder = nullptr;
    }
    LOG(info) << "Set Caps after NV argus = " << capsAfterNvArgus << " for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    LOG(info) << "Set Caps before encoder = " << capsBeforeEncoder << " for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    LOG(info) << "Set Caps after encoder = " << capsAfterEncoder << " for streamId:" << m_streamId << " name:" << m_sensorName << endl;

    gst_bin_add_many(GST_BIN(m_pipeline), m_source, m_capsFilterArgusCameraSrc, m_tee, m_queueYuvSink, m_queueBitstreamSink, m_encoder,
    m_yuvSink, m_bitstreamSink, m_capsFilterBeforeEncoder, m_h265parse, m_capsFilterAfterEncoder, NULL);
#ifdef DUMP_FRAMES
    GstCaps* nvvidconv_filtercaps;
    // Capsfilter for nvvidconv
    nvvidconv_caps_string = "video/x-raw, format=NV12";
    nvvidconv_filtercaps = gst_caps_from_string(nvvidconv_caps_string.c_str());
    g_object_set(G_OBJECT(m_capsFilterNvvidconv), "caps", nvvidconv_filtercaps, NULL);
    gst_caps_unref(nvvidconv_filtercaps);

    gst_bin_add_many(GST_BIN(m_pipeline), m_nvvidconv, m_capsFilterNvvidconv, NULL);

    if (!gst_element_link_many(m_queueYuvSink, m_nvvidconv, m_capsFilterNvvidconv, m_yuvSink, NULL))
    {
        LOG(error) << "Elements could not be linked: queueYuvSink, nvvidconv, capsFilterNvvidconv, yuvSink for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        ret = false;
        goto error;
    }
#else
    if (!gst_element_link_many(m_queueYuvSink, m_yuvSink, NULL))
    {
        LOG(error) << "Elements could not be linked: queueYuvSink,  for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        ret = false;
        goto error;
    }
#endif

    if (!gst_element_link_many(m_source, m_capsFilterArgusCameraSrc, m_tee, NULL))
    {
        LOG(error) << "Elements could not be linked: source, capsFilter, tee for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        ret = false;
        goto error;
    }

    if (!gst_element_link_many(m_queueBitstreamSink, m_capsFilterBeforeEncoder, m_encoder, m_h265parse, m_capsFilterAfterEncoder, m_bitstreamSink, NULL))
    {
        LOG(error) << "Elements could not be linked: queueBitstreamSink, capsFilterBeforeEncoder, encoder, h265parse, capsFilterAfterEncoder, bitstreamSink, for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        ret = false;
        goto error;
    }

    tee_yuv_pad = gst_element_request_pad_simple(m_tee, "src_%u");
    queue_yuv_pad = gst_element_get_static_pad(m_queueYuvSink, "sink");

    tee_bitstream_pad = gst_element_request_pad_simple(m_tee, "src_%u");
    queue_bitstream_pad = gst_element_get_static_pad(m_queueBitstreamSink, "sink");

    if (tee_yuv_pad && queue_yuv_pad && tee_bitstream_pad && queue_bitstream_pad)
    {
        if (tee_yuv_pad && queue_yuv_pad && gst_pad_link(tee_yuv_pad, queue_yuv_pad) != GST_PAD_LINK_OK)
        {
            LOG(error) << "Tee and queueYuvSink pads could not be linked for streamId:" << m_streamId << " name:" << m_sensorName << endl;
            ret = false;
            goto error;
        }

        if (tee_bitstream_pad && queue_bitstream_pad && gst_pad_link(tee_bitstream_pad, queue_bitstream_pad) != GST_PAD_LINK_OK)
        {
            LOG(error) << "Tee and queueBitstreamSink pads could not be linked for streamId:" << m_streamId << " name:" << m_sensorName << endl;
            ret = false;
            goto error;
        }
    }
    else
    {
        LOG(error) << "Invalid tee_yuv_pad/queue_yuv_pad/tee_bitstream_pad/queue_bitstream_pad for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        ret = false;
        goto error;
    }

    if (tee_yuv_pad)
    {
        gst_object_unref(tee_yuv_pad);
        tee_yuv_pad = nullptr;
    }
    if (queue_yuv_pad)
    {
        gst_object_unref(queue_yuv_pad);
        queue_yuv_pad = nullptr;
    }
    if (tee_bitstream_pad)
    {
        gst_object_unref(tee_bitstream_pad);
        tee_bitstream_pad = nullptr;
    }
    if (queue_bitstream_pad)
    {
        gst_object_unref(queue_bitstream_pad);
        queue_bitstream_pad = nullptr;
    }

    g_object_set(m_yuvSink, "emit-signals", TRUE, "sync", FALSE, NULL);
    if (!g_signal_connect(m_yuvSink, "new-sample", G_CALLBACK (on_new_sample_from_sink), this))
    {
        LOG(error) << "Error in g_signal_connect of new-sample for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        goto error;
    }

    g_object_set(m_bitstreamSink, "emit-signals", TRUE, "sync", FALSE, NULL);
    if (!g_signal_connect(m_bitstreamSink, "new-sample", G_CALLBACK(NativeStreamProducer::onNewSampleBitstream), this))
    {
        ret = false;
        LOG(error) << "Error in g_signal_connect of new-sample for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        goto error;
    }

    g_object_set (G_OBJECT (m_source), "sensor-id", sensorId, NULL);
    g_object_set (G_OBJECT (m_h265parse), "config-interval", -1, NULL);

    // Set encoder properties
    g_object_set (G_OBJECT (m_encoder), "idrinterval", m_idrInterval, NULL); // Set IDR interval
    g_object_set (G_OBJECT (m_encoder), "bitrate", m_bitrate, NULL); // Set bitrate to 8 Mbps
    g_object_set (G_OBJECT (m_encoder), "control-rate", 1, NULL);  // Set to CBR
    g_object_set (G_OBJECT (m_encoder), "iframeinterval", m_iframeInterval, NULL); // Set key frame interval
    g_object_set(G_OBJECT(m_encoder), "profile", m_profile, NULL); // 0: baseline, 1: main, 2: high

    if (gst_element_set_state (m_pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set the camera to playing state for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        ret = false;
        goto error;
    }

    GstState  current, pending;
    gst_element_get_state(m_pipeline, &current, &pending, GST_SECOND * 5);
    m_state = current;
    if (current == GST_STATE_NULL)
    {
        LOG(info) << "Current state: NULL (" << current << ") for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    }
    else if (current == GST_STATE_READY)
    {
        LOG(info) << "Current state: READY (" << current << ") for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    }
    else if (current == GST_STATE_PAUSED)
    {
        LOG(info) << "Current state: PAUSED (" << current << ") for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    }
    else if (current == GST_STATE_PLAYING)
    {
        LOG(info) << "Current state: PLAYING (" << current << ") for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    }
    else
    {
        LOG(info) << "Unknown state: " << current << " for streamId:" << m_streamId << " name:" << m_sensorName << endl;
    }

    bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!bus)
    {
        ret = false;
        LOG(error) <<  "Failed to get BUS of Decoder pipeline for streamId:" << m_streamId << " name:" << m_sensorName << endl;
        goto error;
    }
    m_busWatchId = gst_bus_add_watch (bus, busWatch, (void*)this);
    gst_object_unref(bus);

    return ret;
error:
    if (tee_yuv_pad)
    {
        gst_object_unref(tee_yuv_pad);
        tee_yuv_pad = nullptr;
    }
    if (queue_yuv_pad)
    {
        gst_object_unref(queue_yuv_pad);
        queue_yuv_pad = nullptr;
    }
    if (tee_bitstream_pad)
    {
        gst_object_unref(tee_bitstream_pad);
        tee_bitstream_pad = nullptr;
    }
    if (queue_bitstream_pad)
    {
        gst_object_unref(queue_bitstream_pad);
        queue_bitstream_pad = nullptr;
    }

    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
        m_state = GST_STATE_NULL;
    }

    return ret;
}
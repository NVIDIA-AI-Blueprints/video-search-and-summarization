/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "ll_overlay.h"
#include "logger.h"
#include "nvbufwrapper.h"
#include "overlay_internal.h"
#include "LiveMetadataStore.h"
#include "ElasticMetadataStore.h"


NvLLOverlay::NvLLOverlay (const std::string& consumer_name, const std::string& uri, const std::map<std::string, std::string, std::less<>> &opts) : IMediaDataConsumer(consumer_name)
{
    m_uri = uri;

    shared_ptr<StreamInfo> streamInfo;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    std::vector<shared_ptr<StreamInfo>> streamList = deviceManager->getStreamList();
    for (auto const& stream : streamList)
    {
        if (stream->live_proxy_url == m_uri)
        {
            streamInfo = stream;
            m_streamWidth  = stringToInt(streamInfo->settings.encoderValues.resolution.width, 1920);
            m_streamHeight = stringToInt(streamInfo->settings.encoderValues.resolution.height, 1080);
            break;
        }
        else if (stream->live_url == m_uri)
        {
            streamInfo = stream;
            m_streamWidth  = stringToInt(streamInfo->settings.encoderValues.resolution.width, 1920);
            m_streamHeight = stringToInt(streamInfo->settings.encoderValues.resolution.height, 1080);
            break;
        }
    }
    setOptions(opts);
    if (m_sensorType == SENSOR_TYPE_NVSTREAM || GET_CONFIG().enable_gem_drawing)
    {
        if (m_startTime.empty())
        {
            m_startTime = "0";
        }
    }

    string search_start, search_end;
    bool use_frameid = false;
    if (m_sensorType == SENSOR_TYPE_NVSTREAM)
    {
        if ( m_opts.find("startFrameId") != m_opts.end() )
        {
            search_start = m_opts.at("startFrameId");
        }
        if ( m_opts.find("endFrameId") != m_opts.end() )
        {
            search_end = m_opts.at("endFrameId");
        }
        use_frameid = true;
    }
    else
    {
        search_start = m_startTime;
        search_end = m_endTime;
    }

#ifdef JETSON_PLATFORM
    Resolution resolution;
    resolution = GET_CONFIG().webrtc_out_default_resolution;
    if (!resolution.empty() || NvHwDetection::getInstance()->m_useNvV4l2Enc == false)
    {
        m_width = WIDTH_480p, m_height = HEIGHT_480p;
        if (!resolution.empty())
        {
            m_width = stringToInt(resolution.width, WIDTH_480p);
            // Replace with nearest multiple of 8 - Bug 4561987
            m_width = ((m_width + 7) >> 3) << 3;
            m_height = stringToInt(resolution.height, HEIGHT_480p);
        }
    }
#endif
    for (int i = 0; i < OUTPUT_PLANE_NUM_BUFFERS; i++)
    {
        m_cpuPtr[i] = nullptr;
    }

    NvLLOverlayInternal::OverlayParams params;
    params.m_startTime = m_recordedPlayback ? m_isoStartTime : search_start;
    params.m_endTime = m_recordedPlayback ? m_isoEndTime : search_end;
    params.m_isLive = m_recordedPlayback ? false : true;
    params.m_sensorName = m_sensorName;
    params.m_frameRate = m_frameRate;
    params.m_frameSize.m_width = m_width;
    params.m_frameSize.m_height = m_height;
    if (m_compositeShowSensorName)
    {
        m_overlayParams.m_enableSensorNameText = true;
        m_overlayParams.m_sensorNameTextPosX = m_compositeOverlaySensorPosX;
        m_overlayParams.m_sensorNameTextPosY = m_compositeOverlaySensorPosY;
    }
    params.m_bboxParams = m_overlayParams;
    MetadataParams metadataParams;
    metadataParams.m_startTime = m_startTime;
    metadataParams.m_endTime = m_endTime;
    metadataParams.m_sensorName = m_sensorName;
    metadataParams.m_isLive = m_recordedPlayback ? false : true;
    if (m_recordedPlayback)
    {
        m_metadataStore = std::make_shared<ElasticMetadataStore>(metadataParams, use_frameid);
        m_overlay = std::make_shared<NvLLOverlayInternal>(params, m_metadataStore);
    }
    else
    {
        bool startListener = false;
        if (m_overlayParams.m_enableBbox || m_overlayParams.m_enablePose ||
            m_overlayParams.m_enableHalos)
        {
            startListener = true;
        }
        m_metadataStore = std::make_shared<LiveMetadataStore>(m_sensorName, startListener, m_overlayParams.m_enableGodsEyeView);
        m_overlay = std::make_shared<NvLLOverlayInternal>(params, m_metadataStore, use_frameid);
    }
    m_overlay->setProximityClass(m_overlayParams.m_proximityClass);
    m_overlay->setEntrantClass(m_overlayParams.m_entrantClass);
    m_overlay->setProximityAreaFactor(m_overlayParams.m_proximityAreaFactor);
    m_overlay->setProximityAnimation(m_overlayParams.m_proximityAnimation);
    m_overlay->setColorCode(m_overlayParams.m_colorCode);
    m_overlay->setEnableGodsEyeView(m_overlayParams.m_enableGodsEyeView);

    LOG(info) << "Creating m_overlay " << m_overlay << endl;

    m_surfacePool = std::make_shared<NvSurfacePool>();
    m_drawThread = std::thread(&NvLLOverlay::doDrawTask, this);
    if (!GET_CONFIG().overlay_3d_sensor_name.empty())
    {
        m_sensorName3d = GET_CONFIG().overlay_3d_sensor_name;
    }
}

void NvLLOverlay::setOptions(const std::map<std::string, std::string, std::less<>> &opts)
{
    m_opts = opts;
    if ( m_opts.find("peerid") != m_opts.end() )
    {
        m_peerid = m_opts.at("peerid");
    }
    if ( opts.find("sensor_type") != opts.end() )
    {
        m_sensorType = opts.at("sensor_type");
    }
    if ( opts.find("framerate") != opts.end() )
    {
        m_frameRate = stringToDouble(opts.at("framerate"), DEFAULT_FRAME_RATE);
        if (m_frameRate == 0)
        {
            m_frameRate = DEFAULT_FRAME_RATE;
        }
    }
    if ( opts.find("do_composition") != opts.end() )
    {
        m_compositePlayback = true;
    }
    if ( opts.find("overlayShowSensorName") != opts.end() )
    {
        m_compositeShowSensorName = true;
        if ( opts.find("overlaySensorPosX") != opts.end() &&
             opts.find("overlaySensorPosY") != opts.end() )
        {
            m_compositeOverlaySensorPosX = stoi(opts.at("overlaySensorPosX"));
            m_compositeOverlaySensorPosY = stoi(opts.at("overlaySensorPosY"));
        }
    }
    if (opts.find("gods_eye_view") != opts.end() && opts.at("gods_eye_view") == "true")
    {
        m_overlayParams.m_enableGodsEyeView = true;
        LOG(info) << "gods_eye_view = " << m_overlayParams.m_enableGodsEyeView << endl;
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
    }
    else if ((m_uri.find("rtsp://") == 0))
    {
        m_recordedPlayback = false;
        if ( opts.find("startTime") != opts.end() )
        {
            m_startTime = opts.at("startTime");
        }
        if ( opts.find("endTime") != opts.end() )
        {
            m_endTime = opts.at("endTime");
        }
    }
    else if ((m_uri.find("udp:") == 0) || m_overlayParams.m_enableGodsEyeView)
    {
    }
    else
    {
        LOG(error) << "Invalid URL " << m_uri << endl;
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
    if ( opts.find("overlayColor") != opts.end() )
    {
        m_overlayParams.m_bboxColor = opts.at("overlayColor");
    }
    else if ( opts.find("bboxColor") != opts.end() )
    {
        m_overlayParams.m_bboxColor = opts.at("bboxColor");
    }
    if ( opts.find("overlayPose") != opts.end() )
    {
        m_overlayParams.m_enablePose = opts.at("overlayPose") == "true" ? true : false;
    }
    else if ( opts.find("bboxPose") != opts.end() )
    {
        m_overlayParams.m_enablePose = opts.at("bboxPose") == "true" ? true : false;
    }
    if ( opts.find("overlayHalos") != opts.end() )
    {
        m_overlayParams.m_enableHalos = opts.at("overlayHalos") == "true" ? true : false;
    }
    if ( opts.find("overlayThickness") != opts.end() )
    {
        m_overlayParams.m_bboxThickness = stringToInt(opts.at("overlayThickness"), DEFAULT_BBOX_WIDTH);
    }
    else if ( opts.find("bboxThickness") != opts.end() )
    {
        m_overlayParams.m_bboxThickness = stringToInt(opts.at("bboxThickness"), DEFAULT_BBOX_WIDTH);
    }
    if ( opts.find("bboxDebug") != opts.end() )
    {
        m_overlayParams.m_bboxDebug = opts.at("bboxDebug") == "true" ? true: false;
    }
    else if ( opts.find("overlayDebug") != opts.end() )
    {
        m_overlayParams.m_bboxDebug = opts.at("overlayDebug") == "true" ? true: false;
    }
    if ( opts.find("bboxShowObjId") != opts.end() )
    {
        LOG(info) << "bboxShowObjId = " << opts.at("bboxShowObjId") << endl;
        m_overlayParams.m_enableBboxId = opts.at("bboxShowObjId") == "true" ? true: false;
    }
    if ( opts.find("bboxObjIdPosition") != opts.end() )
    {
        int objIdPosition = stringToInt(opts.at("bboxObjIdPosition"), MIDDLE);
        if (objIdPosition >= MIDDLE && objIdPosition <= MAX_BBOX_ID_POSITION)
        {
            m_overlayParams.m_bboxIdPosition = (BBoxIdPosition)objIdPosition;
        }
    }
    if ( opts.find("bboxObjIdTextColor") != opts.end() )
    {
        m_overlayParams.m_bboxIdColor = opts.at("bboxObjIdTextColor");
    }
    if ( opts.find("bboxObjIdTextBGColor") != opts.end() )
    {
        m_overlayParams.m_bboxIdBgColor = opts.at("bboxObjIdTextBGColor");
    }
    if ( opts.find("overlayOpacity") != opts.end() )
    {
        uint8_t opacity = stringToInt(opts.at("overlayOpacity"), DEFAULT_BBOX_OPACITY);
        opacity = std::clamp(opacity, (uint8_t)0, (uint8_t)255);
        m_overlayParams.m_bboxOpacity = stringToInt(opts.at("overlayOpacity"), DEFAULT_BBOX_OPACITY);
    }
    else if ( opts.find("bboxOpacity") != opts.end() )
    {
        m_overlayParams.m_bboxOpacity = stringToInt(opts.at("bboxOpacity"), DEFAULT_BBOX_OPACITY);
    }

    if (opts.find("overlayProximityClass") != opts.end() && !opts.at("overlayProximityClass").empty())
    {
        m_overlayParams.m_proximityClass = opts.at("overlayProximityClass");
    }
    if (opts.find("overlayEntrantClass") != opts.end() && !opts.at("overlayEntrantClass").empty())
    {
        m_overlayParams.m_entrantClass = opts.at("overlayEntrantClass");
    }
    if (opts.find("overlayColorCode") != opts.end() && !opts.at("overlayColorCode").empty())
    {
        string colorCode = opts.at("overlayColorCode");
        auto tokens = splitString(colorCode, ",");
        for (uint i = 0; i < tokens.size(); i++)
        {
            // key=r:g:b:a,key=r:g:b:a,....
            auto keyValue = splitString(tokens[i], "=");
            auto rgba = splitString(keyValue[1], ":");
            std::vector<int> rgba_values;
            for (const auto& value : rgba)
            {
                rgba_values.push_back(std::stoi(value));
            }
            m_overlayParams.m_colorCode[keyValue[0]] = rgba_values;
        }
    }
    if (opts.find("overlayProximityAreaFactor") != opts.end() && !opts.at("overlayProximityAreaFactor").empty())
    {
        m_overlayParams.m_proximityAreaFactor = stringToDouble(opts.at("overlayProximityAreaFactor"), DEFAULT_PROXIMITY_AREA_FACTOR);
    }
    if (opts.find("overlayProximityAnimation") != opts.end() && !opts.at("overlayProximityAnimation").empty())
    {
        m_overlayParams.m_proximityAnimation = opts.at("overlayProximityAnimation");
    }
    if ( opts.find("overlayBbox") != opts.end() )
    {
        m_overlayParams.m_enableBbox = opts.at("overlayBbox") == "true" ? true : false;
        if ( opts.find("overlayObjectId") == opts.end() && opts.find("bboxObjectId") == opts.end() &&
            opts.find("overlayClassType") == opts.end() && opts.find("bboxClassType") == opts.end() )
        {
            m_overlayParams.m_overlayIdList[BBOX].push_back("all");
            m_overlayParams.m_overlayClassTypeList.push_back("all");
        }
    }
    if ( opts.find("overlayTripwire") != opts.end() )
    {
        m_overlayParams.m_enableTripwire = opts.at("overlayTripwire") == "true" ? true : false;
        m_overlayParams.m_overlayIdList[TRIPWIRE].push_back("all");
    }
    if ( opts.find("overlayRoi") != opts.end() )
    {
        m_overlayParams.m_enableROI = opts.at("overlayRoi") == "true" ? true : false;
        m_overlayParams.m_overlayIdList[ROI].push_back("all");
    }
    if ( opts.find("overlayObjectId") != opts.end() )
    {
        string objects = opts.at("overlayObjectId");
        auto tokens = splitString(objects, ",");
        for (uint i = 0; i < tokens.size(); i++)
        {
            m_overlayParams.m_overlayIdList[BBOX].push_back(tokens[i]);
        }
        if ( opts.find("overlayClassType") == opts.end() && opts.find("bboxClassType") == opts.end() )
        {
            m_overlayParams.m_overlayClassTypeList.push_back("all");
        }
    }
    if ( opts.find("overlayClassType") != opts.end() )
    {
        string classType = opts.at("overlayClassType");
        auto tokens = splitString(classType, ",");
        for (uint i = 0; i < tokens.size(); i++)
        {
            m_overlayParams.m_overlayClassTypeList.push_back(tokens[i]);
        }
        if ( opts.find("overlayObjectId") == opts.end() && opts.find("bboxObjectId") == opts.end() )
        {
            m_overlayParams.m_overlayIdList[BBOX].push_back("all");
        }
    }
    if ( opts.find("bboxObjectId") != opts.end() )
    {
        string objects = opts.at("bboxObjectId");
        auto tokens = splitString(objects, ",");
        for (uint i = 0; i < tokens.size(); i++)
        {
            m_overlayParams.m_overlayIdList[BBOX].push_back(tokens[i]);
        }
        if ( opts.find("overlayClassType") == opts.end() && opts.find("bboxClassType") == opts.end() )
        {
            m_overlayParams.m_overlayClassTypeList.push_back("all");
        }
    }
    if ( opts.find("bboxClassType") != opts.end() )
    {
        string classType = opts.at("bboxClassType");
        auto tokens = splitString(classType, ",");
        for (uint i = 0; i < tokens.size(); i++)
        {
            m_overlayParams.m_overlayClassTypeList.push_back(tokens[i]);
        }
        if ( opts.find("overlayObjectId") == opts.end() && opts.find("bboxObjectId") == opts.end() )
        {
            m_overlayParams.m_overlayIdList[BBOX].push_back("all");
        }
    }
    if ( opts.find("source_width") != opts.end() )
    {
        m_width = stringToInt(opts.at("source_width"), WIDTH_1080p);
    }
    if ( opts.find("source_height") != opts.end() )
    {
        m_height = stringToInt(opts.at("source_height"), HEIGHT_1080p);
    }
    if ( opts.find("image_capture") != opts.end() )
    {
        m_imageCapture = true;
    }
}

void NvLLOverlay::setOriginalFrameSize(int w, int h)
{
    // Send to transform is its consumer of overlay
    if (m_consumer)
    {
        m_consumer->setOriginalFrameSize(w, h);
    }
    if (m_isIPCMeta)
    {
        m_overlay->updateSourceResolution(m_streamWidth, m_streamHeight);
        m_overlay->updateIPCStreamResolution(w, h);
    }
    else
    {
        m_overlay->updateSourceResolution(w, h);
    }
#ifdef JETSON_PLATFORM
    /* Update the overlay resolution when out-resolution is not specified and enc is present */
    Resolution resolution;
    resolution = GET_CONFIG().webrtc_out_default_resolution;
    if (resolution.empty() && NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
    {
        m_width = w;
        m_height = h;
        setOverlayResolution(m_width, m_height);
    }
#endif
}

void NvLLOverlay::setIPCMeta ()
{
    if (GET_CONFIG().enable_ipc_path == true)
    {
        m_isIPCMeta = true;
    }
}

void NvLLOverlay::updateStartTime(string start_time)
{
    m_newStartTime = start_time;
}

void NvLLOverlay::reset()
{
    if (m_consumer)
    {
        m_consumer->reset();
    }
}

void NvLLOverlay::onLastFrame()
{
    if (m_consumer)
    {
        m_consumer->onLastFrame();
    }
}

NvLLOverlay::~NvLLOverlay ()
{
    LOG(info) << "Entry " << __METHOD_NAME__ <<  endl;
    FD_Index_Pair fd_index_pair = {-1, -1};
    bool is_sw_mode = GET_CONFIG().use_software_path || g_isGpuPresent == false;

    // Stop the overlay properly
    m_stop = true;
    m_flowData = true;
    m_condVar.notify_all();
    
    // Wait for the thread to finish
    if (m_drawThread.joinable())
    {
        LOG(info) << "Waiting for draw thread to join in destructor" << endl;
        m_drawThread.join();
        LOG(info) << "Draw thread joined successfully in destructor" << endl;
    }
    
    // Clear the queue to prevent any pending frames
    {
        std::lock_guard<std::mutex> queueLock(m_queueLock);
        while (!m_queue.empty()) {
            m_queue.pop();
        }
    }
    if (is_sw_mode)
    {
        // Get free FDs and no need to destroyFd as these are dummy FDs
        do
        {
            fd_index_pair = m_surfacePool->getFreeSurfaceFromQ();
        } while (fd_index_pair.first > 0);
    }
    else
    {
        m_surfacePool->freeSurfacesAndDataStructure(false);
    }
    m_surfacePool.reset();
    for (int i = 0; i < OUTPUT_PLANE_NUM_BUFFERS; i++)
    {
        if (m_cpuPtr[i])
        {
            free (m_cpuPtr[i]);
            m_cpuPtr[i] = nullptr;
        }
    }
    LOG(info) << "Exit " << __METHOD_NAME__ <<  endl;
}


Json::Value NvLLOverlay::getOverlayStatus()
{
    std::lock_guard<std::mutex> lock(m_debugData);
    Json::Value value;
    if (m_overlay)
    {
        value = m_overlay->getOverlayStatus(m_firstFrameTS);
    }
    return value;
}

void NvLLOverlay::setOriginalFrameSize()
{
    if (m_consumer)
    {
        m_consumer->setOriginalFrameSize(m_width, m_height);
    }
}

void NvLLOverlay::stopOverlay()
{
    LOG(info) << "Stopping overlay..." << endl;
    
    // First, stop the thread
    m_stop = true;
    m_flowData = true;
    m_condVar.notify_all();
    
    // Wait for the thread to finish
    if (m_drawThread.joinable())
    {
        LOG(info) << "Waiting for draw thread to join" << endl;
        m_drawThread.join();
        LOG(info) << "Draw thread joined successfully" << endl;
    }
    
    // Clear the queue to prevent any pending frames
    {
        std::lock_guard<std::mutex> queueLock(m_queueLock);
        while (!m_queue.empty()) {
            m_queue.pop();
        }
    }
    
    // Finally, reset the consumer after the thread is stopped
    m_consumer.reset();
    
    LOG(info) << "Overlay stopped successfully" << endl;
}

void NvLLOverlay::setConsumer(std::shared_ptr<IMediaDataConsumer> consumer)
{
    m_consumer = consumer;
    if (m_consumer)
    {
        m_consumer->setOriginalFrameSize(m_width, m_height);
    }
}

void NvLLOverlay::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    // Start performance tracking when frame is received
    m_transcodeStats.startProcessing();

    std::shared_ptr<RawFrameParams> compositor_frame_data = std::static_pointer_cast<RawFrameParams>(frame_data);
    if (frame_data->m_sample)
    {
        gst_sample_ref ((GstSample *)frame_data->m_sample);
    }
    std::lock_guard<std::mutex> queueLock(m_queueLock);
    m_queue.push(compositor_frame_data);
    m_flowData = true;
    m_condVar.notify_all();
}

void NvLLOverlay::doDrawTask()
{
    LOG(warning) << "Draw thread created" << endl;
    while(m_stop == false)
    {
        shared_ptr<RawFrameParams> sink_frame = nullptr;
        FD_Index_Pair fd_index_pair = {-1, -1};
        FdIndexInfo fd_index_info;
        int index = -1;
        bool ret = false;
        NvBufSurface* ip_surf = nullptr;
        NvBufSurface* dst_surf = nullptr;
        GstMetaUnion meta_union;
        int64_t pts = 0;
        bool is_drc = false;
#ifndef JETSON_PLATFORM
        void *data_ptr = nullptr;
#endif
        bool is_sw_mode = GET_CONFIG().use_software_path || g_isGpuPresent == false;

        {
            std::unique_lock<std::mutex> lk(m_queueLock);
            while ((m_queue.empty() || m_flowData == false) && m_stop == false)
            {
                m_flowData = false;
                auto until = std::chrono::system_clock::now() + chrono::milliseconds(1000);
                m_condVar.wait_until(lk, until, [this]{ return m_flowData.load(); });
            }
            if (m_queue.empty() == false)
            {
                sink_frame = m_queue.front();
                m_queue.pop();
            }
        }

        if (sink_frame)
        {
            /* Get the buffer from sample */
            if (sink_frame->m_sample)
            {
                sink_frame->m_gstBuffer = gst_sample_get_buffer (sink_frame->m_sample);
                if (sink_frame->m_gstBuffer == nullptr)
                {
                    LOG (warning) << "No more buffers available from app sink element" << endl;
                    gst_sample_unref (sink_frame->m_sample);
                    continue;
                }
                /* Map the gst buffer */
                if (gst_buffer_map (sink_frame->m_gstBuffer, &sink_frame->m_map, GST_MAP_READ) == false)
                {
                    LOG (warning) << "Map the gst buffer Failed" << endl;
                    gst_sample_unref (sink_frame->m_sample);
                    continue;
                }
            }

#ifndef JETSON_PLATFORM
            if (is_sw_mode)
            {
                is_drc = m_width != m_prevWidth || m_height != m_prevHeight;
                if (is_drc)
                {
                    setOverlayResolution(m_width, m_height);
                }
            }
            else
#endif
            {

                if (sink_frame->m_sample)
                {
                    ip_surf = (NvBufSurface *)sink_frame->m_map.data;
                }
                else
                {
                    NvBufWrapper::getInstance()->NvBufSurfaceFromFd (sink_frame->m_fd, (void **)&ip_surf);
#ifdef JETSON_PLATFORM
                    if (m_isIPCMeta)
                    {
                        meta_union.ipcMeta = (GstNvIpcMeta*)sink_frame->meta;
                    }
                    else
#endif
                    {
                        meta_union.vstMeta = (GstNvVstMeta*)sink_frame->meta;
                    }
                    pts = sink_frame->pts;
                }

                is_drc = ((uint32_t)m_width != ip_surf->surfaceList[0].width) ||
                        ((uint32_t)m_height != ip_surf->surfaceList[0].height);

                // Handle DRC change and update new resolution
                m_width = ip_surf->surfaceList[0].width;
                m_height = ip_surf->surfaceList[0].height;
                setOverlayResolution(m_width, m_height);
            }
            // Update new start time for recording seek cases
            if (!m_newStartTime.empty())
            {
                m_overlay->fetchMetadataAgain(m_newStartTime);
                m_newStartTime = "";
            }

            if (!m_surfacePool->m_surfacesAllocated || is_drc)
            {
                if (is_drc)
                {
                    if (is_sw_mode)
                    {
                        // Get free FDs and no need to destroyFd as these are dummy FDs
                        do
                        {
                            fd_index_pair = m_surfacePool->getFreeSurfaceFromQ();
                        } while (fd_index_pair.first > 0);
                    }
                    m_surfacePool->freeSurfacesAndDataStructure(false);
                    m_surfacePool->m_surfacesAllocated = false;
                    for (int i = 0; i < OUTPUT_PLANE_NUM_BUFFERS; i++)
                    {
                        if (m_cpuPtr[i])
                        {
                            free (m_cpuPtr[i]);
                            m_cpuPtr[i] = nullptr;
                        }
                    }
                }
                LOG(info) << "Allocating surfaces of resolution = " << m_width << " x " << m_height << endl;
#ifndef JETSON_PLATFORM
                if (is_sw_mode)
                {
                    m_surfacePool->allocateSurfaces(OUTPUT_PLANE_NUM_BUFFERS, m_width, m_height, false);
                }
                else
#endif
                {
                    m_surfacePool->allocateSurfaces(OUTPUT_PLANE_NUM_BUFFERS, ip_surf->surfaceList[0].width,
                                                    ip_surf->surfaceList[0].height, true,
                                                    ip_surf->surfaceList[0].colorFormat, ip_surf->surfaceList[0].layout);
                }
                m_prevWidth = m_width;
                m_prevHeight = m_height;
            }

            // Get the free FD from surface pool
            fd_index_pair = m_surfacePool->getFreeSurfaceFromQ();
            index = fd_index_pair.second;
            if (fd_index_pair.first > 0 || is_sw_mode)
            {
#ifndef JETSON_PLATFORM
                if (is_sw_mode)
                {
                    if (index < 0)
                    {
                        LOG(error) << "No free FD available, continue" << endl;
                        continue;
                    }
                    if (!m_cpuPtr[index])
                    {
                        m_cpuPtr[index] = (uint8_t *) malloc (m_width * m_height * 3 / 2);
                    }
                    memcpy((void *)m_cpuPtr[index], sink_frame->m_map.data, sink_frame->m_map.size);
                }
                else
#endif
                {
                    NvBufWrapper::getInstance()->NvBufSurfaceFromFd (fd_index_pair.first, (void **)&dst_surf);
                    NvBufWrapper::getInstance()->NvBufSurfaceCopy (ip_surf, dst_surf);
                }
                if (sink_frame->m_gstBuffer)
                {
#ifdef JETSON_PLATFORM
                    if (GET_CONFIG().enable_ipc_path == true && m_isIPCMeta)
                    {
                        meta_union.ipcMeta = GST_NV_IPC_META_GET(sink_frame->m_gstBuffer);
                        pts = GST_BUFFER_PTS (sink_frame->m_gstBuffer);
                    }
                    else
#endif
                    {
                        meta_union.vstMeta = GST_NV_VST_META_GET (sink_frame->m_gstBuffer);
                        pts = GST_BUFFER_PTS (sink_frame->m_gstBuffer);
                    }
                }

                // Perform overlay using GPU/CPU based on config
#ifndef JETSON_PLATFORM
                if (is_sw_mode)
                {
                    data_ptr = (void *)m_cpuPtr[index];
                    ret = m_overlay->doDraw(data_ptr, &meta_union, pts);
                }
                else
                {
#endif
                    ret = m_overlay->doDraw((void *)dst_surf, &meta_union, pts);
#ifndef JETSON_PLATFORM
                }
#endif

                if (ret == false)
                {
                    if (!is_sw_mode)
                    {
                        // Add the free FD back to surface pool
                        fd_index_info.m_fdIndexPair = fd_index_pair;
                        m_surfacePool->addFreeSurfaceToQ(fd_index_info);
                    }
                    continue;
                }

                std::shared_ptr<RawFrameParams> frame_data = std::make_shared<RawFrameParams>();
                if (ret == true)
                {
                    frame_data->m_fd        = fd_index_pair.first;
                    if (is_sw_mode)
                    {
                        // Create positive dummy FD to ensure fd-index pair is added back to surfacePool.
                        if (frame_data->m_fd <= 0)
                        {
                            frame_data->m_fd        = (fd_index_pair.first * -1) + 1;
                        }
                        frame_data->m_map.data  = m_cpuPtr[index];
                        frame_data->m_map.size  = m_width * m_height * 3 / 2;
                    }
                    frame_data->m_fdWrapperObj  = new std::shared_ptr<fdWrapper>(std::make_shared<fdWrapper>(m_surfacePool, frame_data->m_fd, index));
                    frame_data->m_sourceWidth   = m_overlay->m_width;
                    frame_data->m_sourceHeight  = m_overlay->m_height;
                    frame_data->m_targetWidth   = m_overlay->m_width;
                    frame_data->m_targetHeight  = m_overlay->m_height;
                    frame_data->m_isTransformed = false;
                    frame_data->m_streamId      = getStreamIdFromUrl(m_uri, "/live/");
                    frame_data->pts             = pts;

                    // JPEG encoder accepts I420 input and conversion to be handled by ll_transform
                    if (m_imageCapture && NvHwDetection::getInstance()->m_useNvV4l2Dec)
                    {
                        frame_data->m_targetColorFormat = NVBUF_COLOR_FORMAT_YUV420;
                    }
                    if (m_consumer) {
                        try {
                            m_transcodeStats.finishProcessing();
                            m_consumer->onFrame(frame_data);
                        } catch (const std::exception& e) {
                            LOG(error) << "Exception in consumer onFrame: " << e.what() << endl;
                            break; // Exit the loop if consumer is broken
                        } catch (...) {
                            LOG(error) << "Unknown exception in consumer onFrame" << endl;
                            break; // Exit the loop if consumer is broken
                        }
                    } else {
                        LOG(warning) << "Consumer is null in doDrawTask, skipping frame" << endl;
                    }
                }
            }
        }
    }
    LOG(warning) << "Exiting from Draw thread" << endl;
}

bool NvLLOverlay::streamSettings(const std::unordered_map<std::string, std::string> &opts)
{
    LOG(info) << "------------" << endl;
    for (const auto& pair : opts)
    {
        LOG2(info) << pair.first << ": " << pair.second << std::endl;
    }
    LOG(info) << "------------" << endl;

    if (!m_overlay)
    {
        LOG(error) << "No overlay data found. Retry with overlay enabled in UI" << endl;
        return false;
    }

    bool reinit_overlay = false;
    vector<string> overlayId[OVERLAYCOUNT];
    vector<string> overlayClassType;
    bool overlay_enabled_before = m_overlay->isOverlayEnabled();
    overlayId[BBOX].clear();
    overlayId[ROI].clear();
    overlayId[TRIPWIRE].clear();
    overlayClassType.clear();

    // Get ROI details
    auto it = opts.find("roiShowAll");
    if (it != opts.end())
    {
        if (it->second == "false")
        {
            auto it2 = opts.find("roiObjectId");
            if (it2 != opts.end() && !it2->second.empty())
            {
                string roiId = it2->second;
                auto tokens = splitString(roiId, ",");
                for (uint i = 0; i < tokens.size(); i++)
                {
                    overlayId[ROI].push_back(tokens[i]);
                }
            }
            if (overlayId[ROI].empty())
            {
                overlayId[ROI].push_back("none");
                if (m_overlayParams.m_enableROI)
                {
                    m_overlayParams.m_enableROI = false;
                    reinit_overlay = true;
                }
            }
            else if (!m_overlayParams.m_enableROI)
            {
                m_overlayParams.m_enableROI = true;
                reinit_overlay = true;
            }
        }
        else
        {
            overlayId[ROI].push_back("all");
            if (!m_overlayParams.m_enableROI)
            {
                m_overlayParams.m_enableROI = true;
                reinit_overlay = true;
            }
        }
    }
    // Get tripwire details
    it = opts.find("tripwireShowAll");
    if (it != opts.end())
    {
        if (it->second == "false")
        {
            auto it2 = opts.find("tripwireObjectId");
            if (it2 != opts.end() && !it2->second.empty())
            {
                string tripwireId = it2->second;
                auto tokens = splitString(tripwireId, ",");
                for (uint i = 0; i < tokens.size(); i++)
                {
                    overlayId[TRIPWIRE].push_back(tokens[i]);
                }
            }
            if (overlayId[TRIPWIRE].empty())
            {
                overlayId[TRIPWIRE].push_back("none");
                if (m_overlayParams.m_enableTripwire)
                {
                    m_overlayParams.m_enableTripwire = false;
                    reinit_overlay = true;
                }
            }
            else if (!m_overlayParams.m_enableTripwire)
            {
                m_overlayParams.m_enableTripwire = true;
                reinit_overlay = true;
            }
        }
        else
        {
            overlayId[TRIPWIRE].push_back("all");
            if (!m_overlayParams.m_enableTripwire)
            {
                m_overlayParams.m_enableTripwire = true;
                reinit_overlay = true;
            }
        }
    }
    // Get bbox details
    it = opts.find("bboxShowAll");
    if (it != opts.end())
    {
        if (it->second == "false")
        {
            auto it2 = opts.find("bboxObjectId");
            if (it2 != opts.end() && !it2->second.empty())
            {
                string bboxId = it2->second;
                auto tokens = splitString(bboxId, ",");
                for (uint i = 0; i < tokens.size(); i++)
                {
                    overlayId[BBOX].push_back(tokens[i]);
                }
                if (opts.find("bboxClassType") == opts.end() || opts.find("bboxClassType")->second.empty())
                {
                    overlayClassType.push_back("all");
                }
            }
            auto it3 = opts.find("bboxClassType");
            if (it3 != opts.end() && !it3->second.empty())
            {
                string classType = it3->second;
                auto tokens = splitString(classType, ",");
                for (uint i = 0; i < tokens.size(); i++)
                {
                    overlayClassType.push_back(tokens[i]);
                }
                if (opts.find("bboxObjectId") == opts.end() || opts.find("bboxObjectId")->second.empty())
                {
                    overlayId[BBOX].push_back("all");
                }
            }
            if (overlayId[BBOX].empty() && overlayClassType.empty())
            {
                overlayId[BBOX].push_back("none");
                overlayClassType.push_back("none");
                if (m_overlayParams.m_enableBbox)
                {
                    m_overlayParams.m_enableBbox = false;
                    reinit_overlay = true;
                }
            }
            else if (!m_overlayParams.m_enableBbox)
            {
                m_overlayParams.m_enableBbox = true;
                reinit_overlay = true;
            }
        }
        else
        {
            overlayId[BBOX].push_back("all");
            overlayClassType.push_back("all");
            if (!m_overlayParams.m_enableBbox)
            {
                m_overlayParams.m_enableBbox = true;
                reinit_overlay = true;
            }
        }
    }

    m_overlay->updateIdList(overlayId);
    m_overlay->updateClassTypeList(overlayClassType);
    it = opts.find("overlayColor");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_bboxColor = it->second;
        m_overlay->setBboxColor(m_overlayParams.m_bboxColor);
        it = opts.find("bboxObjIdTextBGColor");
        if (it == opts.end() || it->second.empty())
        {
            m_overlayParams.m_bboxIdBgColor = m_overlayParams.m_bboxColor;
            m_overlay->setBboxIdBgColor(m_overlayParams.m_bboxIdBgColor);
        }
    }
    it = opts.find("overlayPose");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_enablePose = (it->second == "true") ? true : false;
        m_overlay->setBboxPose(m_overlayParams.m_enablePose);
    }
    it = opts.find("overlayHalos");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_enableHalos = (it->second == "true") ? true : false;
        m_overlay->setBboxHalos(m_overlayParams.m_enableHalos);
    }
    it = opts.find("overlayThickness");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_bboxThickness = stoi(it->second);
        m_overlay->setBboxThickness(m_overlayParams.m_bboxThickness);
    }
    it = opts.find("overlayDebug");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_bboxDebug = (it->second == "true") ? true : false;
        m_overlay->setBboxDebug(m_overlayParams.m_bboxDebug);
    }
    it = opts.find("bboxShowObjId");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_enableBboxId = (it->second == "true") ? true : false;
        m_overlay->setBboxId(m_overlayParams.m_enableBboxId);
    }
    it = opts.find("bboxObjIdPosition");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_bboxIdPosition = MIDDLE;
        int objIdPosition = stringToInt(it->second, MIDDLE);
        if (objIdPosition >= MIDDLE && objIdPosition <= MAX_BBOX_ID_POSITION)
        {
            m_overlayParams.m_bboxIdPosition = (BBoxIdPosition)objIdPosition;
        }
        m_overlay->setBboxIdPosition(m_overlayParams.m_bboxIdPosition);
    }
    it = opts.find("bboxObjIdTextColor");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_bboxIdColor = it->second;
        m_overlay->setBboxIdColor(m_overlayParams.m_bboxIdColor);
    }
    it = opts.find("bboxObjIdTextBGColor");
    if (it != opts.end() && !it->second.empty())
    {
        m_overlayParams.m_bboxIdBgColor = it->second;
        m_overlay->setBboxIdBgColor(m_overlayParams.m_bboxIdBgColor);
    }
    it = opts.find("overlayOpacity");
    if (it != opts.end() && !it->second.empty())
    {
        uint8_t opacity = stoi(it->second);
        if (opacity >= 0 && opacity <= 255)
        {
            m_overlayParams.m_bboxOpacity = stoi(it->second);
            m_overlay->setBboxOpacity(m_overlayParams.m_bboxOpacity);
        }
        else
        {
            LOG(error) << "Overlay opacity should be between 0(fully transparent) to 255(not transparent)" << endl;
        }
    }

    // Reset overlay with new overlay params
    if (reinit_overlay)
    {
        NvLLOverlayInternal::OverlayParams params;
        if (m_startTime.empty() && GET_CONFIG().enable_gem_drawing)
        {
            m_startTime = "0";
        }
        params.m_startTime = m_startTime;
        params.m_endTime = m_endTime;
        params.m_sensorName = m_sensorName;
        params.m_frameRate = m_frameRate;
        params.m_frameSize.m_width = m_width;
        params.m_frameSize.m_height = m_height;
        for (uint32_t i = 0; i < OVERLAYCOUNT; i++)
        {
            m_overlayParams.m_overlayIdList[i].clear();
            m_overlayParams.m_overlayIdList[i].assign(overlayId[i].begin(), overlayId[i].end());
        }
        m_overlayParams.m_overlayClassTypeList.clear();
        m_overlayParams.m_overlayClassTypeList.assign(overlayClassType.begin(), overlayClassType.end());
        params.m_bboxParams = m_overlayParams;
        m_overlay->enableOverlay(params, false);
    }

    // Return true to switch consumer if overlay was enabled/disable now.
    return (overlay_enabled_before ^ m_overlay->isOverlayEnabled());
}

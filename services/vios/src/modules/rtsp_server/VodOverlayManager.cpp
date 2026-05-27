/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "VodOverlayManager.hh"
#include "stream_buffer.h"
#include "NvMediaSource.hh"
#include "device_manager.h"
#include "vstmodule.h"
#include "logger.h"
#include "mm_utils.h"

VodOverlayManager::VodOverlayManager(NvMediaSource* mediaSource)
    : m_mediaSource(mediaSource)
{
}

void VodOverlayManager::startOverlayPipeline()
{
    std::map<std::string, std::string, std::less<>> opts;
    string replay_url;

    if (m_mediaSource == nullptr)
    {
        LOG(error) << "Media source is nullptr" << endl;
        return;
    }

    string sensor_id = m_mediaSource->getFilename();
    LOG(info) << "Start overlay pipeline m_filename:" << sensor_id << endl;
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceManager)
    {
        shared_ptr<SensorInfo> sensor = deviceManager->searchSensor(sensor_id);
        if (sensor)
        {
            LOG(info) << "sensor:" << sensor->name << endl;
        }
        else
        {
            LOG(info) << "sensor not found, find in the DB" << endl;
            /* Create & get the stream details from database */
            shared_ptr<SensorInfo> sensor_to_add(new SensorInfo);
            SensorDetailsDBColumns row = GET_DB_INSTANCE()->readSensorDetails(deviceManager->getDeviceId(), sensor_id);
            if (row.sensor_id_value.empty() == false)
            {
                if (VmsErrorCode::NoError != GET_DB_INSTANCE()->getSensorInfoFromDB(sensor_to_add, row))
                {
                    LOG(error) << "Failed to get sensor info from DB for sensor_id: " << sensor_id << endl;
                    return;
                }
                sensor = sensor_to_add;
                sensor->printInfo();
                deviceManager->addOrUpdateSensor(*sensor_to_add);
            }
        }

        opts["sensorID"] = sensor->name;
        opts["sensorId"] = sensor->id;
        opts["sensor_type"] = sensor->type;
        shared_ptr<StreamInfo> stream_info = sensor->getStream(sensor_id);
        if (stream_info)
        {
            LOG(info) << "stream_info:" << stream_info->replay_url << endl;
            replay_url = stream_info->replay_url;

            opts["codec"]   = stream_info->settings.encoderValues.encoding;
            opts["streamId"] = sensor_id;
            opts["framerate"] = stream_info->settings.encoderValues.frameRate;
            opts["govLength"] = stream_info->settings.encoderValues.govLength;
            opts["width"] = stream_info->settings.encoderValues.resolution.width;
            opts["height"] = stream_info->settings.encoderValues.resolution.height;
        }
    }

    string startTime, endTime;
    const string& url_params = m_mediaSource->getUrlParams();
    size_t startTs_pos = url_params.find("startTs=");
    if (startTs_pos != string::npos)
    {
        startTs_pos += 8; // length of "startTs="
        size_t end_pos = url_params.find("&", startTs_pos);
        startTime = url_params.substr(startTs_pos, end_pos - startTs_pos);
    }

    size_t endTs_pos = url_params.find("endTs=");
    if (endTs_pos != string::npos)
    {
        endTs_pos += 6; // length of "endTs="
        size_t end_pos = url_params.find("&", endTs_pos);
        endTime = url_params.substr(endTs_pos, end_pos - endTs_pos);
    }

    if (!replay_url.empty() && (!startTime.empty() || !endTime.empty()))
    {
        replay_url = replay_url + "?startTime=" + startTime + "&endTime=" + endTime;
    }

    replaceString(replay_url, "rtsp://", "file://");

    if (!startTime.empty())
    {
        opts["startTime"] = startTime;
    }
    if (!endTime.empty())
    {
        opts["endTime"] = endTime;
    }

    if (url_params.find("bbox=1") != string::npos)
    {
        opts["overlayBbox"] = "true";
        opts["overlay"] = "true";
    }
    if (url_params.find("dbg=1") != string::npos)
    {
        opts["bboxDebug"] = "true";
        opts["overlay"] = "true";
    }
    if (url_params.find("objIds=") != string::npos)
    {
        // Parse objIds=(1;2;3;4) format
        opts["bboxShowAll"] = "false";
        string objIds;
        size_t objId_pos = url_params.find("objIds=(");
        if (objId_pos != string::npos)
        {
            objId_pos += 8; // length of "objIds=("
            size_t close_paren = url_params.find(")", objId_pos);
            if (close_paren != string::npos)
            {
                objIds = url_params.substr(objId_pos, close_paren - objId_pos);
                if (!objIds.empty() && objIds.back() == ';') {
                    objIds.pop_back();
                }
                // Replace all dashes with commas
                size_t pos = 0;
                while ((pos = objIds.find(';', pos)) != string::npos) {
                    objIds.replace(pos, 1, ",");
                }
                opts["bboxObjectId"] = objIds;
            }
        }
        if (objIds.empty())
        {
            opts["bboxShowAll"] = "true";
        }
    }

    LOG(info) << "REPLAY URL:" << replay_url << endl;
    for (auto itr = opts.begin(); itr != opts.end(); itr++)
    {
        LOG(info) << "opts[" << itr->first << "]:" << itr->second << endl;
    }

    m_vodVideoSource = std::make_shared<VodVideoSource>(replay_url, opts);
    if (m_vodVideoSource)
    {
        m_vodVideoSource->createConsumerPipeline();
        m_vodVideoSource->setBitstreamConsumer(m_mediaSource->getself());
        m_vodVideoSource->setConsumerReady();
        m_vodVideoSource->startStream();
    }
}

void VodOverlayManager::sendFrame(FrameParams& frame_params)
{
    if (m_mediaSource == nullptr)
    {
        LOG(error) << "Media source is nullptr" << endl;
        return;
    }

    std::vector<uint8_t> content;
    if (frame_params.m_buffer == nullptr || frame_params.m_size == 0)
    {
        /* Consider zero-sized frame as EOS and Broadcast RTCP-Bye message */
        string eos_msg = STREAM_MSG_EOS;
        content.insert(content.end(), eos_msg.begin(), eos_msg.end());
        std::shared_ptr<DiscreteFrame> discreteFrame(
                new DiscreteFrame(content, frame_params.m_presentationTime, frame_params.m_latencyStartTime));
        m_mediaSource->m_streamBuf.push(discreteFrame);
        return;
    }
    else
    {
        content.insert(content.end(), frame_params.m_buffer, frame_params.m_buffer + frame_params.m_size);
    }    

    if (iequals(frame_params.m_codec, "h265"))
    {
        vector<std::pair<H265NaluType, int>> h265_nal_list = getListOfH265NalUnits(content);
        for (size_t index = 0; index < h265_nal_list.size(); index++)
        {
            H265NaluType nal_unit = h265_nal_list[index].first;
            if (nal_unit == H265NaluType::AUD_NUT)
            {
                /* Ignore AUD frames if any */
                continue;
            }
            frame_params.m_buffer = content.data() + h265_nal_list[index].second;
            if (index == h265_nal_list.size() - 1)
            {
                frame_params.m_size = content.size() - h265_nal_list[index].second;
            }
            else
            {
                frame_params.m_size = h265_nal_list[index + 1].second - h265_nal_list[index].second;
            }

            std::vector<uint8_t> nal_content;
            nal_content.insert(nal_content.end(), frame_params.m_buffer, frame_params.m_buffer + frame_params.m_size);

            removeH264NalStartCodes(nal_content);

            std::shared_ptr<DiscreteFrame> discreteFrame(
                    new DiscreteFrame(nal_content, frame_params.m_presentationTime, frame_params.m_latencyStartTime));
            discreteFrame->m_codec = frame_params.m_codec;
            discreteFrame->m_nalType = nal_unit;

            /* Push the contents in the stream buffer */
            m_mediaSource->m_streamBuf.push(discreteFrame);
        }
    }
    else
    {
        vector<std::pair<NaluType, int>> h264_nal_list = getListOfNalUnits(content);
        for (size_t index = 0; index < h264_nal_list.size(); index++)
        {
            NaluType nal_unit = h264_nal_list[index].first;
            if (nal_unit == NaluType::kAud || (nal_unit >= NaluType::kStapA && nal_unit <= NaluType::kMtap24))
            {
                continue;
            }
            frame_params.m_buffer = content.data() + h264_nal_list[index].second;
            if (index == h264_nal_list.size() - 1)
            {
                frame_params.m_size = content.size() - h264_nal_list[index].second;
            }
            else
            {
                frame_params.m_size = h264_nal_list[index + 1].second - h264_nal_list[index].second;
            }

            std::vector<uint8_t> nal_content;
            nal_content.insert(nal_content.end(), frame_params.m_buffer, frame_params.m_buffer + frame_params.m_size);

            removeH264NalStartCodes(nal_content);

            std::shared_ptr<DiscreteFrame> discreteFrame(
                    new DiscreteFrame(nal_content, frame_params.m_presentationTime, frame_params.m_latencyStartTime));
            discreteFrame->m_codec = frame_params.m_codec;
            discreteFrame->m_nalType = nal_unit;

             /* Push the contents in the stream buffer */
            m_mediaSource->m_streamBuf.push(discreteFrame);
        }
    }
}

int64_t VodOverlayManager::getFirstTs()
{
    if (m_vodVideoSource)
    {
        return (m_vodVideoSource->getFirstTs() / 1000);
    }
    return 0;
}

void VodOverlayManager::stopOverlayPipeline()
{
    if (m_vodVideoSource)
    {
        m_vodVideoSource->stopAndRemoveConsumers();
        m_vodVideoSource.reset();
    }
}
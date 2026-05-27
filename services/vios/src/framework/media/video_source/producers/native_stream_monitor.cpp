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

#include "native_stream_monitor.h"
#include "logger.h"
#include "sensor_info.h"
#include "utils.h"

NativeStreamMonitor*  NativeStreamMonitor::m_pInstance = nullptr;

bool NativeStreamMonitor::addNativeStream(std::shared_ptr<StreamInfo> stream, const string location)
{
    bool status = true;
    if (stream->id.empty())
    {
        LOG(error) << "Invalid stream id" << endl;
        status = false;
        return status;
    }

    if (true == hasNativeStreamProducer(stream->id))
    {
        LOG(error) << "Native stream already added, so do not need to add again for streamId:" << stream->id << endl;
        return status;
    }

    if (stream->stream_type != StreamType::Native)
    {
        status = false;
        LOG(error) << "Invalid Stream type received streamId:" << stream->id << " streamType:" << stream->stream_type << endl;
        return status;
    }

    auto nativeStream = std::make_shared<NativeStreamProducer>(stream->id, stream->name, location);
    if (nativeStream->startPipeline())
    {
        addNativeStreamProducer(stream->id, nativeStream);
        LOG(info) << "Started NativeStreamProducer pipeline for streamId:" << stream->id << " sensorName:" << stream->name << " location:" << location << endl;
    }
    else
    {
        status = false;
        LOG(info) << "Failed to start NativeStreamProducer pipeline for streamId:" << stream->id << " sensorName:" << stream->name << " location:" << location << endl;
    }

    return status;
}

void NativeStreamMonitor::updateSensorStatus(const string streamId)
{
    bool found = false;
    vector<shared_ptr<SensorInfo>> sensors = m_deviceMngr->getSensorList();
    for (uint32_t i = 0; i < sensors.size(); i++)
    {
        shared_ptr<SensorInfo> sensor = sensors[i];
        if (sensor->type != SENSOR_TYPE_CSI)
        {
            continue;
        }

        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            std::shared_ptr<StreamInfo> stream = streams[j];

            if (stream->id == streamId)
            {
                LOG(info) << "Update sensor status as offline for streamId:" << streamId << endl;
                stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_OFFLINE,
                translateStreamStatusToString(StreamStatus::STREAM_STATUS_OFFLINE)));
                sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraNotFoundError));
                found = true;
                break;
            }
        }

        if (found)
        {
            break;
        }
    }
}

void NativeStreamMonitor::removeNativeStream(std::shared_ptr<StreamInfo> stream)
{
    if (stream->id.empty())
    {
        LOG(error) << "Invalid stream id" << endl;
        return;
    }

    if (false == hasNativeStreamProducer(stream->id))
    {
        LOG(error) << "Native stream is not added, so do not need to add again streamId:" << stream->id << endl;
        return;
    }

    std::shared_ptr<NativeStreamProducer> nativeStreamProducer = getNativeStreamProducer(stream->id);
    if (nativeStreamProducer != nullptr)
    {
        nativeStreamProducer->stopPipeline();
    }

    removeNativeStreamProducer(stream->id);
}

std::shared_ptr<NativeStreamProducer> NativeStreamMonitor::getNativeStreamProducer(const string& stream_id)
{
    if (stream_id.empty())
    {
        LOG(error) << "Invalid stream id" << endl;
        return nullptr;
    }

    std::lock_guard<std::mutex> lock(m_nativeStreamProducerLock);
    auto it = m_nativeStreamProducer.find(stream_id);
    if (it != m_nativeStreamProducer.end())
    {
        return it->second;
    }
    return nullptr;
}

void NativeStreamMonitor::registerDataCallback(std::string streamId, shared_ptr<IMediaDataConsumer> consumer, ConsumerStreamType consumerStreamType)
{
    LOG(info) << "Adding consumer for streamId: " << streamId << endl;
    if (consumer == nullptr)
    {
        LOG(error) << "Consumer is null" << endl;
        return;
    }

    {
        std::lock_guard<std::mutex> lock(m_nativeStreamProducerLock);
        std::map<std::string, std::shared_ptr<NativeStreamProducer>>::iterator it = m_nativeStreamProducer.find(streamId);
        if (it != m_nativeStreamProducer.end())
        {
            it->second->addConsumer(consumer, consumerStreamType);
        }
        else
        {
           LOG(error) << "Native stream producer is not added, so do not proceed for streamId:" << streamId << endl;
        }
    }
}

void NativeStreamMonitor::deregisterDataCallback(shared_ptr<IMediaDataConsumer> consumer, std::string& streamId, ConsumerStreamType consumerStreamType)
{
    LOG(info) << "Removing consumer for streamId: " << streamId << endl;
    if (consumer == nullptr)
    {
        LOG(error) << "Consumer is null" << endl;
        return;
    }
    {
        std::lock_guard<std::mutex> lock(m_nativeStreamProducerLock);
        std::map<std::string, std::shared_ptr<NativeStreamProducer>>::iterator it = m_nativeStreamProducer.find(streamId);
        if (it != m_nativeStreamProducer.end())
        {
            it->second->removeConsumer(consumer, consumerStreamType);
        }
        else
        {
           LOG(error) << "Native stream producer is not added, so do not proceed for streamId:" << streamId << endl;
        }
    }
}

void NativeStreamMonitor::updateStreamSettings(std::string& streamId, SensorSettings& settings)
{
    std::map<std::string, std::shared_ptr<NativeStreamProducer>>::iterator it = m_nativeStreamProducer.find(streamId);
    if (it != m_nativeStreamProducer.end())
    {
        /* Set all encoding parameters */
        it->second->getEncodeSettings(settings.encoderValues, settings.encoderOptions);

        /* Set all Image settings */
        it->second->getImageSettings(settings.imageValues, settings.imageOptions);
    }
}

string NativeStreamMonitor::getVideoCodec(std::string& streamId)
{
    std::map<std::string, std::shared_ptr<NativeStreamProducer>>::iterator it = m_nativeStreamProducer.find(streamId);
    if (it != m_nativeStreamProducer.end())
    {
        return it->second->getVideoCodec();
    }
    return DEFAULT_NATIVE_STREAM_VIDEO_CODEC;
}

NativeStreamMonitor::~NativeStreamMonitor()
{
    try {
        clearAllNativeStreams();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~NativeStreamMonitor: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~NativeStreamMonitor" << endl; } catch (...) { (void)std::current_exception(); }
    }
}
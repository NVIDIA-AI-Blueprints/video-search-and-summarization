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

#pragma once

#include<iostream>
#include "logger.h"
#include "nativestreamproducer.h"

class NativeStreamMonitor
{
private:
    static NativeStreamMonitor* m_pInstance;
    NativeStreamMonitor() { }

public:
    static NativeStreamMonitor* getInstance()
    {
        if(m_pInstance == nullptr)
        {
            m_pInstance  = new NativeStreamMonitor();
        }
        return m_pInstance;
    }

    static void deleteInstance()
    {
        if(m_pInstance != nullptr)
        {
            delete m_pInstance;
            m_pInstance = nullptr;
        }
    }
    ~NativeStreamMonitor();

    void setDeviceManager(std::shared_ptr<DeviceManager> deviceMngr)
    {
        m_deviceMngr = deviceMngr;
    }

    void removeNativeStream(std::shared_ptr<StreamInfo> stream);
    bool addNativeStream(std::shared_ptr<StreamInfo> stream, const string location);
    std::shared_ptr<NativeStreamProducer> getNativeStreamProducer(const string& stream_id);
    void registerDataCallback(std::string deviceId, shared_ptr<IMediaDataConsumer> consumer, ConsumerStreamType consumerStreamType);
    void deregisterDataCallback(shared_ptr<IMediaDataConsumer> consumer, std::string& deviceId, ConsumerStreamType consumerStreamType);
    string getVideoCodec(std::string& streamId);
    void updateStreamSettings(std::string& streamId, SensorSettings& settings);
    void updateSensorStatus(const string streamId);

private:
    std::map<std::string, std::shared_ptr<NativeStreamProducer>>    m_nativeStreamProducer;
    std::mutex                                                      m_nativeStreamProducerLock;
    std::shared_ptr<DeviceManager>                                  m_deviceMngr;

    // Add a new NativeStreamProducer to the map
    void addNativeStreamProducer(const std::string& streamID, const std::shared_ptr<NativeStreamProducer>& nativeStream)
    {
        if (streamID.empty())
        {
            return;
        }

        std::lock_guard<std::mutex> lock(m_nativeStreamProducerLock);
        m_nativeStreamProducer[streamID] = nativeStream;
        LOG(info) << "Native Stream for " << streamID << " added and" << " count:" << m_nativeStreamProducer.size() << std::endl;
    }

    // Remove a NativeStreamProducer from the map
    void removeNativeStreamProducer(const std::string& streamID)
    {
        if (streamID.empty())
        {
            return;
        }

        std::lock_guard<std::mutex> lock(m_nativeStreamProducerLock);
        auto it = m_nativeStreamProducer.find(streamID);
        if (it != m_nativeStreamProducer.end())
        {
            m_nativeStreamProducer.erase(it);
            LOG(info) << "Native Stream for " << streamID << " removed and" << " count:" << m_nativeStreamProducer.size() << std::endl;
        }
        else
        {
            LOG(error) << "Native Stream " << streamID << " not found" << std::endl;
        }
    }

    // Check if a streamID exists in the map
    bool hasNativeStreamProducer(const std::string& streamID)
    {
        if (streamID.empty())
        {
            return false;
        }

        std::lock_guard<std::mutex> lock(m_nativeStreamProducerLock);
        return m_nativeStreamProducer.find(streamID) != m_nativeStreamProducer.end();
    }

    void clearAllNativeStreams()
    {
        std::lock_guard<std::mutex> lock(m_nativeStreamProducerLock);
        std::map<std::string, std::shared_ptr<NativeStreamProducer>>::iterator it;
        for( it = m_nativeStreamProducer.begin(); it != m_nativeStreamProducer.end(); it++)
        {
            shared_ptr<NativeStreamProducer> nativeStreamProducer = it->second;
            if (nativeStreamProducer != nullptr)
            {
                nativeStreamProducer->stopPipeline();
            }
        }
        m_nativeStreamProducer.clear();
        LOG(info) << "All Native streams removed." << std::endl;
    }
};
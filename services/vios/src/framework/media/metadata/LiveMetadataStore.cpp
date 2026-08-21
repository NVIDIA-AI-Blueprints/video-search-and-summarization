/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "LiveMetadataStore.h"
#include "logger.h"
#include "NotificationFactory.h"

// Nested listener implementation
LiveMetadataStore::NotificationListener::NotificationListener(LiveMetadataStore* parent)
    : m_parent(parent)
{
}

void LiveMetadataStore::NotificationListener::onMessage(Json::Value payload)
{
    if (m_parent)
    {
        m_parent->addMetadata(payload);
    }
}

LiveMetadataStore::LiveMetadataStore(const std::string& sensorName, bool startListener, bool isGodsEyeView)
    : m_sensorName(sensorName)
    , m_isGodsEyeView(isGodsEyeView)
{
    if (startListener)
    {
        m_isListenerStarted = true;
        if (!GET_CONFIG().overlay_3d_sensor_name.empty())
        {
            m_sensorName3d = GET_CONFIG().overlay_3d_sensor_name;
        }
        try
        {
            m_notificationConsumer = nv_vms::NotificationFactory::CreateNotificationConsumer();
            if (m_notificationConsumer)
            {
                m_notificationListener = std::make_unique<NotificationListener>(this);
                m_notificationConsumer->registerMessageListener(m_notificationListener.get());
            }
        }
        catch(const std::exception& e)
        {
            LOG(error) << "Error when trying to create notification listener: " <<  e.what() << endl;
        }
    }
}

LiveMetadataStore::~LiveMetadataStore()
{
    if (m_notificationConsumer && m_notificationListener)
    {
        m_notificationConsumer->deregisterMessageListener(m_notificationListener.get());
    }
    m_notificationListener.reset();
    m_metaWait.signal();
}

void LiveMetadataStore::checkAndWaitForMetadata()
{
    /* The overlay draws on the media thread, so this wait is charged to every
    ** frame.  While analytics is publishing, waiting keeps a box on the frame it
    ** belongs to and costs almost nothing because the metadata is already there.
    ** When nothing is publishing - no analytics deployed, or the producer has
    ** stopped - every frame paid the full timeout instead, which held the live
    ** pipeline at 1000/METADATA_WAIT_TIMEOUT_MS frames per second no matter what
    ** the source delivered.  After a few silent frames the wait is abandoned and
    ** the stream runs at source rate; the first metadata to arrive restores it.
    */
    if (m_metadataIdle)
    {
        return;
    }
    while (isMetadataQueueEmpty())
    {
        if (!m_metaWait.wait(METADATA_WAIT_TIMEOUT_MS))
        {
            if (++m_metadataTimeouts >= MAX_CONSECUTIVE_METADATA_TIMEOUTS)
            {
                m_metadataIdle = true;
                LOG(warning) << "No analytics metadata for " << m_metadataTimeouts.load()
                             << " consecutive frames; overlay will draw without waiting"
                             << " until metadata resumes" << endl;
            }
            break;
        }
    }
    if (!isMetadataQueueEmpty())
    {
        m_metadataTimeouts = 0;
    }
}

void LiveMetadataStore::addMetadata(const Json::Value& metadata)
{
    std::string sensorId = metadata.get("sensorId", "").asString();

    if (m_isGodsEyeView)
    {
        if (sensorId == m_sensorName || sensorId == m_sensorName3d)
        {
            // Configured 3d sensor name matches — accept directly
        }
        else if (m_cachedSensorId.empty() && !m_sensorName3d.empty())
        {
            m_cachedSensorId = sensorId;
            LOG(info) << "GodsEyeView auto-cached to sensor: " << sensorId << endl;
        }
        else if (sensorId != m_cachedSensorId)
        {
            return;
        }
    }
    else if (sensorId != m_sensorName && sensorId != m_sensorName3d)
    {
        return;
    }

    std::lock_guard<std::mutex> guard(metadataQueueMutex());
    metadataQueue().push(metadata);
    /* Metadata is flowing again: resume synchronising frames with it. */
    if (m_metadataIdle)
    {
        LOG(info) << "Analytics metadata resumed; overlay will synchronise again" << endl;
    }
    m_metadataIdle = false;
    m_metadataTimeouts = 0;
    m_metaWait.signal();
}

Json::Value LiveMetadataStore::getMetadata(const int64_t frameTS)
{
    if (!m_isListenerStarted)
    {
        return Json::nullValue;
    }
    checkAndWaitForMetadata();

    Json::Value metadata = Json::nullValue;
    {
        std::lock_guard<std::mutex> guard(metadataQueueMutex());
        if (!metadataQueue().empty())
        {
            metadata = metadataQueue().front();
        }
    }
    if (metadata == Json::nullValue)
    {
        return metadata;
    }
    int64_t metadataTS = metadata["epocTime"].asUInt64() * 1000;
    if (metadataTS >= frameTS)
    {
        if (metadataTS == frameTS)
        {
            std::lock_guard<std::mutex> guard(metadataQueueMutex());
            if (!metadataQueue().empty())
            {
                metadataQueue().pop();
            }
        }
        return metadata;
    }

    while(metadataTS < frameTS && !isMetadataQueueEmpty())
    {
        {
            std::lock_guard<std::mutex> guard(metadataQueueMutex());
            if (!metadataQueue().empty())
            {
                metadata = metadataQueue().front();
                metadataTS = metadata["epocTime"].asUInt64() * 1000;
                if (metadataTS >= frameTS)
                {
                    break;
                }
                else
                {
                    metadataQueue().pop();
                }
            }
        }
        checkAndWaitForMetadata();
    }

    if (metadataTS == frameTS)
    {
        std::lock_guard<std::mutex> guard(metadataQueueMutex());
        if (!metadataQueue().empty())
        {
            metadataQueue().pop();
        }
    }
    return metadata;
}

void LiveMetadataStore::reFillMetadata(std::queue<Json::Value>& qToFill, std::mutex& qToFillMutex)
{
    checkAndWaitForMetadata();

    std::queue<Json::Value> tempQueue;
    {
        std::lock_guard<std::mutex> guard(metadataQueueMutex());
        if (!metadataQueue().empty())
        {
            tempQueue.swap(metadataQueue());
        }
    }
    {
        while (!tempQueue.empty())
        {
            {
                std::lock_guard<std::mutex> guard(qToFillMutex);
                qToFill.push(tempQueue.front());
            }
            tempQueue.pop();
        }
    }
}
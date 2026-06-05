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

#pragma once

#include "MetadataStore.h"
#include "syncobject.h"
#include "NotificationFactory.h"

class LiveMetadataStore : public IMetadataStore
{
public:
    LiveMetadataStore(const std::string& sensorName, bool startListener, bool isGodsEyeView = false);
    virtual ~LiveMetadataStore();

    virtual void addMetadata(const Json::Value& metadata) override;
    virtual Json::Value getMetadata(const int64_t frameTS) override;
    virtual void reFillMetadata(std::queue<Json::Value>& qToFill, std::mutex& qToFillMutex) override;

    void checkAndWaitForMetadata();

private:
    class NotificationListener : public nv_vms::INotificationListener
    {
        public:
        explicit NotificationListener(LiveMetadataStore* parent);
        void onMessage(const Json::Value payload) override;

        private:
        LiveMetadataStore* m_parent = nullptr;
    };

    SyncObject                      m_metaWait = {};
    nv_vms::INotificationInterface* m_notificationConsumer = nullptr;
    NotificationListener*           m_notificationListener = nullptr;
    std::string                     m_sensorName = "";
    std::string                     m_sensorName3d = "";
    std::string                     m_cachedSensorId = "";
    bool                            m_isListenerStarted = false;
    bool                            m_isGodsEyeView = false;
};
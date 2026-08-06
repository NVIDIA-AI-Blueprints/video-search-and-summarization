/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "composite_notifier.h"

#include <algorithm>

std::unique_ptr<CompositeNotifier> CompositeNotifier::_instance;
std::mutex CompositeNotifier::_instanceMutex;

CompositeNotifier* CompositeNotifier::getInstance()
{
    std::lock_guard<std::mutex> lock(_instanceMutex);
    if (_instance == nullptr)
    {
        _instance = std::make_unique<CompositeNotifier>(PrivateTag{});
    }
    return _instance.get();
}

void CompositeNotifier::deleteInstance()
{
    std::lock_guard<std::mutex> lock(_instanceMutex);
    _instance.reset();
}

CompositeNotifier::CompositeNotifier(PrivateTag)
{
    // Children track their own connections; the composite itself is always ready.
    m_connected = true;
}

CompositeNotifier::~CompositeNotifier()
{
    stopMessageProcessing();
}

void CompositeNotifier::addNotifier(nv_vms::INotificationInterface* notifier)
{
    if (notifier == nullptr || notifier == this)
    {
        return;
    }
    std::lock_guard<std::mutex> lock(m_notifiersMutex);
    if (std::find(m_notifiers.begin(), m_notifiers.end(), notifier) == m_notifiers.end())
    {
        m_notifiers.push_back(notifier);
    }
}

size_t CompositeNotifier::notifierCount() const
{
    std::lock_guard<std::mutex> lock(m_notifiersMutex);
    return m_notifiers.size();
}

bool CompositeNotifier::deliverMessage(Json::Value& message)
{
    std::vector<nv_vms::INotificationInterface*> notifiers;
    {
        std::lock_guard<std::mutex> lock(m_notifiersMutex);
        notifiers = m_notifiers;
    }
    for (nv_vms::INotificationInterface* notifier : notifiers)
    {
        // sendMessage only enqueues; each child delivers and retries on its own worker.
        notifier->sendMessage(message);
    }
    return true;
}

void CompositeNotifier::retryConnection()
{
    // Nothing to reconnect at this level; children manage their own connections.
}

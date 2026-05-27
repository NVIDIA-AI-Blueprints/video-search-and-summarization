// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "stream_event_manager.h"
#include "logger.h"

namespace nv_vms
{
StreamEventManager& StreamEventManager::getInstance()
{
    static StreamEventManager instance;
    return instance;
}

StreamEventManager::~StreamEventManager()
{
    m_exit.store(true);
    m_queueSync.signal();
    if (m_notifyThread.joinable())
    {
        m_notifyThread.join();
    }
}

void StreamEventManager::start()
{
    std::call_once(m_startFlag, [this]() {
        m_exit.store(false);
        m_notifyThread = std::thread([this]() { notifyTask(); });
    });
}

void StreamEventManager::stop()
{
    m_exit.store(true);
    m_queueSync.signal();
    if (m_notifyThread.joinable())
    {
        m_notifyThread.join();
    }
}

void StreamEventManager::sendEvent(const std::string& url,
                                  StreamStatus status,
                                  StreamEncParam& details)
{
    if (!hasListeners())
    {
        return;
    }

    start();

    auto listenerData = std::make_shared<ListenerContext>();
    listenerData->url = url;
    listenerData->status = status;
    listenerData->details = details;
    {
        std::lock_guard<std::mutex> lock(m_queueMutex);
        m_queue.push(listenerData);
    }
    m_queueSync.signal();
}

VmsErrorCode StreamEventManager::sendEventBlocking(const std::string& url,
                                                   StreamStatus status,
                                                   StreamEncParam& details)
{
    if (!hasListeners())
    {
        LOG(info) << "sendEventBlocking: no listeners registered, skipping event for url: " << url << endl;
        return VmsErrorCode::NoError;
    }

    LOG(info) << "sendEventBlocking: dispatching event, status: " << status
              << ", url: " << url << endl;

    VmsErrorCode result = VmsErrorCode::NoError;
    auto startTime = std::chrono::steady_clock::now();

    std::vector<Callback> callbacksCopy;
    {
        std::lock_guard<std::mutex> lock(m_callbacksMutex);
        callbacksCopy = m_callbacks;
    }
    LOG(info) << "sendEventBlocking: notifying " << callbacksCopy.size() << " callbacks" << endl;
    for (auto& cb : callbacksCopy)
    {
        if (cb)
        {
            VmsErrorCode cbResult = cb(url, status, details);
            if (cbResult != VmsErrorCode::NoError)
            {
                LOG(warning) << "sendEventBlocking: callback returned error "
                             << cbResult << " for url: " << url << endl;
                result = cbResult;
            }
        }
    }

    std::set<IStreamStatusEvent*> listenersCopy;
    {
        std::lock_guard<std::mutex> lock(m_listenerMutex);
        listenersCopy = m_listeners;
    }
    LOG(info) << "sendEventBlocking: notifying " << listenersCopy.size() << " listeners" << endl;
    for (auto listener : listenersCopy)
    {
        if (listener)
        {
            listener->onStreamStatusChange(url, status, details);
        }
    }

    auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - startTime);
    LOG(info) << "sendEventBlocking: completed for url: " << url
              << ", elapsed: " << elapsed.count() << "ms" << endl;
    return result;
}

void StreamEventManager::registerCallback(Callback cb)
{
    if (!cb)
    {
        return;
    }
    std::lock_guard<std::mutex> lock(m_callbacksMutex);
    m_callbacks.push_back(std::move(cb));
}

void StreamEventManager::registerListener(IStreamStatusEvent* listener)
{
    if (!listener)
    {
        return;
    }
    std::lock_guard<std::mutex> lock(m_listenerMutex);
    m_listeners.insert(listener);
}

void StreamEventManager::deregisterListener(IStreamStatusEvent* listener)
{
    std::lock_guard<std::mutex> lock(m_listenerMutex);
    m_listeners.erase(listener);
}

bool StreamEventManager::hasListeners()
{
    std::scoped_lock lock(m_callbacksMutex, m_listenerMutex);
    return !(m_callbacks.empty() && m_listeners.empty());
}

void StreamEventManager::notifyTask()
{
    while (true)
    {
        std::shared_ptr<ListenerContext> listenerData;
        {
            std::unique_lock<std::mutex> lk(m_queueMutex);
            while (m_queue.empty() && !m_exit.load())
            {
                lk.unlock();
                m_queueSync.wait(5000);  // 5 second timeout
                lk.lock();
            }
            if (m_exit.load())
            {
                break;
            }
            if (m_queue.empty())
            {
                continue;
            }
            listenerData = m_queue.front();
            m_queue.pop();
        }

        if (!listenerData)
        {
            continue;
        }

        std::vector<Callback> callbacksCopy;
        {
            std::lock_guard<std::mutex> lock(m_callbacksMutex);
            callbacksCopy = m_callbacks;
        }
        for (auto &cb : callbacksCopy)
        {
            if (cb)
            {
                cb(listenerData->url, listenerData->status, listenerData->details);
            }
        }

        std::set<IStreamStatusEvent*> listenersCopy;
        {
            std::lock_guard<std::mutex> lock(m_listenerMutex);
            listenersCopy = m_listeners;
        }
        for (auto listener : listenersCopy)
        {
            if (listener)
            {
                listener->onStreamStatusChange(listenerData->url,
                                               listenerData->status,
                                               listenerData->details);
            }
        }
    }
}
} // namespace nv_vms

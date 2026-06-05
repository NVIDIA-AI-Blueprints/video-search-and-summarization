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

#pragma once

#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <queue>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "stream_monitor.h"
#include "syncobject.h"

namespace nv_vms
{
class StreamEventManager
{
public:
    using Callback = std::function<VmsErrorCode(const std::string& url,
                                                StreamStatus status,
                                                StreamEncParam& details)>;

    static StreamEventManager& getInstance();

    void start();
    void stop();
    void sendEvent(const std::string& url, StreamStatus status, StreamEncParam& details);
    VmsErrorCode sendEventBlocking(const std::string& url, StreamStatus status,
                                   StreamEncParam& details);

    void registerCallback(Callback cb);
    void registerListener(IStreamStatusEvent* listener);
    void deregisterListener(IStreamStatusEvent* listener);

    ~StreamEventManager();

private:
    StreamEventManager() = default;
    StreamEventManager(const StreamEventManager&) = delete;
    StreamEventManager& operator=(const StreamEventManager&) = delete;
    StreamEventManager(StreamEventManager&&) = delete;
    StreamEventManager& operator=(StreamEventManager&&) = delete;

    void notifyTask();
    bool hasListeners();

    struct ListenerContext
    {
        std::string url;
        StreamStatus status;
        StreamEncParam details;
    };

    std::mutex m_callbacksMutex;
    std::vector<Callback> m_callbacks;

    std::mutex m_listenerMutex;
    std::set<IStreamStatusEvent*> m_listeners;

    std::mutex m_queueMutex;
    SyncObject m_queueSync;
    std::queue<std::shared_ptr<ListenerContext>> m_queue;
    std::thread m_notifyThread;
    std::atomic<bool> m_exit{false};
    std::once_flag m_startFlag;
};
} // namespace nv_vms

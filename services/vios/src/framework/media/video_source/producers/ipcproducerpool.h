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

#include <string>
#include <map>
#include <atomic>
#include <mutex>

#include "libasync++/async++.h"
#include "logger.h"
#include "gstnvipcproducer.h"

typedef std::map<string, shared_ptr<NvIPCProducer>, std::less<>> ipc_producer_map;

class IPCProducerPool
{
    public:
        static IPCProducerPool* getInstance()
        {
            static IPCProducerPool instance;
            return &instance;
        }
        ~IPCProducerPool() {}

        ipc_producer_map getVideoSenderPool()
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            return m_ipcProducerPool;
        }

        void addStream(const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            LOG(info) << "IPC Adding stream: " << url << endl;
            ipc_producer_map::iterator it = m_ipcProducerPool.find(url);
            if (it == m_ipcProducerPool.end())
            {
                shared_ptr<NvIPCProducer> ipc_producer(new NvIPCProducer(url));
                m_ipcProducerPool[url] = ipc_producer;
            }
        }

        void removeStreams()
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            for(const auto &it : m_ipcProducerPool)
            {
                shared_ptr<NvIPCProducer> producer = it.second;
                if (producer)
                {
                    LOG(info) << "Deleting IPC Producer instance: " << it.first << endl;
                    producer.reset();
                }
            }
            m_ipcProducerPool.clear();
	    }

        void removeStream(const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            LOG(info) << "IPC Remove stream: " << url << endl;
            ipc_producer_map::iterator it = m_ipcProducerPool.find(url);
            if (it != m_ipcProducerPool.end())
            {
                shared_ptr<NvIPCProducer> producer = it->second;
                if (producer)
                {
                    LOG(info) << "Deleting IPC Producer instance: " << it->first << endl;
                    producer.reset();
                }
                m_ipcProducerPool.erase(it);
            }
	    }

        shared_ptr<NvIPCProducer> getIPCProducer(const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            ipc_producer_map::iterator it = m_ipcProducerPool.find(url);
            if (it != m_ipcProducerPool.end())
            {
                return m_ipcProducerPool[url];
            }
            else
            {
                return nullptr;
            }
        }

        void setIPCProducer(shared_ptr<NvIPCProducer>& ipc_producer, const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            m_ipcProducerPool[url] = ipc_producer;
        }

    private:
        ipc_producer_map m_ipcProducerPool;
        std::mutex m_poolLock;
};

/*
 * SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <vector>
#include <memory>
#include <mutex>
#include <condition_variable>
#include <chrono>

#define GARBAGE_CLEANUP_MIN_THRESHOLD_USEC 5*1000*1000
#define GARBAGE_CLEANUP_THREAD_WAKEUP_SEC 5

class GarbageCollector
{
    public:
        typedef struct GarbageObject_
        {
            std::shared_ptr<void> m_obj;
            bool m_needDeletion = true;
            struct timeval m_time = {0};
        }GarbageObject;

        static GarbageCollector* getInstace()
        {
            static GarbageCollector _instance;
            return &_instance;
        }

        GarbageCollector()
        {
            LOG(info) << "Creating GarbageCollector" << endl;
            m_exit = false;
            m_garbageCleanupThread = std::thread([this] { this->garbageCleanuptask(); });
        }
        ~GarbageCollector()
        {
            LOG(info) << "~GarbageCollector" << endl;
            m_exit = true;
            m_threadCondWait.notify_all();
            m_garbageCleanupThread.join();
            m_garbageCollector.clear();
            LOG(info) << "Exiting ~GarbageCollector" << endl;
        }

        void insert(shared_ptr<void> object, bool need_deletion = true)
        {
            std::lock_guard<std::mutex> garbageCollectorLock(m_mutex);
            struct timeval timeNow;
            gettimeofday(&timeNow, nullptr);
            GarbageObject obj;
            obj.m_obj = object;
            obj.m_time = timeNow;
            obj.m_needDeletion = need_deletion;
            m_garbageCollector.push_back(obj);
        }

        int size()
        {
            std::lock_guard<std::mutex> garbageCollectorLock(m_mutex);
            return m_garbageCollector.size();
        }
        void garbageCleanuptask()
        {
            while (m_exit == false)
            {
                std::vector<std::shared_ptr<void>> objectsToDelete;
                /* Clear the garbage objects from the list */
                {
                    std::lock_guard<std::mutex> garbageCollectorLock(m_mutex);
                    struct timeval timeNow;
                    gettimeofday(&timeNow, nullptr);
                    std::vector<GarbageObject>::iterator it = m_garbageCollector.begin();
                    while (it != m_garbageCollector.end())
                    {
                        if (it->m_needDeletion &&
                            timevaldiff(it->m_time, timeNow) >= GARBAGE_CLEANUP_MIN_THRESHOLD_USEC)
                        {
                            objectsToDelete.push_back(it->m_obj);
                            it = m_garbageCollector.erase(it);
                        }
                        else
                        {
                            ++it;
                        }
                    }
                }

                // Launch a detached thread to delete the objects
                if (!objectsToDelete.empty())
                {
                    std::thread([objects = std::move(objectsToDelete)]() {
                        // Objects will be automatically deleted when this thread ends
                        // and the vector goes out of scope
                    }).detach();
                }

                // Sleep for given interval or untill get notified.
                {
                    std::unique_lock<std::mutex> lck(m_threadLock);
                    m_threadCondWait.wait_for(lck, std::chrono::seconds(GARBAGE_CLEANUP_THREAD_WAKEUP_SEC));
                }
            }
            LOG(info) << "Exiting the garbageCleanuptask thread" << endl;
        }

        std::thread m_garbageCleanupThread;
        std::vector<GarbageObject> m_garbageCollector;
        std::mutex   m_threadLock;
        std::condition_variable m_threadCondWait;
        std::mutex m_mutex;
        bool m_exit;
};
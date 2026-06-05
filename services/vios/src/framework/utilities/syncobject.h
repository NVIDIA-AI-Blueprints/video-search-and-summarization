/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <mutex>
#include <condition_variable>
#include <atomic>

using namespace std;

class SyncObject
{
    public:
        bool wait(unsigned int duration)
        {
            bool ret = true;
            std::unique_lock<std::mutex> lock(m_Lock);
            auto until = std::chrono::system_clock::now() + std::chrono::milliseconds(duration);
            if(m_flag == false)
            {
                ret = m_cond.wait_until(lock, until , [this]{ return m_flag.load(); });
            }
            m_flag = false;
            return ret;
        }

        void signal()
        {
            std::unique_lock<std::mutex> lock(m_Lock);
            m_flag = true;
            m_cond.notify_all();
        }
    private:
        std::mutex               m_Lock;
        std::condition_variable  m_cond;
        atomic<bool>             m_flag {false};
};
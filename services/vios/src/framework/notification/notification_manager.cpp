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

#include "notification_manager.h"
#include "logger.h"

#include <chrono>
#include <ctime>
#include <iomanip>

#define MSG_EXPIRY_HOURS 1

namespace   {
    bool isMessageExpired(string msgTime)
    {
        std::tm msgTime_tm = {};
        std::istringstream ss(msgTime);
        ss >> std::get_time(&msgTime_tm, "%Y-%m-%dT%H:%M:%SZ");
        auto msgTime_tp = std::chrono::system_clock::from_time_t(std::mktime(&msgTime_tm));

        auto currentTime = std::chrono::system_clock::now();
        // Calculate the difference in hours accuracy
        auto diffHours = std::chrono::duration_cast<std::chrono::hours>(currentTime - msgTime_tp).count();

        // Check if the difference is greater than expiry duration
        return diffHours >= MSG_EXPIRY_HOURS;
    }
}   // unnamed namespace

INotificationInterface::INotificationInterface()
{
    m_retryThread = std::thread([this] { this->processMessageQueue(); });
}

void INotificationInterface::processMessageQueue()
{
    LOG(info) << "Started the message processing thread" << std::endl;
    while (m_exitRetryThread == false)
    {
        Json::Value msg;
        {
            {
                std::unique_lock<std::mutex> lk(m_msgQueueMutex);
                while (m_messages.empty() && m_exitRetryThread == false)
                {
                    auto until = std::chrono::system_clock::now() + 5s;
                    m_msgQueueCv.wait_until(lk, until);
                }
                if (m_messages.empty())
                {
                    continue;
                }
            }

            bool msgSentSuccess = true;
            {
                std::unique_lock<std::mutex> lk(m_msgQueueMutex);
                msg = m_messages.front();
                string createdTime = msg.get("created_at", "").asString();
                if (isMessageExpired(createdTime))
                {
                    LOG(error) << "Removing expired message" << endl;
                    m_messages.pop();
                    continue;
                }
                if (m_connected)
                {
                    msgSentSuccess = deliverMessage(msg);
                    if (msgSentSuccess)
                    {
                        m_messages.pop();
                    }
                }
            }
            if (!m_connected || !msgSentSuccess)
            {
                retryConnection();
                if (!m_connected && m_exitRetryThread == false)
                {
                    std::unique_lock<std::mutex> lk(m_retryConnectionMutex);
                    auto until = std::chrono::system_clock::now() + 5s;
                    m_retryConnectionCv.wait_until(lk, until);
                }
            }
        }
    }
    LOG(info) << "Exited the message processing thread" << std::endl;
}

void INotificationInterface::sendMessage (Json::Value& message)
{
    if (m_libError || m_fatalError)
    {
        LOG(error) << "Cannot send notification, libraryError or m_fatalError" << std::endl;
        return;
    }
    {
        string createdTime = message.get("created_at", "").asString();
        if (createdTime.empty())
        {
            message["created_at"] = getCurrentTime();
        }
        std::unique_lock<std::mutex> lk(m_msgQueueMutex);
        m_messages.push(message);
        m_msgQueueCv.notify_all();
    }

    // Thread should be able to retry connection when new msg arrives
    {
        std::unique_lock<std::mutex> lk(m_retryConnectionMutex);
        m_retryConnectionCv.notify_all();
    }
}

void INotificationInterface::stopMessageProcessing()
{
    m_exitRetryThread = true;
    {
        std::unique_lock<std::mutex> lk(m_msgQueueMutex);
        std::queue<Json::Value> empty;
        std::swap( m_messages, empty );
    }
    m_retryConnectionCv.notify_all();
    m_msgQueueCv.notify_all();
    if (m_retryThread.joinable())
    {
        m_retryThread.join();
    }
}

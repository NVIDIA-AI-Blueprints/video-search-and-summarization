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

#include "event_loop.h"
#include "logger.h"
#include "libasync++/async++.h"

#include <iostream>
#include <assert.h>
#include <chrono>

#define MSG_EXIT_EVENT_LOOP     1
#define MSG_POST_USER_DATA      2
#define MSG_POST_EVENT_ALIVE    3

#define POST_MESSAGE_MAX_WAIT_SEC  15
#define PROCESS_FUNCTION_MAX_WAIT_SEC  10

#define EVENT_LOOP_LOG  if ( (userData->m_taskName != "status") && (userData->m_taskName != "stats") && (userData->m_taskName != "query")) \
                        LOG(info) << "Run task:  "<< userData->m_taskName << ", msg id: "<< userData->m_msgId << " on event loop: " << m_threadName << endl;

struct EventLoopMsg
{
    EventLoopMsg() : id(-1)
                   , msg(nullptr)
    {}
	EventLoopMsg(int i, std::shared_ptr<void> m) { id = i; msg = m; }
	int id;
    std::shared_ptr<void> msg;
};

EventLoop::EventLoop(std::string threadName, process_message func) 
                    : m_thread(nullptr)
                    , m_threadName(threadName)
                    , m_processMsg(func)
                    , m_parent(nullptr)
{
    CreateThread();
}

EventLoop::~EventLoop()
{
    try {
        ExitThread();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~EventLoop: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~EventLoop" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

//----------------------------------------------------------------------------
// CreateThread
//----------------------------------------------------------------------------
bool EventLoop::CreateThread()
{
	if (!m_thread)
    {
		m_thread = std::unique_ptr<std::thread>(new thread(&EventLoop::Process, this));
        std::unique_lock<std::mutex> lk(m_mutex);
        auto until = std::chrono::system_clock::now() + 1s;
        m_cv.wait_until(lk, until);
    }
	return true;
}

//----------------------------------------------------------------------------
// GetThreadId
//----------------------------------------------------------------------------
std::thread::id EventLoop::GetThreadId()
{
	assert(m_thread != nullptr);
	return m_thread->get_id();
}

//----------------------------------------------------------------------------
// GetCurrentThreadId
//----------------------------------------------------------------------------
std::thread::id EventLoop::GetCurrentThreadId()
{
	return this_thread::get_id();
}

void EventLoop::processFunctionWrapper(std::shared_ptr<EventLoopData> userData, void *parent)
{
    m_isProcessFuncDone = false;
    std::thread t([&]()
    {
        if (m_processMsg)
        {
            m_processMsg(userData, m_parent);
        }
        {
            std::unique_lock<std::mutex> l(m_mutexProceeFunc);
            m_isProcessFuncDone = true;
            m_cvProcessFunc.notify_one();
        }
    });
    {
        std::unique_lock<std::mutex> l(m_mutexProceeFunc);
        if (m_isProcessFuncDone.load() == false)
        {
            auto until = PROCESS_FUNCTION_MAX_WAIT_SEC;
            std::shared_ptr<EventLoopOutData> out = userData->m_outResult;
            if (out)
            {
                until = out->m_timeout == 0 ? PROCESS_FUNCTION_MAX_WAIT_SEC : out->m_timeout;
            }
            if(m_cvProcessFunc.wait_for(l, std::chrono::seconds(until)) == std::cv_status::timeout)
            {
                LOG(error) << "EventLoop task: "<< userData->m_taskName << " failed with Timeout" << endl;
                userData->m_error = true;
            }
        }
    }
    if (t.joinable())
    {
        t.join();
    }
    return;
}

void EventLoop::Process()
{
    m_cv.notify_all();
    while (1)
    {
        std::shared_ptr<EventLoopMsg> msg;
        {
            // Wait for a message to be added to the queue
            std::unique_lock<std::mutex> lk(m_mutex);
            m_cv.wait(lk, [this] { return !m_queue.empty() || !m_aliveQueue.empty(); });
            if (m_queue.empty() && m_aliveQueue.empty())
            {
                continue;
            }
            if (m_aliveQueue.empty() == false)
            {
                msg = m_aliveQueue.front();
                m_aliveQueue.pop();
            }
            else if (m_queue.empty() == false)
            {
                msg = m_queue.front();
                m_queue.pop();
            }
        }

        switch (msg->id)
        {
            case MSG_POST_USER_DATA:
            case MSG_POST_EVENT_ALIVE:
            {
                assert(msg->msg != nullptr);

                auto userData = std::static_pointer_cast<EventLoopData>(msg->msg);

                EVENT_LOOP_LOG

                if (m_processMsg && m_parent)
                {
                    if (msg->id == MSG_POST_USER_DATA)
                    {
                        processFunctionWrapper(userData, m_parent);
                    }
                    if (userData->m_expectResult)
                    {
                        std::shared_ptr<EventLoopOutData> out = userData->m_outResult;
                        {
                            std::unique_lock<std::mutex> lk(out->m_outDataLock);
                            userData->m_expectResult = false;
                            out->m_outDataWait.notify_one();
                        }
                    }
                }
                break;
            }
            case MSG_EXIT_EVENT_LOOP:
            {
                LOG(info) << "MSG_EXIT_EVENT_LOOP message received for event loop = " << m_threadName << endl;
                return;
            }

            default:
            {
                LOG(warning) << "Invalid message received" << endl;
            }
        }
    }
    LOG(info) << "Existing Event loop: " << m_threadName << endl;
}

bool EventLoop::postMsg(std::shared_ptr<EventLoopData> data)
{
    bool ret = true;
    if (m_thread == nullptr)
    {
        LOG(error) << "Eventloop thread is null, returning" << endl;
        return true;
    }
    // Create a new EventLoopMsg
    std::shared_ptr<EventLoopMsg> eventLoopMsg(new EventLoopMsg(MSG_POST_USER_DATA, data));
    {
        // Add user data msg to queue and notify worker thread
        std::unique_lock<std::mutex> lk(m_mutex);
        m_queue.push(eventLoopMsg);
        m_cv.notify_all();
    }
    std::shared_ptr<EventLoopOutData> out = data->m_outResult;
    if (out)
    {
        std::unique_lock<std::mutex> lk(out->m_outDataLock);
        if (data->m_expectResult)
        {
            auto until = std::chrono::system_clock::now() + (out->m_timeout == 0 ? 
                    chrono::seconds(POST_MESSAGE_MAX_WAIT_SEC) : chrono::seconds(out->m_timeout));
            bool result = out->m_outDataWait.wait_until(lk, until, [data]{ return !(data->m_expectResult.load()); });
            if (result == false || data->m_error.load())
            {
                LOG(error) << "Timeout occured: for task: " << data->m_taskName << " msg id: "<< data->m_msgId << endl;
                ret = false;
            }
        }
    }
    return ret;
}

void EventLoop::ExitThread()
{
    if (!m_thread)
        return;

    // Create a new EventLoopMsg
    std::shared_ptr<EventLoopMsg> eventLoopMsg(new EventLoopMsg(MSG_EXIT_EVENT_LOOP, 0));

    // Put exit thread message into the queue
    {
        lock_guard<mutex> lock(m_mutex);
        m_queue.push(eventLoopMsg);
        m_cv.notify_all();
    }
    m_processMsg = nullptr;
    LOG(info) << "Waiting for process thread to finish" << endl;
    if (m_thread->joinable())
    {
        m_thread->join();
    }
    m_thread = nullptr;
    LOG(info) << "Exiting from  event loop" << endl;
}

int EventLoop::getPendingMessages()
{
    lock_guard<mutex> lock(m_mutex);
    return m_queue.size();
}

bool EventLoop::testEventLoopRunning()
{
    bool ret = true;
    assert(m_thread != nullptr);
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    std::shared_ptr<EventLoopOutData> out_data(new EventLoopOutData);
    data->m_outResult = out_data;
    data->m_msgId = "event_loop_id";
    data->m_taskName = "testEventLoop";
    data->m_expectResult = true;
    LOG(info) << "Event loop: Send Alive Message" << endl;
    std::shared_ptr<EventLoopMsg> eventLoopMsg(new EventLoopMsg(MSG_POST_EVENT_ALIVE, data));
    {
        // Add user data msg to queue and notify worker thread
        std::unique_lock<std::mutex> lk(m_mutex);
        m_queue.push(eventLoopMsg);
        m_cv.notify_all();
    }
    if (data->m_expectResult)
    {
        std::shared_ptr<EventLoopOutData> out = data->m_outResult;
        std::unique_lock<std::mutex> lk(out->m_outDataLock);
        out->m_timeout = 5;
        auto until = std::chrono::system_clock::now() + (out->m_timeout == 0 ?
                chrono::seconds(POST_MESSAGE_MAX_WAIT_SEC) : chrono::seconds(out->m_timeout));
        if (out->m_outDataWait.wait_until(lk, until, [data]{ return !(data->m_expectResult.load()); }) == false)
        {
            LOG(error) << "Event loop: Timeout occured" << endl;
            ret = false;
            std::shared_ptr<EventLoopMsg> msg;
            uint32_t qLen = m_queue.size();
            LOG(error) << "Pending Message length: "<< qLen << endl;
            for (uint32_t i = 0;  i < qLen; i++)
            {
                msg = m_queue.front();
                auto userData = std::static_pointer_cast<EventLoopData>(msg->msg);
                LOG(error) << "Pending Message name: " << userData->m_taskName << " Msg id: " << userData->m_msgId << endl;
                m_queue.pop();
            }
        }
        else
        {
            LOG(info) << "Event loop running fine" << endl;
        }
    }
    return ret;
}

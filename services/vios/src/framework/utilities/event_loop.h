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

#include <string.h>
#include <vector>
#include <queue>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <json/json.h>

struct EventLoopOutData
{
    std::mutex m_outDataLock;
    std::condition_variable m_outDataWait;
    std::shared_ptr<void> m_outData;
    uint32_t m_timeout = 0;
    virtual ~EventLoopOutData() {}
};
struct EventLoopData
{
    std::string m_msgId;
    std::string m_taskName;
    std::atomic<bool> m_expectResult {false};
    std::atomic<bool> m_error {false};
    std::shared_ptr<EventLoopOutData> m_outResult;
    Json::Value m_inData;
    virtual ~EventLoopData() {}
};

struct EventLoopMsg;

typedef void (*process_message)(std::shared_ptr<EventLoopData>, void*);

class EventLoop
{
public:
    /// Constructor
    EventLoop(std::string threadName, process_message);

    /// Destructor
    ~EventLoop();

    /// Called once to create the worker thread
    /// @return True if thread is created. False otherwise. 
    bool CreateThread();

    /// Called once a program exit to exit the worker thread
    void ExitThread();

    /// Get the ID of this thread instance
    /// @return The worker thread ID
    std::thread::id GetThreadId();

    /// Get the ID of the currently executing thread
    /// @return The current thread ID
    static std::thread::id GetCurrentThreadId();

    /// Add a message to the thread queue
    /// @param[in] data - thread specific message information
    bool postMsg(std::shared_ptr<EventLoopData> msg);

    void setParent(void* parent) { m_parent = parent; }

    int getPendingMessages();

    bool testEventLoopRunning();

private:
    EventLoop(const EventLoop&) = delete;
    EventLoop& operator=(const EventLoop&) = delete;

    /// Entry point for the worker thread
    void Process();

    /// Entry point for timer thread
    void TimerThread();
    void processFunctionWrapper(std::shared_ptr<EventLoopData> userdata, void *parent);

    std::unique_ptr<std::thread> m_thread;
    std::queue<std::shared_ptr<EventLoopMsg>> m_queue;
    std::queue<std::shared_ptr<EventLoopMsg>> m_aliveQueue;
    std::mutex m_mutex;
    std::condition_variable m_cv;
    std::queue<std::shared_ptr<EventLoopOutData>> m_outDataQueue;
    std::string m_threadName;
    process_message m_processMsg;
    void* m_parent;
    std::mutex m_mutexProceeFunc;
    std::condition_variable m_cvProcessFunc;
    std::atomic<bool> m_isProcessFuncDone {false};
};
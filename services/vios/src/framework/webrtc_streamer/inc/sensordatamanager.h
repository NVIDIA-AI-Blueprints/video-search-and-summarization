/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include <fstream>
#include <map>
#include <memory>
#include <chrono>

#include "logger.h"
#include <jsoncpp/json/json.h>
#include "error_code.h"
#include "event_loop.h"
#include "sensor_management.h"
#include "device_manager.h"
#include "libasync++/async++.h"
#include <boost/thread/mutex.hpp>
#include <boost/thread/condition.hpp>
// #include <boost/date_time/posix_time/posix_time.hpp>
#include <boost/timer/timer.hpp>

namespace nv_vms {
class ConditionalWait
{
    public:
        bool WaitForEvent(boost::posix_time::time_duration wait_duration)
        {
            boost::mutex::scoped_lock mtxWaitLock(m_lock);
            boost::system_time const timeout = boost::get_system_time() + wait_duration;
            return m_signalEvent.timed_wait(m_lock, timeout); // wait until signal Event
            // return m_signalEvent.timed_wait<boost::posix_time::time_duration>(m_lock, wait_duration);
        }
        void signalEvent() { m_signalEvent.notify_one(); }
    private:
        boost::mutex m_lock;
        boost::condition m_signalEvent;
};

// A stream is created at m_creationTime. Define (startTime = m_creationTime + m_startOffset)
// and (stopTime = m_creationTime + m_stopOffset).
// Elements in m_metadata happen between [startTime, stopTime] will be played back.
// The timestamps (ts) of the events are published as well as the current system clock or
// an NTP server clock. Nothing should be published between [m_creationTime + [m_startOffset
// When m_pulseMode is true, the scheduler publishes an event at every m_pulseTime interval.
// If m_pulseMode is false, the scheduler only publishes the event according to its timestamp,
// i.e. m_metadata[i]["ts"].
// If m_loopMode is true, after the clock reaches stopTime, the scheduler restarts publishing
// from m_creationTime again.
class MetaDataSchedule
{
    public:
        MetaDataSchedule(
            const string& sensor_id,
            vector<shared_ptr<SensorMetadata>> data,
            bool pulse,
            const string& pulse_time
        );
        MetaDataSchedule(
            const string& sensor_id,
            vector<shared_ptr<SensorMetadata>> data,
            bool pulse,
            const string& pulse_time,
            const string& creation_time,
            const string& start_offset,
            const string& stop_offset,
            bool loop
        );
        ~MetaDataSchedule();
    private:
        void scheduleTask();
        bool publisherTask(vector<shared_ptr<SensorMetadata>>::iterator);
        std::time_t getStartTime();

    private:
        std::string m_sensorId;
        vector<shared_ptr<SensorMetadata>> m_metadata;
        async::task<void> m_schedulerTask;
        ConditionalWait m_wait;
        std::atomic<bool> m_exit {false};
        bool m_pulseMode;
        bool m_loopMode;
        // ConditionalWait::WaitForEvent(long milliseconds)
        boost::posix_time::time_duration m_pulseTime;// milliseconds
        boost::posix_time::ptime m_creationTime;
        boost::posix_time::time_duration m_creationCurrentDelta;
        boost::posix_time::time_duration m_startOffset;// milliseconds
        boost::posix_time::time_duration m_stopOffset;// milliseconds
        float m_secScale;
        float m_msScale;
        float m_usScale;
        float m_nsScale;
};

class SensorDataManager
{
    public:
        SensorDataManager(shared_ptr<SensorManagement> sensorMgmt);
        ~SensorDataManager();
        VmsErrorCode startStream(std::map<std::string, std::string, std::less<>> opts, Json::Value &response);
        VmsErrorCode stopStream(const string& sensor_id, Json::Value &response);
    private:
        shared_ptr<SensorManagement> m_sensorManagement;
        std::map<std::string, unique_ptr<MetaDataSchedule>, std::less<>> m_sensorMap;
};

} //nv_vms
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

#include "sensordatamanager.h"
#include "NotificationFactory.h"

constexpr int DEFAULT_PULSE_TIME_IN_MS = 1000;

MetaDataSchedule::MetaDataSchedule(const string& sensor_id, vector<shared_ptr<SensorMetadata>> data,
                  bool pulse, const string& pulse_time)
                  : m_sensorId(sensor_id)
                  , m_metadata(data)
                  , m_pulseMode(pulse)
                  , m_loopMode(false)
{
    posixTimeResolutionScale(m_secScale, m_msScale, m_usScale, m_nsScale);

    if (pulse_time.empty())// Default is 1000ms or 1sec.
        m_pulseTime = boost::posix_time::time_duration(0, 0, 0, int(DEFAULT_PULSE_TIME_IN_MS * m_msScale));
    else
        m_pulseTime = boost::posix_time::time_duration(0, 0, 0, int(stringToInt(pulse_time) * m_msScale));

    // The first and the last timestamps
    vector<shared_ptr<SensorMetadata>>::iterator it = m_metadata.begin();
    boost::posix_time::ptime firstTime = stringToPosixTime((*it)->data["ts"], m_msScale);
    vector<shared_ptr<SensorMetadata>>::reverse_iterator rit = m_metadata.rbegin();
    boost::posix_time::ptime lastTime = stringToPosixTime((*rit)->data["ts"], m_msScale);
    
    m_creationTime = firstTime;
    // boost::posix_time::ptime currentTime = boost::posix_time::microsec_clock::universal_time();
    // m_creationCurrentDelta = currentTime - m_creationTime;

    // Stream from the first element
    m_startOffset = firstTime - m_creationTime;
    // Stream till the last element
    m_stopOffset = lastTime - m_creationTime;
    
    // INFO 
    boost::posix_time::ptime startTime = m_creationTime + m_startOffset;
    boost::posix_time::ptime stopTime = m_creationTime + m_stopOffset;
    std::string creationTimeStr = posixTimeToString(m_creationTime);
    std::string startTimeStr = posixTimeToString(startTime);
    std::string stopTimeStr = posixTimeToString(stopTime);
    LOG(info) << "Stream " << m_sensorId << " is created at = " << creationTimeStr << endl;
    LOG(info) << "Stream " << m_sensorId << " starts at " << startTimeStr << endl;
    LOG(info) << "Stream " << m_sensorId << " stops at " << stopTimeStr << endl;
    
    m_schedulerTask = async::spawn ([this]
    {
        scheduleTask();
    });
}

MetaDataSchedule::MetaDataSchedule(const string& sensor_id, vector<shared_ptr<SensorMetadata>> data,
                  bool pulse, const string& pulse_time, const string& creation_time, 
                  const string& start_offset, const string& stop_offset, bool loop)
                  : m_sensorId(sensor_id)
                  , m_metadata(data)
                  , m_pulseMode(pulse)
                  , m_loopMode(loop)
{
    posixTimeResolutionScale(m_secScale, m_msScale, m_usScale, m_nsScale);

    if (pulse_time.empty())// Default is 1000ms or 1sec.
        m_pulseTime = boost::posix_time::time_duration(0, 0, 0, int(DEFAULT_PULSE_TIME_IN_MS * m_msScale));
    else
        m_pulseTime = boost::posix_time::time_duration(0, 0, 0, int(stringToInt(pulse_time) * m_msScale));

    // The first and the last timestamps
    vector<shared_ptr<SensorMetadata>>::iterator it = m_metadata.begin();
    boost::posix_time::ptime firstTime = stringToPosixTime((*it)->data["ts"], m_msScale);
    vector<shared_ptr<SensorMetadata>>::reverse_iterator rit = m_metadata.rbegin();
    boost::posix_time::ptime lastTime = stringToPosixTime((*rit)->data["ts"], m_msScale);
    
    if (creation_time.empty())
    {
        // ignore start_offset and stop_offset if creation_time is empty.
        m_creationTime = firstTime;
        // boost::posix_time::ptime currentTime = boost::posix_time::microsec_clock::universal_time();
        // m_creationCurrentDelta = currentTime - m_creationTime;

        // Stream from the first element
        m_startOffset = firstTime - m_creationTime;
        // Stream till the last element
        m_stopOffset = lastTime - m_creationTime;
    }
    else
    {
        m_creationTime = stringToPosixTime(creation_time, m_msScale);
        // boost::posix_time::ptime currentTime = boost::posix_time::microsec_clock::universal_time();
        // m_creationCurrentDelta = currentTime - m_creationTime;

        if (start_offset.empty())// Stream from the first element
            m_startOffset = firstTime - m_creationTime;
        else
            m_startOffset = boost::posix_time::time_duration(0, 0, 0, int(stringToInt(start_offset) * m_msScale));

        if (stop_offset.empty())// Stream till the last element
            m_stopOffset = lastTime - m_creationTime;
        else
            m_stopOffset = boost::posix_time::time_duration(0, 0, 0, int(stringToInt(stop_offset) * m_msScale));

        if (start_offset > stop_offset)
            LOG(error) << "The specified start_offset is larger than stop_offset." << std::endl;

        if (((m_creationTime + m_startOffset) < firstTime) || ((m_creationTime + m_startOffset) > lastTime))
            LOG(warning) << "The specified creation_time and start_offset combo is out of stream data range." << std::endl;

        if (((m_creationTime + m_stopOffset) < firstTime) || ((m_creationTime + m_stopOffset) > lastTime))
            LOG(warning) << "The specified creation_time and stop_offset combo is out of stream data range." << std::endl;
    }

    // DEBUG 
    boost::posix_time::ptime startTime = m_creationTime + m_startOffset;
    boost::posix_time::ptime stopTime = m_creationTime + m_stopOffset;
    std::string creationTimeStr = posixTimeToString(m_creationTime);
    std::string startTimeStr = posixTimeToString(startTime);
    std::string stopTimeStr = posixTimeToString(stopTime);
    LOG(info) << "Stream " << m_sensorId << " is created at = " << creationTimeStr << endl;
    LOG(info) << "Stream " << m_sensorId << " starts at " << startTimeStr << endl;
    LOG(info) << "Stream " << m_sensorId << " stops at " << stopTimeStr << endl;

    m_schedulerTask = async::spawn ([this]
    {
        scheduleTask();
    });
}

// When in pulse mode, i.e. publishing an event at every m_pulseTime interval.
// 1. Locate the previous event and examine whether the previous event's timestamp 
//    prev_event_time is outside of m_creationTime + [m_startOffset, m_stopOffset].
// 2. If prev_event_time is in range, then publish previous event at every m_pulseTime
//    interval till current_time reaches event_ts.
// 3. Publish the current event.
// 4. If the current event is the last one within m_creationTime + [m_startOffset, m_stopOffset],
//    keep publishing the current event at every m_pulseTime interval till m_stopOffset.
//
// When not in pulse mode, the only step is to wait for (event_time - current_time) 
// before publishing the event.
bool MetaDataSchedule::publisherTask(vector<shared_ptr<SensorMetadata>>::iterator it_metadata)
{
    INotificationInterface* notifier = NotificationFactory::CreatePlatformNotification();
    if (!notifier)
    {
        LOG(error) << "INotificationInterface is NULL." << endl;
        return false;
    }

    // current_time, start_time orginates from m_creationTime.
    boost::posix_time::ptime event_time = stringToPosixTime((*it_metadata)->data["ts"], m_msScale);
    boost::posix_time::ptime current_time = boost::posix_time::microsec_clock::universal_time();
    current_time -= m_creationCurrentDelta;
    
    if (m_pulseMode) 
    {
        // publish the previous event at every m_pulseTime interval till current_time >= event_time.
        if (it_metadata != m_metadata.begin()) 
        {
            auto prev_metadata = std::prev(it_metadata);
            boost::posix_time::ptime prev_event_time = stringToPosixTime((*prev_metadata)->data["ts"], m_msScale);
            if (prev_event_time >= (m_creationTime + m_startOffset))
            {
                Json::Value jmetadata;
                Json::Value jsensor;
                jsensor["id"] = m_sensorId;
                for(auto item : (*prev_metadata)->data)
                {
                    jmetadata[item.first] = item.second;
                }
                jsensor["metadata"] = jmetadata;
                
                while (current_time < event_time) 
                {
                    // Update event's ts.
                    jsensor["metadata"]["ts"] = posixTimeToString(current_time);
                    jsensor["created_at"] = getCurrentTime();

                    LOG(info) << "PUBLISH: " << jsensor.toStyledString() << endl;
                    notifier->sendMessage(jsensor);

                    if (m_wait.WaitForEvent(m_pulseTime) || m_exit.load())
                    {
                        LOG(info) << "Metadata publishing is interupted, exiting.." << endl;
                        return false;
                    }
                    
                    // Do not ignore the time in publishing data. current_time += m_pulseTime could introduce
                    // large timing error. For example, if initially event_time - current_time = 10sec, 
                    // m_pulseTime = 2sec, and the data publishing takes 0.5sec, then there will be 5
                    // m_wait.WaitForEvent(2sec) calls and 5 publishing calls before exiting the while loop.
                    current_time = boost::posix_time::microsec_clock::universal_time();
                    current_time -= m_creationCurrentDelta;
                }        
            }
        }
    }

    // sleep/wait any remaining time until current_time >= event_time
    current_time = boost::posix_time::microsec_clock::universal_time();
    current_time -= m_creationCurrentDelta;
    if (current_time < event_time) 
    {
        boost::posix_time::time_duration interval = event_time - current_time;

        if (m_wait.WaitForEvent(interval) || m_exit.load())
        {
            LOG(info) << "Metadata publishing is interupted, exiting.." << endl;
            return false;
        }
    }
    
    Json::Value jmetadata;
    Json::Value jsensor;
    jsensor["id"] = m_sensorId;
    for(auto item : (*it_metadata)->data)
    {
        jmetadata[item.first] = item.second;
    }
    jsensor["metadata"] = jmetadata;
    jsensor["created_at"] = getCurrentTime();
    notifier->sendMessage(jsensor);
    LOG(info) << "PUBLISH: " << jsensor.toStyledString() << endl;
    
    // // DEBUG
    // current_time = boost::posix_time::microsec_clock::universal_time();
    // current_time -= m_creationCurrentDelta;
    // std::string current_time_str = posixTimeToString(current_time);
    // std::string event_time_str = posixTimeToString(event_time);
    // LOG(verbose) << "current_time = " << current_time_str << endl;
    // LOG(verbose) << "event_time = " << event_time_str << endl;
    
    if (m_pulseMode) 
    {
        // When reach the final event within [m_startOffset, m_stopOffset], keep publishing the final 
        // event till the clock reaches m_stopOffset.
        bool do_pulse_event = true;

        auto next_metadata = std::next(it_metadata);
        if (next_metadata != m_metadata.end())
        {
            boost::posix_time::ptime next_event_time = stringToPosixTime((*next_metadata)->data["ts"], m_msScale);
            if (next_event_time <= (m_creationTime + m_stopOffset))
                do_pulse_event = false;
        }

        if (do_pulse_event)
        {
            if (m_wait.WaitForEvent(m_pulseTime) || m_exit.load())
            {
                LOG(info) << "Metadata publishing is interupted, exiting.." << endl;
                return false;
            }
                
            current_time = boost::posix_time::microsec_clock::universal_time();
            current_time -= m_creationCurrentDelta;
            while (current_time <= (m_creationTime + m_stopOffset)) 
            {
                // Update event's ts.
                jsensor["metadata"]["ts"] = posixTimeToString(current_time);
                jsensor["created_at"] = getCurrentTime();
                                    
                LOG(info) << "PUBLISH: " << jsensor.toStyledString() << endl;
                notifier->sendMessage(jsensor);

                if (m_wait.WaitForEvent(m_pulseTime) || m_exit.load())
                {
                    LOG(info) << "Metadata publishing is interupted, exiting.." << endl;
                    return false;
                }
                
                // Do not ignore the time in publishing data. current_time += m_pulseTime could introduce
                // large timing error. For example, if initially event_time - current_time = 10sec, 
                // m_pulseTime = 2sec, and the data publishing takes 0.5sec, then there will be 5
                // m_wait.WaitForEvent(2sec) calls and 5 publishing calls before exiting the while loop.
                current_time = boost::posix_time::microsec_clock::universal_time();
                current_time -= m_creationCurrentDelta;
            }
        }        
    }

    // jmetadata.clear();
    return true;
}

// std::time_t MetaDataSchedule::getStartTime()
// {
//     vector<shared_ptr<SensorMetadata>>::iterator it = m_metadata.begin();
//     shared_ptr<SensorMetadata> first = (*it);
//     vector<shared_ptr<SensorMetadata>>::iterator it2 = ++it;
//     if(it2 != m_metadata.end())
//     {
//         shared_ptr<SensorMetadata> second = (*it2);
//         std::time_t t1 = getEpocTimeInMS(first->data["ts"]);
//         std::time_t t2 = getEpocTimeInMS(second->data["ts"]);
//         return t1 - (t2 - t1);
//     }
//     return 0;
// }

void MetaDataSchedule::scheduleTask()
{
    boost::posix_time::ptime start_time = m_creationTime + m_startOffset;
    boost::posix_time::ptime stop_time = m_creationTime + m_stopOffset;
    boost::posix_time::ptime event_time;

    do 
    {
        bool quit_loop = false;

        // Initiate/reset (currentTime - m_creationTime).
        boost::posix_time::ptime currentTime = boost::posix_time::microsec_clock::universal_time();
        m_creationCurrentDelta = currentTime - m_creationTime;
        
        vector<shared_ptr<SensorMetadata>>::iterator it = m_metadata.begin();
        for(; it != m_metadata.end(); it++)
        {
            event_time = stringToPosixTime((*it)->data["ts"], m_msScale);
            if ((event_time < start_time) || (event_time > stop_time))
                continue;

            if (publisherTask(it) == false)
            {
                quit_loop = true;
                break;
            }
        }

        if (quit_loop)
            break;
    } 
    while(m_loopMode);
}

MetaDataSchedule::~MetaDataSchedule()
{
    LOG(info) << "Exiting from Metadata publisher" << endl;
    m_exit = true;
    m_wait.signalEvent();
    try
    {
        m_schedulerTask.get();
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Caught Exception for m_schedulerTask Async task: " <<  e.what() << endl;
    }
}

SensorDataManager::SensorDataManager(shared_ptr<SensorManagement> sensorMgmt) : m_sensorManagement(sensorMgmt)
{
}

SensorDataManager::~SensorDataManager()
{
}

VmsErrorCode SensorDataManager::startStream(std::map<std::string, std::string, std::less<>> opts, Json::Value &response)
{
    string sensorId = opts["deviceId"] == "" ? opts["sensorId"] : opts["deviceId"];

    bool is_pulse = false;
    std::string pulse_time;
    if ( opts.find("pulse") != opts.end() )
    {
        is_pulse = opts.at("pulse") == "true" ? true : false;
        if ( opts.find("pulse_time") != opts.end() )
        {
            pulse_time = opts.at("pulse_time");
        }
    }

    bool is_loop = false;
    if ( opts.find("loop") != opts.end() )
    {
        is_loop = opts.at("loop") == "true" ? true : false;
    }
    
    std::string creation_time;
    if ( opts.find("creation_time") != opts.end() )
    {
        creation_time = opts.at("creation_time");
    }

    std::string start_offset;
    if ( opts.find("start_offset") != opts.end() )
    {
        start_offset = opts.at("start_offset");
    }

    std::string stop_offset;
    if ( opts.find("stop_offset") != opts.end() )
    {
        stop_offset = opts.at("stop_offset");
    }

    std::shared_ptr<DeviceManager> m_deviceMngr = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (m_deviceMngr)
    {
        shared_ptr<SensorInfo> sensor = m_deviceMngr->getSensorInfo(sensorId);
        if (sensor)
        {
            std::map<std::string, unique_ptr<MetaDataSchedule>, std::less<>>::iterator it = m_sensorMap.find(sensorId);
            if(it != m_sensorMap.end())
            {
                // Avoid streaming the same sensor again.
                LOG(warning) << "Sensor " << sensorId << " is streaming already." << endl;
                return VmsErrorCode::NoError;
            }

            LOG(info) << "Stream start for sensor : " << sensorId << endl;
            vector<shared_ptr<SensorMetadata>> data =  sensor->getMetadata();
            LOG(info) << "Metadata size: " << data.size() << endl;
            // unique_ptr<MetaDataSchedule> sch(new MetaDataSchedule(sensorId, data, is_pulse, pulse_time));
            unique_ptr<MetaDataSchedule> sch(new MetaDataSchedule(sensorId, data, is_pulse, pulse_time,
                creation_time, start_offset, stop_offset, is_loop));
            m_sensorMap[sensorId] = move(sch);
        }
        else
        {
            LOG(warning) << "deviceId/sensorId is not found" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "deviceId/sensorId is not found")
            return VmsErrorCode::InvalidParameterError;
        }
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode SensorDataManager::stopStream(const string& sensor_id, Json::Value &response)
{
    std::map<std::string, unique_ptr<MetaDataSchedule>, std::less<>>::iterator it = m_sensorMap.find(sensor_id);
    if(it != m_sensorMap.end())
    {
        m_sensorMap.erase(it);
    }
    else
    {
        LOG(warning) << "sensorId is not found" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "sensorId is not found")
        return VmsErrorCode::InvalidParameterError;
    }
    return VmsErrorCode::NoError;
}
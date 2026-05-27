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

#include "native_sensors_discovery.h"
#include "config.h"
#include "logger.h"
#include "mm_utils.h"
#include "modules_apis.h"
#include "sensor_info.h"

using namespace nv_vms;

extern "C" ISensorDiscoveryInterface* createObject()
{
    return new NativeSensorsDiscovery;
}

extern "C" void destroyObject(NativeSensorsDiscovery* object)
{
    delete object;
}

//function to remove the spaces from string
static string trim(const string& str, const string& whitespace = " \t")
{
    const auto strBegin = str.find_first_not_of(whitespace);
    if (strBegin == string::npos)
        return ""; // no content

    const auto strEnd = str.find_last_not_of(whitespace);
    const auto strRange = strEnd - strBegin + 1;

    return str.substr(strBegin, strRange);
}

static void parseResolution(const string& resolutionStr, string& width, string& height, string& fps)
{
    size_t xPos = resolutionStr.find('x');
    size_t newLinePos = resolutionStr.find('\n');
    size_t fpsPos = resolutionStr.find('(', xPos);
    size_t fpsEndPos = resolutionStr.find(" fps)");
    if (xPos != string::npos && fpsPos != string::npos)
    {
        width = resolutionStr.substr(0, xPos);
        height = resolutionStr.substr(xPos + 1, newLinePos - (xPos + 1));
        fps = resolutionStr.substr(fpsPos + 1, fpsEndPos - (fpsPos + 1));
    }
}

std::string NativeSensorsDiscovery::getDeviceName(const std::string& devicePath)
{
    size_t pos = devicePath.find_last_of('/');
    if (pos != std::string::npos)
    {
        return devicePath.substr(pos + 1);
    }
    return "";
}

void NativeSensorsDiscovery::doNativeSensorDiscovery(vector<SensorInfo>& sensors)
{
    string cmd = "v4l2-ctl --list-devices";
    FILE* pipe = popen(cmd.c_str(), "r"); // Execute v4l2-ctl command
    unsigned int sensor_count = 0;

    if (!pipe)
    {
        LOG(error) << "Error executing v4l2-ctl command: " << strerror(errno) << " (errno: " << errno << ")" << endl;
        return;
    }

    string output;
    char buffer[128];

    while (!feof(pipe))
    {
        if (fgets(buffer, 128, pipe) != nullptr)
        {
            output += buffer; // Collect the command output
        }
    }

    pclose(pipe); // Close the pipe

    // Extract video capture sensor information
    string delimiter = "\n\n";
    size_t pos = 0;

    while (pos != string::npos)
    {
        string sensorDevicePath;
        if (pos != 0)
        {
            pos += delimiter.length();
        }
        size_t nextPos = output.find(delimiter, pos);
        if (nextPos == string::npos)
        {
            sensorDevicePath = output.substr(pos);
        }
        else
        {
            sensorDevicePath = output.substr(pos, nextPos - pos);
        }

        string delimiter2 = "tegra-capture-vi";
        size_t resPos = sensorDevicePath.find(delimiter2);
        if (resPos == string::npos)
        {
            pos = nextPos;
            continue;
        }

        SensorInfo sensor;
        size_t linePos = sensorDevicePath.find('\n');
        if (linePos == string::npos)
        {
            break;
        }
        sensorDevicePath = sensorDevicePath.substr(linePos + 1);

        linePos = sensorDevicePath.find('\n');
        sensor.location = trim(sensorDevicePath.substr(0, linePos));
        sensorDevicePath = sensorDevicePath.substr(linePos + 1);

        string device = getDeviceName(sensor.location);
        string sensorName = "csi_" + device;
        sensor.id = generate_uuid();
        sensor.sensorId = sensor.id;
        sensor.type = SENSOR_TYPE_CSI;
        sensor.name =  sensorName;
        sensor.ip = sensorName; // Added for debug purpose
        sensor.url = sensor.location; // Added for debug purpose
        LOG(verbose) << "Sensor Name: " << sensor.name << endl;
        LOG(verbose) << "Sensor Path: " << sensor.location << endl;
        LOG(verbose) << "Sensor Id: " << sensor.id << endl;

        string resolutionCmd = "v4l2-ctl --list-formats-ext -d " + sensor.location;
        pipe = popen(resolutionCmd.c_str(), "r"); // Execute v4l2-ctl command

        if (!pipe)
        {
            LOG(error) << "Error executing v4l2-ctl command: " << strerror(errno) << " (errno: " << errno << ")" << endl;
            return;
        }

        string resOutput;
        char resBuffer[128];

        while (!feof(pipe))
        {
            if (fgets(resBuffer, 128, pipe) != nullptr)
            {
                resOutput += resBuffer; // Collect the command output
            }
        }
        LOG(verbose) << resOutput << endl;

        pclose(pipe); // Close the pipe

        delimiter2 = "Bayer";
        resPos = resOutput.find(delimiter2);
        if (resPos == string::npos)
        {
            pos = nextPos;
            continue;
        }

        // Extract video capture resolution information
        delimiter2 = "Size: Discrete ";
        resPos = resOutput.find(delimiter2);
        if (resPos == string::npos)
        {
            pos = nextPos;
            continue;
        }

        while (resPos != string::npos)
        {
            resPos += delimiter2.length();
            size_t nextResPos = resOutput.find(delimiter2, resPos);
            string resolutionStr = resOutput.substr(resPos, nextResPos - resPos);
            shared_ptr<StreamInfo> stream(new StreamInfo);
            stream->sensorId = sensor.id;
            stream->id = sensor.id;
            stream->name = sensor.name;
            stream->isMainStream = false;
            stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_ONLINE,
                    translateStreamStatusToString(StreamStatus::STREAM_STATUS_ONLINE)));
            stream->updateStreamtype(StreamType::Native);

            SensorVideoEncoderSettingsValues video_encValues;
            parseResolution(resolutionStr, video_encValues.resolution.width, video_encValues.resolution.height,
                            video_encValues.frameRate);
            // Avoid display odd resolutions like 3856x4448 shown in v4l-ctl
            if (stoi(video_encValues.resolution.width) % 10 != 0)
            {
                resPos = nextResPos;
                continue;
            }
            if (stoi(video_encValues.resolution.width) == WIDTH_1080p)
            {
                stream->isMainStream = true;
            }
            else
            {
                /* Avoiding other than 1080p resolution, As we are going to support only 1080p stream for CSI camera */
                resPos = nextResPos;
                continue;
            }

            stream->updateVideoEncoderValues(video_encValues);

            LOG(verbose) << "Video Capture Resolution Information: ";
            LOG2(verbose) << "Sensor id:" << stream->sensorId << ", ";
            LOG2(verbose) << "Stream id: " << stream->id << ", ";
            LOG2(verbose) << "Width: " << video_encValues.resolution.width << ", ";
            LOG2(verbose) << "Height: " << video_encValues.resolution.height << ", ";
            LOG2(verbose) << "FPS: " << video_encValues.frameRate << endl;

            resPos = nextResPos;
            sensor.streams.push_back(stream);
        }
        sensor.updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
        //sensor.printInfo();

        sensors.push_back(sensor);
        sensor_count++;

        pos = nextPos;
    }

    // Print total video capture sensors available
    LOG(verbose) << "Total Number of Video Capture Devices: " << sensor_count << endl;
}

NativeSensorsDiscovery::NativeSensorsDiscovery()
{
}

NativeSensorsDiscovery::~NativeSensorsDiscovery()
{
    try {
        LOG(info) << "Destroying NativeSensorsDiscovery" << endl;
        stop();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~NativeSensorsDiscovery: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~NativeSensorsDiscovery" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void NativeSensorsDiscovery::start()
{
    std::lock_guard<std::mutex> sensorsLock(m_monitorMutex);
    m_exit = false;
    m_nativeSensorDiscoveryThread = std::thread([this] { this->nativeSensorsDiscoveryTask(); });
    LOG(info) << "Started Native Sensor discovery task" << endl;
}
void  NativeSensorsDiscovery::stop()
{
    std::lock_guard<std::mutex> sensorsLock(m_monitorMutex);
    m_exit = true;
    m_sleeperWait.notify_all();
    LOG(info) << "Stoping Native Sensor discovery task" << endl;
    m_nativeSensorDiscoveryThread.join();
    LOG(info) << "Stopped Native Sensor discovery task" << endl;
}

int NativeSensorsDiscovery::addNewSensor(SensorInfo& sensor)
{
    sensor.isAutoDiscovered = true;
    sensor.updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
    return publishOnSensorFound(sensor);
}

void NativeSensorsDiscovery::nativeSensorsDiscoveryTask()
{
    while (m_exit == false)
    {
        {
            std::lock_guard<std::mutex> sensorsLock(m_monitorMutex);
            vector<SensorInfo> sensors;
            doNativeSensorDiscovery(sensors);
            m_freshList.clear();

            for (uint32_t ele = 0; ele < sensors.size(); ele++)
            {
                SensorInfo sensor = sensors[ele];

                bool duplicateSensorFound = false;
                for (auto it : m_freshList)
                {
                    SensorInfo& sensorOld = it.second;
                    if (sensorOld.location == sensor.location)
                    {
                        duplicateSensorFound = true;
                        break;
                    }
                }
                if (!duplicateSensorFound)
                {
                    m_freshList[sensor.ip] = sensor;
                }
            }

            refreshCacheSensorList();
            std::vector<shared_ptr<SensorInfo>> cache_list = getCacheSensorList();
            LOG(verbose) << "*********[Fresh List]************* " << "[" << m_freshList.size()  << "]" << endl;
            for (auto it : m_freshList) // Sensor detected
            {
                SensorInfo& sensor = it.second;
                LOG(verbose) << "Fresh Sensor: " << sensor.id << " name:" << sensor.name << endl;
            }
            LOG(verbose) << "**********[END]************" << endl;

            LOG(verbose) << "*********[Cache List]************* " << "[" << cache_list.size()  << "]" << endl;
            for (auto sensor : cache_list) // Sensor previously detected
            {
                LOG(verbose) << "Cache Sensor: " << sensor->id << " name:" << sensor->name << endl;
            }
            LOG(verbose) << "**********[END]************" << endl;

            /* Check for removal of sensor */
            for (auto cache : cache_list)
            {
                const string& cache_sensor_id = cache->sensorId;
                auto cache_sensor = cache;
                bool is_sensor_offline = true;

                if (cache_sensor->type != SENSOR_TYPE_CSI)
                {
                    continue;
                }

                for (auto it : m_freshList)
                {
                    SensorInfo& sensor = it.second;

                    if (cache_sensor->ip == sensor.ip)
                    {
                        is_sensor_offline = false;
                        break;
                    }
                }

                if (is_sensor_offline)
                {
                    LOG(error) << "Removing sensor: " << cache_sensor->ip << endl;
                    publishOnSensorRemoved(cache_sensor_id);
                }
            }

            /* Check for new sensor */
            for (auto fresh : m_freshList)
            {
                const string& ip = fresh.first;
                SensorInfo& new_sensor = fresh.second;
                shared_ptr<SensorInfo> cache_sensor = nullptr;
                if (new_sensor.id.empty() || new_sensor.streams.size() == 0)
                {
                    continue;
                }
                bool isSensorExistInCache = false;
                for (auto cache : cache_list)
                {
                    if (cache->type != SENSOR_TYPE_CSI)
                    {
                        continue;
                    }

                    if (cache && (cache->ip == ip))
                    {
                        isSensorExistInCache = true;
                        cache_sensor = cache;
                        break;
                    }
                }

                if (isSensorExistInCache == false)
                {
                    if (addNewSensor(new_sensor) == 0)
                    {
                        LOG(info) << "Added new sensor: " << new_sensor.id << endl;
                    }
                }
                else
                {
                    if (cache_sensor)
                    {
                        // Check if sensor is in offline state or not, update db in that case.
                        if (cache_sensor->getSensorStatus() == SensorStatusOffline)
                        {
                            publishOnSensorFound(*cache_sensor);
                        }
                        else
                        {
                            // Nothing to be done, cache list is updated.
                        }
                    }
                }
            }
        }

        {
            // Sleep for 5seconds or untill get notified.
            std::unique_lock<std::mutex> lck(m_sleeperLock);
            m_sleeperWait.wait_for(lck,std::chrono::milliseconds(5000));
        }
    }

    LOG(info) << "Exiting from nativeSensorsDiscoveryTask thread.." << endl;
}
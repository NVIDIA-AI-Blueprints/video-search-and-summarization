/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "sensor_data.h"
#include "cmdline_parser.h"
#include "rtspserver.h"
#include "gstdemux.h"
#include "gst_utils.h"
#include "logger.h"

extern "C" ISensorDiscoveryInterface* createObject()
{
    return new SensorDataCollector;
}

extern "C" void destroyObject(SensorDataCollector* object)
{
    delete object;
}

void SensorDataCollector::addSensor()
{
    Json::Value sensor_list;
    Json::Reader reader;
    const string videoFilesPath = GET_CONFIG().nv_streamer_directory_path;
    vector<string> list;
    std::vector<string> containers = GET_CONFIG().metadata_containers;
    getVideoFiles(videoFilesPath, containers, list);
    for(string sensor_data_file : list )
    {
        std::ifstream file(sensor_data_file.c_str());
        if(file.good())
        {
            if(!reader.parse(file, sensor_list, true))
            {
                LOG(error) << "Failed to parse rtsp_streams configuration" << endl;
                return;
            }
            if (sensor_list.isArray())
            {
                Json::Value jsensor = Json::nullValue;
                for (Json::Value::ArrayIndex i = 0; i != sensor_list.size(); i++)
                {
                    SensorInfo sensor;
                    jsensor = sensor_list[i];
                    sensor.id = sensor.sensorId = jsensor.get("sensorID", "").asString();
                    sensor.name = jsensor.get("name", "").asString();
                    sensor.location = jsensor.get("location", "").asString();
                    sensor.updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
                    sensor.updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
                    sensor.type = SENSOR_TYPE_GENERIC;
                    Json::Value jdata_list = jsensor.get("sensorData", Json::nullValue);
                    for(Json::Value::ArrayIndex i = 0; i < jdata_list.size(); i++)
                    {
                        Json::Value row = jdata_list[i];
                        shared_ptr<SensorMetadata> meta_data (new SensorMetadata);
                        for (auto const& key : row.getMemberNames())
                        {
                        meta_data->data[key] = row[key].asCString();
                        }
                        sensor.metadata.push_back(meta_data);
                    }
                    sensor.printInfo();
                    publishOnSensorFound(sensor);
                }
            }
        }
    }
}


void SensorDataCollector::start()
{
    addSensor();
}

void SensorDataCollector::stop()
{

}

int SensorDataCollector::searchSensor(SensorInfo& sensor)
{
    return 0;
}

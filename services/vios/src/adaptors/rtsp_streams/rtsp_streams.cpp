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

#include "rtsp_streams.h"
#include "cmdline_parser.h"
#include "testRTSP.h"
#include "gst_utils.h"
#include "udpclientpool.h"
#include "vstmodule.h"

#define DEFAULT_DEVICE_NAME "Camera"

extern "C" ISensorControlInterface* createObject()
{
    return new RtspStreams;
}

extern "C" void destroyObject( RtspStreams* object )
{
    delete object;
}

void disableStream(const string& rtsp_url, vector<shared_ptr<SensorInfo>>& sensors)
{
    bool found = false;
    for (uint32_t i = 0; i < sensors.size(); i++)
    {
        shared_ptr<SensorInfo> sensor = sensors[i];
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (uint32_t j = 0; j < streams.size(); j++)
        {
            std::shared_ptr<StreamInfo> stream = streams[j];
            if (stream->live_url == rtsp_url)
            {
                found = true;
                sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraNotFoundError));
                sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOffline);
                stream->live_url = stream->live_proxy_url = "";
            }
        }
    }
    if (found == false)
    {
        LOG(error) << "rtsp stream:" << secureUrlForLogging(rtsp_url) << " does not exist in the database" << endl;
    }
}

bool checkIfNameExist(const vector<pair<string, Json::Value>>& list, const string& name)
{
    for (uint32_t i = 0 ; i < list.size(); i++)
    {
        Json::Value stream_info = list[i].second;
        string stream_name = stream_info.get("name", "").asString();
        if (name == stream_name)
        {
            return true;
        }
    }
    return false;
}

string createUniqueName(const vector<pair<string, Json::Value>>& list, string name)
{
    int i = 0;
    if (name.empty())
    {
        // Assign default name to the rtsp stream.
        name = DEFAULT_DEVICE_NAME;
    }
    string str = name;
    while(checkIfNameExist(list, name))
    {
        ++i;
        name = str + string("_") + std::to_string(i);
    }
    return name;
}

int parseRtspStreams(vector<pair<string, Json::Value>>& list, vector<shared_ptr<SensorInfo>>& sensors)
{
    int ret = 0;
    Json::Value config;
    Json::Reader reader;
    std::ifstream file((GET_CMDLINE_PARSER()->getRtspStreamsFilePath()).c_str());
    if(file.good())
    {
        if(!reader.parse(file, config, true))
        {
            LOG(error) << "Failed to parse rtsp_streams configuration" << endl;
            return -1;
        }

        // Check if Nvstreamer is enabled
        Json::Value nvstreamerArray = config["Nvstreamer"];
        for (const auto& nvstreamer : nvstreamerArray)
        {
            bool nvstreamerEnabled = nvstreamer.get("enabled", false).asBool();
            if (nvstreamerEnabled)
            {
                string endpoint = nvstreamer.get("endpoint", "").asString();
                string api = nvstreamer.get("api", "").asString();
                int maxStreamCount = nvstreamer.get("max_stream_count", 8).asInt();
                if (endpoint.empty() || api.empty())
                {
                    LOG(error) << "Failed to parse rtsp_streams.json, provide correct Nvstreamer endpoint & api details" << endl;
                    return -1;
                }
                string nvstreamer_ep = "http://" + endpoint + api;
                LOG(info) << "Nvstreamer endpoint to fetch streams = " << nvstreamer_ep << ", maxStreamCount:" << maxStreamCount << endl;

                string outData;
                Json::Value apiResponse;
                bool ret = curlGetRequest(nvstreamer_ep, outData);
                if (ret == true)
                {
                    Json::Reader reader;
                    bool parsing_successful = reader.parse(outData, apiResponse);
                    if (!parsing_successful)
                    {
                        LOG(error) << "Failed to parse Nvstreamer API JSON data." << endl;
                        return -1;
                    }
                }

                int streamCount = 0;
                for (const auto& streamGroup : apiResponse)
                {
                    std::string uuid = streamGroup.getMemberNames()[0];
                    Json::Value arr = streamGroup[uuid];

                    // Iterate over the array and extract data from each object
                    for (const auto& stream : arr)
                    {
                        if (maxStreamCount > 0 && streamCount >= maxStreamCount)
                        {
                            break;
                        }

                        Json::Value streamInfo;
                        string streamName = stream.get("name", "").asString();
                        streamName = truncateString(streamName, MAX_SENSOR_NAME_LENGTH);
                        streamInfo["name"] = createUniqueName(list, streamName);
                        string streamUrl = stream.get("url", "").asString();
                        //streamInfo["streamId"] = stream.get("streamId", "").asString();

                        streamInfo["url"] = streamUrl;
                        list.push_back(make_pair(streamUrl, streamInfo));
                        streamCount++;
                        if (streamCount >= maxStreamCount)
                        {
                            break;
                        }
                    }
                }
            }
        }

        // Also check if additional streams are provided in streams section
        {
            Json::Value details_array = config["streams"];
            if (details_array.isArray())
            {
                Json::Value elem = Json::nullValue;
                for (Json::Value::ArrayIndex i = 0; i != details_array.size(); i++)
                {
                    Json::Value stream_info;
                    elem = details_array[i];
                    bool enabled = elem.get("enabled", false).asBool();
                    string stream_in = elem.get("stream_in", "").asString();
                    string stream_name = elem.get("name", "").asString();
                    stream_name = truncateString(stream_name, MAX_SENSOR_NAME_LENGTH);
                    if (enabled == true)
                    {
                        if (!stream_in.empty())
                        {
                            stream_name = createUniqueName(list, stream_name);
                            stream_info["name"] = stream_name;
                        }
                        if (stream_in.find("udp") != string::npos)
                        {
                            Json::Value video_info = elem.get("video", Json::nullValue);
                            if (video_info != Json::nullValue)
                            {
                                unsigned video_port = (unsigned)video_info.get("port", 0).asUInt();
                                if (video_port == 0)
                                {
                                    video_port = UdpClientPool::getInstance()->getUdpPort();
                                }
                                stream_info["video_port"] = video_port;
                                stream_info["video_codec"] = video_info.get("codec", "").asString();
                                stream_info["framerate"] = video_info.get("framerate", DEFAULT_FRAMERATE).asInt();
                            }
                            Json::Value audio_info = elem.get("audio", Json::nullValue);
                            if (audio_info != Json::nullValue)
                            {
                                stream_info["enabled"] = audio_info.get("enabled", false).asBool();
                                unsigned audio_port = (unsigned)audio_info.get("port", 0).asUInt();
                                if (audio_port == 0)
                                {
                                    audio_port = UdpClientPool::getInstance()->getUdpPort();
                                }
                                stream_info["audio_port"] = audio_port;
                                stream_info["audio_codec"] = audio_info.get("codec", "").asString();
                                stream_info["sample_rate"] = (unsigned int)audio_info.get("sample_rate_Hz", 0).asUInt();
                                stream_info["bits_per_sample"] = (unsigned int)audio_info.get("bits_per_sample", 0).asUInt();
                            }
                        }
                        list.push_back( make_pair(stream_in, stream_info));

                    }
                    else
                    {
                        // User has disabled the stream, So mark it disabled in database.
                        disableStream(stream_in, sensors);
                    }
                }
            }
        }
    }
    else
    {
        ret = -1;
        LOG(error) << "Error opening rtsp_streams file : " << GET_CMDLINE_PARSER()->getRtspStreamsFilePath() << endl;
    }
    return ret;
}

bool checkAndValidateSensor(vector<shared_ptr<SensorInfo>>& sensors, const pair<string, Json::Value>& user_stream)
{
    bool is_exist = false;
    for(uint32_t i = 0; i < sensors.size(); i++ )
    {
        shared_ptr<SensorInfo> sensor = sensors[i];
        if (sensor)
        {
            vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
            if(streams.size() > 0 && streams[0])
            {
                if (streams[0]->live_url == user_stream.first)
                {
                    LOG(info) << "rtsp stream already exist: " << user_stream.first << endl;
                    Json::Value stream_info = user_stream.second;
                    string stream_name = stream_info.get("name", "").asString();
                    if (streams[0]->id != stream_name)
                    {
                        LOG(info) << "Change in the 'rtsp_out_path' observed for:" << user_stream.first << endl;
                        sensor->id = streams[0]->id = stream_name;
                        sensor->sensorId = streams[0]->sensorId = stream_name;
                        sensor->name = streams[0]->name = stream_name;
                    }
                    is_exist = true;

                    sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
                    sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
                    sensor->printInfo();
                    break;
                }
            }
        }
    }
    return is_exist;
}

void checkAndValidateDbStreams(vector<shared_ptr<SensorInfo>>& sensors,
                                vector<pair<string, Json::Value>> list)
{
    string video_codec, frame_rate, width, height;
    // Check for sensors present in db, but not in rtsp_adaptor file
    for (auto sensor: sensors)
    {
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        bool sensor_present = false;
        if(streams.size() > 0 && streams[0])
        {
            string url = streams[0]->live_url;
            for (auto user_stream: list)
            {
                if (url == user_stream.first)
                {
                    sensor_present = true;
                    break;
                }
            }
            if (!sensor_present)    // sensor present in db, but not in rtsp file
            {
                sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
                sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
                sensor->printInfo();
                width      = streams[0]->settings.encoderValues.resolution.width;
                height     = streams[0]->settings.encoderValues.resolution.height;
                frame_rate = streams[0]->settings.encoderValues.frameRate;

                SensorVideoEncoderSettingsValues values;
                values.encoding = video_codec;
                values.frameRate = frame_rate;
                values.resolution.width = width;
                values.resolution.height = height;
                streams[0]->updateVideoEncoderValues(values);
                if (url.find(NV_STREAMER) != std::string::npos)
                {
                    /* Prevents Nvstreamer buffer overflow issue caused by excessive lookup calls in case of hundreds of streams */
                    usleep(100*1000); // 100ms
                }
            }
        }
    }
}

static void addRtspStreams(vector<shared_ptr<SensorInfo>>& sensors)
{
    string video_codec, frame_rate, width, height;
    string audio_codec, sample_rate, bits_per_sample;
    unsigned int video_port, audio_port;
    bool audio_enabled;

    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceManager.get() == nullptr)
    {
        LOG(error) << "Device Manager instance is null" << endl;
        return;
    }
    vector<pair<string, Json::Value>> list;
    if (parseRtspStreams(list, sensors) == 0)
    {
        checkAndValidateDbStreams(sensors, list);
        for (uint32_t i = 0 ; i < list.size(); i++)
        {
            pair<string, Json::Value> rtsp_stream = list[i];
            if (checkAndValidateSensor(sensors, rtsp_stream))
            {
                // Sensor already exists & validated, So move on to next.
                continue;
            }
            Json::Value stream_info = rtsp_stream.second;
            string stream_name = stream_info.get("name", "").asString();
            string stream_url = rtsp_stream.first;

            shared_ptr<SensorInfo> sensor(new SensorInfo);
            sensor->id = stream_name;
            sensor->sensorId = stream_name;
            sensor->name = stream_name;
            if (stream_url.find("rtsp://") != string::npos)
            {
                sensor->type = SENSOR_TYPE_RTSP;
            }
            else if (stream_url.find("udp") != string::npos)
            {
                sensor->type = SENSOR_TYPE_UDP;
            }
            sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
            sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);

            shared_ptr<StreamInfo> stream(new StreamInfo);
            stream->sensorId = sensor->id;
            stream->id = sensor->id;
            stream->name = sensor->name;
            stream->isMainStream = true;
            stream->updateStreamtype(StreamType::Udp);
            stream->direction = StreamDirectionOut;

            if (stream_url.find("rtsp://") != string::npos)
            {
                stream->updateStreamtype(StreamType::Rtsp);
                stream->live_url = stream_url;
                if (deviceManager->needRtspServer == true && stream_url.find(NV_STREAMER) != std::string::npos)
                {
                    /* Prevents Nvstreamer buffer overflow issue caused by excessive lookup calls in case of hundreds of streams */
                    usleep(100*1000); // 100ms
                }
            }
            else if (stream_url.find("udp") != string::npos)
            {
                video_port = stream_info.get("video_port", 0).asUInt();
                video_codec = stream_info.get("video_codec", "H264").asString();
                frame_rate = to_string(stream_info.get("framerate", DEFAULT_FRAMERATE).asInt());

                audio_enabled = stream_info.get("enabled", false).asBool();
                audio_port = stream_info.get("audio_port", 0).asUInt();
                audio_codec = stream_info.get("audio_codec", "").asString();
                sample_rate = to_string(stream_info.get("sample_rate", 0).asUInt());
                bits_per_sample = to_string(stream_info.get("bits_per_sample", 0).asUInt());

                // Create live_url as => udp:<video_port>:<audio_port>
                stream->updateStreamtype(StreamType::Udp);
                stream->live_url = stream_url + ":" + to_string(video_port) + ":" + to_string(audio_port);
                LOG(info) << "stream name = " << stream->name << ", live_url = " << secureUrlForLogging(stream->live_url) << endl;

                SensorAudioEncoderSettingsValues values;
                values.enable = audio_enabled;
                values.encoding = audio_codec;
                values.sample_rate = sample_rate;
                values.bits_per_sample = bits_per_sample;
                stream->updateAudioEncoderValues(values);
                LOG(info) << "audio_codec = " << audio_codec << ", sample_rate = " << sample_rate << ", bps = " << bits_per_sample << endl;

                stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_STREAMING,
                        translateStreamStatusToString(StreamStatus::STREAM_STATUS_STREAMING)));
            }

            // Set Default values if enc values are empty.
            if (video_codec.empty())
            {
                video_codec = DEFAULT_ENCODING;
            }
            if (frame_rate.empty())
            {
                frame_rate = to_string(DEFAULT_FRAMERATE);
            }
            if (width.empty() || height.empty())
            {
                width = "1920";
                height = "1080";
            }

            SensorVideoEncoderSettingsValues values;
            values.encoding = video_codec;
            values.frameRate = frame_rate;
            values.resolution.width = width;
            values.resolution.height = height;
            stream->updateVideoEncoderValues(values);

            sensor->streams.push_back(stream);
            sensors.push_back(sensor);
            sensor->printInfo();
        }
    }
}

int RtspStreams::connect()
{
    LOG(verbose) << __FUNCTION__<<endl;
    int result = -1;
    string url;
    int online_sensors = 0;
    if (m_adaptorInfo.m_type == TYPE_VST)
    {
        addRtspStreams(m_cacheSensorList);

        int max_sensors = GET_CONFIG().max_sensors_supported;
        std::vector<shared_ptr<SensorInfo>>::iterator it;
        for (it = m_cacheSensorList.begin(); it != m_cacheSensorList.end();)
        {
            auto sensor =  *it;
            bool is_stream_offline = false;
            if (sensor->type == SENSOR_TYPE_RTSP)
            {
                vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
                if (streams.size() > 0 && streams[0]->live_url.empty() == false)
                {
                    is_stream_offline = isRtspServerReachable(streams[0]->live_url) == false;
                }
            }
            else
            {
                ++it;
                continue;
            }

            if (is_stream_offline)
            {
                LOG(error) << "Rtsp Url is Not online: " << secureUrlForLogging(sensor->url) << " sensor: " << sensor->name << " id: " << sensor->id << endl;
                sensor->updateSensorStatus(SensorStatusOffline);
                sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraNotFoundError));
                ++it;
                continue;
            }
            if(online_sensors >= max_sensors)
            {
                LOG(warning) << "Max sensor limit is reached, so deleting extra sensor: " << sensor->name << " id: " << sensor->id << endl;
                it = m_cacheSensorList.erase(it);
                continue;
            }
            else
            {
                ++it;
            }

            LOG(info) << "Online RTSP URL for sensor: " << sensor->name << " id: " << sensor->id << " online sensors cnt:" << online_sensors << endl;

            sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
            ++online_sensors;
        }
        result = 0;
    }

    return result;
}

int RtspStreams::getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors)
{
    return 0;
}

int RtspStreams::getSensorStreamInfo(shared_ptr<SensorInfo>& sensor)
{
    return 0;
}
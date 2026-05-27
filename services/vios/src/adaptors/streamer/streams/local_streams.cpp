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

#include "local_streams.h"
#include "cmdline_parser.h"
#include "rtspserver.h"
#include "mm_utils.h"
#include "gst_utils.h"
#include "modules_apis.h"
#include <cmath>

extern "C" ISensorControlInterface* createObject()
{
    return new LocalStreams;
}

extern "C" void destroyObject( LocalStreams* object )
{
    delete object;
}

LocalStreams::LocalStreams()
{
}

bool LocalStreams::isSensorExist(vector<shared_ptr<SensorInfo>>& sensors, const string &filepath)
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
                if (streams[0]->live_url.empty()) break;
                string path = getFilePathFromUrl(streams[0]->live_url, NV_STREAMER);
                if (path == filepath)
                {
                    LOG(info) << "file already exist:" << filepath << endl;
                    is_exist = true;

                    streams[0]->live_url = streams[0]->replay_url = streams[0]->live_proxy_url =
                        vst_rtsp::rtspUrlPrefix(sensor->id) + string(NV_STREAMER) + filepath;

                    sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
                    sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);

                    if (streams[0]->settings.encoderValues.container.empty()
                        || streams[0]->settings.encoderValues.encoding.empty()
                        || streams[0]->settings.audioEncoderValues.encoding.empty())
                    {
                        // Ideally it should not reach here, Calling again getMediaInfo() if it is empty.
                        Json::Value response;
                        if (getMediaInformation(filepath, response) == 0)
                        {
                            SensorVideoEncoderSettingsValues enc_values;
                            enc_values.container = response.get("Container", EMPTY_STRING).asString();
                            enc_values.encoding = response.get("Codec", EMPTY_STRING).asString();
                            enc_values.frameRate = response.get("Framerate", EMPTY_STRING).asString();
                            enc_values.resolution.width = response.get("Width", EMPTY_STRING).asString();
                            enc_values.resolution.height = response.get("Height", EMPTY_STRING).asString();
                            enc_values.bitrate = response.get("Bitrate", EMPTY_STRING).asString();
                            enc_values.numFrames = response.get("FrameCount", EMPTY_STRING).asString();
                            streams[0]->duration = response.get("Duration", -1).asInt();
                            streams[0]->updateVideoEncoderValues(enc_values);

                            SensorAudioEncoderSettingsValues audio_encValues;
                            audio_encValues.container = response.get("Container", EMPTY_STRING).asString();
                            audio_encValues.encoding = response.get("AudioCodec", EMPTY_STRING).asString();
                            audio_encValues.sample_rate = response.get("SampleRate", EMPTY_STRING).asString();
                            audio_encValues.bits_per_sample = response.get("Depth", EMPTY_STRING).asString();
                            audio_encValues.channels = response.get("Channels", EMPTY_STRING).asString();
                            streams[0]->updateAudioEncoderValues(audio_encValues);
                        }
                    }

                    /* Get key-frame interval for the stream if it is empty */
                    if (streams[0]->settings.encoderValues.encodingInterval.empty())
                    {
                        GstkeyframeParser keyFrameParser;
                        StreamParam param;
                        param.m_inFilePath = filepath;
                        param.m_inCodec = streams[0]->settings.encoderValues.encoding;
                        param.m_inContainer = streams[0]->settings.encoderValues.container;
                        Json::Value parse_result = keyFrameParser.parseKeyframeInterval(param);
                        int res = parse_result.get("keyInt", DEFAUL_KEY_FRAME_INTERVAL).asInt();
                        streams[0]->settings.encoderValues.encodingInterval = to_string(res);
                    }

                    sensor->printInfo();
                    LOG(info) << "- File Container:" << streams[0]->settings.encoderValues.container
                            << ", Video Codec:" << streams[0]->settings.encoderValues.encoding
                            << ", Audio Codec:" << streams[0]->settings.audioEncoderValues.encoding << endl;
                    break;
                }
            }
        }
    }
    return is_exist;
}

bool LocalStreams::transcodeIfNeeded(const string& filePath,
    const Json::Value& mediainfo, const Json::Value& keyFrameParseResult)
{
    bool is_transcode = false;
    bool transcoded = false;
    /* Decide whether this file needs to be transcoded based on bframes or large i-frames */
    string container = mediainfo.get("Container", EMPTY_STRING).asString();
    string codec = mediainfo.get("Codec", EMPTY_STRING).asString();
    string frameRate = mediainfo.get("Framerate", "30").asString();
    int bitrate = mediainfo.get("Bitrate", 8000000).asInt();
    string fileLocation = GET_CONFIG().nv_streamer_directory_path;

    int original_framerate = static_cast<int>(std::round(stringToDouble(frameRate, DEFAULT_FRAMERATE)));
    int original_keyframe_interval = keyFrameParseResult.get("keyFrameInterval", original_framerate).asInt();
    bool is_bframesPresent = keyFrameParseResult.get("bFramesPresent", false).asBool();
    bool is_largeIdrPresent = keyFrameParseResult.get("largeIdrFramesPresent", false).asBool();
    bool is_largeBitratePresent = (bitrate > DEFAULT_NVSTREAMER_MAX_BITRATE) ? true : false;
    if (is_largeBitratePresent)
    {
        bitrate = DEFAULT_NVSTREAMER_MAX_BITRATE;
    }

    LOG(warning) << "keyFrameParseResult: bFramesPresent:" << is_bframesPresent <<
            ", largeIdrFramesPresent:" << is_largeIdrPresent <<
            ", largeBitratePresent:" << is_largeBitratePresent <<
            ", original_framerate:" << original_framerate <<
            ", original_keyframe_interval:" << original_keyframe_interval << endl;
    if (is_bframesPresent)
    {
        LOG(warning) << "--- #### --- Transcoding the video file. bframePresent:" << is_bframesPresent << endl;
        is_transcode = true;
    }

    int transcode_keyframe_interval = 0;
    if (original_keyframe_interval > (MAX_KEYFRAME_INTERVAL_SEC * original_framerate))
    {
        transcode_keyframe_interval = original_framerate;
        LOG(warning) << "--- #### --- Transcoding the video file. keyframe interval is too large: "
            << original_keyframe_interval << ", transcoding to " << transcode_keyframe_interval << endl;
        is_transcode = true;
    }

    if (is_transcode)
    {
        GstTranscode::TranscodeParam enc_params;
        enc_params.m_inCodec = codec;
        enc_params.m_inContainer = container;
        enc_params.m_inFilePath = filePath;
        enc_params.m_outframeRate = static_cast<int>(std::round(stringToDouble(frameRate, DEFAULT_FRAMERATE)));
        enc_params.m_framerateNum = mediainfo.get("FramerateNum", (uint)enc_params.m_outframeRate).asUInt();
        enc_params.m_framerateDenom = mediainfo.get("FramerateDenom", 1u).asUInt();
        enc_params.m_outBitrate = bitrate;
        if (transcode_keyframe_interval > 0)
        {
            enc_params.m_outKeyFrameInterval = transcode_keyframe_interval;
        }
        else
        {
            enc_params.m_outKeyFrameInterval = enc_params.m_outframeRate;
        }

        enc_params.m_allIframes = false;
        enc_params.m_outFilePath = fileLocation + string("/transcoded_") + getFileName(filePath);

        LOG(info) << "Transcode parameters: " << enc_params.m_outFilePath
            << " " << enc_params.m_outframeRate << " " << enc_params.m_outKeyFrameInterval
            << " " << enc_params.m_outBitrate << endl;

        if (TranscodeTaskManager::getInstace()->addTask(enc_params))
        {
            replaceFile(enc_params.m_outFilePath, enc_params.m_inFilePath);
            transcoded = true;
        }
        else
        {
            LOG(error) << "Transcoding failed for file:" << filePath << endl;
        }
        deleteFile(enc_params.m_outFilePath);
    }
    return transcoded;
}

void LocalStreams::addLocalStreams(vector<shared_ptr<SensorInfo>>& sensors)
{
    const string videoFilesPath = GET_CONFIG().nv_streamer_directory_path;
    const size_t max_sensors_supported = GET_CONFIG().max_sensors_supported;
    vector<string> list;
    if (getVideoFiles(videoFilesPath, GET_CONFIG().media_containers, list) == 0)
    {
        // Sort list to match order of existing sensors in sensors vector
        if (!sensors.empty()) 
        {
            vector<string> sorted_list;
            // First add files that match existing sensors in order
            for (const auto& sensor : sensors)
            {
                auto it = find_if(list.begin(), list.end(), 
                    [&sensor](const string& file_path) {
                        return getFileName(file_path) == sensor->name;
                    });
                if (it != list.end())
                {
                    sorted_list.push_back(*it);
                    list.erase(it);
                }
            }
            // Then add any remaining files
            sorted_list.insert(sorted_list.end(), list.begin(), list.end());
            list = sorted_list;
        }

        for (uint32_t i = 0 ; i < list.size(); i++)
        {
            string file_path = list[i];
            string file_name = getFileName(file_path);

            if (checkFileNameLength(file_name))
            {
                LOG(error) << "File name is too long: " << file_name << endl;
                continue;
            }

            if (isSensorExist(sensors, file_path))
            {
                continue;
            }
            if (i >= max_sensors_supported)
            {
                LOG(error) << "Maximum number of streams (" << max_sensors_supported
                            << ") reached. Skipping remaining files." << endl;
                break;
            }
            shared_ptr<SensorInfo> sensor(new SensorInfo);
            sensor->id = file_name + string("_") + to_string(i);
            sensor->sensorId = file_name;
            sensor->type = SENSOR_TYPE_NVSTREAM;
            sensor->name =  file_name;
            sensor->location = file_path;
            sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
            sensor->updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
            shared_ptr<StreamInfo> stream(new StreamInfo);
            stream->sensorId = sensor->id;
            stream->id = sensor->id;
            stream->name = sensor->name;
            stream->isMainStream = true;
            stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_STREAMING,
                        translateStreamStatusToString(StreamStatus::STREAM_STATUS_STREAMING)));
            stream->live_url = stream->replay_url = stream->live_proxy_url =
                vst_rtsp::rtspUrlPrefix(sensor->id) + string(NV_STREAMER) + file_path;
            LOG(info) << stream->live_proxy_url << endl;

            Json::Value mediainfo;
            if (getMediaInformation(file_path, mediainfo) == 0)
            {
                /* Get key-frame interval for the stream (used to decide
                 * whether the file needs to be transcoded). */
                GstkeyframeParser keyFrameParser;
                StreamParam param;
                param.m_inFilePath = file_path;
                param.m_inCodec = mediainfo.get("Codec", EMPTY_STRING).asString();
                param.m_inContainer = mediainfo.get("Container", EMPTY_STRING).asString();
                Json::Value parse_result = keyFrameParser.parseKeyframeInterval(param);

                /* Check if file contains B-frames, large-IDR frames then
                 * transcode it BEFORE we persist any encoder values. The
                 * transcoded output replaces the file at file_path and may
                 * have a different codec/container/keyframe interval, so
                 * everything we record below must reflect the post-transcode
                 * file rather than what we read above. */
                bool wasTranscoded = transcodeIfNeeded(file_path, mediainfo, parse_result);
                if (wasTranscoded)
                {
                    LOG(info) << "Re-reading metadata after in-place transcode of " << file_path << endl;
                    Json::Value postMediaInfo;
                    if (getMediaInformation(file_path, postMediaInfo) == 0)
                    {
                        mediainfo = postMediaInfo;
                    }
                    else
                    {
                        LOG(error) << "Failed to re-read media info after transcode of " << file_path
                                   << "; falling back to pre-transcode metadata (may be stale)" << endl;
                    }
                    /* Re-parse key-frame interval against the transcoded
                     * file so encodingInterval matches the new bitstream.
                     * Uses the post-transcode mediainfo when available,
                     * pre-transcode as a fallback when the re-read failed. */
                    param.m_inCodec = mediainfo.get("Codec", EMPTY_STRING).asString();
                    param.m_inContainer = mediainfo.get("Container", EMPTY_STRING).asString();
                    parse_result = keyFrameParser.parseKeyframeInterval(param);
                }

                SensorVideoEncoderSettingsValues video_encValues;
                video_encValues.container = mediainfo.get("Container", EMPTY_STRING).asString();
                video_encValues.encoding = mediainfo.get("Codec", EMPTY_STRING).asString();
                video_encValues.frameRate = mediainfo.get("Framerate", EMPTY_STRING).asString();
                video_encValues.resolution.width = mediainfo.get("Width", EMPTY_STRING).asString();
                video_encValues.resolution.height = mediainfo.get("Height", EMPTY_STRING).asString();
                video_encValues.bitrate = mediainfo.get("Bitrate", EMPTY_STRING).asString();
                video_encValues.numFrames = mediainfo.get("FrameCount", EMPTY_STRING).asString();
                stream->duration = mediainfo.get("Duration", -1).asInt();
                int res = parse_result.get("keyInt", DEFAUL_KEY_FRAME_INTERVAL).asInt();
                video_encValues.encodingInterval = to_string(res);
                stream->updateVideoEncoderValues(video_encValues);

                SensorAudioEncoderSettingsValues audio_encValues;
                audio_encValues.container = mediainfo.get("Container", EMPTY_STRING).asString();
                audio_encValues.encoding = mediainfo.get("AudioCodec", EMPTY_STRING).asString();
                audio_encValues.sample_rate = mediainfo.get("SampleRate", EMPTY_STRING).asString();
                audio_encValues.bits_per_sample = mediainfo.get("Depth", EMPTY_STRING).asString();
                audio_encValues.channels = mediainfo.get("Channels", EMPTY_STRING).asString();
                stream->updateAudioEncoderValues(audio_encValues);
            }

            LOG(info) << "File Container:" << stream->settings.encoderValues.container
                << ", Video Codec:" << stream->settings.encoderValues.encoding
                << ", Audio Codec:" << stream->settings.audioEncoderValues.encoding << endl;

            sensor->streams.push_back(stream);
            sensors.push_back(sensor);
            sensor->printInfo();
        }
    }
}


int LocalStreams::connect()
{
    return 0;
}

int LocalStreams::getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors)
{
    addLocalStreams(sensors);
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceManager)
    {
        deviceManager->m_isRtspServerReady = true;
    }
    return 0;
}

int LocalStreams::getSensorStreamInfo(shared_ptr<SensorInfo>& sensor)
{
    return 0;
}

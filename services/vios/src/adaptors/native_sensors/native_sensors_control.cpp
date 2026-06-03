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

#include <stdio.h>

#include "native_sensors_control.h"
#include "sensor_info.h"

extern "C" ISensorControlInterface* createObject()
{
    return new NativeSensorControlInterface;
}

extern "C" void destroyObject( NativeSensorControlInterface* object )
{
    delete object;
}

NativeSensorControlInterface::NativeSensorControlInterface()
{
}

int NativeSensorControlInterface::connect()
{
    return 0;
}

int NativeSensorControlInterface::getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors)
{
    return 0;
}

int NativeSensorControlInterface::getSensorStreamInfo(shared_ptr<SensorInfo>& sensor)
{
    return 0;
}

int NativeSensorControlInterface::getSensorImageSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings)
{
    std::vector<std::shared_ptr<SensorInfo>> sensors = getCacheSensorList();
    bool found = false;

    for (auto sensorList : sensors)
    {
        vector<shared_ptr<StreamInfo>> streams = sensorList->getStreams();
        for (auto stream : streams)
        {
            if (stream_id == stream->id)
            {
                settings = stream->settings;

                for (const auto& modes : settings.imageOptions.TemporalNoiseReductionModes)
                {
                    LOG(verbose) << "Supported TemporalNoiseReductionMode values: " << modes << std::endl;
                }
                LOG(verbose) << "Current TemporalNoiseReductionMode value: " << settings.imageValues.TemporalNoiseReductionMode << std::endl;

                for (const auto& modes : settings.imageOptions.WhiteBalanceModes)
                {
                    LOG(verbose) << "Supported WhiteBalanceMode values: " << modes << std::endl;
                }
                LOG(verbose) << "Current WhiteBalanceMode value: " << settings.imageValues.WhiteBalanceMode << std::endl;

                for (const auto& modes : settings.imageOptions.AeAntibandingModes)
                {
                    LOG(verbose) << "Supported AeAntibandingModes values: " << modes << std::endl;
                }
                LOG(verbose) << "Current AeAntibandingModes value: " << settings.imageValues.AeAntibandingMode << std::endl;

                for (const auto& modes : settings.imageOptions.EdgeEnhancementModes)
                {
                    LOG(verbose) << "Supported EdgeEnhancementMode values: " << modes << std::endl;
                }
                LOG(verbose) << "Current EdgeEnhancementMode value: " << settings.imageValues.EdgeEnhancementMode << std::endl;

                LOG(verbose) << "Current EdgeEnhancementStrength value: " << settings.imageValues.EdgeEnhancementStrength << " min:" <<
                settings.imageOptions.EdgeEnhancementStrength.min << " max:" << settings.imageOptions.EdgeEnhancementStrength.max << std::endl;

                LOG(verbose) << "Current ExposureCompensation value: " << settings.imageValues.ExposureCompensation << " min:" <<
                settings.imageOptions.ExposureCompensation.min << " max:" << settings.imageOptions.ExposureCompensation.max << std::endl;

                LOG(verbose) << "Current ColorSaturation value: " << settings.imageValues.ColorSaturation << " min:" <<
                settings.imageOptions.ColorSaturation.min << " max:" << settings.imageOptions.ColorSaturation.max << std::endl;

                found = true;
                break;
            }
        }

        if (found)
        {
            break;
        }
    }
    return 0;
}

int NativeSensorControlInterface::setSensorImageSettings(shared_ptr<SensorInfo>& sensor, const SensorImageSettingsValues& settings)
{
    return 0;
}

int NativeSensorControlInterface::setSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const SensorVideoEncoderSettingsValues& settings)
{
    return 0;
}

int NativeSensorControlInterface::getNetworkInfo(shared_ptr<SensorInfo>& sensor, SensorNetworkInfo& networkInfo)
{
    return 0;
}

int NativeSensorControlInterface::setNetworkInfo(shared_ptr<SensorInfo>& sensor, const SensorNetworkInfo& networkInfo, bool& rebootNeeded)
{
    return 0;
}

int NativeSensorControlInterface::getSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings)
{
    std::vector<std::shared_ptr<SensorInfo>> sensors = getCacheSensorList();
    bool found = false;

    for (auto sensorList : sensors)
    {
        vector<shared_ptr<StreamInfo>> streams = sensorList->getStreams();
        for (auto stream : streams)
        {
            if (stream_id == stream->id)
            {
                settings = stream->settings;

                LOG(verbose) << "--------------------------------------------------------" << endl;
                for (auto encodings: settings.encoderOptions.videoEncodingSupported)
                {
                    LOG(verbose) << "videoEncodingSupported:" << encodings << endl;
                }
                for (auto videoEncoderConfigurationsOptions : settings.encoderOptions.encoderSettingsOptions)
                {
                    LOG(verbose) << "encoding:" << videoEncoderConfigurationsOptions.encoding << endl;
                    LOG(verbose) << "FrameRateSupported:" << videoEncoderConfigurationsOptions.FrameRateSupported << endl;
                    for (auto Resolution: videoEncoderConfigurationsOptions.ResolutionsAvailable)
                    {
                        LOG(verbose) << "Resolution width:" << Resolution.width << " height:" << Resolution.height << endl;
                    }
                    for (auto profilesSupported : videoEncoderConfigurationsOptions.profilesSupported)
                    {
                        LOG(verbose) << "profilesSupported:" << profilesSupported << endl;
                    }
                    LOG(verbose) << "qualityRange min:" << videoEncoderConfigurationsOptions.qualityRange.min << " max:" << videoEncoderConfigurationsOptions.qualityRange.max << endl;
                    LOG(verbose) << "bitrateRange min:" << videoEncoderConfigurationsOptions.BitrateRange.min << " max:" << videoEncoderConfigurationsOptions.BitrateRange.max << endl;
                    LOG(verbose) << "EncodingIntervalRange: min:" << videoEncoderConfigurationsOptions.EncodingIntervalRange.min << " max:" << videoEncoderConfigurationsOptions.EncodingIntervalRange.max << endl;
                    LOG(verbose) << "GovLengthRange min:" << videoEncoderConfigurationsOptions.GovLengthRange.min << " max:" << videoEncoderConfigurationsOptions.GovLengthRange.max << endl;
                }
                LOG(verbose) << "-----------------------------------------------" << endl;

                LOG(verbose) << "--------------------------------------------------------" << endl;
                LOG(verbose) << "encoding: " << settings.encoderValues.encoding << endl;
                LOG(verbose) << "resolution width:" << settings.encoderValues.resolution.width << " height:" << settings.encoderValues.resolution.height << endl;
                LOG(verbose) << "frameRate: " << settings.encoderValues.frameRate << endl;
                LOG(verbose) << "bitrate: " << settings.encoderValues.bitrate << endl;
                LOG(verbose) << "encodingInterval: " << settings.encoderValues.encodingInterval << endl;
                LOG(verbose) << "encodingProfile: " << settings.encoderValues.encodingProfile << endl;
                LOG(verbose) << "quality: " << settings.encoderValues.quality << endl;
                LOG(verbose) << "govLength: " << settings.encoderValues.govLength << endl;
                LOG(verbose) << "--------------------------------------------------------" << endl;

                found = true;
                break;
            }
        }

        if (found)
        {
            break;
        }
    }

    return 0;
}
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

#include "database_common.h"
#include "utils.h"
#include <unordered_set>

void videoRecordHelper(VideoRecordDBColumns &row, unordered_map<string, string> &entries)
{
    for (auto column : entries)
    {
        if (iequals(column.first, VideoRecordDBColumns::sensor_id))
        {
            row.sensor_id_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::stream_id))
        {
            row.stream_id_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::resolution))
        {
            row.resolution_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::start_time))
        {
            row.start_time_value = stringToLong(column.second);
        }
        else if (iequals(column.first, VideoRecordDBColumns::duration))
        {
            row.duration_value = stringToLong(column.second);
        }
        else if (iequals(column.first, VideoRecordDBColumns::file_path))
        {
            row.filepath_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::file_size))
        {
            row.filesize_value = stringToLong(column.second);
        }
        else if (iequals(column.first, VideoRecordDBColumns::file_fps))
        {
            row.filefps_value = stringToLong(column.second);
        }
        else if (iequals(column.first, VideoRecordDBColumns::sensor_name))
        {
            row.sensor_name_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::record_config))
        {
            row.record_config_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::codec))
        {
            row.codec_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::file_protection))
        {
            row.file_protection_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::object_id))
        {
            row.object_id_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::metadata_file_path))
        {
            row.metadata_file_path_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::metadata_json))
        {
            row.metadata_json_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::storage_location))
        {
            row.storage_location_value = stringToLong(column.second);
        }
        else if (iequals(column.first, VideoRecordDBColumns::bucket_name))
        {
            row.bucket_name_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::created_date_time))
        {
            row.created_date_time_value = column.second;
        }
        else if (iequals(column.first, VideoRecordDBColumns::modified_date_time))
        {
            row.modified_date_time_value = column.second;
        }
    }
}

void sensorStreamHelper(SensorStreamsDBColumns &row, unordered_map<string, string> &entries)
{
    for (auto column : entries)
    {
        if (iequals(column.first, SensorStreamsDBColumns::sensor_id))
        {
            row.sensor_id_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::stream_id))
        {
            row.stream_id_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::live_url))
        {
            row.live_url_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::replay_url))
        {
            row.replay_url_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::proxy_url))
        {
            row.proxy_url_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::resolution))
        {
            row.resolution_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::frameRate))
        {
            row.frameRate_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::encoding))
        {
            row.encoding_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::streamStatus))
        {
            row.streamStatus_value = stringToLong(column.second);
        }
        else if (iequals(column.first, SensorStreamsDBColumns::type))
        {
            row.streamType_value = stringToLong(column.second);
        }
        else if (iequals(column.first, SensorStreamsDBColumns::encodingProfile))
        {
            row.encodingProfile_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::encodingInterval))
        {
            row.encodingInterval_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::duration))
        {
            row.duration_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::isMainStream))
        {
            row.isMainStream_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::isAlwaysRecording))
        {
            row.isAlwaysRecording_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::bitrate))
        {
            row.bitrate_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::numFrames))
        {
            row.numFrames_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::audio_container))
        {
            row.audio_container_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::audio_encoding))
        {
            row.audio_encoding_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::audio_sample_rate))
        {
            row.audio_sample_rate_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::audio_bps))
        {
            row.audio_bps_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::audio_channels))
        {
            row.audio_channels_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::created_date_time))
        {
            row.created_date_time_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::modified_date_time))
        {
            row.modified_date_time_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::storageLocation))
        {
            row.storageLocation_value = stringToLong(column.second);
        }
        else if (iequals(column.first, SensorStreamsDBColumns::streamName))
        {
            row.streamName_value = column.second;
        }
        else if (iequals(column.first, SensorStreamsDBColumns::isBframesPresent))
        {
            row.isBframesPresent_value = stringToInt(column.second, 0);
        }
    }
}

// Helper function to validate sensor streams table property names and prevent SQL injection
std::string validateSensorStreamTableProperty(const std::string& property)
{
    // Whitelist of allowed column names for sensor streams table using actual schema constants
    static const std::unordered_set<std::string> allowedColumns =
    {
        SensorStreamsDBColumns::device_id,
        SensorStreamsDBColumns::sensor_id,
        SensorStreamsDBColumns::row_id,
        SensorStreamsDBColumns::created_date_time,
        SensorStreamsDBColumns::modified_date_time,
        SensorStreamsDBColumns::live_url,
        SensorStreamsDBColumns::replay_url,
        SensorStreamsDBColumns::proxy_url,
        SensorStreamsDBColumns::resolution,
        SensorStreamsDBColumns::frameRate,
        SensorStreamsDBColumns::encoding,
        SensorStreamsDBColumns::stream_id,
        SensorStreamsDBColumns::streamStatus,
        SensorStreamsDBColumns::type,
        SensorStreamsDBColumns::encodingProfile,
        SensorStreamsDBColumns::encodingInterval,
        SensorStreamsDBColumns::duration,
        SensorStreamsDBColumns::isMainStream,
        SensorStreamsDBColumns::isAlwaysRecording,
        SensorStreamsDBColumns::storageLocation,
        SensorStreamsDBColumns::bitrate,
        SensorStreamsDBColumns::numFrames,
        SensorStreamsDBColumns::audio_container,
        SensorStreamsDBColumns::audio_encoding,
        SensorStreamsDBColumns::audio_sample_rate,
        SensorStreamsDBColumns::audio_bps,
        SensorStreamsDBColumns::audio_channels,
        SensorStreamsDBColumns::streamName,
        SensorStreamsDBColumns::isBframesPresent
    };
    
    // Check if property is in whitelist
    if (allowedColumns.find(property) != allowedColumns.end())
    {
        return property;
    }
    
    // Return empty string for invalid column names
    return "";
}
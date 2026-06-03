/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/**
 * @file db_test_utils.cpp
 * @brief Implementation of database verification utilities for unit tests
 */

#include "db_test_utils.h"
#include "database.h"
#include "device_manager.h"
#include "sensor_info.h"
#include "config.h"
#include "database_schema.h"
#include <iostream>
#include <filesystem>

using namespace std;
using namespace nv_vms;

namespace DbTestUtils
{

bool addDummyReplaySensorAndStream(std::shared_ptr<DeviceManager> deviceManager,
                                   const string& streamId,
                                   const string& sensorName,
                                   const string& fileUrl)
{
    if (!deviceManager || streamId.empty() || fileUrl.empty())
    {
        cout << "[DB-UTIL] addDummyReplaySensorAndStream: invalid params" << endl;
        return false;
    }

    auto sensor = std::make_shared<SensorInfo>();
    sensor->id = streamId;
    sensor->name = sensorName.empty() ? streamId : sensorName;
    sensor->type = SENSOR_TYPE_FILE;
    sensor->sensorId = streamId;
    sensor->location = fileUrl;
    sensor->url = fileUrl;
    sensor->updateSensorStatus(SensorStatusStreaming);
    sensor->updateHttpErrorStatus(std::make_pair(200, ""));

    auto stream = std::make_shared<StreamInfo>();
    stream->id = stream->sensorId = streamId;
    stream->name = streamId;
    stream->live_url = stream->replay_url = stream->live_proxy_url = fileUrl;
    stream->isMainStream = true;
    stream->storageLocation = StreamStorageTypeLocal;
    stream->stream_type = StreamType::FileDownload;
    stream->duration = -1;
    stream->settings.encoderValues.encoding = "h264";

    sensor->addStreams(stream);
    deviceManager->addOrUpdateSensor(*sensor);

    IDatabaseInterface* db = GET_DB_INSTANCE();
    if (!db)
    {
        cout << "[DB-UTIL] addDummyReplaySensorAndStream: DB not available, DeviceManager entry added only" << endl;
        return true;
    }

    string deviceId = deviceManager->getDeviceId();
    if (deviceId.empty())
    {
        deviceId = db->getLocalDeviceId();
        if (deviceId.empty())
            deviceId = "local";
    }

    SensorDetailsDBColumns sensorRow;
    sensorRow.device_id_value = deviceId;
    sensorRow.sensor_id_value = sensor->id;
    sensorRow.sensor_hw_id_value = sensor->sensorId;
    sensorRow.name_value = sensor->name;
    sensorRow.type_value = sensor->type;
    sensorRow.location_value = sensor->location;
    sensorRow.url_value = sensor->url;
    sensorRow.httpStatus_value = sensor->getHttpErrorStatus().first;
    sensorRow.sensorStatus_value = sensor->getSensorStatus();
    sensorRow.isRemoteSensor_value = "false";

    int ret = db->insertRowSensorDetails(sensorRow);
    if (ret != 0)
    {
        cout << "[DB-UTIL] addDummyReplaySensorAndStream: insertRowSensorDetails failed" << endl;
        return false;
    }

    SensorStreamsDBColumns streamRow;
    streamRow.sensor_id_value = streamId;
    streamRow.stream_id_value = streamId;
    streamRow.live_url_value = fileUrl;
    streamRow.replay_url_value = fileUrl;
    streamRow.proxy_url_value = fileUrl;
    streamRow.encoding_value = "h264";
    streamRow.duration_value = "-1";
    streamRow.streamStatus_value = STREAM_STATUS_STREAMING;
    streamRow.streamType_value = static_cast<int64_t>(StreamType::FileDownload);
    streamRow.isMainStream_value = "true";
    streamRow.isAlwaysRecording_value = "false";
    streamRow.storageLocation_value = static_cast<int64_t>(StreamStorageTypeLocal);
    streamRow.streamName_value = streamId;

    ret = db->insertRowStream(streamRow);
    if (ret != 0)
    {
        cout << "[DB-UTIL] addDummyReplaySensorAndStream: insertRowStream failed" << endl;
        return false;
    }

    // Add VIDEO_RECORD_DETAILS entry so getFileListStreamIdBased returns the file for replay
    string filePath = fileUrl;
    if (filePath.find("file://") == 0)
        filePath = filePath.substr(7);
    if (!std::filesystem::exists(filePath))
    {
        cout << "[DB-UTIL] addDummyReplaySensorAndStream: file does not exist: " << filePath << endl;
        return false;
    }

    VideoRecordDBColumns videoRow;
    videoRow.sensor_id_value = streamId;
    videoRow.stream_id_value = streamId;
    videoRow.start_time_value = 0;
    videoRow.duration_value = 10;  // Must be != FILE_INIT_DURATION (1) for query to match
    videoRow.filepath_value = std::filesystem::absolute(filePath).string();
    videoRow.filesize_value = 0;
    videoRow.filefps_value = 0;
    videoRow.sensor_name_value = sensorName.empty() ? streamId : sensorName;
    videoRow.codec_value = "h264";
    videoRow.storage_location_value = static_cast<int64_t>(StreamStorageTypeLocal);

    ret = db->insertRowVideoRecord(videoRow);
    if (ret != 0)
    {
        cout << "[DB-UTIL] addDummyReplaySensorAndStream: insertRowVideoRecord failed" << endl;
        return false;
    }

    cout << "[DB-UTIL] addDummyReplaySensorAndStream: added sensor/stream/VIDEO_RECORD " << streamId << " for " << filePath << endl;
    return true;
}

StreamDbVerifyResult streamExistsInDb(const string& streamId)
{
    StreamDbVerifyResult result{false, "DB not available or stream not found"};
    IDatabaseInterface* db = GET_DB_INSTANCE();
    if (db == nullptr)
    {
        result.message = "GET_DB_INSTANCE() returned null";
        cout << "[DB-UTIL] DB verify: " << result.message << endl;
        return result;
    }
    SensorStreamsDBColumns row = db->readSensorStreams(streamId);
    if (row.stream_id_value.empty())
    {
        result.message = "No SENSOR_STREAMS row for stream_id '" + streamId + "'";
        cout << "[DB-UTIL] DB verify: " << result.message << endl;
        return result;
    }
    result.found = true;
    result.message = "Stream '" + streamId + "' found in SENSOR_STREAMS";
    cout << "[DB-UTIL] DB verify: " << result.message << endl;
    cout << "[DB-UTIL] Row data: stream_id=" << row.stream_id_value
         << " live_url=" << row.live_url_value
         << " proxy_url=" << row.proxy_url_value
         << " encoding=" << row.encoding_value
         << " streamStatus=" << row.streamStatus_value
         << " isMainStream=" << row.isMainStream_value
         << " isAlwaysRecording=" << row.isAlwaysRecording_value
         << " resolution=" << row.resolution_value
         << " frameRate=" << row.frameRate_value
         << " duration=" << row.duration_value << endl;
    return result;
}

} // namespace DbTestUtils

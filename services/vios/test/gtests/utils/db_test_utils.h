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
 * @file db_test_utils.h
 * @brief Database verification utilities for unit tests
 *
 * Helpers to query and verify DB state (e.g. SENSOR_STREAMS) from recorder/storage tests.
 */

#ifndef DB_TEST_UTILS_H
#define DB_TEST_UTILS_H

#include <string>
#include <memory>

using namespace std;

namespace nv_vms { class DeviceManager; }

/**
 * @brief Database test utilities namespace
 */
namespace DbTestUtils
{
    /**
     * @brief Result of a stream DB verification
     */
    struct StreamDbVerifyResult
    {
        bool found;       ///< true if stream entry exists in SENSOR_STREAMS
        string message;   ///< description for logging
    };

    /**
     * @brief Verify that a stream entry exists in the database (SENSOR_STREAMS)
     *
     * Queries the DB via GET_DB_INSTANCE()->readSensorStreams(streamId).
     * Use after addStream to confirm the stream was persisted.
     *
     * @param streamId Stream identifier (e.g. TEST_STREAM_ID)
     * @return StreamDbVerifyResult with found=true if stream_id_value is non-empty
     */
    StreamDbVerifyResult streamExistsInDb(const string& streamId);

    /**
     * @brief Add dummy SensorInfo and StreamInfo to DeviceManager and DB for replay tests.
     *
     * Creates a file-based sensor/stream entry (modeled after addFile in storage_management_utils).
     * Adds to DeviceManager and writes to SENSOR_DETAILS and SENSOR_STREAMS tables.
     * Prerequisite for ReplayImageCaptureTest. Does not start StreamRecorder or StorageManagement.
     *
     * @param deviceManager DeviceManager instance.
     * @param streamId Stream identifier (used as sensor_id for main stream).
     * @param sensorName Human-readable sensor name.
     * @param fileUrl File URL (e.g. "file:///path/to/video.mp4").
     * @return true if sensor/stream added to DeviceManager and DB entry written successfully.
     */
    bool addDummyReplaySensorAndStream(std::shared_ptr<nv_vms::DeviceManager> deviceManager,
                                       const string& streamId,
                                       const string& sensorName,
                                       const string& fileUrl);
}

#endif // DB_TEST_UTILS_H

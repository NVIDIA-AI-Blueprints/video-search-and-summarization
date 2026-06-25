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

#include "ds_proto_parser.h"
#include "logger.h"
#include "utils.h"
#include <dlfcn.h>
#include <vector>

constexpr const char* ABSOLUTE_LIBRARY_PATH_2D_X86_64 = "/home/vst/vst_release/prebuilts/x86_64/libnvds_schema_2d.so";
constexpr const char* ABSOLUTE_LIBRARY_PATH_3D_X86_64 = "/home/vst/vst_release/prebuilts/x86_64/libnvds_schema_3d.so";
constexpr const char* ABSOLUTE_LIBRARY_PATH_2D_AARCH64 = "/home/vst/vst_release/prebuilts/aarch64/libnvds_schema_2d.so";
constexpr const char* ABSOLUTE_LIBRARY_PATH_3D_AARCH64 = "/home/vst/vst_release/prebuilts/aarch64/libnvds_schema_3d.so";

DsProtoParser* DsProtoParser::getInstance()
{
    static DsProtoParser instance;
    return &instance;
}

DsProtoParser::DsProtoParser()
{
    if (GET_CONFIG().overlay_3d_sensor_name.empty())
    {
        m_data2d = true;
    }
    if (m_data2d)
    {
        LOG(info) << "Using 2D schema" << std::endl;
    }
    else
    {
        LOG(info) << "Using 3D schema" << std::endl;
    }
    if (!loadSchemaLibrary())
    {
        LOG(error) << "Failed to load schema library" << std::endl;
    }
}

DsProtoParser::~DsProtoParser()
{
    if (m_schemaLibHandle != nullptr)
    {
        dlclose(m_schemaLibHandle);
        m_schemaLibHandle = nullptr;
        resetSchemaFunctionPointers();
    }
}

bool DsProtoParser::loadSchemaLibrary()
{
    std::call_once(m_schemaLoadOnceFlag, [this]()
    {
        if (m_schemaLibHandle)
        {
            return; // Already loaded
        }

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        const char* libPath = m_data2d ? ABSOLUTE_LIBRARY_PATH_2D_AARCH64 : ABSOLUTE_LIBRARY_PATH_3D_AARCH64;
#else
        const char* libPath = m_data2d ? ABSOLUTE_LIBRARY_PATH_2D_X86_64 : ABSOLUTE_LIBRARY_PATH_3D_X86_64;
#endif
        m_schemaLibHandle = dlopen(libPath, RTLD_LAZY);
        if (!m_schemaLibHandle)
        {
            LOG(error) << "Failed to load schema library: " << dlerror() << std::endl;
            resetSchemaFunctionPointers();
            return;
        }

        m_frame_new = (frame_new_t)dlsym(m_schemaLibHandle, "nv_frame_new");
        m_frame_parse = (frame_parse_t)dlsym(m_schemaLibHandle, "nv_frame_parse");
        m_frame_get_sensorid = (frame_get_sensorid_t)dlsym(m_schemaLibHandle, "nv_frame_get_sensorid");
        m_frame_destroy = (frame_destroy_t)dlsym(m_schemaLibHandle, "nv_frame_destroy");
        m_frame_get_timestamp_ms = (frame_get_timestamp_ms_t)dlsym(m_schemaLibHandle, "nv_frame_get_timestamp_ms");
        m_frame_get_object_count = (frame_get_object_count_t)dlsym(m_schemaLibHandle, "nv_frame_get_object_count");
        m_frame_get_object = (frame_get_object_t)dlsym(m_schemaLibHandle, "nv_frame_get_object");
        m_object_get_id = (object_get_id_t)dlsym(m_schemaLibHandle, "nv_object_get_id");
        m_object_get_type = (object_get_type_t)dlsym(m_schemaLibHandle, "nv_object_get_type");
        m_object_get_confidence = (object_get_confidence_t)dlsym(m_schemaLibHandle, "nv_object_get_confidence");
        if (m_data2d)
        {
            m_object_has_bbox2d = (object_has_bbox_t)dlsym(m_schemaLibHandle, "nv2d_object_has_bbox");
            m_object_get_bbox = (object_get_bbox_t)dlsym(m_schemaLibHandle, "nv2d_object_get_bbox");
            m_object_has_bbox3d = nullptr;
            m_object_get_bbox3d_coordinates = nullptr;
            m_object_get_bbox3d_confidence = nullptr;
        }
        else
        {
            m_object_has_bbox3d = (object_has_bbox3d_t)dlsym(m_schemaLibHandle, "nv_object_has_bbox3d");
            m_object_get_bbox3d_coordinates = (object_get_bbox3d_coordinates_t)dlsym(m_schemaLibHandle, "nv_object_get_bbox3d_coordinates");
            m_object_get_bbox3d_confidence = (object_get_bbox3d_confidence_t)dlsym(m_schemaLibHandle, "nv_object_get_bbox3d_confidence");
            m_frame_get_info_count = (frame_get_info_count_t)dlsym(m_schemaLibHandle, "nv_frame_get_info_count");
            m_frame_get_info = (frame_get_info_t)dlsym(m_schemaLibHandle, "nv_frame_get_info");
            m_object_has_bbox2d = nullptr;
            m_object_get_bbox = nullptr;
        }

        // Load pose function symbols
        m_object_has_pose = (object_has_pose_t)dlsym(m_schemaLibHandle, "nv_object_has_pose");
        m_object_get_pose_type = (object_get_pose_type_t)dlsym(m_schemaLibHandle, "nv_object_get_pose_type");
        m_object_get_pose_keypoints_count = (object_get_pose_keypoints_count_t)dlsym(m_schemaLibHandle, "nv_object_get_pose_keypoints_count");
        m_object_get_pose_keypoint_coordinates = (object_get_pose_keypoint_coordinates_t)dlsym(m_schemaLibHandle, "nv_object_get_pose_keypoint_coordinates");
        m_object_get_pose_keypoint_quaternion = (object_get_pose_keypoint_quaternion_t)dlsym(m_schemaLibHandle, "nv_object_get_pose_keypoint_quaternion");
        m_object_get_pose_actions_count = (object_get_pose_actions_count_t)dlsym(m_schemaLibHandle, "nv_object_get_pose_actions_count");
        m_object_get_pose_action_type = (object_get_pose_action_type_t)dlsym(m_schemaLibHandle, "nv_object_get_pose_action_type");
        m_object_get_pose_action_confidence = (object_get_pose_action_confidence_t)dlsym(m_schemaLibHandle, "nv_object_get_pose_action_confidence");

        if (!m_frame_new || !m_frame_parse || !m_frame_get_sensorid || !m_frame_destroy
            || !m_frame_get_timestamp_ms || !m_frame_get_object_count || !m_frame_get_object
            || !m_object_get_id || !m_object_get_type || !m_object_get_confidence)
        {
            LOG(error) << "Failed to resolve schema symbols " << dlerror() << std::endl;
            const char *dlsym_error = dlerror();
            if (dlsym_error)
            {
                LOG(error) << "Error while loading symbol 'nv_': " << dlsym_error << std::endl;
                if (m_schemaLibHandle)
                {
                    dlclose(m_schemaLibHandle);
                    m_schemaLibHandle = nullptr;
                }
            }
            resetSchemaFunctionPointers();
            return;
        }
        LOG(info) << "Successfully loaded schema library" << std::endl;
    });

    return m_schemaLibHandle != nullptr;
}

void DsProtoParser::resetSchemaFunctionPointers()
{
    m_frame_get_timestamp_ms = nullptr;
    m_frame_get_object_count = nullptr;
    m_frame_get_object = nullptr;
    m_object_get_id = nullptr;
    m_object_get_type = nullptr;
    m_object_get_confidence = nullptr;
    m_object_has_bbox2d = nullptr;
    m_object_get_bbox = nullptr;
    m_object_has_bbox3d = nullptr;
    m_object_get_bbox3d_coordinates = nullptr;
    m_object_get_bbox3d_confidence = nullptr;
    m_frame_new = nullptr;
    m_frame_parse = nullptr;
    m_frame_get_sensorid = nullptr;
    m_frame_destroy = nullptr;
    m_frame_get_info_count = nullptr;
    m_frame_get_info = nullptr;
}

Json::Value DsProtoParser::parseMessage(const void* msg, int len, int64_t& frameTimeMs)
{
    if (!m_schemaLibHandle && !loadSchemaLibrary())
    {
        static std::atomic<bool> schemaLogOnce = false;
        if (!schemaLogOnce)
        {
            LOG(error) << "Schema library not loaded, cannot parse message" << std::endl;
            schemaLogOnce = true;
        }
        return Json::nullValue;
    }

    Json::Value payload;
    try {
        if (!m_frame_new || !m_frame_parse || !m_frame_get_sensorid ||
            !m_frame_destroy || !m_frame_get_timestamp_ms)
        {
            LOG(error) << "Schema function pointers not initialized" << std::endl;
            return Json::nullValue;
        }
        void* frame = m_frame_new();
        if (!frame || !m_frame_parse(frame, msg, len))
        {
            LOG(error) << "Failed to parse protobuf message" << std::endl;
            m_frame_destroy(frame);
            return Json::nullValue;
        }

        // SensorId
        char* sensorid = m_frame_get_sensorid(frame);
        payload["sensorId"] = sensorid ? sensorid : "";
        free(sensorid);

        // Timestamp
        frameTimeMs = 0;
        frameTimeMs = m_frame_get_timestamp_ms(frame);
        payload["epocTime"] = frameTimeMs;

        // Objects
        Json::Value objectsArray = Json::arrayValue;
        int numObjects = m_frame_get_object_count(frame);
        for (int i = 0; i < numObjects; ++i)
        {
            void* obj = m_frame_get_object(frame, i);
            Json::Value objJson;
            // id, type, confidence
            char* id = m_object_get_id(obj);
            char* type = m_object_get_type(obj);
            objJson["id"] = id ? id : "";
            objJson["type"] = type ? type : "";
            objJson["confidence"] = m_object_get_confidence(obj);
            if (id) free(id);
            if (type) free(type);
            // 3D or 2D bbox
            if (m_data2d)
            {
                if (m_object_has_bbox2d(obj))
                {
                    float leftX, topY, rightX, bottomY;
                    m_object_get_bbox(obj, &leftX, &topY, &rightX, &bottomY);
                    Json::Value bboxJson;
                    bboxJson["leftX"] = leftX;
                    bboxJson["topY"] = topY;
                    bboxJson["rightX"] = rightX;
                    bboxJson["bottomY"] = bottomY;
                    objJson["bbox"] = bboxJson;
                }
            }
            else
            {
                if (m_object_has_bbox3d(obj))
                {
                    // 12 coordinates required for 3D bbox
                    double coords[12];
                    int n = m_object_get_bbox3d_coordinates(obj, coords, 12);
                    Json::Value coordsArray = Json::arrayValue;
                    for (int j = 0; j < n; ++j) coordsArray.append(coords[j]);
                    Json::Value bbox3dJson;
                    bbox3dJson["coordinates"] = coordsArray;
                    bbox3dJson["confidence"] = m_object_get_bbox3d_confidence(obj);
                    objJson["bbox3d"] = bbox3dJson;
                }
            }

            // Add pose data if available
            if (m_object_has_pose && m_object_has_pose(obj))
            {
                Json::Value poseJson;

                // Add pose type
                if (m_object_get_pose_type)
                {
                    char* poseType = m_object_get_pose_type(obj);
                    poseJson["type"] = poseType ? poseType : "";
                    if (poseType) free(poseType);
                }

                // Add keypoints as an array of keypoint objects, size = keypointCount
                Json::Value keypointsArray = Json::arrayValue;
                if (m_object_get_pose_keypoints_count && m_object_get_pose_keypoint_coordinates)
                {
                    int keypointsCount = m_object_get_pose_keypoints_count(obj);
                    for (int i = 0; i < keypointsCount; i++)
                    {
                        Json::Value keypointJson;
                        // Get coordinates for this keypoint (assuming max 4 coordinates per keypoint: x, y, z, confidence)
                        float coordinates[4] = {0.0f, 0.0f, 0.0f, 0.0f}; // Initialize to zero
                        m_object_get_pose_keypoint_coordinates(obj, i, coordinates, 4);
                        Json::Value coordinatesArray = Json::arrayValue;
                        for (int j = 0; j < 4; j++) // Use all 4 coordinates
                        {
                            coordinatesArray.append(coordinates[j]);
                        }
                        keypointJson["coordinates"] = coordinatesArray;

                        // If quaternion data is available, add it as well
                        if (m_object_get_pose_keypoint_quaternion)
                        {
                            float quaternion[4];
                            int quatCount = m_object_get_pose_keypoint_quaternion(obj, i, quaternion, 4);
                            if (quatCount > 0)
                            {
                                Json::Value quaternionArray = Json::arrayValue;
                                for (int q = 0; q < quatCount; q++)
                                {
                                    quaternionArray.append(quaternion[q]);
                                }
                                keypointJson["quaternion"] = quaternionArray;
                            }
                        }
                        keypointsArray.append(keypointJson);
                    }
                }
                poseJson["keypoints"] = keypointsArray;

                // Add actions if available
                if (m_object_get_pose_actions_count && m_object_get_pose_action_type && m_object_get_pose_action_confidence)
                {
                    int actionsCount = m_object_get_pose_actions_count(obj);
                    if (actionsCount > 0)
                    {
                        // For compatibility with overlay_internal.cpp, use the first action as primary
                        char* primaryActionType = m_object_get_pose_action_type(obj, 0);
                        float primaryActionConfidence = m_object_get_pose_action_confidence(obj, 0);
                        poseJson["action"] = primaryActionType ? primaryActionType : "";
                        poseJson["action_confidence"] = primaryActionConfidence;
                        if (primaryActionType) free(primaryActionType);

                        // Also add all actions as an array for completeness
                        Json::Value actionsArray = Json::arrayValue;
                        for (int i = 0; i < actionsCount; i++)
                        {
                            char* actionType = m_object_get_pose_action_type(obj, i);
                            float actionConfidence = m_object_get_pose_action_confidence(obj, i);
                            Json::Value actionJson;
                            actionJson["type"] = actionType ? actionType : "";
                            actionJson["confidence"] = actionConfidence;
                            if (actionType) free(actionType);
                            actionsArray.append(actionJson);
                        }
                        poseJson["actions"] = actionsArray;
                    }
                }

                objJson["pose"] = poseJson;
            }
			objectsArray.append(objJson);
        }
        payload["objects"] = objectsArray;

        if (!m_data2d && m_frame_get_info_count && m_frame_get_info)
        {
            int infoSize = m_frame_get_info_count(frame);
            if (infoSize <= 0)
            {
                m_frame_destroy(frame);
                return payload;
            }
            std::vector<char*> keys(infoSize, nullptr);
            std::vector<char*> values(infoSize, nullptr);
            int infoCount = m_frame_get_info(frame, keys.data(), values.data(), infoSize);

            Json::Value perCameraPayloads = Json::arrayValue;
            for (int i = 0; i < infoCount; i++)
            {
                if (!keys[i] || !values[i])
                {
                    free(keys[i]);
                    free(values[i]);
                    continue;
                }
                std::time_t epochMs = isoToEpoch(std::string(values[i]));
                if (epochMs == 0)
                {
                    free(keys[i]);
                    free(values[i]);
                    continue;
                }
                Json::Value cameraPayload = payload;
                cameraPayload["sensorId"] = std::string(keys[i]);
                cameraPayload["epocTime"] = static_cast<int64_t>(epochMs);
                perCameraPayloads.append(cameraPayload);

                free(keys[i]);
                free(values[i]);
            }

            if (perCameraPayloads.size() > 0)
            {
                m_frame_destroy(frame);
                return perCameraPayloads;
            }
        }

        m_frame_destroy(frame);
    } catch (const std::exception& e) {
        LOG(error) << "Exception while processing message: " << e.what() << std::endl;
        return Json::nullValue;
    }
    return payload;
}
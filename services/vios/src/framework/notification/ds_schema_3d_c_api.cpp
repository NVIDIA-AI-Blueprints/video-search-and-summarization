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

#include "ds_schema.pb.h"
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <google/protobuf/timestamp.pb.h>

extern "C" {

void* nv_frame_new() {
    return new nv::Frame();
}

bool nv_frame_parse(void* frame, const void* data, size_t len) {
    return static_cast<nv::Frame*>(frame)->ParseFromArray(data, len);
}

char* nv_frame_get_sensorid(void* frame) {
    std::string id = static_cast<nv::Frame*>(frame)->sensorid();
    // Use safe strncpy with explicit bounds checking
    char* cstr = (char*)calloc(id.size() + 1, sizeof(char));
    if (cstr)
    {
        strncpy(cstr, id.c_str(), id.size());
        cstr[id.size()] = '\0';  // Guarantee null termination
    }
    return cstr;  // Will be nullptr if allocation failed
}

void nv_frame_destroy(void* frame) {
    delete static_cast<nv::Frame*>(frame);
}

// Get timestamp in epoch ms, returns 0 if not present
int64_t nv_frame_get_timestamp_ms(void* frame) {
    nv::Frame* f = static_cast<nv::Frame*>(frame);
    if (!f->has_timestamp()) return 0;
    const google::protobuf::Timestamp& ts = f->timestamp();
    return ts.seconds() * 1000LL + ts.nanos() / 1000000LL;
}

// Get number of objects
int nv_frame_get_object_count(void* frame) {
    return static_cast<nv::Frame*>(frame)->objects_size();
}

// Get pointer to object at index
void* nv_frame_get_object(void* frame, int idx) {
    nv::Frame* f = static_cast<nv::Frame*>(frame);
    if (idx < 0 || idx >= f->objects_size()) return nullptr;
    return (void*)&(f->objects(idx));
}

// Object-level getters
const char* nv_object_get_id(void* obj) {
    std::string id = static_cast<nv::Object*>(obj)->id();
    // Use safe strncpy with explicit bounds checking
    char* cstr = (char*)calloc(id.size() + 1, sizeof(char));
    if (cstr)
    {
        strncpy(cstr, id.c_str(), id.size());
        cstr[id.size()] = '\0';  // Guarantee null termination
    }
    return cstr;  // Will be nullptr if allocation failed
}
const char* nv_object_get_type(void* obj) {
    std::string type = static_cast<nv::Object*>(obj)->type();
    // Use safe strncpy with explicit bounds checking
    char* cstr = (char*)calloc(type.size() + 1, sizeof(char));
    if (cstr)
    {
        strncpy(cstr, type.c_str(), type.size());
        cstr[type.size()] = '\0';  // Guarantee null termination
    }
    return cstr;  // Will be nullptr if allocation failed
}
float nv_object_get_confidence(void* obj) {
    return static_cast<nv::Object*>(obj)->confidence();
}
// 3D bbox
bool nv_object_has_bbox3d(void* obj) {
    return static_cast<nv::Object*>(obj)->has_bbox3d();
}
int nv_object_get_bbox3d_coordinates(void* obj, double* coords, int max_coords) {
    const nv::Bbox3d& bbox3d = static_cast<nv::Object*>(obj)->bbox3d();
    int n = bbox3d.coordinates_size();
    int count = (n < max_coords) ? n : max_coords;
    for (int i = 0; i < count; ++i) coords[i] = bbox3d.coordinates(i);
    return count;
}
float nv_object_get_bbox3d_confidence(void* obj) {
    return static_cast<nv::Object*>(obj)->bbox3d().confidence();
}

// Pose
bool nv_object_has_pose(void* obj) {
    return static_cast<nv::Object*>(obj)->has_pose();
}
char* nv_object_get_pose_type(void* obj) {
    std::string type = static_cast<nv::Object*>(obj)->pose().type();
    // Use safe strncpy with explicit bounds checking
    char* cstr = (char*)calloc(type.size() + 1, sizeof(char));
    if (cstr)
    {
        strncpy(cstr, type.c_str(), type.size());
        cstr[type.size()] = '\0';  // Guarantee null termination
    }
    return cstr;  // Will be nullptr if allocation failed
}
int nv_object_get_pose_keypoints_count(void* obj) {
    return static_cast<nv::Object*>(obj)->pose().keypoints_size();
}
void nv_object_get_pose_keypoint_coordinates(void* obj, int idx, float* coordinates, int max_coords) {
    const nv::Pose::Keypoint& keypoint = static_cast<nv::Object*>(obj)->pose().keypoints(idx);
    for (int i = 0; i < std::min(keypoint.coordinates_size(), max_coords); i++) {
        coordinates[i] = keypoint.coordinates(i);
    }
}
void nv_object_get_pose_keypoint_quaternion(void* obj, int idx, float* quaternion, int max_quats) {
    const nv::Pose::Keypoint& keypoint = static_cast<nv::Object*>(obj)->pose().keypoints(idx);
    for (int i = 0; i < std::min(keypoint.quaternion_size(), max_quats); i++) {
        quaternion[i] = keypoint.quaternion(i);
    }
}
int nv_object_get_pose_actions_count(void* obj) {
    return static_cast<nv::Object*>(obj)->pose().actions_size();
}
char* nv_object_get_pose_action_type(void* obj, int idx) {
    std::string type = static_cast<nv::Object*>(obj)->pose().actions(idx).type();
    // Use safe strncpy with explicit bounds checking
    char* cstr = (char*)calloc(type.size() + 1, sizeof(char));
    if (cstr)
    {
        strncpy(cstr, type.c_str(), type.size());
        cstr[type.size()] = '\0';  // Guarantee null termination
    }
    return cstr;  // Will be nullptr if allocation failed
}
float nv_object_get_pose_action_confidence(void* obj, int idx) {
    return static_cast<nv::Object*>(obj)->pose().actions(idx).confidence();
}

int nv_frame_get_info_count(void* frame)
{
    if (!frame)
    {
        return 0;
    }
    return static_cast<nv::Frame*>(frame)->info().size();
}

int nv_frame_get_info(void* frame, char** keys, char** values, int maxEntries)
{
    if (!frame || !keys || !values || maxEntries <= 0)
    {
        return 0;
    }
    nv::Frame* f = static_cast<nv::Frame*>(frame);
    const auto& infoMap = f->info();
    if (infoMap.empty())
    {
        return 0;
    }
    int count = 0;
    for (const auto& [key, value] : infoMap)
    {
        if (count >= maxEntries)
        {
            break;
        }
        keys[count] = (char*)calloc(key.size() + 1, sizeof(char));
        values[count] = (char*)calloc(value.size() + 1, sizeof(char));
        if (keys[count] && values[count])
        {
            strncpy(keys[count], key.c_str(), key.size());
            keys[count][key.size()] = '\0';
            strncpy(values[count], value.c_str(), value.size());
            values[count][value.size()] = '\0';
            count++;
        }
        else
        {
            free(keys[count]);
            free(values[count]);
            keys[count] = nullptr;
            values[count] = nullptr;
            break;
        }
    }
    return count;
}

}
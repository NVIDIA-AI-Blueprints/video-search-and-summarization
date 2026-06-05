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

#include "ds_schema_2d.pb.h"
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
// 2D bbox
bool nv2d_object_has_bbox(void* obj) {
    return static_cast<nv::Object*>(obj)->has_bbox();
}
void nv2d_object_get_bbox(void* obj, float* leftX, float* topY, float* rightX, float* bottomY) {
    const nv::Bbox& bbox = static_cast<nv::Object*>(obj)->bbox();
    *leftX = bbox.leftx();
    *topY = bbox.topy();
    *rightX = bbox.rightx();
    *bottomY = bbox.bottomy();
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

}
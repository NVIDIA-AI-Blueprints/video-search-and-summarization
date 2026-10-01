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

#pragma once

#include "database_schema.h"

void videoRecordHelper(nv_vms::VideoRecordDBColumns &row, std::unordered_map<std::string, std::string> &entries);
void sensorStreamHelper(nv_vms::SensorStreamsDBColumns &row, std::unordered_map<std::string, std::string> &entries);

// Helper function to validate sensor streams table property names and prevent SQL injection
std::string validateSensorStreamTableProperty(const std::string& property);
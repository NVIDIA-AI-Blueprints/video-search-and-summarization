/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include <dlfcn.h>
#include <vector>
#include <map>

#include "sensor_control_adaptor.h"
#include "sensor_discovery_adaptor.h"
#include "vms_media_interface.h"

using namespace std;

namespace nv_vms {

class AdaptorLoader
{
public:
    AdaptorLoader();
    ~AdaptorLoader();
    std::shared_ptr<DeviceManager> loadAdaptor(ModuleId module_id = ModuleAll);
private:
    int loadControlAdaptorLibrary(const string& path, ISensorControlInterface** object, void** delObject);
    int loadDiscoveryAdaptorLibrary(const string& path, ISensorDiscoveryInterface** object, void** delObject);
    int loadSensorControlAdaptorLibrary(const string& path, ISensorControlInterface** object, void** delObject);
private:
    vector <destroyControlObject_t> m_adaptorDistructorList;
    vector <void*> m_libs;
};

} //nv_vms



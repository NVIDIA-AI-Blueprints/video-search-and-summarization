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

#include "adaptor_loader.h"
#include "utils.h"

#include <stdlib.h>
#include <stdio.h>
#include <algorithm>
#include <linux/limits.h>
#include <assert.h>
#include <memory>
#include "logger.h"

constexpr const char* MICROSERVICE_DEFAULT_ADAPTOR_NAME = "microservice";
constexpr const char* MICROSERVICE_DEFAULT_ID = "640c3667-81e6-460f-b926-b8008a130dbd";

using namespace nv_vms;

static void writeToFile(const string data)
{
    string file_path = VmsConfigManager::getInstance()->getWebRootPath() + "/config.js";
    std::ofstream fileStream;
    fileStream.open(file_path, std::ofstream::out  | std::ios::trunc);
    LOG(verbose) << "Opening adaptor file " << file_path << endl;
    if (fileStream.fail() == false)
    {
        fileStream << string("window.adaptorType = \"") +  data + string("\"");
        fileStream.close();
    }
    else
    {
        LOG(error) << "Failed to open adaptor file " << file_path << endl;
    }
}

static void* loadLibrary(const string& path)
{
    string lib_path(path);
    replaceString(lib_path, "arch", ARCH);
    char resolved_path[PATH_MAX];
    void* lib_handle = nullptr;
    char *res = realpath(lib_path.c_str(), resolved_path);
    if (res)
    {
        LOG(verbose) << resolved_path << endl;
        // load the triangle library
        lib_handle = dlopen(resolved_path, RTLD_NOW);
    }
    if (!lib_handle)
    {
        LOG(error) << "Cannot load library: " << path << " " << dlerror() << '\n';
        return nullptr;
    }

    // reset errors
    dlerror();
    return lib_handle;
}

AdaptorLoader::AdaptorLoader()
{
}

AdaptorLoader::~AdaptorLoader()
{
#if 0
    vector<destroyControlObject_t*>::iterator it;
    for (it = m_adaptorDistructorList.begin(); it != m_adaptorDistructorList.end(); it++)
    {
        destroyControlObject_t* destroyObject = *it;
        if(destroyObject)
        {
            destroyObject();
        }
    }
#endif
    for (auto lib_handle : m_libs)
    {
        dlclose(lib_handle);
    }
    m_libs.clear();
}


std::shared_ptr<DeviceManager> AdaptorLoader::loadAdaptor(ModuleId module_id)
{
    std::shared_ptr<DeviceManager> deviceManager;
    Json::Value info_;
    if (module_id == ModuleAll || module_id == ModuleSensorManagement)
    {
        Json::Value config = getAdaptorInfo();
        auto server_array = config["vst"];

        string adaptor_str;
        char *env = getenv("ADAPTOR");
        if (env != nullptr)
        {
            adaptor_str = string(env);
        }

        /* Find-out which vst adaptor to use */
        unsigned int adaptor_index_to_use = 0;
        if (adaptor_str.empty() == false)
        {
            for (unsigned int i = 0; i < server_array.size(); i++)
            {
                Json::Value info = server_array[i];
                string adaptor_name = info.get("name", "").asString();
                if (adaptor_name == adaptor_str)
                {
                    adaptor_index_to_use = i;
                    break;
                }
            }
        }
        else
        {
            for (unsigned int i = 0; i < server_array.size(); i++)
            {
                Json::Value info = server_array[i];
                bool enabled = false;
                enabled = info.get("enabled", false).asBool();
                if (enabled)
                {
                    adaptor_index_to_use = i;
                    break;
                }
            }
        }

        info_ = server_array[adaptor_index_to_use];

        string recording_str;
        env = getenv("NEED_RECORDING");
        if (env != nullptr)
        {
            recording_str = string(env);

            if (recording_str.size() > 0)
            {
                if (recording_str == "true")
                {
                    info_["need_recording"] = true;
                }
                else if (recording_str == "false")
                {
                    info_["need_recording"] = false;
                }
            }
        }

        string rtspsever_str;
        env = getenv("NEED_RTSPSERVER");
        if (env != nullptr)
        {
            rtspsever_str = string(env);

            if (rtspsever_str.size() > 0)
            {
                if (rtspsever_str == "true")
                {
                    info_["need_rtsp_server"] = true;
                }
                else if (rtspsever_str == "false")
                {
                    info_["need_rtsp_server"] = false;
                }
            }
        }

        string storage_str;
        env = getenv("NEED_STORAGE");
        if (env != nullptr)
        {
            storage_str = string(env);

            if (storage_str.size() > 0)
            {
                if (storage_str == "true")
                {
                    info_["need_storage_management"] = true;
                }
                else if (storage_str == "false")
                {
                    info_["need_storage_management"] = false;
                }
            }
        }

        string stream_monitor_str;
        env = getenv("NEED_STREAM_MONITORING");
        if (env != nullptr)
        {
            stream_monitor_str = string(env);

            if (stream_monitor_str.size() > 0)
            {
                if (stream_monitor_str == "true")
                {
                    info_["need_stream_monitoring"] = true;
                }
                else if (stream_monitor_str == "false")
                {
                    info_["need_stream_monitoring"] = false;
                }
            }
        }
    }
    else
    {
        info_["name"] = MICROSERVICE_DEFAULT_ADAPTOR_NAME;
        info_["id"] = MICROSERVICE_DEFAULT_ID;
        info_["type"] = "vst";
        if (module_id == ModuleRtspServer)
        {
            info_["need_rtsp_server"] = true;
            info_["need_recording"] = false;
            info_["need_storage_management"] = false;
            info_["need_stream_monitoring"] = false;
            string stream_monitor_str;
            char *env = getenv("NEED_STREAM_MONITORING");
            if (env != nullptr)
            {
                stream_monitor_str = string(env);
                if (!stream_monitor_str.empty() && stream_monitor_str == "true")
                {
                    info_["need_stream_monitoring"] = true;
                }
            }
        }
        else if (module_id == ModuleStreamRecorder)
        {
            info_["need_rtsp_server"] = false;
            info_["need_recording"] = true;
            info_["need_storage_management"] = false;
            info_["need_stream_monitoring"] = true;
        }
        else if (module_id == ModuleStreamBridge)
        {
            info_["need_rtsp_server"] = true;
            info_["need_recording"] = true;
            info_["need_storage_management"] = true;
            info_["need_stream_monitoring"] = true;
        }
        else if (module_id == ModuleLiveStream)
        {
            info_["need_rtsp_server"] = false;
            info_["need_recording"] = false;
            info_["need_storage_management"] = false;
            info_["need_stream_monitoring"] = true;
        }
        else if (module_id == ModuleReplayStream)
        {
            info_["need_rtsp_server"] = false;
            info_["need_recording"] = false;
            info_["need_storage_management"] = false;
            info_["need_stream_monitoring"] = false;
        }
        else if (module_id == ModuleStorageManagement)
        {
            info_["need_recording"] = false;
            info_["need_rtsp_server"] = false;
            info_["need_storage_management"] = true;
            info_["need_stream_monitoring"] = false;
        }
        else if (module_id == ModuleStreamProcessing)
        {
            info_["need_rtsp_server"] = true;
            info_["need_recording"] = true;
            info_["need_storage_management"] = true;
            info_["need_stream_monitoring"] = true;
        }
    }
    std::shared_ptr<DeviceManager> device_manager = std::make_shared<DeviceManager>();
    device_manager->name = info_.get("name", "").asString();
    if (device_manager->name == VST_REMOTE)
    {
        device_manager->isRemoteDevice = true;
    }
    if (device_manager->name == VST_RTSP)
    {
        device_manager->isRtspAdaptor = true;
    }
    LOG(info) << "device_manager->name:" << device_manager->name << endl;
    // VST ID is fetched from database later. The value fetched from adaptor config is not used.
    device_manager->id = info_.get("id", "").asString();
    device_manager->ip = info_.get("ip", "").asString();
    device_manager->user = info_.get("user", "").asString();
    device_manager->password = info_.get("password","").asString();
    device_manager->port = info_.get("port", "").asString();
    device_manager->type = info_.get("type","").asString();
    device_manager->enabled = true;
    device_manager->needRtspServer = info_.get("need_rtsp_server", false).asBool();
    device_manager->needStreamMonitoring = info_.get("need_stream_monitoring", false).asBool();
    device_manager->needRecording = info_.get("need_recording", false).asBool();
    device_manager->needStorageMngt = info_.get("need_storage_management", false).asBool();
    device_manager->url = HTTP + string("://") + device_manager->ip;
    string control_adaptor_lib_path = info_.get("control_adaptor_lib_path", "").asString();
    if(control_adaptor_lib_path.empty() == false)
    {
        LOG(info) << "Loading control adaptor: " << control_adaptor_lib_path << endl;
        std::pair<ISensorControlInterface*, void*>& pair = device_manager->m_sensorControlobjectPair;
        int result = loadControlAdaptorLibrary(control_adaptor_lib_path, &pair.first, &pair.second);
        assert(result == 0);
        assert(pair.first != nullptr);
        assert(pair.second != nullptr);
        LOG(info) << "Loaded control adaptor: " << control_adaptor_lib_path << endl;
        LOG(info) << "result: " << result << endl;
    }
    string discovery_adaptor_lib_paths = info_.get("discovery_adaptor_lib_path", "").asString();
    if(discovery_adaptor_lib_paths.empty() == false)
    {
        std::vector<string> list = splitString(discovery_adaptor_lib_paths, ",");
        if(list.size() > 0)
        {
            for (string path : list)
            {
                LOG(info) << "Loading Discovery adaptor: " << path << endl;

                ISensorDiscoveryInterface* discoveryObject;
                void* destroyObject;
                int ret = loadDiscoveryAdaptorLibrary(path, &discoveryObject, &destroyObject);
                if (ret == 0 && discoveryObject != nullptr && destroyObject != nullptr)
                {
                    std::pair<ISensorDiscoveryInterface*, void*> objects;
                    objects.first = discoveryObject;
                    objects.second = destroyObject;
                    device_manager->m_sensorDiscoveryObjectPairList.push_back(objects);
                }
            }
        }
    }
    device_manager->printInfo();
    deviceManager = device_manager;
    writeToFile(device_manager->type);
    return deviceManager;
}

int AdaptorLoader::loadControlAdaptorLibrary(const string& path, ISensorControlInterface** object, void** delObject)
{
    void* lib_handle = loadLibrary(path);
    if ( lib_handle == nullptr)
    {
        return -1;
    }
    // load the symbols
    createControlObject_t createObject_ = (createControlObject_t) dlsym(lib_handle, "createObject");
    const char* dlsym_error = dlerror();
    if (dlsym_error)
    {
        LOG(error) << "Cannot load symbol create: " << dlsym_error << '\n';
        dlclose(lib_handle);
        return -1;
    }
    // create an instance of the class
    *object = createObject_();
    destroyControlObject_t destroyObject_ = (destroyControlObject_t) dlsym(lib_handle, "destroyObject");
    dlsym_error = dlerror();
    if (dlsym_error)
    {
        LOG(error) << "Cannot load symbol destroy: " << dlsym_error << '\n';
        dlclose(lib_handle);
        return -1;
    }
    *delObject = (void *)destroyObject_;
    m_libs.push_back(lib_handle);
    return 0;
}

int AdaptorLoader::loadDiscoveryAdaptorLibrary(const string& path, ISensorDiscoveryInterface** object, void** delObject)
{
    void* lib_handle = loadLibrary(path);
    if ( lib_handle == nullptr)
    {
        return -1;
    }

    // load the symbols
    createDiscoveryObject_t createObject_ = (createDiscoveryObject_t) dlsym(lib_handle, "createObject");
    const char* dlsym_error = dlerror();
    if (dlsym_error) {
        LOG(error) << "Cannot load symbol create: " << dlsym_error << '\n';
        dlclose(lib_handle);
        return -1;
    }
    // create an instance of the class
    *object = createObject_();

    destroyDiscoveryObject_t destroyObject_ = (destroyDiscoveryObject_t) dlsym(lib_handle, "destroyObject");
    dlsym_error = dlerror();
    if (dlsym_error) {
        LOG(error) << "Cannot load symbol destroy: " << dlsym_error << '\n';
        dlclose(lib_handle);
        return -1;
    }
    *delObject = (void *)destroyObject_;
    m_libs.push_back(lib_handle);
    return 0;
}

int AdaptorLoader::loadSensorControlAdaptorLibrary(const string& path, ISensorControlInterface** object,
                                                   void** delObject)
{
    createControlObject_t createObject_ = nullptr;
    destroyControlObject_t destroyObject_ = nullptr;
    void* lib_handle = loadLibrary(path);
    if ( lib_handle == nullptr)
    {
        LOG(error) << "Failed to load library: " << path << endl;
        return -1;
    }

    // load the symbols
    createObject_ = (createControlObject_t) dlsym(lib_handle, "createObject");
    const char* dlsym_error = dlerror();
    if (dlsym_error)
    {
        LOG(error) << "Cannot load symbol create: " << dlsym_error << endl;
        goto handle_error;
    }
    // create an instance of the class
    *object = createObject_();

    destroyObject_ = (destroyControlObject_t) dlsym(lib_handle, "destroyObject");
    dlsym_error = dlerror();
    if (dlsym_error)
    {
        LOG(error) << "Cannot load symbol destroy: " << dlsym_error << endl;
        goto handle_error;
    }

    *delObject = (void *)destroyObject_;
    m_libs.push_back(lib_handle);
    return 0;

handle_error:
    dlclose(lib_handle);
    return -1;
}
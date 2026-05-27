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

#include "media_adaptor_loader.h"

#include "logger.h"
#include "utils.h"

#include <algorithm>
#include <iostream>
#include <linux/limits.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <string>

namespace nv_vms {

namespace {

void* loadLibrary(const std::string& path)
{
    std::string lib_path(path);
    replaceString(lib_path, "arch", ARCH);
    char resolved_path[PATH_MAX];
    void* lib_handle = nullptr;
    char* res = realpath(lib_path.c_str(), resolved_path);
    if (res)
    {
        LOG(verbose) << resolved_path << std::endl;
        lib_handle = dlopen(resolved_path, RTLD_NOW);
    }
    if (!lib_handle)
    {
        const char* dlerr = dlerror();
        LOG(error) << "Cannot load library: " << path << " Error: " << (dlerr ? dlerr : "none") << '\n';
        return nullptr;
    }

    dlerror();
    return lib_handle;
}

} // namespace

int MediaAdaptorLoader::load(const std::string& path, MediaAdaptorHandle& handle)
{
    handle = {};

    void* lib_handle = loadLibrary(path);
    if (lib_handle == nullptr)
    {
        return -1;
    }

    createMediaObject_t createObject = reinterpret_cast<createMediaObject_t>(dlsym(lib_handle, "createMediaObject"));
    const char* dlsym_error = dlerror();
    if (dlsym_error)
    {
        LOG(error) << "Cannot load symbol createMediaObject: " << dlsym_error << '\n';
        dlclose(lib_handle);
        return -1;
    }
    IMediaInterface* instance = createObject();
    if (instance == nullptr)
    {
        LOG(error) << "createMediaObject returned null for: " << path << '\n';
        dlclose(lib_handle);
        return -1;
    }

    destroyMediaObject_t destroyObject = reinterpret_cast<destroyMediaObject_t>(dlsym(lib_handle, "destroyMediaObject"));
    dlsym_error = dlerror();
    if (dlsym_error)
    {
        LOG(error) << "Cannot load symbol destroyMediaObject: " << dlsym_error << '\n';
        dlclose(lib_handle);
        return -1;
    }
    if (destroyObject == nullptr)
    {
        LOG(error) << "destroyMediaObject symbol resolved to null for: " << path << '\n';
        dlclose(lib_handle);
        return -1;
    }

    handle.instance = instance;
    handle.destroy = destroyObject;
    handle.libraryHandle = lib_handle;

    return 0;
}

} // namespace nv_vms



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

#pragma once

#include <string>
#include "vms_media_types.h"

namespace nv_vms {

class IMediaInterface
{
public:
    virtual ~IMediaInterface() = default;

    virtual int  connect(const ConnectionParams& conn) = 0;
    virtual bool isServerOnline(const std::string& url) = 0;
    virtual Capabilities getCapabilities() const = 0;

    virtual int fetchSnapshot(const SnapshotRequest& req, SnapshotResponse& out) = 0;
    virtual int fetchClip(const ClipRequest& req, ClipResponse& out) = 0;

    virtual void close() {}
};

extern "C" IMediaInterface* createMediaObject();
extern "C" void destroyMediaObject(IMediaInterface* object);

typedef IMediaInterface* (*createMediaObject_t)(void);
typedef void (*destroyMediaObject_t)(IMediaInterface*);

} // namespace nv_vms



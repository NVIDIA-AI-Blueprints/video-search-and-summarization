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

#include <memory>
#include <string>
#include <map>
#include "VodVideoSource.hh"
#include "NvMediaSource.hh"

class NvMediaSource;
class VodOverlayManager
{
public:
    VodOverlayManager(NvMediaSource* mediaSource);
    ~VodOverlayManager() = default;

    void startOverlayPipeline();
    void stopOverlayPipeline();
    void sendFrame(FrameParams& frame_params);
    std::shared_ptr<VodVideoSource> getVodVideoSource() const { return m_vodVideoSource; }
    int64_t getFirstTs();

private:
    NvMediaSource* m_mediaSource;
    std::shared_ptr<VodVideoSource> m_vodVideoSource;
};
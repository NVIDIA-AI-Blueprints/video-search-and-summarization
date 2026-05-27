/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#ifndef NV_HW_DETECTION_H
#define NV_HW_DETECTION_H

#include "logger.h"
#include "nvlibs.h"

constexpr int EXIT_GPU_NOT_FOUND = 10;
class NvHwDetection
{
public:
    static NvHwDetection *getInstance()
    {
        static NvHwDetection _instance;
        return &_instance;
    }

    bool m_useNvV4l2Enc;
    bool m_useNvV4l2Dec;

private:
    NvHwDetection()
    {
        LOG(info) << __func__ << endl;
        if (GET_CONFIG().use_software_path)
        {
            m_useNvV4l2Enc = false;
            m_useNvV4l2Dec = false;
        }
        else
        {
            m_useNvV4l2Enc = GET_CONFIG().use_software_encoder ? false : NvLibs::getInstance()->isV4l2EncPresent();
            m_useNvV4l2Dec = NvLibs::getInstance()->isV4l2DecPresent();
        }
    }

    ~NvHwDetection()
    {
        LOG(info) << __func__ << endl;
    }
};

#endif
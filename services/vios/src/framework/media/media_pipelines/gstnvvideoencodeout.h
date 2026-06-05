/*
 * SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "logger.h"

typedef struct _GstElement GstElement;

class NvVideoEncodeOut
{
    public:
        NvVideoEncodeOut();
        ~NvVideoEncodeOut();
        GstElement* create(const string& file_name = "");
    private:
        GstElement*             m_tee = nullptr;
        GstElement*             m_queue_record = nullptr;
        GstElement*             m_queue_display = nullptr;
        GstElement*             m_encoder = nullptr;
        GstElement*             m_parser2 = nullptr;
        GstElement*             m_muxer = nullptr;
        GstElement*             m_filesink = nullptr;
};
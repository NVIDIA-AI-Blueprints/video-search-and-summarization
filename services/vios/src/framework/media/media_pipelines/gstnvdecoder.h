/*
 * SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <string.h>

class GstNvDecoder
{
    public:
        GstNvDecoder () {}
        ~GstNvDecoder () {}

        virtual int create(bool blocking = false) { return 0; }
        virtual void destroy(bool expect_result) = 0;
        virtual bool pause() = 0;
        virtual string getstate() = 0;
        virtual bool isPlaying() = 0;
        virtual bool getError() = 0;
};
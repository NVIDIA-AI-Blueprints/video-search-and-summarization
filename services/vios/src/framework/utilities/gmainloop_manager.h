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

#include <iostream>
#include <glib.h>
#include "logger.h"

inline constexpr int MAX_GMAIN_LOOP_START_WAIT = 5;

class GMainLoopManager
{
public:
    GMainLoopManager()
    {
        m_loop = g_main_loop_new(nullptr, FALSE);
    }

    ~GMainLoopManager()
    {
        if (m_loop)
        {
            g_main_loop_unref(m_loop);
        }
    }

    void run()
    {
        g_main_loop_run(m_loop);
    }

    void stop()
    {
        if (m_loop)
        {
            g_main_loop_quit(m_loop);
        }
    }

    GMainLoop* getLoop() const
    {
        return m_loop;
    }

    void waitForGloopStart()
    {
        // Wait for the GMainLoop to start
        while(m_loop && (g_main_loop_is_running(m_loop) == false))
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(MAX_GMAIN_LOOP_START_WAIT));
        }
    }

private:
    GMainLoop* m_loop;
};

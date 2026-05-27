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

#include "rtspserver_utils.h"
#include "rtspservermanager.h"
#include "vstmodule.h"
#include "device_manager.h"

#include <mutex>
#include <atomic>
#include <string>
#include <vector>
#include <algorithm>
#include <filesystem>
#include <cstdlib>

namespace
{
    std::mutex g_mutex;
    std::atomic<bool> g_running{false};
    std::string g_mediaDir;

    /* Video extensions recognised when scanning the media directory. */
    static const std::vector<std::string> kVideoExtensions = {
        ".mp4", ".mkv", ".avi", ".mov", ".ts",
        ".h264", ".264", ".h265", ".265", ".hevc"
    };

    /**
     * Load the RTSP server module via ModuleLoader::initialize(ModuleRtspServer).
     * @return true if initialization returned 0, false otherwise.
     */
    bool loadRtspModule()
    {
        ModuleLoader* moduleLoader = ModuleLoader::getInstance();
        int ret = moduleLoader->initialize(ModuleRtspServer);
        return (ret == 0);
    }
}

namespace RtspServerTestUtils
{
    bool start(const std::string& mediaDir)
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_running)
            return true;

        /* Publish the video directory so DynamicRTSPServer (UNIT_TEST) can
         * resolve file paths inside lookupServerMediaSession. */
        g_mediaDir = mediaDir;
        if (!mediaDir.empty())
            setenv("GTEST_VIDEO_DIR", mediaDir.c_str(), 1);

        if (!loadRtspModule())
            return false;

        g_running = true;
        return true;
    }

    void stop()
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_running = false;
        g_mediaDir.clear();
        unsetenv("GTEST_VIDEO_DIR");
        /* No-op for module: full cleanup is test's
         * ModuleLoader::getInstance()->deInitialize() */
    }

    bool isRunning()
    {
        return g_running;
    }

    std::string getBaseUrl()
    {
        if (!g_running)
            return std::string();

        /* Get the base URL from the loaded RTSP server module */
        nv_vms::RtspServerManager* mgr = GET_RTSPSERVER();
        if (mgr)
        {
            std::string url = mgr->getRtspBaseUrl();
            /* Strip trailing '/' if present */
            if (!url.empty() && url.back() == '/')
                url.pop_back();
            return url;
        }

        return std::string();
    }

    std::string getTestStreamUrl()
    {
        std::string url;
        if (g_running)
        {
            std::vector<std::string> urls = getVideoFileUrls();
            if (!urls.empty())
                url = urls[0];
        }
        return url;
    }

    std::vector<std::string> getVideoFileUrls()
    {
        std::vector<std::string> urls;
        std::string base = getBaseUrl();
        if (base.empty() || g_mediaDir.empty())
            return urls;

        try
        {
            std::filesystem::path baseDir =
                std::filesystem::canonical(g_mediaDir);

            for (const auto& entry :
                 std::filesystem::recursive_directory_iterator(baseDir))
            {
                if (!entry.is_regular_file())
                    continue;

                std::string ext = entry.path().extension().string();
                std::transform(ext.begin(), ext.end(), ext.begin(),
                               [](unsigned char c) { return std::tolower(c); });

                if (std::find(kVideoExtensions.begin(), kVideoExtensions.end(), ext)
                    != kVideoExtensions.end())
                {
                    /* Use relative path from video base directory so the RTSP
                     * server lookup can reconstruct the full path by prepending
                     * GTEST_VIDEO_DIR.
                     * e.g. baseDir=/data/videos, file=/data/videos/sub/clip.mp4
                     *   -> relPath = "sub/clip.mp4"
                     *   -> URL     = rtsp://.../test/sub/clip.mp4
                     *   -> server  = GTEST_VIDEO_DIR + "/" + "sub/clip.mp4" */
                    std::string relPath =
                        std::filesystem::relative(entry.path(), baseDir).string();
                    urls.push_back(base + "/test/" + relPath);
                }
            }
        }
        catch (const std::exception& /* e */)
        {
            /* directory missing or not readable -- return empty list */
        }

        std::sort(urls.begin(), urls.end());
        return urls;
    }

    std::string getStreamUrlForFile(const std::string& filename)
    {
        std::string base = getBaseUrl();
        if (base.empty() || filename.empty())
            return std::string();
        return base + "/test/" + filename;
    }
}

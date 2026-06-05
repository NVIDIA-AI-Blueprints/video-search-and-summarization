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

/**
 * @file test_main.cpp
 * @brief Common main entry point for all GTest modules
 * 
 * This file contains the main() function that initializes Google Test
 * and runs all test suites. Individual test modules (storage.cpp, etc.)
 * should contain only test fixtures and test cases, not main().
 * 
 * Video file configuration (checked in this order during static init):
 *   1. --video-dir=<path> command-line option (read from /proc/self/cmdline)
 *   2. VIDEO_DIR environment variable
 *   3. Default: ./tools/data with fallback to ./tools/data/sample_10sec_h264.mp4
 *
 * Examples:
 *   ./vst_test --video-dir=./tmp_videos/ --gtest_list_tests
 *   VIDEO_DIR=./tmp_videos ./vst_test --gtest_list_tests
 */

#include "gtest/gtest.h"
#include "gmainloop_manager.h"
#include "libasync++/async++.h"
#include <iostream>
#include <vector>
#include <string>
#include <filesystem>
#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <gst/gst.h>
#include "nvbufwrapper.h"
#include "utils.h"
#include "nvhwdetection.h"
#include "utils/storage_test_utils.h"  // For StorageTestUtils::cleanTestEnvironment
#include "utils/system_stats_listener.h"

// Forward declaration (defined below).
std::vector<std::string> scanVideoFiles(const std::string& dirPath);

/**
 * @brief Parse --video-dir= from /proc/self/cmdline (Linux only).
 *
 * This allows the command-line flag to influence parameterized test
 * registration, which happens during static init -- before main().
 * /proc/self/cmdline contains null-separated argv entries and is
 * available as soon as the process image is loaded.
 *
 * @return The directory path if --video-dir= was found, empty string otherwise.
 */
static std::string parseVideoDirFromProcCmdline()
{
    std::ifstream cmdline("/proc/self/cmdline", std::ios::binary);
    if (!cmdline.is_open())
        return {};

    // /proc/self/cmdline has argv entries separated by '\0'
    std::string token;
    const std::string prefix = "--video-dir=";
    while (std::getline(cmdline, token, '\0'))
    {
        if (token.compare(0, prefix.size(), prefix) == 0)
        {
            return token.substr(prefix.size());
        }
    }
    return {};
}

// Global test configuration
namespace TestConfig
{
    std::string videoDirectory = "./tools/data";  // Default directory
    bool videoDirFromCmdline = false;  // True if --video-dir= or VIDEO_DIR was given

    /**
     * @brief Returns the canonical video file list, initializing it on first call.
     *
     * Uses the Construct-On-First-Use idiom so that INSTANTIATE_TEST_SUITE_P in
     * any translation unit can safely call this during static init regardless of
     * the order object files are linked.
     *
     * Resolution order:
     *   1. --video-dir=<path> from command line (parsed via /proc/self/cmdline)
     *   2. VIDEO_DIR environment variable
     *   3. Default directory ./tools/data
     *   4. Fallback to single file ./tools/data/sample_10sec_h264.mp4
     */
    std::vector<std::string>& getVideoFiles()
    {
        static std::vector<std::string> files = []() {
            std::vector<std::string> result;

            // 1. Check --video-dir= from command line (via /proc/self/cmdline)
            std::string cmdlineDir = parseVideoDirFromProcCmdline();
            if (!cmdlineDir.empty())
            {
                videoDirectory = cmdlineDir;
                videoDirFromCmdline = true;
                result = scanVideoFiles(cmdlineDir);
            }

            // 2. Check VIDEO_DIR environment variable
            if (result.empty() && !videoDirFromCmdline)
            {
                const char* envDir = std::getenv("VIDEO_DIR");
                if (envDir && envDir[0] != '\0')
                {
                    videoDirectory = envDir;
                    videoDirFromCmdline = true;
                    result = scanVideoFiles(envDir);
                }
            }

            // 3. Scan default directory
            if (result.empty() && !videoDirFromCmdline)
            {
                result = scanVideoFiles(videoDirectory);
            }

            // 4. Fallback: single default test file
            if (result.empty() && !videoDirFromCmdline)
            {
                std::string defaultFile = "./tools/data/sample_10sec_h264.mp4";
                if (std::filesystem::exists(defaultFile))
                {
                    result.push_back(defaultFile);
                }
            }
            return result;
        }();
        return files;
    }
    
    // Convenience alias -- kept for backward compatibility with code that reads
    // TestConfig::videoFiles directly (e.g. SetUp methods).
    // Points to the same underlying vector returned by getVideoFiles().
    std::vector<std::string>& videoFiles = getVideoFiles();
}

/**
 * @brief Scan directory for video files
 * 
 * @param dirPath Directory path to scan
 * @return Vector of video file paths
 */
std::vector<std::string> scanVideoFiles(const std::string& dirPath)
{
    std::vector<std::string> videoFiles;
    
    std::cout << "[SCAN] Scanning directory: " << dirPath << std::endl;
    
    if (!std::filesystem::exists(dirPath))
    {
        std::cout << "[SCAN] Directory does not exist: " << dirPath << std::endl;
        return videoFiles;
    }
    
    if (!std::filesystem::is_directory(dirPath))
    {
        std::cout << "[SCAN] Path is not a directory: " << dirPath << std::endl;
        return videoFiles;
    }
    
    // Supported video extensions
    std::vector<std::string> videoExtensions = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".h264", ".h265"};
    
    try
    {
        for (const auto& entry : std::filesystem::directory_iterator(dirPath))
        {
            if (entry.is_regular_file())
            {
                std::string filePath = entry.path().string();
                std::string extension = entry.path().extension().string();
                
                // Convert extension to lowercase for comparison
                std::transform(extension.begin(), extension.end(), extension.begin(),
                               [](char c) { return static_cast<char>(std::tolower(static_cast<unsigned char>(c))); });
                
                // Check if extension matches video formats
                if (std::find(videoExtensions.begin(), videoExtensions.end(), extension) != videoExtensions.end())
                {
                    videoFiles.push_back(filePath);
                    std::cout << "[SCAN]   Found: " << filePath << std::endl;
                }
            }
        }
    }
    catch (const std::exception& e)
    {
        std::cout << "[SCAN] Error scanning directory: " << e.what() << std::endl;
    }
    
    std::cout << "[SCAN] Found " << videoFiles.size() << " video file(s)" << std::endl;
    
    return videoFiles;
}

/**
 * GTest environment: cleans vst_data / vst_video directories.
 *
 * SetUp  -- wipes and recreates directories before any test runs (fresh start).
 * TearDown -- wipes directories after ALL tests have finished (final cleanup).
 *
 * Registered BEFORE GMainLoopTestEnvironment so that GTest tears them down in
 * reverse order: g_main_loop stops first, then directories are cleaned.
 */
class CleanupEnvironment : public ::testing::Environment
{
public:
    void SetUp() override
    {
        std::cout << "[ENV-CLEANUP] Initial cleanup before all tests" << std::endl;
        StorageTestUtils::cleanTestEnvironment(true);
    }
    void TearDown() override
    {
        std::cout << "[ENV-CLEANUP] Final cleanup after all tests" << std::endl;
        StorageTestUtils::cleanTestEnvironment(false);
    }
};

/** GTest environment: runs GLib main loop in a background task (same as VmsServer::startGLoop). */
class GMainLoopTestEnvironment : public ::testing::Environment
{
public:
    void SetUp() override
    {
        std::cout << "[ENV] Starting g_main_loop..." << std::endl;
        std::cout.flush();
        m_gmainLoopTask = async::spawn([this]() { m_gmainLoop.run(); });
        m_gmainLoop.waitForGloopStart();
        std::cout << "[ENV] g_main_loop is running" << std::endl;
        std::cout.flush();
    }
    void TearDown() override
    {
        m_gmainLoop.stop();
        try { m_gmainLoopTask.get(); } catch (const std::exception& e) { std::cerr << "[ENV] " << e.what() << std::endl; }
    }
private:
    GMainLoopManager m_gmainLoop;
    async::task<void> m_gmainLoopTask;
};

static void printCustomHelp()
{
    std::cout << "\nVST custom options:\n"
              << "  --video-dir=<path>   Directory containing video files for parameterized tests.\n"
              << "                       Can also be set via VIDEO_DIR env variable.\n"
              << "                       Default: ./tools/data\n"
              << "  --stats-csv          Write per-test results and system stats to CSV files:\n"
              << "                         test_results.csv  (suite, name, status, duration, failures)\n"
              << "                         system_stats.csv  (CPU, memory, GPU, I/O metrics)\n"
              << std::endl;
}

int main(int argc, char** argv)
{
    detectGPU();
    NvHwDetection::getInstance();
    NvBufWrapper::getInstance();

    bool statsCsv = false;
    bool helpRequested = false;

    for (int i = 1; i < argc; ++i)
    {
        std::string arg(argv[i]);
        if (arg == "--stats-csv")
            statsCsv = true;
        if (arg == "--help" || arg == "-h" || arg == "--gtest_help")
            helpRequested = true;
    }

    if (helpRequested)
        printCustomHelp();

    gst_init(&argc, &argv);

    ::testing::InitGoogleTest(&argc, argv);
    ::testing::AddGlobalTestEnvironment(new CleanupEnvironment());
    ::testing::AddGlobalTestEnvironment(new GMainLoopTestEnvironment());

    ::testing::TestEventListeners& listeners = ::testing::UnitTest::GetInstance()->listeners();
    listeners.Append(new SystemStatsTestListener(statsCsv));

    std::cout << "\n======================================================" << std::endl;
    std::cout << "VMS Shim Module Tests" << std::endl;
    std::cout << "======================================================" << std::endl;

    std::cout << "\n[INIT] Video directory: " << TestConfig::videoDirectory << std::endl;
    std::cout << "[INIT] Source: " << (TestConfig::videoDirFromCmdline ? "--video-dir / VIDEO_DIR" : "default") << std::endl;
    std::cout << "[INIT] Video files (" << TestConfig::videoFiles.size() << "):" << std::endl;
    for (size_t i = 0; i < TestConfig::videoFiles.size(); i++)
    {
        std::cout << "[INIT]   [" << i << "] " << TestConfig::videoFiles[i] << std::endl;
    }
    
    if (TestConfig::videoFiles.empty())
    {
        std::cout << "[INIT] No video files found -- parameterized download tests will not run." << std::endl;
    }
    
    std::cout << "======================================================\n" << std::endl;
    
    int result = RUN_ALL_TESTS();
    
    std::cout << "\n======================================================" << std::endl;
    if (result == 0)
    {
        std::cout << "SUCCESS: All tests PASSED!" << std::endl;
    }
    else
    {
        std::cout << "FAILURE: Some tests FAILED!" << std::endl;
    }
    std::cout << "======================================================\n" << std::endl;
    
    return result;
}

/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <filesystem>
#include <functional>
#include <mutex>
#include <string>
#include <system_error>

namespace nv_vms::chunk_upload
{
inline constexpr const char* DIRECTORY_NAME = ".nvstreamer-uploads";
inline constexpr auto IDLE_TIMEOUT = std::chrono::hours(24);

inline bool isValidIdentifier(const std::string& identifier)
{
    return !identifier.empty() && identifier.size() <= 128 &&
        std::all_of(identifier.begin(), identifier.end(), [](unsigned char c) {
            return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                (c >= '0' && c <= '9') || c == '-' || c == '_';
        });
}

inline std::filesystem::path directory(const std::filesystem::path& storage,
                                       const std::string& identifier)
{
    return (storage / DIRECTORY_NAME / identifier).lexically_normal();
}

inline std::mutex& sessionMutex(const std::filesystem::path& path)
{
    // A bounded set of locks avoids retaining state for every upload identifier.
    static std::array<std::mutex, 256> locks;
    return locks[std::hash<std::string>{}(path.lexically_normal().string()) % locks.size()];
}

class Activity
{
public:
    explicit Activity(const std::filesystem::path& path)
        : m_path(path), m_lock(sessionMutex(path)) {}

    ~Activity()
    {
        // Refresh inactivity after the request finishes, including a slow transfer.
        // Successful finalization has already removed the directory.
        std::error_code error;
        std::filesystem::last_write_time(m_path,
            std::filesystem::file_time_type::clock::now(), error);
    }

    Activity(const Activity&) = delete;
    Activity& operator=(const Activity&) = delete;

private:
    std::filesystem::path m_path;
    std::unique_lock<std::mutex> m_lock;
};

inline size_t cleanupAbandonedUploads(const std::filesystem::path& storage,
                                     std::error_code& error)
{
    namespace fs = std::filesystem;
    error.clear();
    const fs::path root = storage / DIRECTORY_NAME;
    std::error_code scanError;
    const auto rootStatus = fs::symlink_status(root, scanError);
    if (scanError == std::errc::no_such_file_or_directory)
    {
        return 0;
    }
    if (scanError)
    {
        error = scanError;
        return 0;
    }
    // Only scan the dedicated upload area, never media directories or symlinks.
    if (!fs::is_directory(rootStatus))
    {
        return 0;
    }

    size_t removed = 0;
    const auto cutoff = fs::file_time_type::clock::now() - IDLE_TIMEOUT;
    for (fs::directory_iterator it(root, scanError), end;
         it != end && !scanError; it.increment(scanError))
    {
        std::error_code entryError;
        const auto status = it->symlink_status(entryError);
        if (entryError)
        {
            error = entryError;
            continue;
        }
        if (!fs::is_directory(status) || !isValidIdentifier(it->path().filename().string()))
        {
            continue;
        }

        // Hold the same lock as the request through the age check and deletion.
        std::unique_lock<std::mutex> lock(sessionMutex(it->path()), std::try_to_lock);
        if (!lock.owns_lock())
        {
            continue;
        }
        const auto modified = fs::last_write_time(it->path(), entryError);
        if (!entryError && modified < cutoff)
        {
            fs::remove_all(it->path(), entryError);
            if (!entryError)
            {
                ++removed;
            }
        }
        if (entryError)
        {
            error = entryError;
        }
    }
    if (scanError)
    {
        error = scanError;
    }
    return removed;
}
}

/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fcntl.h>
#include <string>
#include <system_error>
#include <sys/file.h>
#include <unistd.h>

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

class SessionLock
{
public:
    explicit SessionLock(const std::filesystem::path& session, bool nonBlocking = false)
    {
        // These bounded lock files stay outside session directories and are never
        // unlinked by cleanup, so every process locks the same persistent inode.
        const auto lockDirectory = session.parent_path() / ".locks";
        std::filesystem::create_directories(lockDirectory);
        // FNV-1a is deterministic across processes, architectures and mount paths.
        uint64_t slot = 14695981039346656037ULL;
        for (unsigned char c : session.filename().string())
        {
            slot = (slot ^ c) * 1099511628211ULL;
        }
        const auto lockFile = lockDirectory / (std::to_string(slot % 256) + ".lock");
        m_descriptor = ::open(lockFile.c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0666);
        if (m_descriptor < 0)
        {
            throw std::system_error(errno, std::generic_category(), "Opening upload session lock");
        }
        const int operation = LOCK_EX | (nonBlocking ? LOCK_NB : 0);
        int result;
        do
        {
            result = ::flock(m_descriptor, operation);
        } while (result != 0 && errno == EINTR);
        if (result != 0)
        {
            const int error = errno;
            ::close(m_descriptor);
            m_descriptor = -1;
            if (!nonBlocking || (error != EWOULDBLOCK && error != EAGAIN))
            {
                throw std::system_error(error, std::generic_category(), "Locking upload session");
            }
        }
    }

    ~SessionLock()
    {
        if (m_descriptor >= 0)
        {
            ::close(m_descriptor);
        }
    }

    bool ownsLock() const { return m_descriptor >= 0; }
    SessionLock(const SessionLock&) = delete;
    SessionLock& operator=(const SessionLock&) = delete;

private:
    int m_descriptor = -1;
};

class Activity
{
public:
    explicit Activity(const std::filesystem::path& path)
        : m_path(path), m_lock(path) {}

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
    SessionLock m_lock;
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

        try
        {
            // Hold the shared filesystem lock through the age check and deletion.
            SessionLock lock(it->path(), true);
            if (!lock.ownsLock())
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
        }
        catch (const std::system_error& exception)
        {
            // Fail closed if the filesystem does not support locking.
            entryError = exception.code();
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

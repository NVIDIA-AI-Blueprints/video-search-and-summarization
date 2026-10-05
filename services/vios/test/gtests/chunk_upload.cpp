/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

#include "chunk_upload.h"
#include <gtest/gtest.h>
#include <cstdlib>
#include <fstream>
#include <future>
#include <poll.h>
#include <signal.h>
#include <sys/wait.h>

namespace fs = std::filesystem;
namespace upload = nv_vms::chunk_upload;

class ChunkUploadCleanupTest : public ::testing::Test
{
protected:
    void SetUp() override
    {
        std::string pattern = (fs::temp_directory_path() / "vios-chunk-upload-XXXXXX").string();
        const char* path = mkdtemp(pattern.data());
        ASSERT_NE(path, nullptr);
        m_storage = path;
    }

    void TearDown() override
    {
        std::error_code error;
        if (!m_storage.empty())
        {
            fs::remove_all(m_storage, error);
        }
    }

    fs::path createSession(const std::string& identifier, bool stale)
    {
        const auto path = upload::directory(m_storage, identifier);
        fs::create_directories(path);
        std::ofstream(path / "filepart") << "partial video";
        if (stale)
        {
            fs::last_write_time(path, fs::file_time_type::clock::now() - std::chrono::hours(25));
        }
        return path;
    }

    size_t cleanup()
    {
        std::error_code error;
        const auto removed = upload::cleanupAbandonedUploads(m_storage, error);
        EXPECT_FALSE(error) << error.message();
        return removed;
    }

    void runOtherProcess(const fs::path& path, bool graceful)
    {
        int ready[2];
        int release[2];
        ASSERT_EQ(::pipe(ready), 0);
        ASSERT_EQ(::pipe(release), 0);
        // Fork before acquiring a lock so the child does not inherit an already
        // locked file descriptor. It must coordinate using shared storage alone.
        const pid_t child = ::fork();
        ASSERT_NE(child, -1);
        if (child == 0)
        {
            ::close(ready[0]);
            ::close(release[1]);
            try
            {
                upload::Activity activity(path);
                const char signal = '1';
                if (::write(ready[1], &signal, 1) != 1)
                {
                    ::_exit(1);
                }
                char resume;
                if (::read(release[0], &resume, 1) != 1)
                {
                    ::_exit(1);
                }
                if (!graceful)
                {
                    // Simulate a crash: no Activity destructor or timestamp refresh.
                    ::_exit(0);
                }
            }
            catch (...)
            {
                ::_exit(1);
            }
            ::_exit(0);
        }

        ::close(ready[1]);
        ::close(release[0]);
        pollfd readyPoll{ready[0], POLLIN, 0};
        const int notified = ::poll(&readyPoll, 1, 5000);
        char signal = '0';
        if (notified == 1)
        {
            const auto read = ::read(ready[0], &signal, 1);
            EXPECT_EQ(read, 1);
        }
        EXPECT_EQ(signal, '1');
        if (signal == '1')
        {
            EXPECT_EQ(cleanup(), 0);
            EXPECT_TRUE(fs::exists(path / "filepart"));
            const char resume = '1';
            EXPECT_EQ(::write(release[1], &resume, 1), 1);
        }
        else
        {
            ::kill(child, SIGKILL);
        }
        ::close(ready[0]);
        ::close(release[1]);
        int status = 0;
        EXPECT_EQ(::waitpid(child, &status, 0), child);
        EXPECT_TRUE(WIFEXITED(status));
        EXPECT_EQ(WEXITSTATUS(status), 0);
    }

    fs::path m_storage;
};

TEST(ChunkUploadIdentifierTest, RejectsPathsAndAcceptsClientIdentifiers)
{
    EXPECT_TRUE(upload::isValidIdentifier("3fb47afe-3dcc-4f97-96f3-d5324f27b735"));
    EXPECT_TRUE(upload::isValidIdentifier("upload_123"));
    EXPECT_TRUE(upload::isValidIdentifier(std::string(128, 'a')));
    for (const auto& identifier : {"", ".", "..", "../media", "/media", "a/b", "a\\b"})
    {
        EXPECT_FALSE(upload::isValidIdentifier(identifier)) << identifier;
    }
    EXPECT_FALSE(upload::isValidIdentifier(std::string(129, 'a')));
}

TEST_F(ChunkUploadCleanupTest, MissingUploadAreaIsHarmless)
{
    EXPECT_EQ(cleanup(), 0);
}

TEST_F(ChunkUploadCleanupTest, RemovesStaleSessionsWithoutTouchingRecentUploadsOrMedia)
{
    const auto stale = createSession("abandoned", true);
    const auto recent = createSession("recent", false);
    const auto mediaDirectory = m_storage / "existing-media";
    fs::create_directory(mediaDirectory);
    std::ofstream(m_storage / "warehouse.mp4") << "completed video";
    std::ofstream(mediaDirectory / "clip.mp4") << "existing media";
    fs::last_write_time(mediaDirectory, fs::file_time_type::clock::now() - std::chrono::hours(25));

    EXPECT_EQ(cleanup(), 1);
    EXPECT_FALSE(fs::exists(stale));
    EXPECT_TRUE(fs::exists(recent / "filepart"));
    EXPECT_TRUE(fs::exists(m_storage / "warehouse.mp4"));
    EXPECT_TRUE(fs::exists(mediaDirectory / "clip.mp4"));
}

TEST_F(ChunkUploadCleanupTest, ActiveSlowRequestIsProtectedAndRefreshesInactivity)
{
    const auto path = createSession("slow-upload", true);
    {
        upload::Activity activity(path);
        // The scheduler runs on another thread while a request holds the lock.
        auto monitor = std::async(std::launch::async, [this]() { return cleanup(); });
        EXPECT_EQ(monitor.get(), 0);
        EXPECT_TRUE(fs::exists(path / "filepart"));
    }
    EXPECT_EQ(cleanup(), 0);
    // A subsequent interruption is eventually reclaimed.
    fs::last_write_time(path, fs::file_time_type::clock::now() - std::chrono::hours(25));
    EXPECT_EQ(cleanup(), 1);
}

TEST_F(ChunkUploadCleanupTest, IgnoresSymlinksAndFilesInUploadArea)
{
    const auto external = m_storage / "external";
    fs::create_directory(external);
    std::ofstream(external / "clip.mp4") << "preserve";
    fs::create_directory(m_storage / upload::DIRECTORY_NAME);
    fs::create_directory_symlink(external, upload::directory(m_storage, "linked-session"));
    std::ofstream(upload::directory(m_storage, "ordinary-file")) << "preserve";
    fs::last_write_time(external, fs::file_time_type::clock::now() - std::chrono::hours(25));

    EXPECT_EQ(cleanup(), 0);
    EXPECT_TRUE(fs::exists(external / "clip.mp4"));
    EXPECT_TRUE(fs::is_symlink(upload::directory(m_storage, "linked-session")));
    EXPECT_TRUE(fs::is_regular_file(upload::directory(m_storage, "ordinary-file")));
}

TEST_F(ChunkUploadCleanupTest, DoesNotFollowSymlinkedUploadRoot)
{
    const auto external = m_storage / "external";
    fs::create_directories(external / "old-directory");
    std::ofstream(external / "old-directory" / "video.mp4") << "preserve";
    fs::last_write_time(external / "old-directory",
        fs::file_time_type::clock::now() - std::chrono::hours(25));
    fs::create_directory_symlink(external, m_storage / upload::DIRECTORY_NAME);

    EXPECT_EQ(cleanup(), 0);
    EXPECT_TRUE(fs::exists(external / "old-directory" / "video.mp4"));
}

TEST_F(ChunkUploadCleanupTest, SuccessfulFinalizationDoesNotRecreateSession)
{
    const auto path = createSession("completed", false);
    {
        upload::Activity activity(path);
        fs::remove_all(path);
    }
    EXPECT_FALSE(fs::exists(path));
    EXPECT_EQ(cleanup(), 0);
}

TEST_F(ChunkUploadCleanupTest, ProtectsUploadInAnotherProcess)
{
    const auto path = createSession("shared-upload", true);
    runOtherProcess(path, true);
    EXPECT_TRUE(fs::exists(path / "filepart"));
    EXPECT_EQ(cleanup(), 0);
}

TEST_F(ChunkUploadCleanupTest, ProcessExitReleasesLockForCleanup)
{
    const auto path = createSession("crashed-upload", true);
    runOtherProcess(path, false);
    EXPECT_EQ(cleanup(), 1);
    EXPECT_FALSE(fs::exists(path));
}

TEST_F(ChunkUploadCleanupTest, LockFailurePreservesChunks)
{
    const auto path = createSession("lock-failure", true);
    std::ofstream(path.parent_path() / ".locks") << "blocks lock directory creation";
    EXPECT_THROW(upload::Activity activity(path), std::system_error);
    std::error_code error;
    EXPECT_EQ(upload::cleanupAbandonedUploads(m_storage, error), 0);
    EXPECT_TRUE(error);
    EXPECT_TRUE(fs::exists(path / "filepart"));
}

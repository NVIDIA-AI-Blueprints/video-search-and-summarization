/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

#include "chunk_upload.h"
#include <gtest/gtest.h>
#include <cstdlib>
#include <fstream>
#include <future>

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

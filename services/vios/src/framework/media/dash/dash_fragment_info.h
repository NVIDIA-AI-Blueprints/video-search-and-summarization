/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/* How much media a DASH session has actually published.
 *
 * A segment is only as long as the distance between the keyframes it was cut
 * on, so its length follows the encoder's keyframe interval and not the target
 * duration the muxer was asked for.  At one keyframe a second the segments run
 * about a second; at an interval of 250 on a 30 fps source they run over eight.
 * Anything that reasons in seconds therefore has to measure the fragments
 * rather than count them, which is what these helpers are for.  They are shared
 * by the manifest normaliser and the preroll gate so both agree on the answer.
 */

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

namespace vst::dash {

inline uint32_t readBigEndianUint32(const std::string& data, size_t offset)
{
    if (offset + 4 > data.size())
    {
        return 0;
    }
    return (static_cast<uint32_t>(static_cast<unsigned char>(data[offset])) << 24U)
           | (static_cast<uint32_t>(static_cast<unsigned char>(data[offset + 1])) << 16U)
           | (static_cast<uint32_t>(static_cast<unsigned char>(data[offset + 2])) << 8U)
           | static_cast<uint32_t>(static_cast<unsigned char>(data[offset + 3]));
}

/* The duration of one movie fragment: the traf's trun boxes, using per-sample
 * durations when they are present and the tfhd default otherwise. */
inline uint64_t trafDurationTicks(const std::string& body, size_t begin, size_t end)
{
    uint64_t total = 0;
    uint32_t defaultSampleDuration = 0;
    size_t offset = begin;
    while (offset + 8 <= end)
    {
        const uint32_t size = readBigEndianUint32(body, offset);
        if (size < 8 || offset + size > end)
        {
            break;
        }
        const std::string type = body.substr(offset + 4, 4);
        if (type == "tfhd")
        {
            const uint32_t flags = readBigEndianUint32(body, offset + 8) & 0x00FFFFFFU;
            if ((flags & 0x000008U) != 0U)
            {
                size_t field = offset + 16; // past size, type, version/flags, track_ID
                if ((flags & 0x000001U) != 0U) { field += 8; } // base_data_offset
                if ((flags & 0x000002U) != 0U) { field += 4; } // sample_description_index
                defaultSampleDuration = readBigEndianUint32(body, field);
            }
        }
        else if (type == "trun")
        {
            const uint32_t flags = readBigEndianUint32(body, offset + 8) & 0x00FFFFFFU;
            const uint32_t sampleCount = readBigEndianUint32(body, offset + 12);
            if ((flags & 0x000100U) != 0U)
            {
                // Per-sample durations are present; they are the exact answer.
                size_t field = offset + 16;
                if ((flags & 0x000001U) != 0U) { field += 4; } // data_offset
                if ((flags & 0x000004U) != 0U) { field += 4; } // first_sample_flags
                size_t stride = 4;
                if ((flags & 0x000200U) != 0U) { stride += 4; }
                if ((flags & 0x000400U) != 0U) { stride += 4; }
                if ((flags & 0x000800U) != 0U) { stride += 4; }
                for (uint32_t index = 0; index < sampleCount; ++index)
                {
                    if (field + 4 > end)
                    {
                        break;
                    }
                    total += readBigEndianUint32(body, field);
                    field += stride;
                }
            }
            else
            {
                total += static_cast<uint64_t>(defaultSampleDuration) * sampleCount;
            }
        }
        offset += size;
    }
    return total;
}

/* How much media a segment file holds.
 *
 * A segment is not one movie fragment.  The muxer emits a fragment on its own
 * cadence but can only close a segment on a keyframe, so at a keyframe interval
 * longer than that cadence one segment file carries several moof/mdat pairs -
 * nine of them on a 250 frame interval.  Reading only the first trun therefore
 * reports a ninth of the truth, which is invisible while the encoder emits a
 * keyframe every segment and badly wrong as soon as it does not.  Walk the
 * boxes and sum every fragment in the file.
 *
 * The walk is structural rather than a search for the four bytes "trun",
 * because those bytes occur freely inside mdat payload.
 */
inline uint64_t fragmentDurationTicks(const std::string& body)
{
    uint64_t total = 0;
    size_t offset = 0;
    while (offset + 8 <= body.size())
    {
        const uint32_t size = readBigEndianUint32(body, offset);
        if (size < 8 || offset + size > body.size())
        {
            break;
        }
        if (body.compare(offset + 4, 4, "moof") == 0)
        {
            // moof children: mfhd then one traf per track.
            size_t child = offset + 8;
            const size_t moofEnd = offset + size;
            while (child + 8 <= moofEnd)
            {
                const uint32_t childSize = readBigEndianUint32(body, child);
                if (childSize < 8 || child + childSize > moofEnd)
                {
                    break;
                }
                if (body.compare(child + 4, 4, "traf") == 0)
                {
                    total += trafDurationTicks(body, child + 8, child + childSize);
                }
                child += childSize;
            }
        }
        offset += size;
    }
    return total;
}

inline uint64_t readFragmentDuration(const std::filesystem::path& file)
{
    std::ifstream input(file, std::ios::binary);
    if (!input)
    {
        return 0;
    }
    const std::string body((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    return fragmentDurationTicks(body);
}

inline uint32_t mediaTimescaleIn(const std::filesystem::path& directory)
{
    // dashsink resets mp4mux per fragment, so each session's first file is a
    // self-contained initialization MP4.  Read its mdhd timescale instead of
    // assuming the H264 90kHz clock or a particular mp4mux default.
    std::ifstream input(directory / "video_0_1.mp4", std::ios::binary);
    if (!input)
    {
        return 1000;
    }
    const std::string initialization((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    const size_t mdhd = initialization.find("mdhd");
    if (mdhd == std::string::npos || mdhd + 8 > initialization.size())
    {
        return 1000;
    }
    const uint8_t version = static_cast<uint8_t>(initialization[mdhd + 4]);
    const size_t timescale = mdhd + (version == 1 ? 24 : 16);
    const uint32_t value = readBigEndianUint32(initialization, timescale);
    return value == 0 ? 1000 : value;
}

/* Seconds of media on disk, and how many fragments carry it.
 *
 * Counting fragments and multiplying by the target duration answers a different
 * question, and gets it wrong by the ratio between the keyframe interval and
 * that target: a session with a 250 frame interval publishes eight times the
 * media per fragment that the count implies.
 */
struct PublishedMedia
{
    double seconds = 0.0;
    unsigned fragments = 0;
    // The longest fragment, which is the one that ran a whole keyframe
    // interval.  An average is dragged down by the short fragment a source
    // produces when its keyframe cadence is interrupted.
    double longestSeconds = 0.0;
};

inline PublishedMedia publishedMedia(const std::filesystem::path& directory)
{
    PublishedMedia published;
    const uint32_t timescale = mediaTimescaleIn(directory);
    if (timescale == 0)
    {
        return published;
    }
    uint64_t ticks = 0;
    std::error_code ec;
    for (const std::filesystem::directory_entry& entry : std::filesystem::directory_iterator(directory, ec))
    {
        if (ec)
        {
            break;
        }
        const std::string name = entry.path().filename().string();
        if (entry.path().extension() != ".mp4" || name.rfind("video_", 0) != 0)
        {
            continue;
        }
        ++published.fragments;
        const uint64_t fragmentTicks = readFragmentDuration(entry.path());
        ticks += fragmentTicks;
        const double fragmentSeconds =
            static_cast<double>(fragmentTicks) / static_cast<double>(timescale);
        if (fragmentSeconds > published.longestSeconds)
        {
            published.longestSeconds = fragmentSeconds;
        }
    }
    published.seconds = static_cast<double>(ticks) / static_cast<double>(timescale);
    return published;
}

} // namespace vst::dash

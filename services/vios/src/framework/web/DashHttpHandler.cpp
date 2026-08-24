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

#include <ctime>
#include <iomanip>
#include <map>
#include <set>
#include <unordered_map>
#include <mutex>
#include <sstream>
#include <string_view>
#include "DashHttpHandler.h"

#include "UserAuthHandler.h"
#include "config.h"
#include "dash_session_manager.h"
#include "logger.h"

#include <array>
#include <chrono>
#include <fstream>
#include <iterator>
#include <string>
#include <thread>

namespace {

bool parsePath(std::string path, std::string& token, std::string& fileName)
{
    const size_t query = path.find('?');
    if (query != std::string::npos)
    {
        path.resize(query);
    }
    if (path.rfind("/vst/dash/", 0) == 0)
    {
        path.erase(0, 4);
    }
    constexpr const char* prefix = "/dash/";
    if (path.rfind(prefix, 0) != 0)
    {
        return false;
    }
    const size_t tokenStart = std::char_traits<char>::length(prefix);
    const size_t slash = path.find('/', tokenStart);
    if (slash == std::string::npos || slash == tokenStart || slash + 1 >= path.size())
    {
        return false;
    }
    token = path.substr(tokenStart, slash - tokenStart);
    fileName = path.substr(slash + 1);
    const auto safe = [](const std::string& value) {
        return !value.empty() && value.find("..") == std::string::npos
               && value.find('/') == std::string::npos && value.find('\\') == std::string::npos;
    };
    return safe(token) && safe(fileName);
}

void sendText(struct mg_connection* connection, int status, const char* statusText,
              const std::string& body, const char* extraHeaders = "")
{
    mg_printf(connection,
              "HTTP/1.1 %d %s\r\n"
              "Content-Type: text/plain\r\n"
              "Cache-Control: no-store\r\n"
              "%s"
              "Content-Length: %zu\r\n\r\n",
              status, statusText, extraHeaders, body.size());
    mg_write(connection, body.data(), body.size());
}

uint32_t readBigEndianUint32(const std::string& data, size_t offset)
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


// The muxer can only start a segment on a keyframe, so the segments it writes
// are not the uniform length the SegmentTemplate duration advertises: on a
// re-encoded replay stream roughly every other one holds a single frame.  The
// only trustworthy length is the one recorded in the fragment itself, so it is
// read from tfhd/trun rather than assumed.
uint64_t fragmentDurationTicks(const std::string& body)
{
    const size_t trun = body.find("trun");
    if (trun == std::string::npos || trun + 12 > body.size())
    {
        return 0;
    }
    const uint32_t trunFlags = readBigEndianUint32(body, trun + 4) & 0x00FFFFFFU;
    const uint32_t sampleCount = readBigEndianUint32(body, trun + 8);
    if (sampleCount == 0)
    {
        return 0;
    }

    if ((trunFlags & 0x000100U) != 0U)
    {
        // Per-sample durations are present; they are the exact answer.
        size_t offset = trun + 12;
        if ((trunFlags & 0x000001U) != 0U)
        {
            offset += 4; // data_offset
        }
        if ((trunFlags & 0x000004U) != 0U)
        {
            offset += 4; // first_sample_flags
        }
        size_t stride = 4;
        if ((trunFlags & 0x000200U) != 0U) { stride += 4; }
        if ((trunFlags & 0x000400U) != 0U) { stride += 4; }
        if ((trunFlags & 0x000800U) != 0U) { stride += 4; }
        uint64_t total = 0;
        for (uint32_t index = 0; index < sampleCount; ++index)
        {
            if (offset + 4 > body.size())
            {
                return 0;
            }
            total += readBigEndianUint32(body, offset);
            offset += stride;
        }
        return total;
    }

    // Otherwise every sample lasts tfhd.default_sample_duration.
    const size_t tfhd = body.find("tfhd");
    if (tfhd == std::string::npos || tfhd + 12 > body.size())
    {
        return 0;
    }
    const uint32_t tfhdFlags = readBigEndianUint32(body, tfhd + 4) & 0x00FFFFFFU;
    if ((tfhdFlags & 0x000008U) == 0U)
    {
        return 0;
    }
    size_t offset = tfhd + 12; // past version/flags and track_ID
    if ((tfhdFlags & 0x000001U) != 0U) { offset += 8; }
    if ((tfhdFlags & 0x000002U) != 0U) { offset += 4; }
    return static_cast<uint64_t>(readBigEndianUint32(body, offset)) * sampleCount;
}

uint64_t readFragmentDuration(const std::filesystem::path& file)
{
    std::ifstream input(file, std::ios::binary);
    if (!input)
    {
        return 0;
    }
    const std::string body((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    return fragmentDurationTicks(body);
}

// dashsink creates the next file before it has finished writing the current
// one.  Looking only for a moof (or for a briefly stable file size) can
// therefore serve a truncated mdat under load.  Validate the top-level ISO
// BMFF boxes instead: every sized box must be wholly present in the file, and
// a media response must contain both the movie fragment and its media data.
bool hasCompleteMediaFragment(const std::filesystem::path& file)
{
    std::ifstream input(file, std::ios::binary);
    if (!input)
    {
        return false;
    }
    const std::string body((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    bool hasMoof = false;
    bool hasMdat = false;
    size_t offset = 0;
    while (offset + 8 <= body.size())
    {
        const uint32_t size = readBigEndianUint32(body, offset);
        const std::string_view type(body.data() + offset + 4, 4);
        if (size == 0)
        {
            // A zero-sized mdat extends to EOF, so the bytes currently on disk
            // are its complete payload.  Other zero-sized boxes are not valid
            // before a media fragment in our dashsink output.
            return type == "mdat" && hasMoof;
        }
        if (size == 1 || size < 8 || static_cast<uint64_t>(offset) + size > body.size())
        {
            // dashsink's output uses 32-bit box sizes.  Treat a large-size box
            // as unavailable rather than risk serving it without its 64-bit
            // length field and payload.
            return false;
        }
        hasMoof = hasMoof || type == "moof";
        hasMdat = hasMdat || type == "mdat";
        offset += size;
    }
    return offset == body.size() && hasMoof && hasMdat;
}

// Caches the measured length of every segment a session has produced, keyed by
// its output directory.  Entries outlive the files themselves: retention
// deletes old segments, but their durations are still needed to place the
// segments that follow them on the timeline.
class SegmentDurations
{
public:
    static SegmentDurations& instance()
    {
        static SegmentDurations durations;
        return durations;
    }

    // Measures anything not seen before and returns every known segment.  Only
    // segments the muxer has finished are considered: a segment is complete
    // once its successor exists, which is when the muxer closed it.
    std::map<uint64_t, uint64_t> refresh(const std::filesystem::path& directory)
    {
        std::set<uint64_t> present;
        std::error_code ec;
        for (const auto& entry : std::filesystem::directory_iterator(directory, ec))
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
            const size_t underscore = name.rfind('_');
            const size_t extension = name.rfind(".mp4");
            if (underscore == std::string::npos || extension == std::string::npos)
            {
                continue;
            }
            try
            {
                present.insert(std::stoull(name.substr(underscore + 1, extension - underscore - 1)));
            }
            catch (const std::exception&)
            {
            }
        }

        const std::string key = directory.string();
        std::lock_guard<std::mutex> lock(m_mutex);
        std::map<uint64_t, uint64_t>& known = m_durations[key];
        for (const uint64_t number : present)
        {
            if (known.count(number) != 0 || present.count(number + 1) == 0)
            {
                continue;
            }
            const std::filesystem::path path = directory / ("video_0_" + std::to_string(number) + ".mp4");
            if (!hasCompleteMediaFragment(path))
            {
                continue;
            }
            const uint64_t ticks = readFragmentDuration(path);
            if (ticks > 0)
            {
                known[number] = ticks;
            }
        }
        return known;
    }

    // Decode time at which a segment starts: the sum of everything before it.
    // A segment whose length was never measured falls back to the nominal one
    // so a gap in the cache cannot shift the whole timeline.
    uint64_t startTicks(const std::filesystem::path& directory, uint64_t number, uint64_t nominalTicks)
    {
        const std::map<uint64_t, uint64_t> known = refresh(directory);
        uint64_t total = 0;
        for (uint64_t index = 1; index < number; ++index)
        {
            const auto entry = known.find(index);
            total += entry != known.end() ? entry->second : nominalTicks;
        }
        return total;
    }

    void forget(const std::filesystem::path& directory)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_durations.erase(directory.string());
    }

private:
    std::mutex m_mutex;
    std::unordered_map<std::string, std::map<uint64_t, uint64_t>> m_durations;
};

uint32_t mediaTimescale(const std::filesystem::path& mediaPath)
{
    // dashsink resets mp4mux per fragment, so each session's first file is a
    // self-contained initialization MP4.  Read its mdhd timescale instead of
    // assuming the H264 90kHz clock or a particular mp4mux default.
    std::ifstream input(mediaPath.parent_path() / "video_0_1.mp4", std::ios::binary);
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

bool sendFile(struct mg_connection* connection, const DashAssetResult& asset, bool initOnly)
{
    std::ifstream input(asset.path, std::ios::binary);
    if (!input)
    {
        return false;
    }
    std::string body((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    if (body.empty())
    {
        return false;
    }

    // The initialization segment must carry ftyp/moov only.  dashsink writes
    // self-initializing files, so drop everything from the first moof onwards
    // when the file is requested through the initialization URL; otherwise the
    // first second of media would live inside the init segment and every media
    // segment would sit one segment duration ahead of the timeline the MPD
    // advertises.
    if (initOnly)
    {
        // find() returns npos when the fragment has not been written yet, and
        // npos passes any >= comparison, so the result must be tested for npos
        // explicitly: erase(npos - 4) is past the end and throws.  A file that
        // holds no fragment yet is already ftyp/moov only and needs no trimming.
        const size_t moof = body.find("moof");
        if (moof != std::string::npos && moof >= 4)
        {
            body.erase(moof - 4);
        }
    }

    // dashsink resets mp4mux for every file.  Each file therefore has a
    // duplicate ftyp/moov and a tfdt starting at zero; Chrome appends only the
    // first one.  Turn every file into a plain media fragment with a decode
    // timeline that matches SegmentTemplate@startNumber=1.
    const std::string name = asset.path.filename().string();
    const size_t underscore = name.rfind('_');
    const size_t extension = name.rfind(".mp4");
    if (!initOnly && asset.mimeType == "video/mp4" && name.rfind("video_", 0) == 0
        && underscore != std::string::npos && extension != std::string::npos)
    {
        try
        {
            const uint64_t number = std::stoull(name.substr(underscore + 1, extension - underscore - 1));
            if (number >= 1)
            {
                // dashsink creates the next segment and writes its ftyp/moov
                // header up to a second before it writes the fragment, so a
                // file can legitimately exist with no moof in it yet.  find()
                // then returns npos, which compares greater than 4, and
                // erase(0, npos - 4) would drop the whole buffer and answer the
                // request with an empty 200 - the client treats that as a lost
                // second of video.  Report it as not yet available instead.
                const size_t moof = body.find("moof");
                if (moof == std::string::npos || moof < 4)
                {
                    return false;
                }
                body.erase(0, moof - 4);
                const size_t mfhd = body.find("mfhd");
                if (mfhd != std::string::npos && mfhd + 12 <= body.size())
                {
                    // mfhd.sequence_number is required to progress across
                    // media fragments; a reset mp4mux writes 1 in every
                    // independently generated file.
                    const uint32_t sequence = static_cast<uint32_t>(number);
                    for (size_t index = 0; index < 4; ++index)
                    {
                        body[mfhd + 11 - index] = static_cast<char>(sequence >> (index * 8));
                    }
                }
                const size_t tfdt = body.find("tfdt");
                if (tfdt != std::string::npos && tfdt + 12 <= body.size())
                {
                    const uint8_t version = static_cast<uint8_t>(body[tfdt + 4]);
                    // Each file restarts mp4mux at zero, so the decode time is
                    // rebuilt from the segment index.  It must scale with the
                    // configured segment duration: assuming one second here
                    // silently compresses the timeline for any other setting.
                    const uint64_t segmentTicks = static_cast<uint64_t>(
                        std::max(1, GET_CONFIG().dash_segment_duration_sec)) * mediaTimescale(asset.path);
                    // Segments are only as long as the muxer could make them,
                    // so the decode time is the sum of what came before rather
                    // than a multiple of the nominal duration; otherwise the
                    // timeline claims media the segments do not contain.
                    const uint64_t time = SegmentDurations::instance().startTicks(
                        asset.path.parent_path(), number, segmentTicks);
                    const size_t value = tfdt + 8;
                    const size_t width = version == 1 ? 8 : 4;
                    if (value + width <= body.size())
                    {
                        for (size_t index = 0; index < width; ++index)
                        {
                            body[value + width - 1 - index] = static_cast<char>(time >> (index * 8));
                        }
                    }
                }
            }
        }
        catch (const std::exception&)
        {
            return false;
        }
    }
    // Past this point the response is committed: the status line and
    // Content-Length are on the wire, so the caller must never emit a second
    // response.  A failed write can only be logged - turning it into a 404
    // would append a whole extra response to a keep-alive connection, and the
    // client would read those bytes as the reply to its next request.
    mg_printf(connection,
              "HTTP/1.1 200 OK\r\n"
              "Content-Type: %s\r\n"
              "Cache-Control: no-store, no-cache, must-revalidate\r\n"
              "Accept-Ranges: bytes\r\n"
              "Content-Length: %zu\r\n\r\n",
              asset.mimeType.c_str(), body.size());
    if (mg_write(connection, body.data(), body.size()) < 0)
    {
        LOG(error) << "DASH: short write while sending " << asset.path.filename().string();
    }
    return true;
}

void replaceAll(std::string& value, const std::string& from, const std::string& to)
{
    if (from.empty())
    {
        return;
    }
    size_t position = 0;
    while ((position = value.find(from, position)) != std::string::npos)
    {
        value.replace(position, from.size(), to);
        position += to.size();
    }
}

std::string attributeValue(const std::string& tag, const char* attribute)
{
    const std::string key = std::string(attribute) + "=\"";
    const size_t start = tag.find(key);
    if (start == std::string::npos)
    {
        return {};
    }
    const size_t valueStart = start + key.size();
    const size_t valueEnd = tag.find('"', valueStart);
    return valueEnd == std::string::npos ? std::string{} : tag.substr(valueStart, valueEnd - valueStart);
}

std::string representationIdBefore(const std::string& manifest, size_t position)
{
    const size_t representation = manifest.rfind("<Representation", position);
    if (representation == std::string::npos)
    {
        return "video_0";
    }
    const size_t end = manifest.find('>', representation);
    if (end == std::string::npos || end > position)
    {
        return "video_0";
    }
    const std::string id = attributeValue(manifest.substr(representation, end - representation + 1), "id");
    return id.empty() ? "video_0" : id;
}

void replaceAttribute(std::string& manifest, size_t begin, size_t end,
                      const char* attribute, const char* value)
{
    const std::string key = std::string(attribute) + "=\"";
    const size_t position = manifest.find(key, begin);
    if (position == std::string::npos || position >= end)
    {
        return;
    }
    const size_t valueStart = position + key.size();
    const size_t valueEnd = manifest.find('"', valueStart);
    if (valueEnd == std::string::npos || valueEnd > end)
    {
        return;
    }
    manifest.replace(valueStart, valueEnd - valueStart, value);
}

// dash.js positions the playhead at (now - availabilityStartTime - liveDelay).
// With the real start time that leaves only liveDelay seconds of media ahead of
// the playhead, so the buffer is capped by availability rather than by policy
// and any network jitter is heard as a stall.  Publishing an availability start
// that is kDashAvailabilityShiftSec later moves the playhead that much further
// behind live, which leaves a real catalogue in front of it to buffer from.
// The value is derived from the manifest's own timestamp, so every refresh
// yields the same answer and the player does not jump between live ranges.
// Together with the player's live delay this is the whole latency budget: the
// playhead sits (shift + liveDelay) behind the newest media, so that sum is
// both how much catalogue must exist before playback can start and how much
// jitter the buffer can absorb.  Six seconds here plus a five second delay
// meant no first frame until eleven seconds of media existed, whatever the
// preroll gate was set to.
// This, not the player's configured live delay, is what actually decides how
// far behind the edge the playhead sits.  Measured with no shift the player
// rode within two to three seconds of the newest segment however large a delay
// it was given, which survives a local link and vanishes on one with real round
// trip time: the buffer reaches zero and playback stalls once per segment.
// Publishing availability this much later moves the playhead back by the same
// amount and gives it a cushion that does not depend on the player honouring a
// request.
// Keep an explicit cushion behind the edge.  The initial catalogue is large
// enough to cover this shift, and without it Chrome can begin only one or two
// segments behind the live edge and drain at every segment boundary.
constexpr int kDashLiveAvailabilityShiftSec = 4;
constexpr int kDashReplayAvailabilityShiftSec = 6;

// How often the player is asked to refetch the manifest.
// Part of the floor under the live delay: a segment the player has not been
// told about yet cannot be fetched, so a slower refresh raises the minimum
// latency the player can hold.
constexpr const char* kDashManifestRefreshPeriod = "PT0.25S";

// A live manifest with no time shift buffer depth describes an availability
// window that starts when the session did and grows without bound, and a player
// joining is entitled to start anywhere in it.  Chrome starts near the beginning
// and is then as far behind live as the session is old - eighteen seconds on a
// session barely a minute in, climbing - while Edge starts near the edge and
// plays cleanly from the same manifest.  Publishing a bounded window makes the
// live edge the only sensible place to start, so every player agrees.
// Room for a playhead that has fallen behind to be rescued.  At thirty seconds
// a player that starts on a fresh session is outside the window almost at once,
// and media under a playhead outside the window is evicted, which turns a lag
// into a permanent freeze.  The segments are retained on disk regardless, so a
// longer window costs only manifest size.
constexpr int kDashTimeShiftBufferDepthSec = 90;

void setMinimumUpdatePeriod(std::string& manifest, const char* period)
{
    const std::string key = "minimumUpdatePeriod=\"";
    const size_t begin = manifest.find(key);
    if (begin == std::string::npos)
    {
        return;
    }
    const size_t valueBegin = begin + key.size();
    const size_t valueEnd = manifest.find('"', valueBegin);
    if (valueEnd == std::string::npos)
    {
        return;
    }
    manifest.replace(valueBegin, valueEnd - valueBegin, period);
}

// dashsink writes its MPD only while the packager is configured.  Its
// publishTime consequently stays at session creation even though the HTTP
// handler builds a new SegmentTimeline as additional fMP4 fragments arrive.
// A DASH client is entitled to ignore an MPD update whose publishTime has not
// advanced; Chrome/dash.js then remains on the initial three-to-four segment
// view and freezes as soon as that buffer is consumed.  This response is the
// publication point for the generated timeline, so stamp it at send time.
void updatePublishTime(std::string& manifest)
{
    const std::string key = "publishTime=\"";
    const size_t begin = manifest.find(key);
    if (begin == std::string::npos)
    {
        return;
    }
    const size_t valueBegin = begin + key.size();
    const size_t valueEnd = manifest.find('"', valueBegin);
    if (valueEnd == std::string::npos)
    {
        return;
    }
    const auto now = std::chrono::system_clock::now();
    const std::time_t seconds = std::chrono::system_clock::to_time_t(now);
    const auto milliseconds =
        std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() % 1000;
    std::tm utc{};
    if (gmtime_r(&seconds, &utc) == nullptr)
    {
        return;
    }
    char stamp[32] = {0};
    if (std::strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%S", &utc) == 0)
    {
        return;
    }
    std::ostringstream preciseStamp;
    preciseStamp << stamp << '.' << std::setfill('0') << std::setw(3) << milliseconds << 'Z';
    manifest.replace(valueBegin, valueEnd - valueBegin, preciseStamp.str());
}

bool shiftAvailabilityStart(std::string& manifest, int seconds)
{
    const std::string key = "availabilityStartTime=\"";
    const size_t begin = manifest.find(key);
    if (begin == std::string::npos)
    {
        return false;
    }
    const size_t valueBegin = begin + key.size();
    const size_t valueEnd = manifest.find('"', valueBegin);
    if (valueEnd == std::string::npos)
    {
        return false;
    }
    const std::string value = manifest.substr(valueBegin, valueEnd - valueBegin);
    std::tm tm{};
    std::istringstream parser(value);
    parser >> std::get_time(&tm, "%Y-%m-%dT%H:%M:%S");
    if (parser.fail())
    {
        return false;
    }
    const std::time_t shifted = timegm(&tm) + seconds;
    std::tm out{};
    if (gmtime_r(&shifted, &out) == nullptr)
    {
        return false;
    }
    char buffer[32] = {0};
    if (std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &out) == 0)
    {
        return false;
    }
    manifest.replace(valueBegin, valueEnd - valueBegin, buffer);
    return true;
}

// Without a UTCTiming element dash.js falls back to its built in clock source,
// which is the public https://time.akamai.com endpoint.  On an air gapped or
// egress filtered deployment that request hangs or fails and the player silently
// drops back to the device clock; a skewed device clock then mis-computes the
// live edge.  Publishing the server's own time removes the external dependency
// and makes every viewer agree on the same live edge.  "direct" is used rather
// than "http-head" because it needs no extra request and no HEAD handler.
void setTimeShiftBufferDepth(std::string& manifest, int seconds)
{
    if (manifest.find("timeShiftBufferDepth=") != std::string::npos)
    {
        return;
    }
    const std::string anchor = "type=\"dynamic\"";
    const size_t at = manifest.find(anchor);
    if (at == std::string::npos)
    {
        return;
    }
    const std::string attribute = " timeShiftBufferDepth=\"PT" + std::to_string(seconds) + "S\"";
    manifest.insert(at + anchor.size(), attribute);
}

void addUtcTiming(std::string& manifest)
{
    if (manifest.find("UTCTiming") != std::string::npos)
    {
        return;
    }
    const size_t close = manifest.rfind("</MPD>");
    if (close == std::string::npos)
    {
        return;
    }
    const auto now = std::chrono::system_clock::now();
    const std::time_t seconds = std::chrono::system_clock::to_time_t(now);
    const auto milliseconds =
        std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() % 1000;
    std::tm utc{};
    if (gmtime_r(&seconds, &utc) == nullptr)
    {
        return;
    }
    char stamp[32] = {0};
    if (std::strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%S", &utc) == 0)
    {
        return;
    }
    std::ostringstream element;
    element << "<UTCTiming schemeIdUri=\"urn:mpeg:dash:utc:direct:2014\" value=\"" << stamp << '.'
            << std::setfill('0') << std::setw(3) << milliseconds << "Z\"/>";
    manifest.insert(close, element.str());
}

// dashsink advertises one fixed duration for every segment.  That only holds
// when the source hands the muxer a keyframe on each boundary; a re-encoded
// stream does not, so the player waits for media the segment never contained.
// Replacing the fixed duration with the measured timeline tells the player what
// each segment really holds.
// windowTicks bounds how much of the session the timeline describes; zero keeps
// all of it.  A live manifest that advertises a thirty second window while
// listing every segment back to the start of the session contradicts itself, and
// a player is entitled to believe the listing: it then treats the whole session
// as seekable and may begin playback at the far end of it, which leaves it as
// far behind live as the session is old.
void applyMeasuredSegmentTimeline(std::string& manifest, const std::filesystem::path& directory,
                                  uint32_t timescale, uint64_t windowTicks)
{
    const std::map<uint64_t, uint64_t> segments = SegmentDurations::instance().refresh(directory);
    if (segments.empty())
    {
        return;
    }

    constexpr const char* templateTag = "<SegmentTemplate ";
    const size_t position = manifest.find(templateTag);
    if (position == std::string::npos)
    {
        return;
    }
    const size_t close = manifest.find("/>", position);
    if (close == std::string::npos)
    {
        return;
    }

    std::string tag = manifest.substr(position, close - position);
    const size_t durationAttribute = tag.find(" duration=\"");
    if (durationAttribute != std::string::npos)
    {
        const size_t valueEnd = tag.find('"', durationAttribute + 11);
        if (valueEnd != std::string::npos)
        {
            tag.erase(durationAttribute, valueEnd - durationAttribute + 1);
        }
    }
    if (tag.find("timescale=\"") == std::string::npos)
    {
        tag += " timescale=\"" + std::to_string(timescale) + "\"";
    }

    uint64_t total = 0;
    for (const auto& [number, duration] : segments)
    {
        (void)number;
        total += duration;
    }
    const uint64_t cutoff = (windowTicks > 0 && total > windowTicks) ? total - windowTicks : 0;

    std::ostringstream entries;
    uint64_t firstPublished = 0;
    uint64_t start = 0;
    uint64_t runDuration = 0;
    uint64_t runStart = 0;
    uint64_t repeats = 0;
    bool runOpen = false;
    const auto flushRun = [&entries, &runDuration, &runStart, &repeats, &runOpen]() {
        if (!runOpen)
        {
            return;
        }
        entries << "<S t=\"" << runStart << "\" d=\"" << runDuration << "\"";
        if (repeats > 0)
        {
            entries << " r=\"" << repeats << "\"";
        }
        entries << "/>\n";
        runOpen = false;
        repeats = 0;
    };
    for (const auto& [number, duration] : segments)
    {
        // Segments that have fallen out of the advertised window are still on
        // disk for a moment, but listing them invites a player to start there.
        if (start + duration <= cutoff)
        {
            start += duration;
            continue;
        }
        if (firstPublished == 0)
        {
            firstPublished = number;
        }
        // Equal length neighbours collapse into one entry with @r, which keeps
        // the manifest small across a long session.
        if (runOpen && duration == runDuration)
        {
            ++repeats;
        }
        else
        {
            flushRun();
            runStart = start;
            runDuration = duration;
            runOpen = true;
        }
        start += duration;
    }
    flushRun();

    // The listing and startNumber must name the same first segment, or the
    // player asks for one that was pruned and takes a 404 on its first fetch.
    if (firstPublished > 0)
    {
        const std::string key = "startNumber=\"";
        const size_t at = tag.find(key);
        if (at != std::string::npos)
        {
            const size_t valueBegin = at + key.size();
            const size_t valueEnd = tag.find('"', valueBegin);
            if (valueEnd != std::string::npos)
            {
                tag.replace(valueBegin, valueEnd - valueBegin, std::to_string(firstPublished));
            }
        }
    }

    std::ostringstream timeline;
    timeline << tag << ">\n<SegmentTimeline>\n" << entries.str()
             << "</SegmentTimeline>\n</SegmentTemplate>";

    manifest.replace(position, close - position + 2, timeline.str());
}


// dashsink stamps availabilityStartTime when its pipeline is constructed, but a
// session that decodes, draws an overlay and re-encodes does not produce its
// first segment until seconds later.  The player then computes a live edge
// ahead of the media that exists and sits on it with no cushion.  Anchoring
// availability to when the first segment was actually written describes what
// happened rather than what was intended, and it adapts to however long a
// particular path takes to start.
void anchorAvailabilityToFirstSegment(std::string& manifest, const std::filesystem::path& directory)
{
    std::error_code ec;
    const std::filesystem::path firstSegment = directory / "video_0_1.mp4";
    const auto written = std::filesystem::last_write_time(firstSegment, ec);
    if (ec)
    {
        return;
    }
    const auto systemTime = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
        written - std::filesystem::file_time_type::clock::now() + std::chrono::system_clock::now());
    const std::time_t seconds = std::chrono::system_clock::to_time_t(systemTime);
    std::tm utc{};
    if (gmtime_r(&seconds, &utc) == nullptr)
    {
        return;
    }
    char stamp[32] = {0};
    if (std::strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%SZ", &utc) == 0)
    {
        return;
    }
    const std::string key = "availabilityStartTime=\"";
    const size_t begin = manifest.find(key);
    if (begin == std::string::npos)
    {
        return;
    }
    const size_t valueBegin = begin + key.size();
    const size_t valueEnd = manifest.find('"', valueBegin);
    if (valueEnd == std::string::npos)
    {
        return;
    }
    manifest.replace(valueBegin, valueEnd - valueBegin, stamp);
}

void normalizeLiveManifest(std::string& manifest, const std::filesystem::path& directory,
                           bool replay)
{
    // dashsink writes self-initializing fMP4 segments but omits SegmentTemplate@initialization.
    // dash.js requires that attribute, so expose segment 1 as the initialization segment and
    // begin normal media fetching at segment 2.
    constexpr const char* templateTag = "<SegmentTemplate ";
    size_t position = 0;
    while ((position = manifest.find(templateTag, position)) != std::string::npos)
    {
        const size_t close = manifest.find("/>", position);
        if (close == std::string::npos)
        {
            break;
        }
        const size_t bodyStart = position + std::char_traits<char>::length(templateTag);
        const std::string tag = manifest.substr(position, close - position + 2);
        if (tag.find("initialization=\"") == std::string::npos)
        {
            std::string initialization = attributeValue(tag, "media");
            replaceAll(initialization, "$RepresentationID$", representationIdBefore(manifest, position));
            replaceAll(initialization, "$Number$", "init");
            if (!initialization.empty())
            {
                const std::string insertion = "initialization=\"" + initialization + "\" startNumber=\"1\" ";
                manifest.insert(bodyStart, insertion);
                position = close + insertion.size() + 2;
                continue;
            }
        }
        position = close + 2;
    }

    // A decode/draw/encode overlay path does not produce exactly one second
    // per fragment: it emits shortened and lengthened fragments at keyframe
    // boundaries.  A nominal fixed duration makes Chrome consume the fMP4
    // timestamps against the wrong availability schedule and eventually drain
    // its buffer.  Keep this a dynamic MPD, but publish its measured sliding
    // timeline so every completed live fragment has its real duration.
    const uint32_t timescale = mediaTimescale(directory / "video_0_1.mp4");
    applyMeasuredSegmentTimeline(manifest, directory, timescale,
                                 replay ? 0 : static_cast<uint64_t>(kDashTimeShiftBufferDepthSec) * timescale);

    // dashsink emits a valid dynamic MPD for live sessions.  Do not add a
    // finite/static duration to it: that makes dash.js prefetch the entire
    // growing stream as VOD instead of following the live edge.
    const bool isDynamic = manifest.find("type=\"dynamic\"") != std::string::npos;
    if (isDynamic)
    {
        anchorAvailabilityToFirstSegment(manifest, directory);
        shiftAvailabilityStart(manifest, replay ? kDashReplayAvailabilityShiftSec
                                                : kDashLiveAvailabilityShiftSec);
        // dashsink advertises a one second refresh.  The SegmentTemplate carries a
        // fixed duration, but Chrome needs an updated availability window before
        // it will request the next fMP4 fragment.  A one-second refresh races a
        // one-second segment boundary and produces periodic BUFFER_EMPTY events
        // with an overlay.  Poll at a quarter second so the next segment is
        // requested before the current one is exhausted.
        setMinimumUpdatePeriod(manifest, kDashManifestRefreshPeriod);
        updatePublishTime(manifest);
        // Live only.  A replay session publishes its whole recording window on
        // purpose, so bounding it would cut the viewer off from the start of
        // what they asked to watch.
        if (!replay)
        {
            setTimeShiftBufferDepth(manifest, kDashTimeShiftBufferDepthSec);
        }
        addUtcTiming(manifest);
        // dashsink leaves the first dynamic Period without @start.  Although
        // optional in the spec, dash.js 5 does not compose such a period into
        // a playable stream.  The appsrc timestamps are normalized to zero,
        // so PT0S is the correct timeline origin.
        const size_t period = manifest.find("<Period");
        if (period != std::string::npos)
        {
            const size_t periodEnd = manifest.find('>', period);
            if (periodEnd != std::string::npos
                && manifest.find("start=\"", period) == std::string::npos)
            {
                manifest.insert(period + std::char_traits<char>::length("<Period"), " start=\"PT0S\"");
            }
        }
        return;
    }

    const size_t mpdEnd = manifest.find('>', manifest.find("<MPD"));
    if (mpdEnd != std::string::npos)
    {
        replaceAttribute(manifest, 0, mpdEnd, "mediaPresentationDuration", "PT86400S");
    }
    const size_t period = manifest.find("<Period");
    if (period != std::string::npos)
    {
        const size_t periodEnd = manifest.find('>', period);
        if (periodEnd != std::string::npos)
        {
            replaceAttribute(manifest, period, periodEnd, "duration", "PT86400S");
        }
    }
}

bool sendManifest(struct mg_connection* connection, const DashAssetResult& asset)
{
    std::ifstream input(asset.path, std::ios::binary);
    if (!input)
    {
        return false;
    }
    std::string manifest((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    if (manifest.empty())
    {
        return false;
    }
    normalizeLiveManifest(manifest, asset.path.parent_path(), asset.replay);
    // Temporary DASH_DIAG instrumentation.  An MPD is normally fetched every
    // second, so remove this once the live-buffer investigation is complete.
    const size_t mpdBegin = manifest.find("<MPD");
    const size_t mpdEnd = mpdBegin == std::string::npos ? std::string::npos : manifest.find('>', mpdBegin);
    const std::string mpdTag = mpdEnd == std::string::npos
        ? std::string{}
        : manifest.substr(mpdBegin, mpdEnd - mpdBegin + 1);
    LOG(info) << "DASH_DIAG MPD path=" << asset.path.filename().string()
               << " replay=" << asset.replay
               << " availabilityStartTime=" << attributeValue(mpdTag, "availabilityStartTime")
               << " publishTime=" << attributeValue(mpdTag, "publishTime")
               << " suggestedPresentationDelay=" << attributeValue(mpdTag, "suggestedPresentationDelay")
               << " timeShiftBufferDepth=" << attributeValue(mpdTag, "timeShiftBufferDepth")
               << " bytes=" << manifest.size();
    mg_printf(connection,
              "HTTP/1.1 200 OK\r\n"
              "Content-Type: application/dash+xml\r\n"
              "Cache-Control: no-store, no-cache, must-revalidate\r\n"
              "Content-Length: %zu\r\n\r\n",
              manifest.size());
    // Committed response: see the note in sendFile.  A write failure is logged
    // rather than reported, so the caller never appends a second response.
    if (mg_write(connection, manifest.data(), manifest.size()) < 0)
    {
        LOG(error) << "DASH: short write while sending manifest " << asset.path.filename().string();
    }
    return true;
}

bool waitForMediaSegment(const std::filesystem::path& path)
{
    // A segment advertised while dashsink is closing must not be returned as a
    // partial fMP4: Chrome discards that append, and the next fragment becomes
    // a permanent SourceBuffer gap.  Wait for structurally complete media
    // instead of guessing from a 50 ms file-size pause.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (std::chrono::steady_clock::now() < deadline)
    {
        if (hasCompleteMediaFragment(path))
        {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    return hasCompleteMediaFragment(path);
}

} // namespace

bool DashHttpHandler::handleGet(CivetServer* /*server*/, struct mg_connection* connection)
{
    const struct mg_request_info* requestInfo = mg_get_request_info(connection);
    if (requestInfo == nullptr || requestInfo->request_uri == nullptr)
    {
        return false;
    }

    if (GET_CONFIG().use_multi_user)
    {
        Json::Value request;
        request["url"] = requestInfo->request_uri;
        if (!UserAuthHandler::isAuthorized(request, Json::Value(Json::objectValue), connection))
        {
            sendText(connection, 401, "Unauthorized", "Unauthorized");
            return true;
        }
    }

    std::string token;
    std::string fileName;
    if (!parsePath(requestInfo->request_uri, token, fileName))
    {
        sendText(connection, 400, "Bad Request", "Invalid DASH path");
        return true;
    }

    // The initialization URL has no file of its own: it is segment 1 with the
    // media fragment removed.
    bool initOnly = false;
    const std::string initSuffix = "_init.mp4";
    if (fileName.size() > initSuffix.size()
        && fileName.compare(fileName.size() - initSuffix.size(), initSuffix.size(), initSuffix) == 0)
    {
        initOnly = true;
        fileName = fileName.substr(0, fileName.size() - initSuffix.size()) + "_1.mp4";
    }

    const DashAssetResult asset = DashSessionManager::instance().resolveAsset(token, fileName);
    if (!asset.valid)
    {
        sendText(connection, 404, "Not Found", "DASH asset not found");
        return true;
    }
    if (asset.starting)
    {
        sendText(connection, 202, "Accepted", "DASH manifest is starting", "Retry-After: 1\r\n");
        return true;
    }
    const bool isManifest = asset.mimeType == "application/dash+xml";
    if (!isManifest && !waitForMediaSegment(asset.path))
    {
        sendText(connection, 404, "Not Found", "DASH asset not found");
        return true;
    }
    // sendManifest/sendFile return false only while nothing has been written,
    // so a 404 here can never follow a partially sent body.  The file must not
    // be probed again after a successful send: segment retention may delete it
    // at any moment, and a second response on a keep-alive connection desyncs
    // the byte stream for every request that follows on that socket.
    const bool served = isManifest ? sendManifest(connection, asset) : sendFile(connection, asset, initOnly);
    if (!served)
    {
        sendText(connection, 404, "Not Found", "DASH asset not found");
    }
    else if (!isManifest)
    {
        // Temporary DASH_DIAG instrumentation.  A fragment is logged only
        // after it has been completely written to the HTTP response.
        std::error_code ec;
        const uintmax_t size = std::filesystem::file_size(asset.path, ec);
        LOG(info) << "DASH_DIAG segment token=" << token
                   << " file=" << asset.path.filename().string()
                   << " initOnly=" << initOnly
                   << " durationTicks=" << (initOnly ? 0 : readFragmentDuration(asset.path))
                   << " bytes=" << (ec ? 0 : size);
    }
    return true;
}

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

#include "halo_safety.h"
#include "config.h"
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <sys/time.h>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <cerrno>

constexpr int MAX_HALO_SAFETY_DATA_SIZE = 16;
constexpr int DEFAULT_HALO_SAFETY_PORT = 12345;
constexpr const char* DEFAULT_HALO_SAFETY_PROXIMITY_CLASS = "Forklift";

constexpr int HALO_SAFETY_THREAD_SLEEP_TIME_MS = 100;
constexpr int HALO_SAFETY_COMMAND_MASK = 0x07;

constexpr int DEFAULT_FONT_SIZE_HALO_TEXT = 16;
constexpr int DEFAULT_FONT_SIZE_HALO_TEXT_YOFFSET = DEFAULT_FONT_SIZE_HALO_TEXT * 3;
constexpr float DEFAULT_FONT_SIZE_HALO_TEXT_FACTOR = 0.8f;

namespace {

/** CRC-32 (ISO 3309 / ITU-T V.42, poly 0xEDB88320) over two ranges: [0..19] and [24..63]. */
uint32_t atl_crc32_regions(const uint8_t* pkt)
{
    uint32_t crc = 0xFFFFFFFFu;
    auto feed_byte = [&crc](uint8_t b) {
        crc ^= b;
        for (int k = 0; k < 8; ++k) {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    };
    for (size_t i = 0; i < 20; ++i) {
        feed_byte(pkt[i]);
    }
    for (size_t i = 24; i < ATL_CMD_PACKET_SIZE; ++i) {
        feed_byte(pkt[i]);
    }
    return ~crc;
}

uint16_t read_le_u16(const uint8_t* p)
{
    return static_cast<uint16_t>(static_cast<unsigned>(p[0]) | (static_cast<unsigned>(p[1]) << 8));
}

uint32_t read_le_u32(const uint8_t* p)
{
    return static_cast<uint32_t>(static_cast<unsigned>(p[0]) | (static_cast<unsigned>(p[1]) << 8)
        | (static_cast<unsigned>(p[2]) << 16) | (static_cast<unsigned>(p[3]) << 24));
}

uint64_t read_le_u64(const uint8_t* p)
{
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) {
        v |= static_cast<uint64_t>(p[i]) << (8 * i);
    }
    return v;
}

void write_le_u16(uint8_t* p, uint16_t v)
{
    p[0] = static_cast<uint8_t>(v & 0xFFu);
    p[1] = static_cast<uint8_t>((v >> 8) & 0xFFu);
}

void write_le_u32(uint8_t* p, uint32_t v)
{
    p[0] = static_cast<uint8_t>(v & 0xFFu);
    p[1] = static_cast<uint8_t>((v >> 8) & 0xFFu);
    p[2] = static_cast<uint8_t>((v >> 16) & 0xFFu);
    p[3] = static_cast<uint8_t>((v >> 24) & 0xFFu);
}

void write_le_u64(uint8_t* p, uint64_t v)
{
    for (int i = 0; i < 8; ++i) {
        p[i] = static_cast<uint8_t>((v >> (8 * i)) & 0xFFu);
    }
}

bool atl_command_is_known(uint8_t cmd)
{
    return cmd == ATL_COMMAND_HEARTBEAT || cmd == ATL_COMMAND_HARDWARE_ERROR || cmd == ATL_COMMAND_MUTE
        || cmd == ATL_COMMAND_SOFTWARE_ERROR || cmd == ATL_COMMAND_UNMUTE;
}

static bool parse_metadata_object_id_u32(const std::string& s, uint32_t& out)
{
    if (s.empty()) {
        return false;
    }
    errno = 0;
    char* end = nullptr;
    const unsigned long v = std::strtoul(s.c_str(), &end, 0);
    if (errno != 0 || end == s.c_str() || *end != '\0' || v > 0xFFFFFFFFUL) {
        return false;
    }
    out = static_cast<uint32_t>(v);
    return true;
}

bool atl_packet_validate(const uint8_t* pkt, size_t len)
{
    if (len != ATL_CMD_PACKET_SIZE) {
        return false;
    }
    if (pkt[0] != ATL_PACKET_IDENTIFIER) {
        return false;
    }
    const uint32_t recv_crc = read_le_u32(pkt + 20);
    const uint32_t calc = atl_crc32_regions(pkt);
    if (calc != recv_crc) {
        return false;
    }
    if (!atl_command_is_known(pkt[3])) {
        return false;
    }
    return true;
}

} // namespace

// =============================================================================
// HaloSafetyCommandListener Implementation
// =============================================================================

HaloSafetyCommandListener::HaloSafetyCommandListener(int port)
    : m_port(port), m_socket(-1), m_running(false)
{
}

HaloSafetyCommandListener::~HaloSafetyCommandListener()
{
    try {
        stop();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~HaloSafetyCommandListener: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~HaloSafetyCommandListener" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

int HaloSafetyCommandListener::start()
{
    if (m_running)
    {
        LOG(warning) << "Halo safety listener already running" << endl;
        return 0;
    }

    stop();

    if (GET_CONFIG().halo_safety_udp_port > 0) {
        m_port = GET_CONFIG().halo_safety_udp_port;
    } else if (m_port <= 0) {
        m_port = DEFAULT_HALO_SAFETY_PORT;
    }

    m_running = true;
    try
    {
        m_thread = std::thread(&HaloSafetyCommandListener::listenerThread, this);

        std::this_thread::sleep_for(std::chrono::milliseconds(HALO_SAFETY_THREAD_SLEEP_TIME_MS));

        if (m_running)
        {
            LOG(info) << "Halo safety ATL listener started on port " << m_port << endl;
            return 0;
        }
        else
        {
            if (m_thread.joinable())
            {
                m_thread.join();
            }
            LOG(error) << "Failed to start halo safety listener" << endl;
            return -1;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception starting halo safety listener: " << e.what() << endl;
        m_running = false;
        return -1;
    }
}

void HaloSafetyCommandListener::stop()
{
    if (!m_running)
    {
        return;
    }

    LOG(info) << "Stopping halo safety listener" << endl;
    m_running = false;

    if (m_socket != -1)
    {
        shutdown(m_socket, SHUT_RDWR);
        close(m_socket);
        m_socket = -1;
    }

    if (m_thread.joinable())
    {
        try
        {
            m_thread.join();
        }
        catch (const std::exception& e)
        {
            LOG(error) << "Exception joining halo safety thread: " << e.what() << endl;
        }
    }

    {
        std::lock_guard<std::mutex> lock(m_dataMutex);
        m_latestBytes.clear();
    }

    LOG(info) << "Halo safety listener stopped" << endl;
}

bool HaloSafetyCommandListener::isRunning() const
{
    return m_running;
}

std::vector<unsigned char> HaloSafetyCommandListener::getLatestBytes()
{
    std::lock_guard<std::mutex> lock(m_dataMutex);
    return m_latestBytes;
}

void HaloSafetyCommandListener::sendAtlAcknowledgement(const struct sockaddr_in& client, uint16_t seq,
                                                       uint8_t command)
{
    if (m_socket < 0) {
        return;
    }

    uint8_t out[ATL_CMD_PACKET_SIZE];
    std::memset(out, 0, sizeof(out));
    out[0] = ATL_PACKET_IDENTIFIER;
    write_le_u16(out + 1, seq);
    out[3] = command;

    const auto now = std::chrono::system_clock::now();
    const auto epoch = now.time_since_epoch();
    const uint64_t ts_sec = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::seconds>(epoch).count());
    const uint64_t ts_us = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(epoch).count() % 1000000ULL);
    write_le_u64(out + 4, ts_sec);
    write_le_u64(out + 12, ts_us);
    /* bytes 24–63 zeroed */
    const uint32_t crc = atl_crc32_regions(out);
    write_le_u32(out + 20, crc);

    const ssize_t sent = sendto(m_socket, out, sizeof(out), 0, reinterpret_cast<const struct sockaddr*>(&client),
                                sizeof(client));
    if (sent != static_cast<ssize_t>(sizeof(out))) {
        LOG(warning) << "Halo safety ATL ACK sendto failed or partial, ret=" << sent << endl;
    } else {
        LOG(verbose) << "Halo safety ATL ACK sent seq=" << seq << " cmd=0x" << std::hex << static_cast<int>(command)
                     << std::dec << endl;
    }
}

void HaloSafetyCommandListener::listenerThread()
{
    struct sockaddr_in server_addr, client_addr;
    unsigned char buffer[ATL_CMD_PACKET_SIZE];
    socklen_t client_len;
    ssize_t received_bytes;
    char client_ip[INET_ADDRSTRLEN];

    m_socket = socket(AF_INET, SOCK_DGRAM, 0);
    if (m_socket < 0)
    {
        LOG(error) << "Halo safety socket creation failed" << endl;
        m_running = false;
        return;
    }
    LOG(info) << "Halo safety socket created" << endl;

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    server_addr.sin_port = htons(static_cast<uint16_t>(m_port));

    if (bind(m_socket, reinterpret_cast<struct sockaddr*>(&server_addr), sizeof(server_addr)) < 0)
    {
        LOG(error) << "Halo safety socket binding failed on port " << m_port << endl;
        close(m_socket);
        m_socket = -1;
        m_running = false;
        return;
    }

    LOG(info) << "Halo safety ATL listener bound on port " << m_port << endl;

    while (m_running)
    {
        client_len = sizeof(client_addr);
        received_bytes = recvfrom(m_socket, buffer, sizeof(buffer), 0,
                                  reinterpret_cast<struct sockaddr*>(&client_addr), &client_len);

        if (received_bytes < 0)
        {
            if (m_running)
            {
                LOG(error) << "Halo safety recvfrom failed" << endl;
            }
            continue;
        }

        inet_ntop(AF_INET, &client_addr.sin_addr, client_ip, sizeof(client_ip));

        if (static_cast<size_t>(received_bytes) != ATL_CMD_PACKET_SIZE) {
            LOG(verbose) << "Halo safety ATL: discard packet size " << received_bytes << " from " << client_ip << endl;
            continue;
        }

        if (!atl_packet_validate(buffer, static_cast<size_t>(received_bytes))) {
            LOG(verbose) << "Halo safety ATL: discard invalid packet from " << client_ip << endl;
            continue;
        }

        const uint16_t seq = read_le_u16(buffer + 1);
        const uint8_t cmd = buffer[3];
        sendAtlAcknowledgement(client_addr, seq, cmd);

        {
            std::lock_guard<std::mutex> lock(m_dataMutex);
            m_latestBytes.assign(buffer, buffer + received_bytes);
        }
    }

    close(m_socket);
    m_socket = -1;
    LOG(info) << "Halo safety listener thread exited" << endl;
}

// =============================================================================
// HaloSafetyManager Implementation
// =============================================================================

HaloSafetyManager::HaloSafetyManager()
{
}

HaloSafetyManager::~HaloSafetyManager()
{
}

bool HaloSafetyManager::checkHalosData(const std::string& obj_type, const std::string& proximity_class,
                                       bool& draw_halo_text, const std::string& metadata_object_id)
{
    draw_halo_text = false;
    std::string halo_safety_proximity_class = proximity_class;

    if (!halo_safety_proximity_class.empty())
    {
        if (!is_in_class_list(obj_type, halo_safety_proximity_class))
        {
            return false;
        }
    }
    else
    {
        halo_safety_proximity_class = DEFAULT_HALO_SAFETY_PROXIMITY_CLASS;
        if (!(GET_CONFIG().halo_safety_proximity_class).empty())
        {
            halo_safety_proximity_class = GET_CONFIG().halo_safety_proximity_class;
        }
        if (!is_in_class_list(obj_type, halo_safety_proximity_class))
        {
            return false;
        }
    }

    draw_halo_text = true;
    const bool active = processHaloSafetyData(HaloSafetyCommandListener::getInstance());

    uint32_t atl_ids[2] = {0, 0};
    {
        std::lock_guard<std::mutex> lock(m_dataLock);
        atl_ids[0] = m_atlPayloadObjectIds[0];
        atl_ids[1] = m_atlPayloadObjectIds[1];
    }

    const bool atl_targets_objects = (atl_ids[0] != 0u || atl_ids[1] != 0u);
    if (atl_targets_objects) {
        uint32_t oid = 0;
        if (!parse_metadata_object_id_u32(metadata_object_id, oid)) {
            draw_halo_text = false;
            return false;
        }
        const bool id_match = (atl_ids[0] != 0u && oid == atl_ids[0]) || (atl_ids[1] != 0u && oid == atl_ids[1]);
        if (!id_match) {
            draw_halo_text = false;
            return false;
        }
    }

    return active;
}

bool HaloSafetyManager::processHaloSafetyData(HaloSafetyCommandListener* networkListener)
{
    bool ret = (m_currentData.command == HALO_SAFETY_COMMAND_ACTIVE);

    if (!networkListener)
    {
        LOG(error) << "Network listener is null" << endl;
        return ret;
    }

    if (!networkListener->isRunning())
    {
        LOG(error) << "Halo safety listener is not running" << endl;
        return ret;
    }

    std::vector<unsigned char> rawBytes = networkListener->getLatestBytes();

    if (rawBytes.empty())
    {
        return ret;
    }

    HaloSafetyData decodedData;
    if (!decodeHaloSafetyData(rawBytes.data(), rawBytes.size(), decodedData))
    {
        LOG(verbose) << "Halo safety ATL decode skipped" << endl;
        return ret;
    }

    /* TODO: Currently this is WIP from PSF data. So keep code commented out for now.
     * Latest object-record IDs from wire (bytes 24 / 44); used by checkHalosData for per-object gating.
     */
#if 0
     {
        std::lock_guard<std::mutex> lock(m_dataLock);
        m_atlPayloadObjectIds[0] = decodedData.atl_object_id[0];
        m_atlPayloadObjectIds[1] = decodedData.atl_object_id[1];
    }
#endif

    if (decodedData.command == ATL_COMMAND_HEARTBEAT || decodedData.command == ATL_COMMAND_HARDWARE_ERROR
        || decodedData.command == ATL_COMMAND_SOFTWARE_ERROR)
    {
        LOG(verbose) << "Halo safety ATL: non-action command, keep prior state" << endl;
        return ret;
    }

    if (decodedData.command != ATL_COMMAND_MUTE && decodedData.command != ATL_COMMAND_UNMUTE)
    {
        LOG(verbose) << "Halo safety ATL: unknown opcode after decode" << endl;
        return ret;
    }

    std::lock_guard<std::mutex> lock(m_dataLock);
    m_currentData = decodedData;

    ret = (m_currentData.command == HALO_SAFETY_COMMAND_ACTIVE);

    return ret;
}

bool HaloSafetyManager::decodeHaloSafetyData(const unsigned char* packet, size_t packetSize, HaloSafetyData& decodedData)
{
    if (packetSize != ATL_CMD_PACKET_SIZE)
    {
        return false;
    }

    const uint8_t* pkt = reinterpret_cast<const uint8_t*>(packet);

    decodedData.command = pkt[3];
    decodedData.seq = read_le_u16(pkt + 1);
    const uint64_t ts_sec = read_le_u64(pkt + 4);
    const uint64_t ts_us = read_le_u64(pkt + 12);
    decodedData.microseconds = ts_us;
    decodedData.fullTimestamp = ts_sec * 1000000ULL + ts_us;
    decodedData.timeString.clear();
    /* Object record 0 at byte 24, object record 1 at byte 44 (packed 20-byte structs, id is first u32). */
    decodedData.atl_object_id[0] = read_le_u32(pkt + 24);
    decodedData.atl_object_id[1] = read_le_u32(pkt + 44);

    return true;
}

static OSD_ColorParams getHaloTextColor(const std::string& color)
{
    if (color == "red")
    {
        return OSD_COLOR_RED;
    }
    else if (color == "green")
    {
        return OSD_COLOR_GREEN;
    }
    else if (color == "blue")
    {
        return OSD_COLOR_BLUE;
    }
    else if (color == "yellow")
    {
        return OSD_COLOR_YELLOW;
    }
    else if (color == "black")
    {
        return OSD_COLOR_BLACK;
    }
    else if (color == "white")
    {
        return OSD_COLOR_WHITE;
    }
    else if (color == "orange")
    {
        return OSD_COLOR_ORANGE;
    }
    else
    {
        return OSD_COLOR_RED;
    }
}

void HaloSafetyManager::drawHaloText(const Point& left_top, const Point& right_bottom,
                                     const std::string& text, OsdContext_t context, GstBuffer* buffer)
{
    OSD_TextParams* text_params = (OSD_TextParams*)malloc(sizeof(OSD_TextParams));
    if (text_params != nullptr)
    {
        char* cstr = (char*)calloc(text.size() + 1, sizeof(char));
        if (cstr != nullptr)
        {
            strncpy(cstr, text.c_str(), text.size());
            cstr[text.size()] = '\0';
            text_params->text = cstr;
        }
        else
        {
            LOG(error) << "Failed to allocate memory for halo text" << endl;
            text_params->text = nullptr;
        }

        if (GET_CONFIG().halo_safety_text_size > 0)
        {
            text_params->font_size = GET_CONFIG().halo_safety_text_size;
        }
        else
        {
            text_params->font_size = DEFAULT_FONT_SIZE_HALO_TEXT;
        }

        float bbox_center_x = (left_top.x + right_bottom.x) / 2.0f;

        float text_width = text.size() * text_params->font_size * DEFAULT_FONT_SIZE_HALO_TEXT_FACTOR;

        text_params->pos_x = bbox_center_x - text_width;
        text_params->pos_y = left_top.y - DEFAULT_FONT_SIZE_HALO_TEXT_YOFFSET;
        text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

        if (text == GET_CONFIG().halo_safety_active_text || text == HALO_SAFETY_COMMAND_ACTIVE_STRING)
        {
            text_params->border_color = OSD_COLOR_RED;
            if (!GET_CONFIG().halo_safety_active_text_color.empty())
            {
                text_params->border_color = getHaloTextColor(GET_CONFIG().halo_safety_active_text_color);
            }
            text_params->bg_color = OSD_COLOR_WHITE;
            if (!GET_CONFIG().halo_safety_active_text_bg_color.empty())
            {
                text_params->bg_color = getHaloTextColor(GET_CONFIG().halo_safety_active_text_bg_color);
            }
        }
        else if (text == GET_CONFIG().halo_safety_inactive_text || text == HALO_SAFETY_COMMAND_INACTIVE_STRING)
        {
            text_params->border_color = OSD_COLOR_GREEN;
            if (!GET_CONFIG().halo_safety_inactive_text_color.empty())
            {
                text_params->border_color = getHaloTextColor(GET_CONFIG().halo_safety_inactive_text_color);
            }
            text_params->bg_color = OSD_COLOR_WHITE;
            if (!GET_CONFIG().halo_safety_inactive_text_bg_color.empty())
            {
                text_params->bg_color = getHaloTextColor(GET_CONFIG().halo_safety_inactive_text_bg_color);
            }
        }
        else
        {
            text_params->border_color = OSD_COLOR_ORANGE;
            text_params->bg_color = OSD_COLOR_WHITE;
        }

        if (buffer)
        {
            GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
        }
        else
        {
            OsdMeta meta;
            meta.meta_type = OSD_TEXT;
            meta.params = (void *)text_params;
            GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
        }
    }
    else
    {
        LOG(error) << "Failed to allocate text_params for halo text" << endl;
    }
}

HaloSafetyData HaloSafetyManager::getCurrentData()
{
    std::lock_guard<std::mutex> lock(m_dataLock);
    return m_currentData;
}

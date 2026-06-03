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

#pragma once

#include "overlay_internal.h"
#include <thread>
#include <atomic>
#include <cstdint>

struct sockaddr_in;

/** HOISA ATL (Autonomous Threshold Logic) UDP command packet — fixed 64 bytes (HOISA V1.2). */
inline constexpr unsigned int ATL_CMD_PACKET_SIZE = 64u;
inline constexpr unsigned int ATL_PACKET_IDENTIFIER = 0xA2u;

inline constexpr unsigned int ATL_COMMAND_HEARTBEAT = 0x00u;
inline constexpr unsigned int ATL_COMMAND_HARDWARE_ERROR = 0x01u;
inline constexpr unsigned int ATL_COMMAND_MUTE = 0x02u;
inline constexpr unsigned int ATL_COMMAND_SOFTWARE_ERROR = 0x03u;
inline constexpr unsigned int ATL_COMMAND_UNMUTE = 0x07u;

/** Legacy names kept for overlay logic (MUTE = suppress safety hold, UNMUTE = engage per ATL table). */
inline constexpr unsigned int HALO_SAFETY_COMMAND_ALIVE = 0x00u;
inline constexpr unsigned int HALO_SAFETY_COMMAND_ERROR = 0x03u;
inline constexpr unsigned int HALO_SAFETY_COMMAND_INACTIVE = 0x02u;
inline constexpr unsigned int HALO_SAFETY_COMMAND_ACTIVE = 0x07u;
inline constexpr const char* HALO_SAFETY_COMMAND_ACTIVE_STRING = "Standard Mode";
inline constexpr const char* HALO_SAFETY_COMMAND_INACTIVE_STRING = "Efficient Mode";

// Halo Safety Data Structures
typedef struct _HaloSafetyData {
    uint8_t command;
    uint16_t seq;
    std::string timeString;  // unused for ATL (kept for API compatibility)
    uint64_t microseconds;    // sub-second component from ATL when present
    uint64_t fullTimestamp;   // UTC epoch microseconds (ts_seconds * 1e6 + ts_microseconds)
    /** Object record 0 / 1 application ids from ATL bytes 24 and 44 (0 = slot unused / not populated). */
    uint32_t atl_object_id[2];

    _HaloSafetyData()
        : command(HALO_SAFETY_COMMAND_ACTIVE), seq(0), timeString(""), microseconds(0), fullTimestamp(0),
          atl_object_id{0, 0}
    {
    }
} HaloSafetyData;

/**
 * @class HaloSafetyCommandListener
 * @brief Manages UDP network listener for Halo Safety data packets
 *
 * This class handles the UDP socket operations and network thread for receiving
 * Halo Safety packets. It can be used independently in different contexts
 * (e.g., PeerConnection, overlay processing, etc.)
 */
class HaloSafetyCommandListener {
public:

    static HaloSafetyCommandListener* getInstance()
    {
        static HaloSafetyCommandListener _instance;
        return &_instance;
    }

    /**
     * @brief Constructor
     * @param port UDP port to listen on (default: 12345)
     */
    HaloSafetyCommandListener(int port = 12345);

    /**
     * @brief Destructor - automatically stops listener if running
     */
    ~HaloSafetyCommandListener();

    /**
     * @brief Start the UDP listener thread
     * @return 0 on success, -1 on failure
     */
    int start();

    /**
     * @brief Stop the UDP listener thread
     */
    void stop();

    /**
     * @brief Check if listener is currently running
     * @return true if running, false otherwise
     */
    bool isRunning() const;

    /**
     * @brief Get the latest received Halo Safety packet bytes
     * @return Vector of raw packet bytes (thread-safe copy)
     */
    std::vector<unsigned char> getLatestBytes();

private:
    /**
     * @brief Network listener thread function
     */
    void listenerThread();

    /** Send 64-byte ATL ACK (echo seq + command, fresh UTC timestamp, zeroed object records, new CRC). */
    void sendAtlAcknowledgement(const struct sockaddr_in& client, uint16_t seq, uint8_t command);

    int m_port;
    int m_socket;
    std::atomic<bool> m_running;
    std::thread m_thread;
    std::mutex m_dataMutex;
    std::vector<unsigned char> m_latestBytes;
};

/**
 * @class HaloSafetyManager
 * @brief Manages Halo Safety data processing, decoding, and rendering
 *
 * This class handles the business logic for Halo Safety:
 * - Decoding received packets
 * - Processing command data
 * - Drawing halo safety text overlays
 */
class HaloSafetyManager {
public:
    HaloSafetyManager();
    ~HaloSafetyManager();

    /**
     * @brief Check if halo should be drawn based on object type, proximity class, and optional ATL object_id match
     * @param obj_type Object type to check
     * @param proximity_class Proximity class filter
     * @param draw_halo_text [out] Set to true if halo text should be drawn
     * @param metadata_object_id Bbox object id; when ATL lists non-zero object_id(s), halos apply only if this
     *        id matches a populated slot (while all ATL object_id fields are zero, matching is skipped).
     * @return true if UNMUTE (safety active) applies for this object, false otherwise
     */
    bool checkHalosData(const std::string& obj_type, const std::string& proximity_class, bool& draw_halo_text,
                        const std::string& metadata_object_id);

    /**
     * @brief Process incoming Halo Safety data from network listener
     * @param networkListener Pointer to network listener instance
     * @return true if UNMUTE command is active, false otherwise
     */
    bool processHaloSafetyData(HaloSafetyCommandListener* networkListener);

    /**
     * @brief Decode raw Halo Safety packet data
     * @param packet Raw packet bytes
     * @param packetSize Size of packet in bytes
     * @param decodedData [out] Decoded data structure
     * @return true on successful decode, false on error
     */
    bool decodeHaloSafetyData(const unsigned char* packet, size_t packetSize, HaloSafetyData& decodedData);

    /**
     * @brief Draw halo safety text overlay
     * @param left_top Top-left corner of bounding box
     * @param right_bottom Bottom-right corner of bounding box
     * @param text Text to display
     * @param context OSD context
     * @param buffer GStreamer buffer (optional)
     */
    void drawHaloText(const Point& left_top, const Point& right_bottom,
                      const std::string& text, OsdContext_t context, GstBuffer* buffer);

    /**
     * @brief Get current Halo Safety data
     * @return Copy of current data (thread-safe)
     */
    HaloSafetyData getCurrentData();

private:
    std::mutex m_dataLock;
    HaloSafetyData m_currentData = HaloSafetyData();
    /** Copied from the last successfully decoded ATL packet (bytes 24 and 44). */
    uint32_t m_atlPayloadObjectIds[2] = {0, 0};
};

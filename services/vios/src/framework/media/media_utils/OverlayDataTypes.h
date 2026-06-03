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

#include <string>
#include <vector>
#include <map>
#include <atomic>
#include <mutex>
#include <queue>
#include <jsoncpp/json/json.h>

#ifdef JETSON_PLATFORM
inline constexpr int DEFAULT_BBOX_WIDTH = 2;
#else
inline constexpr int DEFAULT_BBOX_WIDTH = 1;
#endif
inline constexpr int DEFAULT_BBOX_OPACITY = 255;
inline constexpr double DEFAULT_PROXIMITY_AREA_FACTOR = 1.5;

typedef enum
{
    BBOX = 0,
    TRIPWIRE = 1,
    ROI = 2,
    OVERLAYCOUNT = 3,
    UNKNOWN = 0xFF
} OverlayTypes;

typedef enum BBoxIdPosition
{
    MIDDLE = 0,
    TOP_LEFT = 1,
    TOP_RIGHT = 2,
    BOTTOM_LEFT = 3,
    BOTTOM_RIGHT = 4,
    MAX_BBOX_ID_POSITION = 5
} BBoxIdPosition;

struct OverlayBBoxParams
{
    uint16_t                m_bboxThickness;
    uint8_t                 m_bboxOpacity;
    std::string             m_bboxColor;
    bool                    m_bboxDebug;
    bool                    m_enableBbox;
    bool                    m_enableTripwire;
    bool                    m_enableROI;
    std::vector<std::string> m_overlayIdList[OVERLAYCOUNT];
    bool                    m_enableSensorNameText;
    int                     m_sensorNameTextPosX;
    int                     m_sensorNameTextPosY;
    std::string             m_proximityClass;
    std::string             m_entrantClass;
    double                  m_proximityAreaFactor;
    std::string             m_proximityAnimation;
    std::map<std::string, std::vector<int>, std::less<>> m_colorCode;
    bool                    m_enableGodsEyeView;
    bool                    m_enablePose;
    bool                    m_enableBboxId;
    BBoxIdPosition          m_bboxIdPosition;
    std::string             m_bboxIdColor;
    std::string             m_bboxIdBgColor;
    bool                    m_enableHalos;
    std::vector<string>     m_overlayClassTypeList;

    OverlayBBoxParams() : m_bboxThickness(DEFAULT_BBOX_WIDTH)
                        , m_bboxOpacity(DEFAULT_BBOX_OPACITY)
                        , m_bboxColor("")
                        , m_bboxDebug(false)
                        , m_enableBbox(false)
                        , m_enableTripwire(false)
                        , m_enableROI(false)
                        , m_enableSensorNameText(false)
                        , m_sensorNameTextPosX(0)
                        , m_sensorNameTextPosY(0)
                        , m_proximityClass("")
                        , m_entrantClass("")
                        , m_proximityAreaFactor(DEFAULT_PROXIMITY_AREA_FACTOR)
                        , m_proximityAnimation("")
                        , m_enableGodsEyeView(false)
                        , m_enablePose(false)
                        , m_enableBboxId(false)
                        , m_bboxIdPosition(MIDDLE)
                        , m_bboxIdColor("white")
                        , m_bboxIdBgColor("black")
                        , m_enableHalos(false)
                        , m_overlayClassTypeList()
    { }

    OverlayBBoxParams(const OverlayBBoxParams& params)
    {
        this->m_bboxColor = params.m_bboxColor;
        this->m_bboxDebug = params.m_bboxDebug;
        this->m_bboxOpacity = params.m_bboxOpacity;
        this->m_bboxThickness = params.m_bboxThickness;
        this->m_enableTripwire = params.m_enableTripwire;
        this->m_enableROI = params.m_enableROI;
        this->m_enableBbox = params.m_enableBbox;
        this->m_enableSensorNameText = params.m_enableSensorNameText;
        this->m_sensorNameTextPosX = params.m_sensorNameTextPosX;
        this->m_sensorNameTextPosY = params.m_sensorNameTextPosY;
        this->m_proximityClass = params.m_proximityClass;
        this->m_entrantClass = params.m_entrantClass;
        this->m_proximityAreaFactor = params.m_proximityAreaFactor;
        this->m_proximityAnimation = params.m_proximityAnimation;
        this->m_colorCode = params.m_colorCode;
        this->m_enableGodsEyeView = params.m_enableGodsEyeView;
        this->m_enablePose = params.m_enablePose;
        this->m_enableBboxId = params.m_enableBboxId;
        this->m_bboxIdPosition = params.m_bboxIdPosition;
        this->m_bboxIdColor = params.m_bboxIdColor;
        this->m_bboxIdBgColor = params.m_bboxIdBgColor;
        this->m_enableHalos = params.m_enableHalos;
        this->m_overlayClassTypeList = params.m_overlayClassTypeList;
        for (uint32_t i = 0; i < OVERLAYCOUNT; ++i)
        {
            this->m_overlayIdList[i] = params.m_overlayIdList[i];
        }
    }
};

typedef struct _searchParams
{
    _searchParams(const std::string& startTime, const std::string& endTime
                , const std::string& sensor_id) : m_start_time(startTime)
                                            , m_end_time(endTime)
                                            , m_sensor_id(sensor_id)
                {}
    _searchParams() {}
    std::string     m_start_time;
    std::string     m_end_time;
    std::string     m_sensor_id;
    Json::UInt64    m_search_after = Json::nullValue;
    bool            m_useId = false;
} SearchParams;

typedef struct _BBoxMetaData
{
private:
    std::mutex              m_hitsLockOwned;
    std::queue<Json::Value> m_qHitsOwned;

public:
    std::mutex&             m_hitsLock;
    std::queue<Json::Value>& m_qHits;

    uint32_t                m_index {0};
    SearchParams            m_searchParams;
    std::atomic<uint16_t>   m_dataSize {0};
    std::atomic<bool>       m_searching {false};
    bool                    m_isLive = false;

    _BBoxMetaData()
        : m_hitsLock(m_hitsLockOwned)
        , m_qHits(m_qHitsOwned)
    {}

    _BBoxMetaData(std::queue<Json::Value>& hitsQueue, std::mutex& hitsMutex)
        : m_hitsLockOwned()
        , m_qHitsOwned()
        , m_hitsLock(hitsMutex)
        , m_qHits(hitsQueue)
    {}
} BBoxMetaData;
/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "logger.h"

#include <string.h>
#include <vector>
#include <mutex>
#include <queue>
#include <condition_variable>
#include <atomic>
#include <dlfcn.h>
#include <jsoncpp/json/json.h>

#include "OverlayDataTypes.h"
#include "libasync++/async++.h"
#include "mm_utils.h"
#include "syncobject.h"
#include "osd/llosd.h"
#include "cudaLoader.h"
#include "gstnvvstmeta.h"
#include "gstnvipcmeta.h"
#include "osd/gstcuosdmeta.h"
#include "MetadataStore.h"
#include "ReplayMetadataStore.h"
#include "network_utils.h"

// Forward declarations
class HaloSafetyManager;

inline constexpr int MAX_LINES    = 20;
inline constexpr int MAX_ARROWS   = 2;
inline constexpr int MAX_POINTS   = 40;
/* OSD Color Parameters */
static constexpr OSD_ColorParams OSD_COLOR_RED = {255, 0, 0, 255};
static constexpr OSD_ColorParams OSD_COLOR_GREEN = {0, 255, 0, 255};
static constexpr OSD_ColorParams OSD_COLOR_BLUE = {0, 0, 255, 255};
static constexpr OSD_ColorParams OSD_COLOR_BLACK = {0, 0, 0, 255};
static constexpr OSD_ColorParams OSD_COLOR_WHITE = {255, 255, 255, 255};
static constexpr OSD_ColorParams OSD_COLOR_YELLOW = {255, 255, 0, 255};
static constexpr OSD_ColorParams OSD_COLOR_NVGREEN = {118, 185, 0, 255};
static constexpr OSD_ColorParams OSD_COLOR_RED_TRANSPARENT = {255, 0, 0, 75};
static constexpr OSD_ColorParams OSD_COLOR_ORANGE = {204, 85, 0, 255};
static constexpr OSD_ColorParams OSD_COLOR_ORANGE_TRANSPARENT = {204, 85, 0, 75};

#define GET_OSD_INSTANCE NvOsdLibs::getInstance

typedef struct _GstElement GstElement;
typedef CuOsdMeta* (*gst_buffer_add_cu_osd_meta_t) (GstBuffer *, gint, gpointer);
typedef OsdContext_t (*osd_init_t) (bool, int);
typedef void (*osd_destroy_t) (OsdContext_t);
typedef void (*osd_add_metadata_t) (OsdContext_t, OsdMeta *);
typedef void (*osd_draw_t) (OsdContext_t, void *);
typedef void (*osd_global_init_t) ();
typedef void (*osd_global_destroy_t) ();

union GstMetaUnion
{
    GstNvVstMeta* vstMeta;
    GstNvIpcMeta* ipcMeta;
};

struct Point2D
{
    float x;
    float y;
    
    Point2D(float _x = 0, float _y = 0) 
        : x(_x), y(_y) {}
};

struct Point3D
{
    float x;
    float y;
    float z;
    
    Point3D(float _x = 0, float _y = 0, float _z = 0) 
        : x(_x), y(_y), z(_z) {}
};

struct Circle
{
    float centerX;
    float centerY;
    float radius;
};

struct ProximityState {
    vector<Point2D> corners;
    string objectId;
    string objType;
    bool isInInnerCircle = false;
    bool isInOuterCircle = false;
    float centerX = 0;
    float centerY = 0;
    float radius = 0;
    double confidence = 0.0;
    vector<string> connectedEntrants;
    vector<string> connectedProximity;
};

typedef struct _BBoxDrawingData
{
    _BBoxDrawingData() : m_timestampTolerance(0)
                       , m_searchParams{}
                       , m_overlay{}
                       , m_mismatches{}
                       , m_shifts{}
                       , m_numObjects{}
                       , m_frameSize{}
    { }
    std::queue<uint64_t>    m_frameTSQueue;
    std::mutex              m_frameTSLock;
    std::condition_variable m_frameTsQueueCond;
    uint32_t                m_timestampTolerance;
    SearchParams            m_searchParams;
    OverlayBBoxParams       m_overlay;
    vector<pair<std::string, std::string> > m_mismatches;
    vector<pair<std::string, std::string> > m_shifts;
    vector<tuple<std::string, std::string, int64_t> > m_numObjects;
    bool                    m_isLive = false;
    FrameSize               m_frameSize;
    double                  m_frameRate = 30.0;
} BBoxDrawingData;
struct Point
{
    int x;
    int y;
};

// Generic helper function to check if an object type is in a comma-separated class list
bool is_in_class_list(const string& obj_type, const string& classList);

class NvOsdLibs
{
public:
    static NvOsdLibs* getInstance();
    static void deleteInstance();

    osd_init_t osd_init;
    osd_destroy_t osd_destroy;
    osd_add_metadata_t osd_add_metadata;
    osd_draw_t osd_draw;
    osd_global_init_t osd_global_init;
    osd_global_destroy_t osd_global_destroy;
    gst_buffer_add_cu_osd_meta_t gst_buffer_add_cu_osd_meta;

    bool isError()  { return error; }

private:
    static NvOsdLibs* _instance;
    void* handle_nvCuLib = nullptr;
    void* handle_nvCuosdmetaLib = nullptr;
    bool error = false;

    NvOsdLibs();
    ~NvOsdLibs();
};

class NvLLOverlayInternal
{
    public:

        typedef struct _OverlayParams
        {
            _OverlayParams() : m_startTime("")
                             , m_endTime ("")
                             , m_sensorName("")
                             , m_frameRate(30)
            {
            }
            string m_startTime;
            string m_endTime;
            string m_sensorName;
            double m_frameRate;
            FrameSize m_frameSize;
            OverlayBBoxParams m_bboxParams;
            bool m_isLive = false;
        } OverlayParams;

        typedef struct _TripwireDetails
        {
            string id;
            string name;
            string stats;
            OSD_LineParams        *wires[MAX_LINES] = {nullptr};
            unsigned int            wires_count = 0;
            OSD_ArrowParams       *direction[MAX_ARROWS] = {nullptr};
            unsigned int            direction_count = 0;
            OSD_PointParams       *endpoints[MAX_POINTS] = {nullptr};
            unsigned int            endpoints_count = 0;
        } Tripwire;

        typedef struct _RoiDetails
        {
            string id;
            string name;
            string stats;
            OSD_LineParams        *lines[MAX_LINES] = {nullptr};
            unsigned int            lines_count = 0;
            OSD_PointParams       *endpoints[MAX_POINTS] = {nullptr};
            unsigned int            endpoints_count = 0;
        } Roi;

        typedef struct _CalibrationData
        {
            vector<vector<float>> intrinsicMatrix; // 3x3 Camera intrinsic matrix
            vector<float> proj_w2c_matrix;  // World to camera projection matrix
            vector<float> proj_w2p_matrix;  // World to pixel projection matrix
            string name;
            float scaleFactor;                   // Scale factor for coordinate conversion
            struct {
                float x;                         // X translation to global coordinates
                float y;                         // Y translation to global coordinates
            } translationToGlobalCoordinates;    // Translation to global coordinates
        } CalibrationData;

        // Map of object id to object type, corners, and confidence
        std::map<string, std::tuple<string, vector<Point2D>, double>, std::less<>> activeObjectCorners;
        std::map<std::string, ProximityState, std::less<>> proximityStates;
        std::map<std::string, ProximityState, std::less<>> entrantStates;

        NvLLOverlayInternal();
        NvLLOverlayInternal(OverlayParams& params, std::shared_ptr<IMetadataStore> metadataStore,
                        bool use_frameid = false, bool wait_for_es_query = false);
        ~NvLLOverlayInternal();

        void enableOverlay(OverlayParams& params, bool use_frameid = false, bool wait_for_es_query = false);
        void addFrameTs(int64_t ts);
        Json::Value getOverlayStatus(uint64_t firstFrameTS);
        void drawTripwire(GstBuffer* buffer = nullptr);
        void drawRoi(GstBuffer* buffer = nullptr);
        void readTripwire();
        void readRoi();
        void readCalibrationData();
        bool processOsdSinkPadBufferProbeStreamer (void* buf, GstNvVstMeta *meta);
        bool processOsdSinkPadBufferProbe (void* buf, GstMetaUnion *meta, int64_t pts = 0);
        void fetchMetadataAgain (string new_start);
        void updateIdList(std::vector<string> idList[OVERLAYCOUNT]);
        void updateClassTypeList(std::vector<string> classTypeList);
        void setBboxColor(string color) { m_bboxParams.m_overlay.m_bboxColor = color; }
        void setBboxPose(bool pose) { m_bboxParams.m_overlay.m_enablePose = pose; }
        void setBboxHalos(bool halos) { m_bboxParams.m_overlay.m_enableHalos = halos; }
        void setBboxThickness(uint16_t thickness) { m_bboxParams.m_overlay.m_bboxThickness = thickness; }
        void setBboxDebug(bool debug) { m_bboxParams.m_overlay.m_bboxDebug = debug; }
        void setBboxOpacity(uint8_t opacity) { m_bboxParams.m_overlay.m_bboxOpacity = opacity; }
        void setBboxId(bool enableBboxId) { m_bboxParams.m_overlay.m_enableBboxId = enableBboxId; }
        void setBboxIdPosition(BBoxIdPosition position) { m_bboxParams.m_overlay.m_bboxIdPosition = position; }
        void setBboxIdColor(const std::string& color) { m_bboxParams.m_overlay.m_bboxIdColor = color; }
        void setBboxIdBgColor(const std::string& color) { m_bboxParams.m_overlay.m_bboxIdBgColor = color; }
        void setProximityClass(string proximityClass) { m_bboxParams.m_overlay.m_proximityClass = proximityClass; }
        void setEntrantClass(string entrantClass) { m_bboxParams.m_overlay.m_entrantClass = entrantClass; }
        void setProximityAreaFactor(double proximityAreaFactor) { m_bboxParams.m_overlay.m_proximityAreaFactor = proximityAreaFactor; }
        void setProximityAnimation(string proximityAnimation) { m_bboxParams.m_overlay.m_proximityAnimation = proximityAnimation; }
        void setColorCode(std::map<string, vector<int>, std::less<>> colorCode) { m_bboxParams.m_overlay.m_colorCode = colorCode; }
        void setEnableGodsEyeView(bool enableGodsEyeView) { m_bboxParams.m_overlay.m_enableGodsEyeView = enableGodsEyeView; }
        void updateSourceResolution(int width, int height);
        void updateIPCStreamResolution(int width, int height);
        bool isOverlayEnabled();
        bool isBboxEnabled() { return m_enableBbox; }
        bool doDraw (void* data, GstMetaUnion *meta, int64_t pts = 0);
        void draw_bbox_cuosd(Json::Value & objects, BBoxDrawingData* box_params,
                vector<string> m_bboxList, vector<string> m_classTypeList, OsdContext_t context, GstBuffer *buffer);
        GstElement* create();
        int draw_3d_bbox(const vector<Point2D>& corners2d, const string& obj_type,
                         BBoxDrawingData* box_params, OsdContext_t context, GstBuffer* buffer,
                         const string& object_id = "",
                         const OSD_ColorParams& override_color = {0,0,0,0},
                         bool first_pass = false, double confidence = 0.0);
        void draw_bbox_id_cuosd(const Point& left_top, const Point& right_bottom,
                         const string& object_id,
                         BBoxDrawingData* box_params,
                         OsdContext_t context,
                         GstBuffer* buffer);
        void draw_bbox_id_for_3d_projected_corners(const vector<Point2D>& corners2d,
                         const string& object_id,
                         BBoxDrawingData* box_params,
                         OsdContext_t context,
                         GstBuffer* buffer);
        void draw_pose_cuosd(const std::vector<float>& keypoints,
                            const std::string& action_label,
                            BBoxDrawingData* box_params,
                            OsdContext_t context,
                            GstBuffer* buffer,
                            int x = 20, int y = 20);
        bool check_pose_data(const Json::Value& object,
                            const std::map<std::string, float, std::less<>>& coordinates,
                            std::vector<float>& keypoints,
                            std::string& action_label,
                            int& label_x,
                            int& label_y);
        Json::Value getMetadata(int64_t frameTS);
        void draw_ellipse_around_2d_bbox(const Point& left_top, const Point& right_bottom,
                            BBoxDrawingData* box_params, OsdContext_t context, GstBuffer* buffer);
    public:
        bool                    m_use_protobuf = false;
        bool                    m_useId = false;
        bool                    m_isWaitForESQuery = false;
        string                  m_sensorName = "";
        int                     m_width = WIDTH_1080p;
        int                     m_height = HEIGHT_1080p;
        GstElement*             m_filter = nullptr;
    private:
        GstElement*                 m_nvosd = nullptr;
        BBoxDrawingData             m_bboxParams;
        async::task<void>           m_elasticTask;
        std::mutex                  m_debugData;
        std::string                 m_bboxColor;
        uint16_t                    m_bboxThickness = DEFAULT_BBOX_WIDTH;
        uint8_t                     m_bboxOpacity = DEFAULT_BBOX_OPACITY;
        bool                        m_bboxDebug = false;
        bool                        m_enableBboxId = false;
        BBoxIdPosition              m_bboxIdPosition = MIDDLE;
        string                      m_bboxIdColor = "white";
        string                      m_bboxIdBgColor = "black";
        bool                        m_prevEnableOverlay = false;
        bool                        m_enableBbox = false;
        bool                        m_enableHalos = false;
        bool                        m_enableTripwire = false;
        bool                        m_enableRoi = false;
        bool                        m_enablePose = false;
        std::map<string, Tripwire>  m_tripwireList;
        std::map<string, Roi>       m_roiList;
        int64_t                     m_lastTripwireReadTime = 0;
        int64_t                     m_lastRoiReadTime = 0;
        std::vector<string>         m_idList[OVERLAYCOUNT];
        std::mutex                  m_idLock;
        std::vector<string>         m_classTypeList;
        std::mutex                  m_classTypeLock;
        std::thread                 m_readTripwireThread;
        std::thread                 m_readRoiThread;
        std::mutex                  m_tripwireLock;
        std::mutex                  m_roiLock;
        bool                        m_tripwireExit = false;
        bool                        m_roiExit = false;
        SyncObject                  m_tripwireSync = {};
        SyncObject                  m_roiSync = {};
        bool                        m_enableSensorNameText = false;
        int                         m_sensorNameTextPosX = 0;
        int                         m_sensorNameTextPosY = 0;
        std::atomic<int>            m_sourceWidth {WIDTH_1080p};
        std::atomic<int>            m_sourceHeight {HEIGHT_1080p};
        std::atomic<int>            m_ipcSourceWidth {WIDTH_1080p};
        std::atomic<int>            m_ipcSourceHeight {HEIGHT_1080p};
        void*                       osd_ctx = nullptr;
        std::shared_ptr<IMetadataStore> m_metadataStore = nullptr;
        std::shared_ptr<ReplayMetadataStore> m_replayMetadataStore = nullptr;
        bool                        m_isGst = false;
        std::map<string, CalibrationData, std::less<>> m_calibrationData;
        std::mutex                  m_calibrationLock;
        SyncObject                  m_metaWait = {};
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
        OsdCpuDataContext*          m_cpuCtx = nullptr;
#endif
        std::unique_ptr<HaloSafetyManager> m_haloSafetyManager;
};

/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "metropolis_perception_app.h"
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <cuda_runtime_api.h>
#include <errno.h>
#include <glib.h>
#include <gst/gst.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/inotify.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/timeb.h>
#include <sys/types.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>
//#include <map>
//#include <vector>

#include "deepstream_app.h"
#include "deepstream_config_file_parser.h"
#include "gstnvdsmeta.h"
#include "nvds_version.h"
#include "nvdsmeta_schema.h"
#include "nvds_tracker_meta.h"

/* External reference to global error capture buffer */
extern gchar *g_nvds_last_error_message;

/**
 * Logging levels
 */
#define LOG_LVL_FATAL 0
#define LOG_LVL_ERROR 1
#define LOG_LVL_WARN 2
#define LOG_LVL_INFO 3
#define LOG_LVL_DEBUG 4

#define MAX_DISPLAY_LEN (64)
#define MAX_TIME_STAMP_LEN (64)
#define STREAMMUX_BUFFER_POOL_SIZE (16)

#define INOTIFY_EVENT_SIZE (sizeof(struct inotify_event))
#define INOTIFY_EVENT_BUF_LEN (1024 * (INOTIFY_EVENT_SIZE + 16))

#define IS_YAML(file) \
  (g_str_has_suffix(file, ".yml") || g_str_has_suffix(file, ".yaml"))

#define MAX_LINES_IN_LABEL_FILE 320
#define MAX_CHAR_LENGTH_PER_LINE 100

/** @{
 * Macro's below and corresponding code-blocks are used to demonstrate
 * nvmsgconv + Broker Metadata manipulation possibility
 */

/**
 * IMPORTANT Note 1:
 * The code within the check for model_used ==
 * APP_CONFIG_ANALYTICS_RESNET_PGIE_3SGIE_TYPE_COLOR_MAKE is applicable as
 * sample demo code for configs that use resnet PGIE model with class ID's: {0,
 * 1, 2, 3} for {CAR, BICYCLE, PERSON, ROADSIGN} followed by optional Tracker +
 * 3 X SGIEs (Vehicle-Type,Color,Make) only! Please comment out the code if
 * using any other custom PGIE + SGIE combinations and use the code as reference
 * to write your own NvDsEventMsgMeta generation code in
 * generate_event_msg_meta() function
 */
typedef enum {
  APP_CONFIG_ANALYTICS_FSL = 0,
  APP_CONFIG_ANALYTICS_MTMC = 1,
  APP_CONFIG_ANALYTICS_RESNET_PGIE_3SGIE_TYPE_COLOR_MAKE = 2,
  APP_CONFIG_ANALYTICS_FSL_DUAL_HEAD = 3,
  APP_CONFIG_ANALYTICS_GDINO = 4,
  APP_CONFIG_ANALYTICS_SPARSE4D = 5,
  APP_CONFIG_ANALYTICS_MODELS_UNKNOWN = 6,
  APP_CONFIG_ANALYTICS_RTDETR_ITS = 7,
} AppConfigAnalyticsModel;

/**
 * IMPORTANT Note 2:
 * GENERATE_DUMMY_META_EXT macro implements code
 * that assumes APP_CONFIG_ANALYTICS_RESNET_PGIE_3SGIE_TYPE_COLOR_MAKE
 * case discussed above, and generate dummy metadata
 * for other classes like Person class
 *
 * Vehicle class schema meta (NvDsVehicleObject) is filled
 * in properly from Classifier-Metadata;
 * see in-code documentation and usage of
 * schema_fill_sample_sgie_vehicle_metadata()
 */
#define GENERATE_DUMMY_META_EXT

/** Following class-ID's
 * used for demonstration code
 * assume an ITS detection model
 * which outputs CLASS_ID=0 for Vehicle class
 * and CLASS_ID=2 for Person class
 * and SGIEs X 3 same as the sample DS config for test5-app:
 * configs/test5_config_file_src_infer_tracker_sgie.txt
 */

#define SECONDARY_GIE_VEHICLE_TYPE_UNIQUE_ID (4)
#define SECONDARY_GIE_VEHICLE_COLOR_UNIQUE_ID (5)
#define SECONDARY_GIE_VEHICLE_MAKE_UNIQUE_ID (6)

#define RESNET10_PGIE_3SGIE_TYPE_COLOR_MAKECLASS_ID_CAR (0)
#ifdef GENERATE_DUMMY_META_EXT
#define RESNET10_PGIE_3SGIE_TYPE_COLOR_MAKECLASS_ID_PERSON (2)
#endif
/** @} */

#ifdef EN_DEBUG
#define LOGD(...) printf(__VA_ARGS__)
#else
#define LOGD(...)
#endif

static TestAppCtx *testAppCtx;
GST_DEBUG_CATEGORY(NVDS_APP);

/** @{ imported from deepstream-app as is */

#define MAX_INSTANCES 128
#define APP_TITLE "MetropolisPerceptionApp"

#define DEFAULT_X_WINDOW_WIDTH 1920
#define DEFAULT_X_WINDOW_HEIGHT 1080

AppCtx *appCtx[MAX_INSTANCES];
static guint cintr = FALSE;
static GMainLoop *main_loop = NULL;
static gchar **cfg_files = NULL;
static gchar **input_files = NULL;
static gchar **override_cfg_file = NULL;
static gboolean playback_utc = FALSE;
static gboolean print_version = FALSE;
static gboolean show_bbox_text = FALSE;
static gboolean force_tcp = TRUE;
static gboolean print_dependencies_version = FALSE;
static gboolean quit = FALSE;
static gboolean use_tracker_reid = FALSE;
static gboolean show_sensor_id = FALSE;
static guint tracker_reid_store_age = 0;
static gint return_value = 0;
static guint num_instances;
static guint num_input_files;
static GMutex fps_lock;
static gdouble fps[MAX_SOURCE_BINS];
static gdouble fps_avg[MAX_SOURCE_BINS];
//static std::map<int, int> reid_cache;

static Display *display = NULL;
static Window windows[MAX_INSTANCES] = {0};

static GThread *x_event_thread = NULL;
static GMutex disp_lock;

static guint rrow, rcol, rcfg;
static gboolean rrowsel = FALSE, selecting = FALSE;
static AppConfigAnalyticsModel model_used = APP_CONFIG_ANALYTICS_MODELS_UNKNOWN;
static gint log_level = 0;
static gint message_rate = 30;

static struct timeval ota_request_time;
static struct timeval ota_completion_time;

typedef struct _OTAInfo {
  AppCtx *appCtx;
  gchar *override_cfg_file;
} OTAInfo;

// Variable to store labels for FSL dual head model
char labels[MAX_LINES_IN_LABEL_FILE][MAX_CHAR_LENGTH_PER_LINE];
guint dual_head_classes = 315;

// static gint include_fps = 0;
static gint target_class = 0;

/** @} imported from deepstream-app as is */
GOptionEntry entries[] = {
    {"version", 'v', 0, G_OPTION_ARG_NONE, &print_version,
     "Print DeepStreamSDK version", NULL},
    {"tiledtext", 0, 0, G_OPTION_ARG_NONE, &show_bbox_text,
     "Display Bounding box labels in tiled mode", NULL},
    {"version-all", 0, 0, G_OPTION_ARG_NONE, &print_dependencies_version,
     "Print DeepStreamSDK and dependencies version", NULL},
    {"cfg-file", 'c', 0, G_OPTION_ARG_FILENAME_ARRAY, &cfg_files,
     "Set the config file", NULL},
    {"override-cfg-file", 'o', 0, G_OPTION_ARG_FILENAME_ARRAY,
     &override_cfg_file,
     "Set the override config file, used for on-the-fly model update feature",
     NULL},
    {"input-file", 'i', 0, G_OPTION_ARG_FILENAME_ARRAY, &input_files,
     "Set the input file", NULL},
    {"playback-utc", 'p', 0, G_OPTION_ARG_INT, &playback_utc,
     "Playback utc; default=false (base UTC from file-URL or RTCP Sender "
     "Report) =true (base UTC from file/rtsp URL)",
     NULL},
    {"pgie-model-used", 'm', 0, G_OPTION_ARG_INT, &model_used,
     "PGIE Model used; {0: FSL}, {1: MTMC}, {2: Resnet 4-class [Car, "
     "Bicycle, Person, Roadsign]}, {3: FSL With Dual Head}, {4: GDINO}, "
     "{5: Sparse4D}, {6: Unknown [DEFAULT]}, {7: RT-DETR ITS}",
     NULL},
    {"no-force-tcp", 0, G_OPTION_FLAG_REVERSE, G_OPTION_ARG_NONE, &force_tcp,
     "Do not force TCP for RTP transport", NULL},
    {"log-level", 'l', 0, G_OPTION_ARG_INT, &log_level,
     "Log level for prints, default=0", NULL},
    {"message-rate", 'r', 0, G_OPTION_ARG_INT, &message_rate,
     "Message rate for broker", NULL},
    {"target-class", 't', 0, G_OPTION_ARG_INT, &target_class,
     "Target class for MTMC", NULL},
    {"tracker-reid", 0, 0, G_OPTION_ARG_NONE, &use_tracker_reid,
     "Use tracker re-identification as embedding", NULL},
    {"reid-store-age", 0, 0, G_OPTION_ARG_INT, &tracker_reid_store_age,
     "Tracker reid store age", NULL},
    {"show-sensor-id", 0, 0, G_OPTION_ARG_NONE, &show_sensor_id,
     "Show sensor ID in performance output", NULL},
    {NULL},
};

/**
 * @brief  Fill NvDsVehicleObject with the NvDsClassifierMetaList
 *         information in NvDsObjectMeta
 *         NOTE: This function assumes the test-application is
 *         run with 3 X SGIEs sample config:
 *         test5_config_file_src_infer_tracker_sgie.txt
 *         or an equivalent config
 *         NOTE: If user is adding custom SGIEs, make sure to
 *         edit this function implementation
 * @param  obj_params [IN] The NvDsObjectMeta as detected and kept
 *         in NvDsBatchMeta->NvDsFrameMeta(List)->NvDsObjectMeta(List)
 * @param  obj [IN/OUT] The NvDSMeta-Schema defined Vehicle metadata
 *         structure
 */
static void schema_fill_sample_sgie_vehicle_metadata(NvDsObjectMeta *obj_params,
                                                     NvDsVehicleObject *obj);

/**
 * @brief  Performs model update OTA operation
 *         Sets "model-engine-file" configuration parameter
 *         on infer plugin to initiate model switch OTA process
 * @param  ota_appCtx [IN] App context pointer
 */
void apply_ota(AppCtx *ota_appCtx);

/**
 * @brief  Thread which handles the model-update OTA functionlity
 *         1) Adds watch on the changes made in the provided ota-override-file,
 *            if changes are detected, validate the model-update change request,
 *            intiate model-update OTA process
 *         2) Frame drops / frames without inference should NOT be detected in
 *            this on-the-fly model update process
 *         3) In case of model update OTA fails, error message will be printed
 *            on the console and pipeline continues to run with older
 *            model configuration
 * @param  gpointer [IN] Pointer to OTAInfo structure
 * @param  gpointer [OUT] Returns NULL in case of thread exits
 */
gpointer ota_handler_thread(gpointer data);

static void generate_ts_rfc3339(char *buf, int buf_size) {
  time_t tloc;
  struct tm tm_log;
  struct timespec ts;

  clock_gettime(CLOCK_REALTIME, &ts);
  tloc = ts.tv_sec;  // Direct assignment instead of memcpy
  gmtime_r(&tloc, &tm_log);

  // Format the main timestamp
  int written = strftime(buf, buf_size, "%Y-%m-%dT%H:%M:%S", &tm_log);
  if (written > 0 && written < buf_size) {
    // Safely append milliseconds
    int ms = ts.tv_nsec / 1000000;
    snprintf(buf + written, buf_size - written, ".%.3dZ", ms);
  }
}

void nanoseconds_to_rfc3339(int64_t nanoseconds, char *output, size_t output_size) {
    time_t seconds = nanoseconds / 1000000000;
    int32_t nanos = nanoseconds % 1000000000;

    struct tm tm_buf;
    struct tm *tm_info = gmtime_r(&seconds, &tm_buf);

    char time_str[26];
    strftime(time_str, 26, "%Y-%m-%dT%H:%M:%S", tm_info);

    snprintf(output, output_size, "%s.%09dZ", time_str, nanos);
}

static GstClockTime generate_ts_rfc3339_from_ts(char *buf, int buf_size,
                                                GstClockTime ts, gchar *src_uri,
                                                gint stream_id) {
  time_t tloc;
  struct tm tm_log;
  int ms;
  GstClockTime ts_generated;

  if (playback_utc || (appCtx[0]->config.multi_source_config[stream_id].type !=
                       NV_DS_SOURCE_RTSP)) {
    if (testAppCtx->streams[stream_id].meta_number == 0) {
      testAppCtx->streams[stream_id].timespec_first_frame =
          extract_utc_from_uri(src_uri);
      tloc = testAppCtx->streams[stream_id].timespec_first_frame.tv_sec;
      ms =
          testAppCtx->streams[stream_id].timespec_first_frame.tv_nsec / 1000000;
      testAppCtx->streams[stream_id].gst_ts_first_frame = ts;
      ts_generated = GST_TIMESPEC_TO_TIME(
          testAppCtx->streams[stream_id].timespec_first_frame);
      if (ts_generated == 0) {
        if (log_level >= LOG_LVL_WARN) {
          g_print(
              "WARNING; playback mode used with URI [%s] not conforming to "
              "timestamp format;"
              " check README; using system-time\n",
              src_uri);
        }
        clock_gettime(CLOCK_REALTIME,
                      &testAppCtx->streams[stream_id].timespec_first_frame);
        ts_generated = GST_TIMESPEC_TO_TIME(
            testAppCtx->streams[stream_id].timespec_first_frame);
      }
    } else {
      GstClockTime ts_current =
          GST_TIMESPEC_TO_TIME(
              testAppCtx->streams[stream_id].timespec_first_frame) +
          (ts - testAppCtx->streams[stream_id].gst_ts_first_frame);
      struct timespec timespec_current;
      GST_TIME_TO_TIMESPEC(ts_current, timespec_current);
      tloc = timespec_current.tv_sec;
      ms = timespec_current.tv_nsec / 1000000;
      ts_generated = ts_current;
    }
  } else {
    /** ts itself is UTC Time in ns */
    struct timespec timespec_current;
    GST_TIME_TO_TIMESPEC(ts, timespec_current);
    tloc = timespec_current.tv_sec;
    ms = timespec_current.tv_nsec / 1000000;
    ts_generated = ts;
  }

  gmtime_r(&tloc, &tm_log);
  // Format the main timestamp
  int written = strftime(buf, buf_size, "%Y-%m-%dT%H:%M:%S", &tm_log);
  if (written > 0 && written < buf_size) {
    // Safely append milliseconds
    snprintf(buf + written, buf_size - written, ".%.3dZ", ms);
  }

  if (log_level >= LOG_LVL_DEBUG) {
    LOGD("ts=%s\n", buf);
  }

  return ts_generated;
}

static gpointer meta_copy_func(gpointer data, gpointer user_data) {
  NvDsUserMeta *user_meta = (NvDsUserMeta *)data;
  NvDsEventMsgMeta *srcMeta = (NvDsEventMsgMeta *)user_meta->user_meta_data;
  NvDsEventMsgMeta *dstMeta = NULL;

  dstMeta = (NvDsEventMsgMeta *)g_memdup(srcMeta, sizeof(NvDsEventMsgMeta));

  if (srcMeta->ts) dstMeta->ts = g_strdup(srcMeta->ts);

  if (srcMeta->objSignature.size > 0) {
    dstMeta->objSignature.signature = (gdouble *)g_memdup(
        srcMeta->objSignature.signature, srcMeta->objSignature.size);
    dstMeta->objSignature.size = srcMeta->objSignature.size;
  }

  if (srcMeta->objectId) {
    dstMeta->objectId = g_strdup(srcMeta->objectId);
  }

  if (srcMeta->sensorStr) {
    dstMeta->sensorStr = g_strdup(srcMeta->sensorStr);
  }

  if (srcMeta->extMsgSize > 0) {
    if (srcMeta->objType == NVDS_OBJECT_TYPE_VEHICLE) {
      NvDsVehicleObject *srcObj = (NvDsVehicleObject *)srcMeta->extMsg;
      NvDsVehicleObject *obj =
          (NvDsVehicleObject *)g_malloc0(sizeof(NvDsVehicleObject));
      if (srcObj->type) obj->type = g_strdup(srcObj->type);
      if (srcObj->make) obj->make = g_strdup(srcObj->make);
      if (srcObj->model) obj->model = g_strdup(srcObj->model);
      if (srcObj->color) obj->color = g_strdup(srcObj->color);
      if (srcObj->license) obj->license = g_strdup(srcObj->license);
      if (srcObj->region) obj->region = g_strdup(srcObj->region);

      dstMeta->extMsg = obj;
      dstMeta->extMsgSize = sizeof(NvDsVehicleObject);
    } else if (srcMeta->objType == NVDS_OBJECT_TYPE_PERSON) {
      NvDsPersonObject *srcObj = (NvDsPersonObject *)srcMeta->extMsg;
      NvDsPersonObject *obj =
          (NvDsPersonObject *)g_malloc0(sizeof(NvDsPersonObject));

      obj->age = srcObj->age;

      if (srcObj->gender) obj->gender = g_strdup(srcObj->gender);
      if (srcObj->cap) obj->cap = g_strdup(srcObj->cap);
      if (srcObj->hair) obj->hair = g_strdup(srcObj->hair);
      if (srcObj->apparel) obj->apparel = g_strdup(srcObj->apparel);

      dstMeta->extMsg = obj;
      dstMeta->extMsgSize = sizeof(NvDsPersonObject);
    }
    //! Extensions for Fewshot Learning
    else if (srcMeta->objType == NVDS_OBJECT_TYPE_PRODUCT) {
      NvDsProductObject *srcObj = (NvDsProductObject *)srcMeta->extMsg;
      NvDsProductObject *obj =
          (NvDsProductObject *)g_malloc0(sizeof(NvDsProductObject));
      if (srcObj->brand) obj->brand = g_strdup(srcObj->brand);
      if (srcObj->type) obj->type = g_strdup(srcObj->type);
      if (srcObj->shape) obj->shape = g_strdup(srcObj->shape);

      dstMeta->extMsg = obj;
      dstMeta->extMsgSize = sizeof(NvDsProductObject);
    }
  }

  if (srcMeta->embedding.embedding_length > 0) {
    dstMeta->embedding.embedding_length = srcMeta->embedding.embedding_length;
    dstMeta->embedding.embedding_vector =
        g_memdup(srcMeta->embedding.embedding_vector,
                 srcMeta->embedding.embedding_length * sizeof(float));
  }

  if(srcMeta->has3DTracking) {
    dstMeta->has3DTracking = true;
    dstMeta->singleView3DTracking.ptWorldFeet[0] = srcMeta->singleView3DTracking.ptWorldFeet[0];
    dstMeta->singleView3DTracking.ptWorldFeet[1] = srcMeta->singleView3DTracking.ptWorldFeet[1];
    dstMeta->singleView3DTracking.ptImgFeet[0] = srcMeta->singleView3DTracking.ptImgFeet[0];
    dstMeta->singleView3DTracking.ptImgFeet[1] = srcMeta->singleView3DTracking.ptImgFeet[1];
    dstMeta->singleView3DTracking.convexHull.numFilled = srcMeta->singleView3DTracking.convexHull.numFilled;
    dstMeta->singleView3DTracking.convexHull.points =
        g_memdup(srcMeta->singleView3DTracking.convexHull.points,
                    srcMeta->singleView3DTracking.convexHull.numFilled * 2 * sizeof(gint));
    memcpy(dstMeta->singleView3DTracking.bbox3d.boxes_3d,
           srcMeta->singleView3DTracking.bbox3d.boxes_3d,
           sizeof(srcMeta->singleView3DTracking.bbox3d.boxes_3d));
    dstMeta->singleView3DTracking.bbox3d.scores_3d = srcMeta->singleView3DTracking.bbox3d.scores_3d;
  }
  else {
    dstMeta->has3DTracking = false;
  }
  return dstMeta;
}

static void meta_free_func(gpointer data, gpointer user_data) {
  NvDsUserMeta *user_meta = (NvDsUserMeta *)data;
  NvDsEventMsgMeta *srcMeta = (NvDsEventMsgMeta *)user_meta->user_meta_data;
  user_meta->user_meta_data = NULL;

  if (srcMeta->ts) {
    g_free(srcMeta->ts);
  }

  if (srcMeta->objSignature.size > 0) {
    g_free(srcMeta->objSignature.signature);
    srcMeta->objSignature.size = 0;
  }

  if (srcMeta->objectId) {
    g_free(srcMeta->objectId);
  }

  if (srcMeta->sensorStr) {
    g_free(srcMeta->sensorStr);
  }

  if (srcMeta->extMsgSize > 0) {
    if (srcMeta->objType == NVDS_OBJECT_TYPE_VEHICLE) {
      NvDsVehicleObject *obj = (NvDsVehicleObject *)srcMeta->extMsg;
      if (obj->type) g_free(obj->type);
      if (obj->color) g_free(obj->color);
      if (obj->make) g_free(obj->make);
      if (obj->model) g_free(obj->model);
      if (obj->license) g_free(obj->license);
      if (obj->region) g_free(obj->region);
    } else if (srcMeta->objType == NVDS_OBJECT_TYPE_PERSON) {
      NvDsPersonObject *obj = (NvDsPersonObject *)srcMeta->extMsg;

      if (obj->gender) g_free(obj->gender);
      if (obj->cap) g_free(obj->cap);
      if (obj->hair) g_free(obj->hair);
      if (obj->apparel) g_free(obj->apparel);
    }
    //! Extensions for Fewshot Learning
    else if (srcMeta->objType == NVDS_OBJECT_TYPE_PRODUCT) {
      NvDsProductObject *obj = (NvDsProductObject *)srcMeta->extMsg;

      if (obj->brand) g_free(obj->brand);
      if (obj->type) g_free(obj->type);
      if (obj->shape) g_free(obj->shape);
    }

    g_free(srcMeta->extMsg);
    srcMeta->extMsg = NULL;
    srcMeta->extMsgSize = 0;
  }

  if (srcMeta->embedding.embedding_vector) {
    g_free(srcMeta->embedding.embedding_vector);
  }
  srcMeta->embedding.embedding_length = 0;

  if (srcMeta->has3DTracking && srcMeta->singleView3DTracking.convexHull.points) {
    g_free(srcMeta->singleView3DTracking.convexHull.points);
    srcMeta->singleView3DTracking.convexHull.numFilled = 0;
  }

  g_free(srcMeta);
}

#ifdef GENERATE_DUMMY_META_EXT
static void generate_vehicle_meta(gpointer data) {
  NvDsVehicleObject *obj = (NvDsVehicleObject *)data;

  obj->type = g_strdup("sedan-dummy");
  obj->color = g_strdup("blue");
  obj->make = g_strdup("Bugatti");
  obj->model = g_strdup("M");
  obj->license = g_strdup("XX1234");
  obj->region = g_strdup("CA");
}

static void generate_person_meta(gpointer data) {
  NvDsPersonObject *obj = (NvDsPersonObject *)data;
  obj->age = 45;
  obj->cap = g_strdup("none-dummy-person-info");
  obj->hair = g_strdup("black");
  obj->gender = g_strdup("male");
  obj->apparel = g_strdup("formal");
}
//! Extensions for Fewshot Learning
// Create product meta object
static void generate_product_meta(gpointer data) {
  NvDsProductObject *obj = (NvDsProductObject *)data;
  obj->brand = g_strdup("");
  obj->type = g_strdup("");
  obj->shape = g_strdup("");
}
#endif /**< GENERATE_DUMMY_META_EXT */


static void destroy_embedding_queue() {
  for (gint stream_id = 0; stream_id < MAX_SOURCE_BINS; stream_id++) {
    GQueue *prev_frames_embedding = testAppCtx->streams[stream_id].frame_embedding_queue;

    /** Remove outdated embedding*/
    if (prev_frames_embedding) {
      while (!g_queue_is_empty(prev_frames_embedding)) {
        FrameEmbedding *frame_embedding = (FrameEmbedding *) g_queue_pop_tail(prev_frames_embedding);
        for (GList *l = frame_embedding->obj_embeddings; l; l = l->next) {
          ObjEmbedding *obj_emb = (ObjEmbedding *) l->data;
          g_free(obj_emb->embedding);
          g_free(obj_emb);
        }
        g_list_free(frame_embedding->obj_embeddings);
        g_free(frame_embedding);
      }
      g_queue_free(prev_frames_embedding);
    }
  }
}

static void pop_embedding_queue(gint stream_id, gint frame_num) {
    GQueue *prev_frames_embedding = testAppCtx->streams[stream_id].frame_embedding_queue;

    if (prev_frames_embedding) {  /** Remove outdated embedding*/
      while (!g_queue_is_empty(prev_frames_embedding)) {
        FrameEmbedding *frame_embedding = (FrameEmbedding *) g_queue_peek_tail(prev_frames_embedding);
        if (frame_embedding->frame_num < frame_num - (gint) tracker_reid_store_age) {
          frame_embedding = (FrameEmbedding *) g_queue_pop_tail(prev_frames_embedding);
          for (GList *l = frame_embedding->obj_embeddings; l; l = l->next) {
            ObjEmbedding *obj_emb = (ObjEmbedding *) l->data;
            g_free(obj_emb->embedding);
            g_free(obj_emb);
          }
          g_list_free(frame_embedding->obj_embeddings);
          g_free(frame_embedding);
        }
        else {
          break;
        }
      }
    }
}

float *retrieve_embedding_queue(gint stream_id, gint frame_num, guint64 target_obj_id, int* p_num_elements) {
  float* embedding_data = NULL;
  GQueue *prev_frames_embedding = testAppCtx->streams[stream_id].frame_embedding_queue;

  if (prev_frames_embedding == NULL || g_queue_is_empty(prev_frames_embedding))
    return embedding_data;

  /** Find history embedding*/
  /**When queue tail is not reached and embedding not found */
  for (guint i=0; i < g_queue_get_length(prev_frames_embedding) && embedding_data == NULL; i++) {

    FrameEmbedding *frame_embedding = (FrameEmbedding *) g_queue_peek_nth(prev_frames_embedding, i);
    /** Out of history range */
    if (frame_embedding->frame_num < frame_num - (gint) tracker_reid_store_age)
      break;

    for (GList *l = frame_embedding->obj_embeddings; l; l = l->next) {
      ObjEmbedding *obj_emb = (ObjEmbedding *) l->data;
      if (obj_emb->object_id == target_obj_id) {
        embedding_data = obj_emb->embedding;
        *p_num_elements = obj_emb->num_elements;
        break;
      }
    }
  }
  return embedding_data;

}

static void push_to_embedding_queue(gint stream_id, FrameEmbedding *frame_embedding) {
  if (testAppCtx->streams[stream_id].frame_embedding_queue == NULL) {
    testAppCtx->streams[stream_id].frame_embedding_queue = g_queue_new();
  }
  g_queue_push_head (testAppCtx->streams[stream_id].frame_embedding_queue, frame_embedding);
}

static void generate_event_msg_meta(AppCtx *appCtx, gpointer data,
                                    gint class_id, gboolean useTs,
                                    GstClockTime ts, gchar *src_uri,
                                    gint stream_id, guint sensor_id,
                                    NvDsObjectMeta *obj_params, float scaleW,
                                    float scaleH, NvDsFrameMeta *frame_meta,
                                    float *embedding_data, int numElements,
                                    gboolean embedding_on_device, gchar* label) {
  NvDsEventMsgMeta *meta = (NvDsEventMsgMeta *)data;
  GstClockTime ts_generated = 0;

  meta->objType = NVDS_OBJECT_TYPE_UNKNOWN; /**< object unknown */
  /* The sensor_id is parsed from the source group name which has the format
   * [source<sensor-id>]. */
  meta->sensorId = sensor_id;
  meta->placeId = sensor_id;
  meta->moduleId = sensor_id;
  meta->frameId = frame_meta->frame_num;
  meta->ts = (gchar *)g_malloc0(MAX_TIME_STAMP_LEN + 1);
  meta->objectId = (gchar *)g_malloc0(MAX_LABEL_SIZE);

  if (embedding_data) {
    // printf("Malloc embedding: %d\n", numElements*4);
    meta->embedding.embedding_vector =
        (float *)g_malloc0(numElements * sizeof(float));
    if (embedding_on_device) {
      cudaMemcpy(meta->embedding.embedding_vector, embedding_data,
               numElements * sizeof(float), cudaMemcpyDeviceToHost);
    } else {
      cudaMemcpy(meta->embedding.embedding_vector, embedding_data,
               numElements * sizeof(float), cudaMemcpyHostToHost);
    }
    meta->embedding.embedding_length = numElements;
  } else {
    meta->embedding.embedding_length = 0;
  }

  meta->has3DTracking = false;
  meta->visibility = 1.0;

  for (NvDsMetaList *l_user = obj_params->obj_user_meta_list;
             l_user != NULL; l_user = l_user->next) {
          NvDsUserMeta *user_meta = (NvDsUserMeta *)l_user->data;
    if (user_meta->base_meta.meta_type == NVDS_OBJ_IMAGE_FOOT_LOCATION) {
      meta->has3DTracking = true;
      float *pPtFeet = (float*)user_meta->user_meta_data;
      meta->singleView3DTracking.ptImgFeet[0] = pPtFeet[0];
      meta->singleView3DTracking.ptImgFeet[1] = pPtFeet[1];
    }
    else if (user_meta->base_meta.meta_type == NVDS_OBJ_WORLD_FOOT_LOCATION) {
      float *pPtFeet = (float*)user_meta->user_meta_data;
      meta->singleView3DTracking.ptWorldFeet[0] = pPtFeet[0];
      meta->singleView3DTracking.ptWorldFeet[1] = pPtFeet[1];
    }
    else if (user_meta->base_meta.meta_type == NVDS_OBJ_VISIBILITY) {
      meta->visibility = *(float*)user_meta->user_meta_data;
    }
    else if (user_meta->base_meta.meta_type == NVDS_OBJ_IMAGE_CONVEX_HULL) {
      NvDsObjConvexHull* pConvexHull = (NvDsObjConvexHull *) user_meta->user_meta_data;
      meta->singleView3DTracking.convexHull.points = g_malloc0(sizeof(gint) * pConvexHull->numPointsAllocated * 2);
      meta->singleView3DTracking.convexHull.numFilled = pConvexHull->numPoints;
      for (uint32_t i=0; i < pConvexHull->numPoints; i++) {
        meta->singleView3DTracking.convexHull.points[2*i] = pConvexHull->list[2*i];
        meta->singleView3DTracking.convexHull.points[2*i+1] = pConvexHull->list[2*i+1];
      }
    }
    else if (user_meta->base_meta.meta_type == NVDS_OBJ_3D_META) {
      meta->has3DTracking = true;
      NvDsObj3DBbox *p3DBbox = (NvDsObj3DBbox*)user_meta->user_meta_data;
      meta->singleView3DTracking.bbox3d.boxes_3d[0] = p3DBbox->xCentre;
      meta->singleView3DTracking.bbox3d.boxes_3d[1] = p3DBbox->yCentre;
      meta->singleView3DTracking.bbox3d.boxes_3d[2] = p3DBbox->zCentre;
      meta->singleView3DTracking.bbox3d.boxes_3d[3] = p3DBbox->xLen;
      meta->singleView3DTracking.bbox3d.boxes_3d[4] = p3DBbox->yLen;
      meta->singleView3DTracking.bbox3d.boxes_3d[5] = p3DBbox->zLen;
      meta->singleView3DTracking.bbox3d.boxes_3d[6] = p3DBbox->xRot;
      meta->singleView3DTracking.bbox3d.boxes_3d[7] = p3DBbox->yRot;
      meta->singleView3DTracking.bbox3d.boxes_3d[8] = p3DBbox->zRot;
      meta->singleView3DTracking.bbox3d.boxes_3d[9] = p3DBbox->xVel;
      meta->singleView3DTracking.bbox3d.boxes_3d[10] = p3DBbox->yVel;
      meta->singleView3DTracking.bbox3d.boxes_3d[11] = p3DBbox->zVel;
      meta->singleView3DTracking.bbox3d.scores_3d = obj_params->confidence;
    }
  }

  meta->confidence = obj_params->confidence;

  // Initialize the buffer with zeros and ensure null termination
  memset(meta->objectId, 0, MAX_LABEL_SIZE);
  snprintf(meta->objectId, MAX_LABEL_SIZE, "%s", obj_params->obj_label);

  nanoseconds_to_rfc3339(ts, meta->ts, MAX_TIME_STAMP_LEN+1);

  /**
   * Valid attributes in the metadata sent over nvmsgbroker:
   * a) Sensor ID (shall be configured in nvmsgconv config file)
   * b) bbox info (meta->bbox) <- obj_params->rect_params (attr_info have sgie
   * info) c) tracking ID (meta->trackingId) <- obj_params->object_id
   */

  /** bbox - resolution is scaled by nvinfer back to
   * the resolution provided by streammux
   * We have to scale it back to original stream resolution
   */

  meta->bbox.left = obj_params->rect_params.left * scaleW;
  meta->bbox.top = obj_params->rect_params.top * scaleH;
  meta->bbox.width = obj_params->rect_params.width * scaleW;
  meta->bbox.height = obj_params->rect_params.height * scaleH;

  /** tracking ID */
  meta->trackingId = obj_params->object_id;

  /** sensor ID when streams are added using nvmultiurisrcbin REST API */
  NvDsSensorInfo *sensorInfo = get_sensor_info(appCtx, stream_id);
  if (sensorInfo) {
    /** this stream was added using REST API; we have Sensor Info!
     * Note: using NvDsSensorInfo->sensor_name instead of sensor_id
     * to use camera_name field from the stream/add REST API
     */
    // g_print(
    //     "this stream [%d:%s] was added using REST API; we have Sensor
    //     Info\n", sensorInfo->source_id, sensorInfo->sensor_id);
    meta->sensorStr = g_strdup(sensorInfo->sensor_name);
  }

  (void)ts_generated;

  /*
   * This demonstrates how to attach custom objects.
   * Any custom object as per requirement can be generated and attached
   * like NvDsVehicleObject / NvDsPersonObject. Then that object should
   * be handled in gst-nvmsgconv component accordingly.
   */
  if (model_used == APP_CONFIG_ANALYTICS_RESNET_PGIE_3SGIE_TYPE_COLOR_MAKE) {
    if (class_id == RESNET10_PGIE_3SGIE_TYPE_COLOR_MAKECLASS_ID_CAR) {
      meta->type = NVDS_EVENT_MOVING;
      meta->objType = NVDS_OBJECT_TYPE_VEHICLE;
      meta->objClassId = RESNET10_PGIE_3SGIE_TYPE_COLOR_MAKECLASS_ID_CAR;

      NvDsVehicleObject *obj =
          (NvDsVehicleObject *)g_malloc0(sizeof(NvDsVehicleObject));
      schema_fill_sample_sgie_vehicle_metadata(obj_params, obj);

      meta->extMsg = obj;
      meta->extMsgSize = sizeof(NvDsVehicleObject);
    }
#ifdef GENERATE_DUMMY_META_EXT
    else if (class_id == RESNET10_PGIE_3SGIE_TYPE_COLOR_MAKECLASS_ID_PERSON) {
      meta->type = NVDS_EVENT_ENTRY;
      meta->objType = NVDS_OBJECT_TYPE_PERSON;
      meta->objClassId = RESNET10_PGIE_3SGIE_TYPE_COLOR_MAKECLASS_ID_PERSON;

      NvDsPersonObject *obj =
          (NvDsPersonObject *)g_malloc0(sizeof(NvDsPersonObject));
      generate_person_meta(obj);

      meta->extMsg = obj;
      meta->extMsgSize = sizeof(NvDsPersonObject);
    }
#endif /**< GENERATE_DUMMY_META_EXT */
  } else if (model_used == APP_CONFIG_ANALYTICS_FSL) {
    meta->type = NVDS_EVENT_MOVING;
    meta->objType = NVDS_OBJECT_TYPE_PRODUCT;
#ifdef GENERATE_DUMMY_META_EXT
    NvDsProductObject *obj =
        (NvDsProductObject *)g_malloc0(sizeof(NvDsProductObject));
    generate_product_meta(obj);

    meta->extMsg = obj;
    meta->extMsgSize = sizeof(NvDsProductObject);
#endif
  } else if (model_used == APP_CONFIG_ANALYTICS_MTMC) {
    // MODIFIED: Generate metadata for all classes, not just target_class
    meta->type = NVDS_EVENT_MOVING;
    
    // FIXED: Use UNKNOWN objType so message converter uses objectId field instead of enum strings
    // This allows custom labels (forklift, pallet, etc.) to appear in Redis instead of 
    // predefined strings like "Person", "Vehicle"
    meta->objType = NVDS_OBJECT_TYPE_UNKNOWN;
    
#ifdef GENERATE_DUMMY_META_EXT
    // No extended metadata needed for MTMC mode with UNKNOWN objType
    // The objectId field will contain the actual class label
#endif
  } else if (model_used == APP_CONFIG_ANALYTICS_FSL_DUAL_HEAD) {
    meta->type = NVDS_EVENT_MOVING;
    // TODO-MB: WAR to retrieve class name during payload generation by sending
    // objType as UNKNOWN so that it will go with objectId string instead of
    // the converted string.
    // Need to redo this once DS-SDK supports the same.
    meta->objType = NVDS_OBJECT_TYPE_UNKNOWN;
    // Initialize the buffer with zeros and ensure null termination
    memset(meta->objectId, 0, MAX_CHAR_LENGTH_PER_LINE);
    snprintf(meta->objectId, MAX_CHAR_LENGTH_PER_LINE, "%s", label);
#ifdef GENERATE_DUMMY_META_EXT
    // NvDsProductObject *obj =
    //     (NvDsProductObject *)g_malloc0(sizeof(NvDsProductObject));
    // generate_product_meta(obj);

    // obj->type = g_strdup(label);
    // meta->extMsg = obj;
    // meta->extMsgSize = sizeof(NvDsProductObject);
#endif
  } else if (model_used == APP_CONFIG_ANALYTICS_RTDETR_ITS) {
    // RT-DETR ITS model - acts similar to unknown model type
    meta->type = NVDS_EVENT_MOVING;
    meta->objType = NVDS_OBJECT_TYPE_UNKNOWN;
    // Keep the default objectId that was already set from obj_params->obj_label
  }
}

static void
generate_event_msg_meta_dummy (AppCtx * appCtx, gpointer data, gint stream_id,
    NvDsFrameMeta * frame_meta)
{
  NvDsEventMsgMeta *meta = (NvDsEventMsgMeta *) data;
  GstClockTime ts_generated = 0;
  gchar * src_uri = appCtx->config.multi_source_config[stream_id].uri;

  meta->objType = NVDS_OBJECT_TYPE_DUMMY; /**< object dummy */
  /* The sensor_id is parsed from the source group name which has the format
   * [source<sensor-id>]. */
  meta->sensorId = appCtx->config.multi_source_config[stream_id].camera_id;
  meta->placeId = appCtx->config.multi_source_config[stream_id].camera_id;
  meta->moduleId = appCtx->config.multi_source_config[stream_id].camera_id;
  meta->frameId = frame_meta->frame_num;
  meta->ts = (gchar *) g_malloc0 (MAX_TIME_STAMP_LEN + 1);

  nanoseconds_to_rfc3339(frame_meta->ntp_timestamp, meta->ts, MAX_TIME_STAMP_LEN+1);

  /** sensor ID when streams are added using nvmultiurisrcbin REST API */
  NvDsSensorInfo* sensorInfo = get_sensor_info(appCtx, stream_id);
  if(sensorInfo) {
    /** this stream was added using REST API; we have Sensor Info! */
    LOGD("this stream [%d:%s] was added using REST API; we have Sensor Info\n",
        sensorInfo->source_id, sensorInfo->sensor_id);
    meta->sensorStr = g_strdup (sensorInfo->sensor_id);
  }
  (void) ts_generated;
}

/**
 * Callback function to be called once all inferences (Primary + Secondary)
 * are done. This is opportunity to modify content of the metadata.
 * e.g. Here Person is being replaced with Man/Woman and corresponding counts
 * are being maintained. It should be modified according to network classes
 * or can be removed altogether if not required.
 */
static void bbox_generated_probe_after_analytics(AppCtx *appCtx, GstBuffer *buf,
                                                 NvDsBatchMeta *batch_meta,
                                                 guint index) {

  if (model_used == APP_CONFIG_ANALYTICS_SPARSE4D) {
    LOGD("Running Sparse4d model\n");
    char *verbose = getenv("SPARSE4D_DEBUG_TS");

    if(batch_meta->batch_user_meta_list) {  //for SPARSE4D model
      for (NvDsMetaList *l = batch_meta->batch_user_meta_list; l; l = l->next) {
        NvDsUserMeta *user_event_meta = (NvDsUserMeta *)(l->data);
        if (user_event_meta && user_event_meta->base_meta.meta_type == NVDS_CUSTOM_MSG_SPARSE4D) {
          NvDsBbox3dObjectList *bbox3d_list = (NvDsBbox3dObjectList *)user_event_meta->user_meta_data;
          for (NvDsMetaList * l_frame = batch_meta->frame_meta_list; l_frame != NULL;
              l_frame = l_frame->next) {
            NvDsFrameMeta *frame_meta = (NvDsFrameMeta *) l_frame->data;
            if (verbose != NULL) {
              char *endptr;
              long verbose_val = strtol(verbose, &endptr, 10);
              // Check for conversion errors and valid range
              if (endptr != verbose && *endptr == '\0' && verbose_val == 1) {
                if ((bbox3d_list->count < MAX_ENTRIES) && (frame_meta != NULL)) {
                  g_strlcpy(bbox3d_list->entries[bbox3d_list->count].source_id,
                    frame_meta->sensorInfo_meta.sensor_name,
                    MAX_SOURCE_ID_LEN);
                  bbox3d_list->entries[bbox3d_list->count].timestamp = frame_meta->ntp_timestamp;
                  bbox3d_list->count++;
                } else {
                  g_print ("This is either EOS case or some error has happened\n");
                  break;
                }
              }
            }
          }
        }
      }
    }
  } else {
    NvDsObjectMeta *obj_meta = NULL;
    GstClockTime buffer_pts = 0;
    guint32 stream_id = 0;
    gboolean valid_class_id=0;

    for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL;
        l_frame = l_frame->next) {
      NvDsFrameMeta *frame_meta = (NvDsFrameMeta *)l_frame->data;
      valid_class_id=0;
      stream_id = frame_meta->source_id;

      //! DEBUGGER_START for ending at 5mins
      if (log_level >= 100) {
        if (frame_meta->frame_num >= 9000) {
          quit = TRUE;
          g_main_loop_quit(main_loop);
          break;
        }
      }
      //! DEBUGGER_END

      GstClockTime buf_ntp_time = 0;
      if (playback_utc == FALSE) {
        /** Calculate the buffer-NTP-time
         * derived from this stream's RTCP Sender Report here:
         */
        StreamSourceInfo *src_stream = &testAppCtx->streams[stream_id];
        buf_ntp_time = frame_meta->ntp_timestamp;

        if (buf_ntp_time < src_stream->last_ntp_time) {
          NVGSTDS_WARN_MSG_V(
              "Source %d: NTP timestamps are backward in time."
              " Current: %lu previous: %lu",
              stream_id, buf_ntp_time, src_stream->last_ntp_time);
        }
        src_stream->last_ntp_time = buf_ntp_time;
      }

      FrameEmbedding *frame_embedding = NULL;
      if (use_tracker_reid && tracker_reid_store_age > 0) {
        pop_embedding_queue(stream_id, frame_meta->frame_num);
      }

      GList *l;
      if(frame_meta->num_obj_meta) {
        for (l = frame_meta->obj_meta_list; l != NULL; l = l->next) {
          /* Now using above information we need to form a text that should
          * be displayed on top of the bounding box, so lets form it here. */

          obj_meta = (NvDsObjectMeta *)(l->data);
          
          // MODIFIED: Allow all classes in MTMC mode, not just target_class
          // Comment out the restrictive filtering for multi-class detection
          /*
          if (model_used == APP_CONFIG_ANALYTICS_MTMC &&
              obj_meta->class_id != target_class) {
            continue;
          }
          */
          valid_class_id=1;
          if (!(frame_meta->frame_num % message_rate)) {
            /**
             * Enable only if this callback is after tiler
             * NOTE: Scaling back code-commented
             * now that bbox_generated_probe_after_analytics() is post analytics
             * (say pgie, tracker or sgie)
             * and before tiler, no plugin shall scale metadata and will be
             * corresponding to the nvstreammux resolution
             */
            float scaleW = 0;
            float scaleH = 0;
            /* Frequency of messages to be send will be based on use case.
            * Here message is being sent for first object every 30 frames.
            */
            buffer_pts = frame_meta->buf_pts;
            if (!appCtx->config.streammux_config.pipeline_width ||
                !appCtx->config.streammux_config.pipeline_height) {
              if (log_level >= LOG_LVL_ERROR) {
                g_print("invalid pipeline params\n");
              }
              return;
            }
            if (log_level >= LOG_LVL_DEBUG) {
              LOGD("stream %d==%d [%d X %d]\n", frame_meta->source_id,
                  frame_meta->pad_index, frame_meta->source_frame_width,
                  frame_meta->source_frame_height);
            }
            scaleW = (float)frame_meta->source_frame_width /
                    appCtx->config.streammux_config.pipeline_width;
            scaleH = (float)frame_meta->source_frame_height /
                    appCtx->config.streammux_config.pipeline_height;

            if (log_level == 99 || log_level == 100) {
              if (playback_utc == FALSE) {
                g_print(
                    "[DEBUG]: Timestamp: Frame(frame_meta->buf_pts) "
                    "[%" GST_TIME_FORMAT
                    "] RTCP "
                    "sender report(buf_ntp_time) [%" GST_TIME_FORMAT
                    " ]; Playback (False)\n",
                    GST_TIME_ARGS(frame_meta->buf_pts),
                    GST_TIME_ARGS(buf_ntp_time));
              } else {
                g_print(
                    "[DEBUG]: Timestamp: Frame(frame_meta->buf_pts) "
                    "[%" GST_TIME_FORMAT
                    "] RTCP "
                    "sender report(buf_ntp_time) [%" GST_TIME_FORMAT
                    " ]; Playback (True)\n",
                    GST_TIME_ARGS(frame_meta->buf_pts),
                    GST_TIME_ARGS(buf_ntp_time));
              }
            }
            if (playback_utc == FALSE) {
              /** Use the buffer-NTP-time derived from this stream's RTCP Sender
               * Report here:
               */
              buffer_pts = buf_ntp_time;
            }

            gchar dual_head_label[MAX_CHAR_LENGTH_PER_LINE]; //Label for FSL Dual Head
            float *embedding_data = NULL;
            int numElements = 0;
            gboolean embedding_on_device = false;

            //! Attaching Embedding tensor metadata
            for (NvDsMetaList *l_user = obj_meta->obj_user_meta_list;
                l_user != NULL; l_user = l_user->next) {
              NvDsUserMeta *user_meta = (NvDsUserMeta *)l_user->data;
              if (use_tracker_reid && user_meta->base_meta.meta_type == NVDS_TRACKER_OBJ_REID_META) {
                /** Use embedding from tracker reid*/
                NvDsObjReid *pReidObj = (NvDsObjReid *) (user_meta->user_meta_data);
                if (pReidObj != NULL && pReidObj->ptr_host != NULL && pReidObj->featureSize > 0) {
                  numElements = pReidObj->featureSize;
                  embedding_data = (float *)(pReidObj->ptr_host);

                  if (tracker_reid_store_age > 0) {
                    if (frame_embedding == NULL) {
                      frame_embedding = (FrameEmbedding *) g_malloc0(sizeof(FrameEmbedding));
                      frame_embedding->frame_num = frame_meta->frame_num;
                      frame_embedding->obj_embeddings = NULL;
                    }
                    ObjEmbedding *obj_emb = (ObjEmbedding *) g_malloc0(sizeof(ObjEmbedding));
                    obj_emb->object_id = obj_meta->object_id;
                    obj_emb->num_elements = numElements;
                    obj_emb->embedding = g_malloc0(sizeof(float) * numElements);
                    // memcpy(obj_emb->embedding, embedding_data, sizeof(float) * numElements);
                    cudaMemcpy(obj_emb->embedding, (float *)(pReidObj->ptr_host),
                      sizeof(float) * numElements, cudaMemcpyDeviceToHost);
                    frame_embedding->obj_embeddings = g_list_append(frame_embedding->obj_embeddings, obj_emb);
                  }
                }
              }
              else if ((!use_tracker_reid) && user_meta->base_meta.meta_type == NVDSINFER_TENSOR_OUTPUT_META) {
                /* Use embedding from SGIE reid */
                NvDsInferTensorMeta *tensor_meta =
                    (NvDsInferTensorMeta *)user_meta->user_meta_data;

                NvDsInferDims embedding_dims =
                    tensor_meta->output_layers_info[0].inferDims;

                numElements = embedding_dims.d[0];
                embedding_data = (float *)(tensor_meta->out_buf_ptrs_dev[0]);
                embedding_on_device = true;
              }

              /* Get the labels for the FSL DUAL HEAD app */
              if (model_used == APP_CONFIG_ANALYTICS_FSL_DUAL_HEAD) {
                /* Convert to tensor metadata */
                NvDsInferTensorMeta *tensor_meta =
                    (NvDsInferTensorMeta *) user_meta->user_meta_data;

                /* Copy tensor from GPU to host*/
                for (unsigned int i = 0; i < tensor_meta->num_output_layers; i++) {
                  NvDsInferLayerInfo *info = &tensor_meta->output_layers_info[i];
                  info->buffer = tensor_meta->out_buf_ptrs_host[i];
                  if (tensor_meta->out_buf_ptrs_dev[i]) {
                    cudaMemcpy (tensor_meta->out_buf_ptrs_host[i], tensor_meta->out_buf_ptrs_dev[i],
                        info->inferDims.numElements * 4, cudaMemcpyDeviceToHost);
                  }
                }

                NvDsInferDimsCHW dims;

                /* Access the 1-indexed output layer to get probs */
                getDimsCHWFromDims (dims, tensor_meta->output_layers_info[1].inferDims);
                dual_head_classes = dims.c;

                float *outputCoverageBuffer =
                    (float *) tensor_meta->output_layers_info[1].buffer;
                float maxProbability = 0;
                bool attrFound = false;
                NvDsInferAttribute attr;

                /* Get the output with max probability */
                for (unsigned int c = 0; c < dual_head_classes; c++) {
                  float probability = outputCoverageBuffer[c];
                  if (probability > 0 && probability > maxProbability) {
                    maxProbability = probability;
                    attrFound = true;
                    attr.attributeIndex = 0;
                    attr.attributeValue = c;
                    attr.attributeConfidence = probability;
                  }
                }

                /* Generate classifer metadata and attach to obj_meta */
                if (attrFound) {
                  NvDsClassifierMeta *classifier_meta =
                      nvds_acquire_classifier_meta_from_pool (batch_meta);

                  classifier_meta->unique_component_id = tensor_meta->unique_id;

                  /* Create NvDsLabel Info*/
                  NvDsLabelInfo *label_info =
                      nvds_acquire_label_info_meta_from_pool (batch_meta);
                  label_info->result_class_id = attr.attributeValue;
                  label_info->result_prob = attr.attributeConfidence;

                  // Safely copy the label with bounds checking
                  snprintf(label_info->result_label, sizeof(label_info->result_label), "%s",
                          labels[label_info->result_class_id]);

                  gchar *temp = obj_meta->text_params.display_text;
                  obj_meta->text_params.display_text =
                      g_strconcat (temp, " ", label_info->result_label, NULL);
                  g_free (temp);

                  nvds_add_label_info_meta_to_classifier (classifier_meta, label_info);
                  nvds_add_classifier_meta_to_object (obj_meta, classifier_meta);
                  snprintf(dual_head_label, sizeof(dual_head_label), "%s", label_info->result_label);
                }
              }
            }

            if (use_tracker_reid && tracker_reid_store_age > 0 && embedding_data == NULL) {
              embedding_data = retrieve_embedding_queue(stream_id, frame_meta->frame_num,
                obj_meta->object_id, &numElements);
            }

            /** Generate NvDsEventMsgMeta for every object */
            NvDsEventMsgMeta *msg_meta =
                (NvDsEventMsgMeta *)g_malloc0(sizeof(NvDsEventMsgMeta));
            // FIXED: Pass the actual object label instead of dual_head_label for MTMC mode
            gchar *label_to_pass = (model_used == APP_CONFIG_ANALYTICS_FSL_DUAL_HEAD) ? 
                                   dual_head_label : obj_meta->obj_label;
            generate_event_msg_meta(
                appCtx, msg_meta, obj_meta->class_id, TRUE,
                /**< useTs NOTE: Pass FALSE for files without base-timestamp in URI
                 */
                buffer_pts, appCtx->config.multi_source_config[stream_id].uri,
                stream_id, stream_id, obj_meta, scaleW, scaleH, frame_meta,
                embedding_data, numElements, embedding_on_device, label_to_pass);
            if (log_level == 99 || log_level == 100) {
              g_print("[DEBUG]: Timestamp after msg meta creation: %s\n",
                      msg_meta->ts);

              g_print(
                  "[DEBUG]: NvDsEventMsgMeta: {'sensor-id': '%s', 'frameId': '%d', "
                  "'timestamp': '%s', 'object-id': '%s', 'confidence': '%f', "
                  "'bbox': [%.2f, %.2f, %.2f, %.2f]}\n",
                  msg_meta->sensorId, msg_meta->frameId, msg_meta->ts,
                  msg_meta->objectId, msg_meta->confidence, msg_meta->bbox.left,
                  msg_meta->bbox.top, msg_meta->bbox.width, msg_meta->bbox.height);
            }

            testAppCtx->streams[stream_id].meta_number++;
            NvDsUserMeta *user_event_meta =
                nvds_acquire_user_meta_from_pool(batch_meta);
            if (user_event_meta) {
              /*
              * Since generated event metadata has custom objects for
              * Vehicle / Person which are allocated dynamically, we are
              * setting copy and free function to handle those fields when
              * metadata copy happens between two components.
              */
              user_event_meta->user_meta_data = (void *)msg_meta;
              user_event_meta->base_meta.batch_meta = batch_meta;
              user_event_meta->base_meta.meta_type = NVDS_EVENT_MSG_META;
              user_event_meta->base_meta.copy_func =
                  (NvDsMetaCopyFunc)meta_copy_func;
              user_event_meta->base_meta.release_func =
                  (NvDsMetaReleaseFunc)meta_free_func;
              nvds_add_user_meta_to_frame(frame_meta, user_event_meta);
            } else {
              if (log_level >= LOG_LVL_ERROR) {
                g_print("Error in attaching event meta to buffer\n");
              }
            }
          }
        }
      }
      if(appCtx->config.dummy_payload && (valid_class_id == 0))
      {
          NvDsEventMsgMeta *msg_meta =
            (NvDsEventMsgMeta *) g_malloc0 (sizeof (NvDsEventMsgMeta));
          generate_event_msg_meta_dummy (appCtx, msg_meta, stream_id, frame_meta);
          NvDsUserMeta *user_event_meta =
            nvds_acquire_user_meta_from_pool (batch_meta);
          if (user_event_meta) {
            /*
              * Since generated event metadata has custom objects for
              * Vehicle / Person which are allocated dynamically, we are
              * setting copy and free function to handle those fields when
              * metadata copy happens between two components.
              */
            user_event_meta->user_meta_data = (void *) msg_meta;
            user_event_meta->base_meta.batch_meta = batch_meta;
            user_event_meta->base_meta.meta_type = NVDS_EVENT_MSG_META;
            user_event_meta->base_meta.copy_func =
              (NvDsMetaCopyFunc) meta_copy_func;
            user_event_meta->base_meta.release_func =
              (NvDsMetaReleaseFunc) meta_free_func;
            nvds_add_user_meta_to_frame (frame_meta, user_event_meta);
          } else {
            g_print ("Error in attaching event meta to buffer\n");
          }
      }
      if (use_tracker_reid && tracker_reid_store_age > 0 && frame_embedding) {
        push_to_embedding_queue(stream_id, frame_embedding);
      }

      //! DEBUGGER_START to add timestamp to OSD label
      if (log_level == 100 || log_level == 99) {
        GstClockTime ts_generated;
        NvDsDisplayMeta *display_meta =
            nvds_acquire_display_meta_from_pool(batch_meta);
        NvOSD_TextParams *txt_params = &display_meta->text_params[0];
        display_meta->num_labels = 1;
        txt_params->display_text = g_malloc0(MAX_DISPLAY_LEN);
        char timestamp_buf[MAX_TIME_STAMP_LEN];
        ts_generated = generate_ts_rfc3339_from_ts(
            timestamp_buf, MAX_TIME_STAMP_LEN, buf_ntp_time, "", stream_id);
        int offset = snprintf(txt_params->display_text, MAX_DISPLAY_LEN, "%s",
                              timestamp_buf);
        /* Now set the offsets where the string should appear */
        txt_params->x_offset = 10;
        txt_params->y_offset = 12;

        /* Font , font-color and font-size */
        txt_params->font_params.font_name = "Serif";
        txt_params->font_params.font_size = 10;
        txt_params->font_params.font_color.red = 1.0;
        txt_params->font_params.font_color.green = 1.0;
        txt_params->font_params.font_color.blue = 1.0;
        txt_params->font_params.font_color.alpha = 1.0;

        /* Text background color */
        txt_params->set_bg_clr = 1;
        txt_params->text_bg_clr.red = 0.0;
        txt_params->text_bg_clr.green = 0.0;
        txt_params->text_bg_clr.blue = 0.0;
        txt_params->text_bg_clr.alpha = 1.0;
        nvds_add_display_meta_to_frame(frame_meta, display_meta);
      }

      //! DEBUGGER_END

      testAppCtx->streams[stream_id].frameCount++;
    }
  }
}

/** @{ imported from deepstream-app as is */

/**
 * Function to handle program interrupt signal.
 * It installs default handler after handling the interrupt.
 */
static void _intr_handler(int signum) {
  struct sigaction action;

  NVGSTDS_ERR_MSG_V("User Interrupted.. \n");

  memset(&action, 0, sizeof(action));
  action.sa_handler = SIG_DFL;

  sigaction(SIGINT, &action, NULL);

  cintr = TRUE;
}

/**
 * callback function to print the performance numbers of each stream.
 */
static void
perf_cb (gpointer context, NvDsAppPerfStruct * str)
{
  static guint header_print_cnt = 0;
  guint i;
  AppCtx *appCtx = (AppCtx *) context;
  guint numf = str->num_instances;

  g_mutex_lock (&fps_lock);
  guint active_src_count = 0;

  if (!str->use_nvmultiurisrcbin) {
    for (i = 0; i < numf; i++) {
      fps[i] = str->fps[i];
      if (fps[i]){
        active_src_count++;
      }
      fps_avg[i] = str->fps_avg[i];
    }
    g_print("Active sources : %u\n", active_src_count);
    if (header_print_cnt % 20 == 0) {
      g_print ("\n**PERF:  ");
      for (i = 0; i < numf; i++) {
        g_print ("FPS %d (Avg)\t", i);
      }
      g_print ("\n");
      header_print_cnt = 0;
    }
    header_print_cnt++;

    time_t t = time (NULL);
    struct tm tm_buf;
    struct tm *tm = localtime_r (&t, &tm_buf);
    char asc_buf[26];
    printf ("%s", asctime_r (tm, asc_buf));
    if (num_instances > 1)
      g_print ("PERF(%d): ", appCtx->index);
    else
      g_print ("**PERF:  ");

    for (i = 0; i < numf; i++) {
      g_print ("%.5f (%.5f)\t", fps[i], fps_avg[i]);
    }
  } else {
    for (guint j = 0; j < str->active_source_size; j++) {
      i = str->source_detail[j].source_id;
      fps[i] = str->fps[i];
      if (fps[i]){
        active_src_count++;
      }
      fps_avg[i] = str->fps_avg[i];
    }
    g_print("Active sources : %u\n", active_src_count);
    if (header_print_cnt % 20 == 0) {
      g_print ("\n**PERF:  ");
      for (guint j = 0; j < str->active_source_size; j++) {
        i = str->source_detail[j].source_id;
        g_print ("FPS %d (Avg)\t", i);
      }
      g_print ("\n");
      header_print_cnt = 0;
    }
    header_print_cnt++;

    time_t t = time (NULL);
    struct tm tm_buf;
    struct tm *tm = localtime_r (&t, &tm_buf);
    char asc_buf[26];
    printf ("%s", asctime_r (tm, asc_buf));
    if (num_instances > 1)
      g_print ("PERF(%d): ", appCtx->index);
    else
      g_print ("**PERF:  ");

    g_print("\n");
    for (guint j = 0; j < str->active_source_size; j++) {
      i = str->source_detail[j].source_id;
      g_print ("%.5f (%.5f)\t", fps[i], fps_avg[i]);
      if (str->stream_name_display) {
        if (show_sensor_id)
          g_print("source_id : %d stream_name %s sensor_id %s\n",i,str->source_detail[j].sensor_name,str->source_detail[j].sensor_id);
        else
          g_print("source_id : %d stream_name %s\n",i,str->source_detail[j].sensor_name);
      }
    }
  }
  g_print ("\n");
  g_mutex_unlock (&fps_lock);
}

/**
 * Loop function to check the status of interrupts.
 * It comes out of loop if application got interrupted.
 */
static gboolean check_for_interrupt(gpointer data) {
  if (quit) {
    return FALSE;
  }

  if (cintr) {
    cintr = FALSE;

    quit = TRUE;
    g_main_loop_quit(main_loop);

    return FALSE;
  }
  return TRUE;
}

/*
 * Function to install custom handler for program interrupt signal.
 */
static void _intr_setup(void) {
  struct sigaction action;

  memset(&action, 0, sizeof(action));
  action.sa_handler = _intr_handler;

  sigaction(SIGINT, &action, NULL);
}

static gboolean kbhit(void) {
  struct timeval tv;
  fd_set rdfs;

  tv.tv_sec = 0;
  tv.tv_usec = 0;

  FD_ZERO(&rdfs);
  FD_SET(STDIN_FILENO, &rdfs);

  select(STDIN_FILENO + 1, &rdfs, NULL, NULL, &tv);
  return FD_ISSET(STDIN_FILENO, &rdfs);
}

/*
 * Function to enable / disable the canonical mode of terminal.
 * In non canonical mode input is available immediately (without the user
 * having to type a line-delimiter character).
 */
static void changemode(int dir) {
  static struct termios oldt, newt;

  if (dir == 1) {
    tcgetattr(STDIN_FILENO, &oldt);
    newt = oldt;
    newt.c_lflag &= ~(ICANON);
    tcsetattr(STDIN_FILENO, TCSANOW, &newt);
  } else
    tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
}

static void print_runtime_commands(void) {
  g_print(
      "\nRuntime commands:\n"
      "\th: Print this help\n"
      "\tq: Quit\n\n"
      "\tp: Pause\n"
      "\tr: Resume\n\n");

  if (appCtx[0]->config.tiled_display_config.enable) {
    g_print(
        "NOTE: To expand a source in the 2D tiled display and view object "
        "details,"
        " left-click on the source.\n"
        "      To go back to the tiled display, right-click anywhere on the "
        "window.\n\n");
  }
}

/**
 * Loop function to check keyboard inputs and status of each pipeline.
 */
static gboolean event_thread_func(gpointer arg) {
  guint i;
  gboolean ret = TRUE;

  // Check if all instances have quit
  for (i = 0; i < num_instances; i++) {
    if (!appCtx[i]->quit) break;
  }

  if (i == num_instances) {
    quit = TRUE;
    g_main_loop_quit(main_loop);
    return FALSE;
  }
  // Check for keyboard input
  if (!kbhit()) {
    // continue;
    return TRUE;
  }
  int c = fgetc(stdin);

  gint source_id;
  GstElement *tiler = appCtx[rcfg]->pipeline.tiled_display_bin.tiler;

  if (appCtx[rcfg]->config.tiled_display_config.enable) {
    // g_object_get(G_OBJECT(tiler), "show-source", &source_id, NULL);

    if (selecting) {
      if (rrowsel == FALSE) {
        if (c >= '0' && c <= '9') {
          rrow = c - '0';
          if (log_level >= LOG_LVL_DEBUG) {
            g_print("--selecting source  row %d--\n", rrow);
          }
          rrowsel = TRUE;
        }
      } else {
        if (c >= '0' && c <= '9') {
          int tile_num_columns =
              appCtx[rcfg]->config.tiled_display_config.columns;
          rcol = c - '0';
          selecting = FALSE;
          rrowsel = FALSE;
          source_id = tile_num_columns * rrow + rcol;
          if (log_level >= LOG_LVL_DEBUG) {
            g_print("--selecting source  col %d sou=%d--\n", rcol, source_id);
          }
          if (source_id >= (gint)appCtx[rcfg]->config.num_source_sub_bins) {
            source_id = -1;
          } else {
            appCtx[rcfg]->show_bbox_text = TRUE;
            appCtx[rcfg]->active_source_index = source_id;
            // g_object_set(G_OBJECT(tiler), "show-source", source_id, NULL);
          }
        }
      }
    }
  }
  switch (c) {
    case 'h':
      print_runtime_commands();
      break;
    case 'p':
      for (i = 0; i < num_instances; i++) pause_pipeline(appCtx[i]);
      break;
    case 'r':
      for (i = 0; i < num_instances; i++) resume_pipeline(appCtx[i]);
      break;
    case 'q':
      quit = TRUE;
      g_main_loop_quit(main_loop);
      ret = FALSE;
      break;
    case 'c':
      if (appCtx[rcfg]->config.tiled_display_config.enable &&
          selecting == FALSE && source_id == -1) {
        if (log_level >= LOG_LVL_DEBUG) g_print("--selecting config file --\n");
        c = fgetc(stdin);
        if (c >= '0' && c <= '9') {
          rcfg = c - '0';
          if (rcfg < num_instances) {
            if (log_level >= LOG_LVL_DEBUG)
              g_print("--selecting config  %d--\n", rcfg);
          } else {
            if (log_level >= LOG_LVL_DEBUG)
              g_print("--selected config file %d out of bound, reenter\n",
                      rcfg);
            rcfg = 0;
          }
        }
      }
      break;
    case 'z':
      if (appCtx[rcfg]->config.tiled_display_config.enable && source_id == -1 &&
          selecting == FALSE) {
        if (log_level >= LOG_LVL_DEBUG) g_print("--selecting source --\n");
        selecting = TRUE;
      } else {
        if (!show_bbox_text) {
          GstElement *nvosd =
              appCtx[rcfg]->pipeline.instance_bins[0].osd_bin.nvosd;
          g_object_set(G_OBJECT(nvosd), "display-text", FALSE, NULL);
          g_object_set(G_OBJECT(tiler), "show-source", -1, NULL);
        }
        appCtx[rcfg]->active_source_index = -1;
        selecting = FALSE;
        rcfg = 0;
        if (log_level >= LOG_LVL_DEBUG) g_print("--tiled mode --\n");
      }
      break;
    default:
      break;
  }
  return ret;
}

static int get_source_id_from_coordinates(float x_rel, float y_rel,
                                          AppCtx *appCtx) {
  int tile_num_rows = appCtx->config.tiled_display_config.rows;
  int tile_num_columns = appCtx->config.tiled_display_config.columns;

  int source_id = (int)(x_rel * tile_num_columns);
  source_id += ((int)(y_rel * tile_num_rows)) * tile_num_columns;

  /* Don't allow clicks on empty tiles. */
  if (source_id >= (gint)appCtx->config.num_source_sub_bins) source_id = -1;

  return source_id;
}

/**
 * Thread to monitor X window events.
 */
static gpointer nvds_x_event_thread(gpointer data) {
  g_mutex_lock(&disp_lock);
  while (display) {
    XEvent e;
    guint index;
    while (XPending(display)) {
      XNextEvent(display, &e);
      switch (e.type) {
        case ButtonPress: {
          XWindowAttributes win_attr;
          XButtonEvent ev = e.xbutton;
          gint source_id;
          GstElement *tiler;

          XGetWindowAttributes(display, ev.window, &win_attr);

          for (index = 0; index < MAX_INSTANCES; index++)
            if (ev.window == windows[index]) break;

          tiler = appCtx[index]->pipeline.tiled_display_bin.tiler;
          g_object_get(G_OBJECT(tiler), "show-source", &source_id, NULL);

          if (ev.button == Button1 && source_id == -1) {
            source_id = get_source_id_from_coordinates(
                ev.x * 1.0 / win_attr.width, ev.y * 1.0 / win_attr.height,
                appCtx[index]);
            if (source_id > -1) {
              g_object_set(G_OBJECT(tiler), "show-source", source_id, NULL);
              appCtx[index]->active_source_index = source_id;
              appCtx[index]->show_bbox_text = TRUE;
              GstElement *nvosd =
                  appCtx[index]->pipeline.instance_bins[0].osd_bin.nvosd;
              g_object_set(G_OBJECT(nvosd), "display-text", TRUE, NULL);
            }
          } else if (ev.button == Button3) {
            g_object_set(G_OBJECT(tiler), "show-source", -1, NULL);
            appCtx[index]->active_source_index = -1;
            if (!show_bbox_text) {
              appCtx[index]->show_bbox_text = FALSE;
              GstElement *nvosd =
                  appCtx[index]->pipeline.instance_bins[0].osd_bin.nvosd;
              g_object_set(G_OBJECT(nvosd), "display-text", FALSE, NULL);
            }
          }
        } break;
        case KeyRelease: {
          KeySym p, r, q;
          guint i;
          p = XKeysymToKeycode(display, XK_P);
          r = XKeysymToKeycode(display, XK_R);
          q = XKeysymToKeycode(display, XK_Q);
          if (e.xkey.keycode == p) {
            for (i = 0; i < num_instances; i++) pause_pipeline(appCtx[i]);
            break;
          }
          if (e.xkey.keycode == r) {
            for (i = 0; i < num_instances; i++) resume_pipeline(appCtx[i]);
            break;
          }
          if (e.xkey.keycode == q) {
            quit = TRUE;
            g_main_loop_quit(main_loop);
          }
        } break;
        case ClientMessage: {
          Atom wm_delete;
          for (index = 0; index < MAX_INSTANCES; index++)
            if (e.xclient.window == windows[index]) break;

          wm_delete = XInternAtom(display, "WM_DELETE_WINDOW", 1);
          if (wm_delete != None && wm_delete == (Atom)e.xclient.data.l[0]) {
            quit = TRUE;
            g_main_loop_quit(main_loop);
          }
        } break;
      }
    }
    g_mutex_unlock(&disp_lock);
    g_usleep(G_USEC_PER_SEC / 20);
    g_mutex_lock(&disp_lock);
  }
  g_mutex_unlock(&disp_lock);
  return NULL;
}

/**
 * callback function to add application specific metadata.
 * Here it demonstrates how to display the URI of source in addition to
 * the text generated after inference.
 */
static gboolean overlay_graphics(AppCtx *appCtx, GstBuffer *buf,
                                 NvDsBatchMeta *batch_meta, guint index) {
  return TRUE;
}

/**
 * Callback function to notify the status of the model update
 */
static void infer_model_updated_cb(GstElement *gie, gint err,
                                   const gchar *config_file) {
  double otaTime = 0;
  gettimeofday(&ota_completion_time, NULL);

  otaTime = (ota_completion_time.tv_sec - ota_request_time.tv_sec) * 1000.0;
  otaTime += (ota_completion_time.tv_usec - ota_request_time.tv_usec) / 1000.0;

  const char *err_str = (err == 0 ? "ok" : "failed");
  if (log_level >= LOG_LVL_DEBUG)
    g_print(
        "\nModel Update Status: Updated model : %s, OTATime = %f ms, result: "
        "%s "
        "\n\n",
        config_file, otaTime, err_str);
}

/**
 * Function to print detected Inotify handler events
 * Used only for debugging purposes
 */
static void display_inotify_event(struct inotify_event *i_event) {
  if (log_level >= LOG_LVL_DEBUG) {
    printf("    watch decriptor =%2d; ", i_event->wd);
    if (i_event->cookie > 0) printf("cookie =%4d; ", i_event->cookie);

    printf("mask = ");
    if (i_event->mask & IN_ACCESS) printf("IN_ACCESS ");
    if (i_event->mask & IN_ATTRIB) printf("IN_ATTRIB ");
    if (i_event->mask & IN_CLOSE_NOWRITE) printf("IN_CLOSE_NOWRITE ");
    if (i_event->mask & IN_CLOSE_WRITE) printf("IN_CLOSE_WRITE ");
    if (i_event->mask & IN_CREATE) printf("IN_CREATE ");
    if (i_event->mask & IN_DELETE) printf("IN_DELETE ");
    if (i_event->mask & IN_DELETE_SELF) printf("IN_DELETE_SELF ");
    if (i_event->mask & IN_IGNORED) printf("IN_IGNORED ");
    if (i_event->mask & IN_ISDIR) printf("IN_ISDIR ");
    if (i_event->mask & IN_MODIFY) printf("IN_MODIFY ");
    if (i_event->mask & IN_MOVE_SELF) printf("IN_MOVE_SELF ");
    if (i_event->mask & IN_MOVED_FROM) printf("IN_MOVED_FROM ");
    if (i_event->mask & IN_MOVED_TO) printf("IN_MOVED_TO ");
    if (i_event->mask & IN_OPEN) printf("IN_OPEN ");
    if (i_event->mask & IN_Q_OVERFLOW) printf("IN_Q_OVERFLOW ");
    if (i_event->mask & IN_UNMOUNT) printf("IN_UNMOUNT ");

    if (i_event->mask & IN_CLOSE) printf("IN_CLOSE ");
    if (i_event->mask & IN_MOVE) printf("IN_MOVE ");
    if (i_event->mask & IN_UNMOUNT) printf("IN_UNMOUNT ");
    if (i_event->mask & IN_IGNORED) printf("IN_IGNORED ");
    if (i_event->mask & IN_Q_OVERFLOW) printf("IN_Q_OVERFLOW ");
    printf("\n");

    if (i_event->len > 0)
      printf("        name = %s mask= %x \n", i_event->name, i_event->mask);
  }
}

/**
 * Perform model-update OTA operation
 */
void apply_ota(AppCtx *ota_appCtx) {
  GstElement *primary_gie = NULL;

  if (ota_appCtx->override_config.primary_gie_config.enable) {
    primary_gie =
        ota_appCtx->pipeline.common_elements.primary_gie_bin.primary_gie;
    gchar *model_engine_file_path =
        ota_appCtx->override_config.primary_gie_config.model_engine_file_path;

    gettimeofday(&ota_request_time, NULL);
    if (model_engine_file_path) {
      if (log_level >= LOG_LVL_DEBUG) {
        g_print("\nNew Model Update Request %s ----> %s\n",
                GST_ELEMENT_NAME(primary_gie), model_engine_file_path);
      }
      g_object_set(G_OBJECT(primary_gie), "model-engine-file",
                   model_engine_file_path, NULL);
    } else {
      if (log_level >= LOG_LVL_DEBUG) {
        g_print(
            "\nInvalid New Model Update Request received. Property "
            "model-engine-path is not set\n");
      }
    }
  }
}

/**
 * Independent thread to perform model-update OTA process based on the inotify
 * events It handles currently two scenarios 1) Local Model Update Request (e.g.
 * Standalone Appliation) In this case, notifier handler watches for the
 * ota_override_file changes 2) Cloud Model Update Request (e.g. EGX with
 * Kubernetes) In this case, notifier handler watches for the ota_override_file
 * changes along with
 *    ..data directory which gets mounted by EGX deployment in Kubernetes
 * environment.
 */
gpointer ota_handler_thread(gpointer data) {
  int length, i = 0;
  char buffer[INOTIFY_EVENT_BUF_LEN];
  OTAInfo *ota = (OTAInfo *)data;
  gchar *ota_ds_config_file = ota->override_cfg_file;
  AppCtx *ota_appCtx = ota->appCtx;
  struct stat file_stat = {0};
  GstElement *primary_gie = NULL;
  gboolean connect_pgie_signal = FALSE;

  ota_appCtx->ota_inotify_fd = inotify_init();

  if (ota_appCtx->ota_inotify_fd < 0) {
    perror("inotify_init");
    return NULL;
  }

  char *real_path_ds_config_file = realpath(ota_ds_config_file, NULL);
  if (log_level >= LOG_LVL_DEBUG) {
    g_print("REAL PATH = %s\n", real_path_ds_config_file);
  }

  gchar *ota_dir = g_path_get_dirname(real_path_ds_config_file);
  ota_appCtx->ota_watch_desc =
      inotify_add_watch(ota_appCtx->ota_inotify_fd, ota_dir, IN_ALL_EVENTS);

  int ret = lstat(ota_ds_config_file, &file_stat);
  ret = ret;

  if (S_ISLNK(file_stat.st_mode)) {
    if (log_level >= LOG_LVL_DEBUG) {
      printf(" Override File Provided is Soft Link\n");
    }
    gchar *parent_ota_dir = g_strdup_printf("%s/..", ota_dir);
    ota_appCtx->ota_watch_desc = inotify_add_watch(
        ota_appCtx->ota_inotify_fd, parent_ota_dir, IN_ALL_EVENTS);
  }

  while (1) {
    i = 0;
    length = read(ota_appCtx->ota_inotify_fd, buffer, INOTIFY_EVENT_BUF_LEN);

    if (length < 0) {
      perror("read");
    }

    if (quit == TRUE) goto done;

    while (i < length) {
      struct inotify_event *event = (struct inotify_event *)&buffer[i];

      // Enable below function to print the inotify events, used for debugging
      // purpose
      if (0) {
        display_inotify_event(event);
      }

      if (connect_pgie_signal == FALSE) {
        primary_gie =
            ota_appCtx->pipeline.common_elements.primary_gie_bin.primary_gie;
        if (primary_gie) {
          g_signal_connect(G_OBJECT(primary_gie), "model-updated",
                           G_CALLBACK(infer_model_updated_cb), NULL);
          connect_pgie_signal = TRUE;
        } else {
          if (log_level >= LOG_LVL_WARN) {
            printf(
                "Gstreamer pipeline element nvinfer is yet to be created or "
                "invalid\n");
          }
          continue;
        }
      }

      if (event->len) {
        if (event->mask & IN_MOVED_TO) {
          if (strstr("..data", event->name)) {
            memset(&ota_appCtx->override_config, 0,
                   sizeof(ota_appCtx->override_config));
            if (!IS_YAML(ota_ds_config_file)) {
              if (!parse_config_file(&ota_appCtx->override_config,
                                     ota_ds_config_file)) {
                NVGSTDS_ERR_MSG_V("Failed to parse config file '%s'",
                                  ota_ds_config_file);
                if (log_level >= LOG_LVL_ERROR) {
                  g_print(
                      "Error: ota_handler_thread: Failed to parse config file "
                      "'%s'",
                      ota_ds_config_file);
                }
              } else {
                apply_ota(ota_appCtx);
              }
            } else if (IS_YAML(ota_ds_config_file)) {
              if (!parse_config_file_yaml(&ota_appCtx->override_config,
                                          ota_ds_config_file)) {
                NVGSTDS_ERR_MSG_V("Failed to parse config file '%s'",
                                  ota_ds_config_file);
                if (log_level >= LOG_LVL_ERROR) {
                  g_print(
                      "Error: ota_handler_thread: Failed to parse config file "
                      "'%s'",
                      ota_ds_config_file);
                }
              } else {
                apply_ota(ota_appCtx);
              }
            }
          }
        }
        if (event->mask & IN_CLOSE_WRITE) {
          if (!(event->mask & IN_ISDIR)) {
            if (strstr(ota_ds_config_file, event->name)) {
              if (log_level >= LOG_LVL_DEBUG) {
                g_print("File %s modified.\n", event->name);
              }

              memset(&ota_appCtx->override_config, 0,
                     sizeof(ota_appCtx->override_config));
              if (!IS_YAML(ota_ds_config_file)) {
                if (!parse_config_file(&ota_appCtx->override_config,
                                       ota_ds_config_file)) {
                  NVGSTDS_ERR_MSG_V("Failed to parse config file '%s'",
                                    ota_ds_config_file);
                  if (log_level >= LOG_LVL_ERROR) {
                    g_print(
                        "Error: ota_handler_thread: Failed to parse config "
                        "file "
                        "'%s'",
                        ota_ds_config_file);
                  }
                } else {
                  apply_ota(ota_appCtx);
                }
              } else if (IS_YAML(ota_ds_config_file)) {
                if (!parse_config_file_yaml(&ota_appCtx->override_config,
                                            ota_ds_config_file)) {
                  NVGSTDS_ERR_MSG_V("Failed to parse config file '%s'",
                                    ota_ds_config_file);
                  if (log_level >= LOG_LVL_ERROR) {
                    g_print(
                        "Error: ota_handler_thread: Failed to parse config "
                        "file "
                        "'%s'",
                        ota_ds_config_file);
                  }
                } else {
                  apply_ota(ota_appCtx);
                }
              }
            }
          }
        }
      }
      i += INOTIFY_EVENT_SIZE + event->len;
    }
  }
done:
  inotify_rm_watch(ota_appCtx->ota_inotify_fd, ota_appCtx->ota_watch_desc);
  close(ota_appCtx->ota_inotify_fd);

  free(real_path_ds_config_file);
  g_free(ota_dir);

  g_free(ota);
  return NULL;
}

/**
 * Parse labels from label file and fill them in the global variable: `labels`
 */
void get_labels_from_file(gchar* classifier_label_file){

  // Use file pointer
  FILE *file = fopen(classifier_label_file, "r");

  // Return if file is not found
  if (file == NULL) {
    perror("Error opening file.");
    return;
  }

  guint line_count = 0;
  while (fgets(labels[line_count], MAX_CHAR_LENGTH_PER_LINE, file) != NULL) {

    // Remove newline character from the end of the line
    labels[line_count][strcspn(labels[line_count], "\n")] = '\0';
    line_count++;

    if (line_count == dual_head_classes)
      break;
    else if (line_count > dual_head_classes) {
      g_print("You have more labels than classes. Labels after line %d will not be used.\n", dual_head_classes);
      break;
    }
  }

  if ( line_count < dual_head_classes)
    g_print("You have more classes than labels. Empty strings will be used in metadata field when a class without label is predicted.\n");

  // Close the file
  fclose(file);
}

/** @} imported from deepstream-app as is */

int main(int argc, char *argv[]) {
  testAppCtx = (TestAppCtx *)g_malloc0(sizeof(TestAppCtx));
  GOptionContext *ctx = NULL;
  GOptionGroup *group = NULL;
  GError *error = NULL;
  guint i;
  OTAInfo *otaInfo = NULL;
  gchar versionString[256] = {0};

  ctx = g_option_context_new("NVIDIA Metropolis Perception");
  group = g_option_group_new("abc", NULL, NULL, NULL, NULL);
  g_option_group_add_entries(group, entries);

  g_option_context_set_main_group(ctx, group);
  g_option_context_add_group(ctx, gst_init_get_option_group());

  GST_DEBUG_CATEGORY_INIT(NVDS_APP, "NVDS_APP", 0, NULL);

  if (!g_option_context_parse(ctx, &argc, &argv, &error)) {
    NVGSTDS_ERR_MSG_V("%s", error->message);
    g_print("%s", g_option_context_get_help(ctx, TRUE, NULL));
    return -1;
  }

  snprintf(versionString, sizeof(versionString), "%s:%s", IMAGE_PATH, IMAGE_TAG);
  if (log_level >= LOG_LVL_INFO) {
    g_print("Starting Perception Application Image %s\n", versionString);
    g_print("Tiled text: %d\n", show_bbox_text);
    g_print("Playback UTC: %d\n", playback_utc);
    g_print("PGIE model used: %d\n", model_used);
    g_print("no-force-tcp: %d\n", force_tcp);
    g_print("Log level: %d\n", log_level);
    g_print("Message rate: %d\n", message_rate);
    g_print("target-class: %d\n", target_class);
    g_print("Show sensor ID: %d\n", show_sensor_id);
  }

  if (log_level == 99 || log_level == 100) {
    show_bbox_text = TRUE;
  }

  if (print_version) {
    g_print("deepstream-test5-app version %d.%d.%d\n", NVDS_APP_VERSION_MAJOR,
            NVDS_APP_VERSION_MINOR, NVDS_APP_VERSION_MICRO);
    return 0;
  }

  if (print_dependencies_version) {
    g_print("deepstream-test5-app version %d.%d.%d\n", NVDS_APP_VERSION_MAJOR,
            NVDS_APP_VERSION_MINOR, NVDS_APP_VERSION_MICRO);
    return 0;
  }

  if (cfg_files) {
    num_instances = g_strv_length(cfg_files);
  }
  if (input_files) {
    num_input_files = g_strv_length(input_files);
  }

  if (!cfg_files || num_instances == 0) {
    NVGSTDS_ERR_MSG_V("Specify config file with -c option");
    return_value = -1;
    goto done;
  }

  for (i = 0; i < num_instances; i++) {
    appCtx[i] = (AppCtx *)g_malloc0(sizeof(AppCtx));
    appCtx[i]->person_class_id = -1;
    appCtx[i]->car_class_id = -1;
    appCtx[i]->index = i;
    appCtx[i]->active_source_index = -1;
    if (show_bbox_text) {
      appCtx[i]->show_bbox_text = TRUE;
    }

    if (input_files && input_files[i]) {
      appCtx[i]->config.multi_source_config[0].uri =
          g_strdup_printf("file://%s", input_files[i]);
      g_free(input_files[i]);
    }

    /* Initialize msgapi EARLY - before config parsing
     * This allows error reporting even if config parsing fails */
    if (!msgapi_init_early (appCtx[i], cfg_files[i])) {
      g_print("** INFO: Early Message API initialization skipped or failed - error propagation may not be available\n");
    }

    /* Clear previous error messages before parsing */
    if (g_nvds_last_error_message) {
      g_free(g_nvds_last_error_message);
      g_nvds_last_error_message = NULL;
    }

    if (IS_YAML(cfg_files[i])) {
      if (!parse_config_file_yaml(&appCtx[i]->config, cfg_files[i])) {
        NVGSTDS_ERR_MSG_V("Failed to parse config file '%s'", cfg_files[i]);
        // Capture the detailed error from the global error buffer
        if (g_nvds_last_error_message) {
          appCtx[i]->last_error = g_strdup(g_nvds_last_error_message);
          g_free(g_nvds_last_error_message);
          g_nvds_last_error_message = NULL;
        }
        appCtx[i]->return_value = -1;
        goto done;
      }
    } else {
      if (!parse_config_file(&appCtx[i]->config, cfg_files[i])) {
        NVGSTDS_ERR_MSG_V("Failed to parse config file '%s'", cfg_files[i]);
        // Capture the detailed error from the global error buffer
        if (g_nvds_last_error_message) {
          appCtx[i]->last_error = g_strdup(g_nvds_last_error_message);
          g_free(g_nvds_last_error_message);
          g_nvds_last_error_message = NULL;
        }
        appCtx[i]->return_value = -1;
        goto done;
      }
    }

    if (override_cfg_file && override_cfg_file[i]) {
      if (!g_file_test(
              override_cfg_file[i],
              (GFileTest)(G_FILE_TEST_IS_REGULAR | G_FILE_TEST_IS_SYMLINK))) {
        if (log_level >= LOG_LVL_FATAL) {
          g_print("Override file %s does not exist, quitting...\n",
                  override_cfg_file[i]);
        }
        appCtx[i]->return_value = -1;
        goto done;
      }
      otaInfo = (OTAInfo *)g_malloc0(sizeof(OTAInfo));
      otaInfo->appCtx = appCtx[i];
      otaInfo->override_cfg_file = override_cfg_file[i];
      appCtx[i]->ota_handler_thread =
          g_thread_new("ota-handler-thread", ota_handler_thread, otaInfo);
    }
  }

  for (i = 0; i < num_instances; i++) {
    for (guint j = 0; j < appCtx[i]->config.num_source_sub_bins; j++) {
      /** Force the source (applicable only if RTSP)
       * to use TCP for RTP/RTCP channels.
       * forcing TCP to avoid problems with UDP port usage from within docker-
       * container.
       * The UDP RTCP channel when run within docker had issues receiving
       * RTCP Sender Reports from server
       */
      if (force_tcp)
        appCtx[i]->config.multi_source_config[j].select_rtp_protocol = 0x04;
    }
    if (!create_pipeline(appCtx[i], bbox_generated_probe_after_analytics, NULL,
                         perf_cb, overlay_graphics)) {
      NVGSTDS_ERR_MSG_V("Failed to create pipeline");
      return_value = -1;
      goto done;
    }
    /** Now add probe to RTPSession plugin src pad */
    for (guint j = 0; j < appCtx[i]->pipeline.multi_src_bin.num_bins; j++) {
      testAppCtx->streams[j].id = j;
    }
    /** In test5 app, as we could have several sources connected
     * for a typical IoT use-case, raising the nvstreammux's
     * buffer-pool-size to 16 */
    g_object_set(appCtx[i]->pipeline.multi_src_bin.streammux,
                 "buffer-pool-size", STREAMMUX_BUFFER_POOL_SIZE, NULL);
  }

  if (model_used == APP_CONFIG_ANALYTICS_FSL_DUAL_HEAD) {
    NvDsGieConfig *classifier_config = &appCtx[0]->config.secondary_gie_sub_bin_config[0];
    gboolean classifier_enabled = classifier_config->enable;

    if (!classifier_enabled){
      perror("Dual Head Classifier is disabled.");
      return 1;
    }

    gchar *classifier_label_file = classifier_config->label_file_path;
    get_labels_from_file(classifier_label_file);

    if (log_level == 98) {
      uint line_count = sizeof(labels) / sizeof(labels[0]);
      g_print("Read %d lines from the file:\n", line_count);
      for (int i = 0; i < line_count; i++) {
          g_print("Label %d: %s\n", i + 1, labels[i]);
      }
    }
  }

  main_loop = g_main_loop_new(NULL, FALSE);

  _intr_setup();
  g_timeout_add(400, check_for_interrupt, NULL);

  g_mutex_init(&disp_lock);
  display = XOpenDisplay(NULL);
  for (i = 0; i < num_instances; i++) {
    guint j;

    if (!show_bbox_text) {
      GstElement *nvosd = appCtx[i]->pipeline.instance_bins[0].osd_bin.nvosd;
      g_object_set(G_OBJECT(nvosd), "display-text", FALSE, NULL);
    }

    if (gst_element_set_state(appCtx[i]->pipeline.pipeline, GST_STATE_PAUSED) ==
        GST_STATE_CHANGE_FAILURE) {
      NVGSTDS_ERR_MSG_V("Failed to set pipeline to PAUSED");
      return_value = -1;
      goto done;
    }

    for (j = 0; j < appCtx[i]->config.num_sink_sub_bins; j++) {
      XTextProperty xproperty;
      gchar *title;
      guint width, height;
      XSizeHints hints = {0};

      if (!GST_IS_VIDEO_OVERLAY(
              appCtx[i]->pipeline.instance_bins[0].sink_bin.sub_bins[j].sink)) {
        continue;
      }

      if (!display) {
        NVGSTDS_ERR_MSG_V("Could not open X Display");
        return_value = -1;
        goto done;
      }

      if (appCtx[i]->config.sink_bin_sub_bin_config[j].render_config.width)
        width =
            appCtx[i]->config.sink_bin_sub_bin_config[j].render_config.width;
      else
        width = appCtx[i]->config.tiled_display_config.width;

      if (appCtx[i]->config.sink_bin_sub_bin_config[j].render_config.height)
        height =
            appCtx[i]->config.sink_bin_sub_bin_config[j].render_config.height;
      else
        height = appCtx[i]->config.tiled_display_config.height;

      width = (width) ? width : DEFAULT_X_WINDOW_WIDTH;
      height = (height) ? height : DEFAULT_X_WINDOW_HEIGHT;

      hints.flags = PPosition | PSize;
      hints.x =
          appCtx[i]->config.sink_bin_sub_bin_config[j].render_config.offset_x;
      hints.y =
          appCtx[i]->config.sink_bin_sub_bin_config[j].render_config.offset_y;
      hints.width = width;
      hints.height = height;

      windows[i] = XCreateSimpleWindow(
          display, RootWindow(display, DefaultScreen(display)), hints.x,
          hints.y, width, height, 2, 0x00000000, 0x00000000);

      XSetNormalHints(display, windows[i], &hints);

      if (num_instances > 1)
        title = g_strdup_printf(APP_TITLE "-%d", i);
      else
        title = g_strdup(APP_TITLE);
      if (XStringListToTextProperty((char **)&title, 1, &xproperty) != 0) {
        XSetWMName(display, windows[i], &xproperty);
        XFree(xproperty.value);
      }

      XSetWindowAttributes attr = {0};
      if ((appCtx[i]->config.tiled_display_config.enable &&
           appCtx[i]->config.tiled_display_config.rows *
                   appCtx[i]->config.tiled_display_config.columns ==
               1) ||
          (appCtx[i]->config.tiled_display_config.enable == 0)) {
        attr.event_mask = KeyRelease;
      } else if (appCtx[i]->config.tiled_display_config.enable) {
        attr.event_mask = ButtonPress | KeyRelease;
      }
      XChangeWindowAttributes(display, windows[i], CWEventMask, &attr);

      Atom wmDeleteMessage = XInternAtom(display, "WM_DELETE_WINDOW", False);
      if (wmDeleteMessage != None) {
        XSetWMProtocols(display, windows[i], &wmDeleteMessage, 1);
      }
      XMapRaised(display, windows[i]);
      XSync(display, 1);  // discard the events for now
      gst_video_overlay_set_window_handle(
          GST_VIDEO_OVERLAY(
              appCtx[i]->pipeline.instance_bins[0].sink_bin.sub_bins[j].sink),
          (gulong)windows[i]);
      gst_video_overlay_expose(GST_VIDEO_OVERLAY(
          appCtx[i]->pipeline.instance_bins[0].sink_bin.sub_bins[j].sink));
      if (!x_event_thread)
        x_event_thread =
            g_thread_new("nvds-window-event-thread", nvds_x_event_thread, NULL);
    }
  }

  /* Dont try to set playing state if error is observed */
  if (return_value != -1) {
    for (i = 0; i < num_instances; i++) {
      if (gst_element_set_state(appCtx[i]->pipeline.pipeline,
                                GST_STATE_PLAYING) ==
          GST_STATE_CHANGE_FAILURE) {
        g_print("\ncan't set pipeline to playing state.\n");
        return_value = -1;
        goto done;
      }
    }
  }

  print_runtime_commands();

  changemode(1);

  g_timeout_add(40, event_thread_func, NULL);
  g_main_loop_run(main_loop);

  changemode(0);

done:

  g_print("Quitting\n");

  /* GENERIC error reporting via msgapi - catches ALL failures
   * Check each appCtx individually since return_value might not be set yet */
  for (i = 0; i < num_instances; i++) {
    if (appCtx[i] && appCtx[i]->return_value == -1) {
      // Use the captured error message if available, otherwise construct a generic one
      gchar *error_msg = NULL;
      if (appCtx[i]->last_error) {
        error_msg = g_strdup(appCtx[i]->last_error);
      } else if (i < num_instances && cfg_files && cfg_files[i]) {
        error_msg = g_strdup_printf("Application failed to start with config file: %s", cfg_files[i]);
      }

      msgapi_report_error_and_cleanup(appCtx[i], error_msg);

      if (error_msg) {
        g_free(error_msg);
      }
      if (appCtx[i]->last_error) {
        g_free(appCtx[i]->last_error);
        appCtx[i]->last_error = NULL;
      }
    }
  }

  for (i = 0; i < num_instances; i++) {
    if (appCtx[i] == NULL) continue;

    if (appCtx[i]->return_value == -1) return_value = -1;

    destroy_pipeline(appCtx[i]);

    if (appCtx[i]->ota_handler_thread && override_cfg_file[i]) {
      inotify_rm_watch(appCtx[i]->ota_inotify_fd, appCtx[i]->ota_watch_desc);
      g_thread_join(appCtx[i]->ota_handler_thread);
    }

    g_mutex_lock(&disp_lock);
    if (windows[i]) XDestroyWindow(display, windows[i]);
    windows[i] = 0;
    g_mutex_unlock(&disp_lock);

    g_free(appCtx[i]);
  }

  g_mutex_lock(&disp_lock);
  if (display) XCloseDisplay(display);
  display = NULL;
  g_mutex_unlock(&disp_lock);
  g_mutex_clear(&disp_lock);

  if (main_loop) {
    g_main_loop_unref(main_loop);
  }

  if (ctx) {
    g_option_context_free(ctx);
  }

  if (return_value == 0) {
    g_print("App run successful\n");
  } else {
    g_print("App run failed\n");
  }

  gst_deinit();

  if (tracker_reid_store_age > 0) {
    destroy_embedding_queue();
  }
  g_free(testAppCtx);

  return return_value;

  return 0;
}

static gchar *get_first_result_label(NvDsClassifierMeta *classifierMeta) {
  GList *n;
  for (n = classifierMeta->label_info_list; n != NULL; n = n->next) {
    NvDsLabelInfo *labelInfo = (NvDsLabelInfo *)(n->data);
    if (labelInfo->result_label[0] != '\0') {
      return g_strdup(labelInfo->result_label);
    }
  }
  return NULL;
}

static void schema_fill_sample_sgie_vehicle_metadata(NvDsObjectMeta *obj_params,
                                                     NvDsVehicleObject *obj) {
  if (!obj_params || !obj) {
    return;
  }

  /** The JSON obj->classification, say type, color, or make
   * according to the schema shall have null (unknown)
   * classifications (if the corresponding sgie failed to provide a label)
   */
  obj->type = NULL;
  obj->make = NULL;
  obj->model = NULL;
  obj->color = NULL;
  obj->license = NULL;
  obj->region = NULL;

  GList *l;
  for (l = obj_params->classifier_meta_list; l != NULL; l = l->next) {
    NvDsClassifierMeta *classifierMeta = (NvDsClassifierMeta *)(l->data);
    switch (classifierMeta->unique_component_id) {
      case SECONDARY_GIE_VEHICLE_TYPE_UNIQUE_ID:
        obj->type = get_first_result_label(classifierMeta);
        break;
      case SECONDARY_GIE_VEHICLE_COLOR_UNIQUE_ID:
        obj->color = get_first_result_label(classifierMeta);
        break;
      case SECONDARY_GIE_VEHICLE_MAKE_UNIQUE_ID:
        obj->make = get_first_result_label(classifierMeta);
        break;
      default:
        break;
    }
  }
}

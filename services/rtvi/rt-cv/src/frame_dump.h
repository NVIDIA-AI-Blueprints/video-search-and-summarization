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

/*
 * frame_dump.h — Runtime JPEG + metadata dump feature for a DeepStream pipeline.
 *
 * Self-contained module. Attaches a probe to the nvtracker SRC pad and, for the
 * cameras that are switched ON, writes one JPEG + one JSON metadata file
 * (detection bbox + tracker id/bbox) per frame, FPS-gated using the mux PTS.
 *
 * Output layout:
 *   <output_dir>/<sensor_id>/<YYYY-MM-DD>/<frame_num>.jpg
 *   <output_dir>/<sensor_id>/<YYYY-MM-DD>/<frame_num>.txt   (JSON)
 *
 * Control via a small built-in HTTP server (default port 9857, configurable):
 *   POST /dump/config  {"enable":true,"fps":5,"location":"/data/dumps","quality":80}
 *   POST /dump/camera  {"sensor_id":"cam-01","enable":true}
 *   GET  /dump/status
 *
 * nvmultiurisrcbin is NOT touched — this owns its own port/state.
 */
#ifndef FRAME_DUMP_H
#define FRAME_DUMP_H

#include <gst/gst.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Configuration passed once at startup. Runtime-changeable fields (marked) can
 * also be overridden later via POST /dump/config. */
typedef struct _FrameDumpConfig {
  const char *http_port;          /* control port; NULL -> "9857"              */
  const char *bind_address;       /* "0.0.0.0" (default) or "127.0.0.1"        */
  const char *default_output_dir; /* initial dump location (runtime-changeable)*/
  guint       gpu_id;             /* GPU for JPEG encode                       */
  gboolean    enabled;            /* initial global on/off (runtime-changeable)*/
  guint       fps;                /* initial fps (0 -> 1)                       */
  gint        quality;            /* JPEG quality (0 -> 80)                     */
  gboolean    hour_subfolder;     /* add /<HH>/ level (runtime-changeable)     */
  gboolean    unique_filenames;   /* name <frame_num>_<pts> (runtime-changeable)*/
  guint       retention_days;     /* delete day-folders older than N; 0 = keep */
  gboolean    all_cameras;        /* dump every source (ignores per-camera set) */
  const char *enable_sensors;     /* ";"-separated sensor_ids ON at startup    */

  /* Kafka notification: on every dumped frame, publish a JSON message (jpg/txt
   * paths + metadata) to a Kafka topic via the DeepStream nvds_msgapi Kafka
   * protocol adaptor (loaded at runtime; no extra link dependency). */
  gboolean    kafka_enable;       /* enable notifications (runtime-changeable)  */
  const char *kafka_broker;       /* "host;port" connection string (startup)    */
  const char *kafka_topic;        /* notification topic (runtime-changeable)    */
  const char *kafka_proto_lib;    /* adaptor .so; NULL -> libnvds_kafka_proto   */
  const char *kafka_config_file;  /* optional Kafka producer config file        */
} FrameDumpConfig;

/* Start the feature: creates the JPEG-encode context, starts the HTTP control
 * server + async writer + retention sweeper. Returns TRUE on success.
 * `cfg` may be NULL for defaults. */
gboolean frame_dump_init(const FrameDumpConfig *cfg);

/* Convenience: read the [frame-dump] group from a DeepStream config file
 * (GKeyFile .txt) and call frame_dump_init(). Missing group -> feature stays
 * off but the object is still created so REST can enable it later.
 * Keys (all optional): enable, fps, location, port, bind-address, gpu-id,
 * quality, hour-subfolder, unique-filenames, retention-days, enable-sensors. */
gboolean frame_dump_init_from_file(const char *config_file, guint gpu_id);

/* One-call wiring for the shared deepstream-app framework. On the FIRST call it
 * lazily starts the feature from `config_file`'s [frame-dump] group (gpu-id read
 * from that group; deinit auto-registered via atexit); every call attaches the
 * probe to `tracker`. Safe to call from create_common_elements() of every
 * pipeline — no-op if `tracker` is NULL. */
void frame_dump_wire(const char *config_file, GstElement *tracker);

/* Attach the dump probe to the nvtracker element's SRC pad. Call after the
 * pipeline elements exist. */
gboolean frame_dump_attach(GstElement *tracker);

/* Stop the HTTP server + writer, flush, and release the encode context. */
void frame_dump_deinit(void);

#ifdef __cplusplus
}
#endif

#endif /* FRAME_DUMP_H */

/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/**
 * @file
 * <b>NVIDIA GStreamer VST: Metadata </b>
 *
 * @b Description: This file defines the Metadata structure used to
 * carry VST metadata in GStreamer pipeline.
 */

#ifndef GST_NV_VST_META_H
#define GST_NV_VST_META_H

#include <gst/gst.h>

#ifdef __cplusplus
extern "C"
{
#endif

/* test metadata for PTS/DTS and duration */
typedef struct
{
  /* parent GstMeta */
  GstMeta meta;
  /* identifier of the buffer */ 
  gint id;
  /* presentation timestamp of the buffer */
  GstClockTime pts;
  /* Current timestamp in microseconds */
  int64_t ts;
} GstNvVstMeta;

GType gst_nv_vst_meta_api_get_type (void);
#define GST_NV_VST_META_API_TYPE (gst_nv_vst_meta_api_get_type())

const GstMetaInfo *gst_nv_vst_meta_get_info (void);
#define GST_NV_VST_META_INFO (gst_nv_vst_meta_get_info())

#define GST_NV_VST_META_STRING "nvvstmeta"

#define GST_NV_VST_META_GET(buf) ((GstNvVstMeta *)gst_buffer_get_meta(buf,GST_NV_VST_META_API_TYPE))
#define GST_NV_VST_META_ADD(buf) ((GstNvVstMeta *)gst_buffer_add_meta(buf,GST_NV_VST_META_INFO,nullptr))

#ifdef __cplusplus
}
#endif

#endif

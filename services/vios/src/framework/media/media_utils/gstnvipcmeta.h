/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#ifndef GST_NV_IPC_META_H
#define GST_NV_IPC_META_H
#include <gst/gst.h>
inline constexpr int MAX_NUM_RECTS = 32;
#ifdef __cplusplus
extern "C"
{
#endif
typedef struct
{
  float left;
  float top;
  float width;
  float height;
} GstNvIpcRectParams;
/* test metadata for PTS/DTS and duration */
typedef struct
{
  /* parent GstMeta */
  GstMeta meta;
  guint num_rects;
  GstNvIpcRectParams rect_params[MAX_NUM_RECTS];
} GstNvIpcMeta;
GType gst_nv_ipc_meta_api_get_type (void);
#define GST_NV_IPC_META_API_TYPE (gst_nv_ipc_meta_api_get_type())
const GstMetaInfo *gst_nv_ipc_meta_get_info (void);
#define GST_NV_IPC_META_INFO (gst_nv_ipc_meta_get_info())
inline constexpr const char* GST_NV_IPC_META_STRING = "nvipcmeta";
#define GST_NV_IPC_META_GET(buf) ((GstNvIpcMeta *)gst_buffer_get_meta(buf,GST_NV_IPC_META_API_TYPE))
#define GST_NV_IPC_META_ADD(buf) ((GstNvIpcMeta *)gst_buffer_add_meta(buf,GST_NV_IPC_META_INFO,nullptr))
void serialize_meta (GstBuffer *buffer, guint8 **data, guint *length);
void deserialize_meta (GstBuffer *buffer, guint8 *data, guint length);
#ifdef __cplusplus
}
#endif
#endif
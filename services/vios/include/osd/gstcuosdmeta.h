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

#include <gst/gst.h>

extern "C" {
typedef struct _CuOsdMeta CuOsdMeta;

struct _CuOsdMeta {
  GstMeta       meta;

  gint          meta_type;
  gpointer      params;
};

GType cu_osd_meta_api_get_type (void);

#define CU_OSD_META_API_TYPE (cu_osd_meta_api_get_type())

#define gst_buffer_get_cu_osd_meta(b) \
  ((CuOsdMeta*)gst_buffer_get_meta((b),CU_OSD_META_API_TYPE))
const GstMetaInfo *cu_osd_meta_get_info (void);
#define CU_OSD_META_INFO (cu_osd_meta_get_info())

CuOsdMeta * gst_buffer_add_cu_osd_meta (GstBuffer *buffer,gint meta_type, gpointer params);
}

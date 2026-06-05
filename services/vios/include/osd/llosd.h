/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#ifndef LLOSD_H
#define LLOSD_H

#include <stdbool.h>

extern "C" {
typedef struct{}      OsdContext;
typedef OsdContext*   OsdContext_t;

typedef struct _OsdMeta OsdMeta;
typedef struct _OsdCpuDataContext OsdCpuDataContext;

typedef enum OsdMetaType{
  OSD_RECTANGLE = 1,
  OSD_TEXT,
  OSD_LINE,
  OSD_ARROW,
  OSD_POINT,
  OSD_CIRCLE,
  OSD_ELLIPSE,
  OSD_COUNT,
  OSD_UNKNOWN = 0xFF
} OsdMetaType;

typedef enum {
  OSD_GPU_ID = 1,
  OSD_ENABLE_CPU_MODE,
  OSD_TYPE_UNKNOWN = 0xFF
} OsdAttribute;

struct _OsdMeta {
  int        meta_type;
  void*      params;
};

typedef struct _OSD_ColorParams OSD_ColorParams ;
typedef struct _OSD_RectParams OSD_RectParams;
typedef struct _OSD_TextParams OSD_TextParams;
typedef struct _OSD_LineParams OSD_LineParams;
typedef struct _OSD_ArrowParams OSD_ArrowParams;
typedef struct _OSD_PointParams OSD_PointParams;
typedef struct _OSD_CircleParams OSD_CircleParams;
typedef struct _OSD_EllipseParams OSD_EllipseParams;

struct _OSD_ColorParams {
  int red;
  int green;
  int blue;
  int alpha;
};

struct _OSD_CircleParams {
  int pos_x;
  int pos_y;
  int radius;
  int thickness;
  OSD_ColorParams border_color;
  OSD_ColorParams bg_color;
};

struct _OSD_EllipseParams {
  int pos_x;
  int pos_y;
  int width;
  int height;
  float yaw;
  int thickness;
  OSD_ColorParams border_color;
  OSD_ColorParams bg_color;
};

struct _OSD_RectParams {
  int top;
  int left;
  int right;
  int bottom;
  int thickness;
  OSD_ColorParams border_color;
  OSD_ColorParams bg_color;
};

struct _OSD_TextParams {
  int pos_x;
  int pos_y;
  int font_size;
  char* text;
  OSD_ColorParams border_color;
  OSD_ColorParams bg_color;
  char* font_type;
};

struct _OSD_PointParams {
  int pos_x;
  int pos_y;
  int radius;
  OSD_ColorParams color;
};

struct _OSD_LineParams {
  int pos_x0;
  int pos_y0;
  int pos_x1;
  int pos_y1;
  int thickness;
  bool is_interpolation;
  OSD_ColorParams color;
};

struct _OSD_ArrowParams {
  int pos_x0;
  int pos_y0;
  int pos_x1;
  int pos_y1;
  int thickness;
  int arrow_size;
  bool is_interpolation;
  OSD_ColorParams color;
};

struct _OsdCpuDataContext {
  int width;
  int height;
  void **data;
  int size;
  bool is_rgba;
};

void osd_global_init();
void osd_global_destroy();
OsdContext_t osd_init (bool, int);
void osd_destroy (OsdContext_t);
void osd_add_metadata (OsdContext_t, OsdMeta*);
void osd_draw (OsdContext_t, void *);

}
#endif
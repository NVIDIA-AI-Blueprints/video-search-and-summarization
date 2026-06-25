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


#ifndef __METROPOLIS_PERCEPTION_APP_H__
#define __METROPOLIS_PERCEPTION_APP_H__

#include <gst/gst.h>
#include "deepstream_config.h"

typedef struct
{
  gint anomaly_count;
  gint meta_number;
  struct timespec timespec_first_frame;
  GstClockTime gst_ts_first_frame;
  GMutex lock_stream_rtcp_sr;
  guint32 id;
  gint frameCount;
  GstClockTime last_ntp_time;
  GQueue *frame_embedding_queue;
} StreamSourceInfo;

typedef struct
{
  StreamSourceInfo streams[MAX_SOURCE_BINS];
} TestAppCtx;

typedef struct
{
  gint frame_num;
  GList *obj_embeddings;
} FrameEmbedding;

typedef struct
{
  guint64 object_id;
  guint num_elements;
  float *embedding;
} ObjEmbedding;

struct timespec extract_utc_from_uri (gchar * uri);

#endif /**< __METROPOLIS_PERCEPTION_APP_H__ */

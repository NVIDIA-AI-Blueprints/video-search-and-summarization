/*
 * SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
 * GStreamer Timestamp Filter Plugin
 *
 * Filters buffers based on a list of target timestamps.
 * Passes buffers when PTS >= next target timestamp, then advances to next timestamp.
 * Sends EOS when all timestamps have been matched.
 */

#ifndef __GST_TIMESTAMP_FILTER_H__
#define __GST_TIMESTAMP_FILTER_H__

#include <gst/gst.h>
#include <gst/base/gstbasetransform.h>
#include <vector>

G_BEGIN_DECLS

#define GST_TYPE_TIMESTAMP_FILTER \
  (gst_timestamp_filter_get_type())
#define GST_TIMESTAMP_FILTER(obj) \
  (G_TYPE_CHECK_INSTANCE_CAST((obj), GST_TYPE_TIMESTAMP_FILTER, GstTimestampFilter))
#define GST_TIMESTAMP_FILTER_CLASS(klass) \
  (G_TYPE_CHECK_CLASS_CAST((klass), GST_TYPE_TIMESTAMP_FILTER, GstTimestampFilterClass))
#define GST_IS_TIMESTAMP_FILTER(obj) \
  (G_TYPE_CHECK_INSTANCE_TYPE((obj), GST_TYPE_TIMESTAMP_FILTER))
#define GST_IS_TIMESTAMP_FILTER_CLASS(klass) \
  (G_TYPE_CHECK_CLASS_TYPE((klass), GST_TYPE_TIMESTAMP_FILTER))

typedef struct _GstTimestampFilter GstTimestampFilter;
typedef struct _GstTimestampFilterClass GstTimestampFilterClass;

/**
 * GstTimestampFilter:
 *
 * Opaque data structure.
 */
struct _GstTimestampFilter
{
  GstBaseTransform base_transform;

  /* Properties */
  gchar *timestamps_str;           /* Comma-separated timestamps string */
  gboolean send_eos_when_done;     /* Send EOS when all timestamps matched */
  gboolean drop_before_first;      /* Drop all buffers before first timestamp */

  /* State */
  std::vector<guint64> *timestamps; /* Sorted list of target timestamps (ns) */
  guint current_index;              /* Current position in timestamps array */
  gboolean eos_sent;                /* Flag to track if EOS was sent */
  GstClockTime segment_start;       /* Segment start time for timestamp normalization */
  gboolean respect_segment_start;   /* Whether to normalize timestamps based on segment start */
  GMutex lock;                      /* Thread safety lock */

  /* Statistics */
  guint64 buffers_passed;
  guint64 buffers_dropped;
};

struct _GstTimestampFilterClass
{
  GstBaseTransformClass base_transform_class;
};

GType gst_timestamp_filter_get_type(void);

GST_ELEMENT_REGISTER_DECLARE(timestampfilter);

G_END_DECLS

#endif /* __GST_TIMESTAMP_FILTER_H__ */

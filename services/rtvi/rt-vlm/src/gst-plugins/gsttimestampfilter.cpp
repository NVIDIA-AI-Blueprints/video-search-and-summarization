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
 * GStreamer Timestamp Filter Plugin
 *
 * This plugin filters video/audio buffers based on presentation timestamps (PTS).
 * It maintains a sorted list of target timestamps and only passes buffers whose
 * PTS matches or exceeds the next target timestamp.
 *
 * Properties:
 *   - timestamps: Comma-separated list of timestamps in nanoseconds
 *   - send-eos-when-done: Send EOS downstream when all timestamps matched (default: TRUE)
 *   - drop-before-first: Drop all buffers before the first timestamp (default: TRUE)
 *
 * Example:
 *   gst-launch-1.0 filesrc location=video.mp4 ! decodebin ! \
 *     timestampfilter timestamps="1000000000,2000000000,3000000000" ! \
 *     videoconvert ! autovideosink
 */

#include "gsttimestampfilter.h"
#include <algorithm>
#include <sstream>
#include <string>

GST_DEBUG_CATEGORY_STATIC(gst_timestamp_filter_debug);
#define GST_CAT_DEFAULT gst_timestamp_filter_debug

/* Properties */
enum
{
  PROP_0,
  PROP_TIMESTAMPS,
  PROP_SEND_EOS_WHEN_DONE,
  PROP_DROP_BEFORE_FIRST,
  PROP_BUFFERS_PASSED,
  PROP_BUFFERS_DROPPED,
};

#define DEFAULT_SEND_EOS_WHEN_DONE TRUE
#define DEFAULT_DROP_BEFORE_FIRST TRUE

/* Pad templates */
static GstStaticPadTemplate sink_template = GST_STATIC_PAD_TEMPLATE(
    "sink",
    GST_PAD_SINK,
    GST_PAD_ALWAYS,
    GST_STATIC_CAPS_ANY);

static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE(
    "src",
    GST_PAD_SRC,
    GST_PAD_ALWAYS,
    GST_STATIC_CAPS_ANY);

#define gst_timestamp_filter_parent_class parent_class
G_DEFINE_TYPE(GstTimestampFilter, gst_timestamp_filter, GST_TYPE_BASE_TRANSFORM);

GST_ELEMENT_REGISTER_DEFINE(timestampfilter, "timestampfilter", GST_RANK_NONE,
                            GST_TYPE_TIMESTAMP_FILTER);

/* Function prototypes */
static void gst_timestamp_filter_set_property(GObject *object, guint prop_id,
                                              const GValue *value, GParamSpec *pspec);
static void gst_timestamp_filter_get_property(GObject *object, guint prop_id,
                                              GValue *value, GParamSpec *pspec);
static void gst_timestamp_filter_finalize(GObject *object);
static GstFlowReturn gst_timestamp_filter_transform_ip(GstBaseTransform *base,
                                                       GstBuffer *buf);
static gboolean gst_timestamp_filter_start(GstBaseTransform *base);
static gboolean gst_timestamp_filter_stop(GstBaseTransform *base);
static gboolean gst_timestamp_filter_sink_event(GstBaseTransform *base,
                                                 GstEvent *event);

/* Parse comma-separated timestamp string into vector */
static gboolean parse_timestamps(const gchar *str, std::vector<guint64> &timestamps)
{
  if (!str || str[0] == '\0')
  {
    timestamps.clear();
    return TRUE; /* Empty list is valid */
  }

  timestamps.clear();
  std::string input(str);
  std::stringstream ss(input);
  std::string token;

  while (std::getline(ss, token, ','))
  {
    // Trim whitespace safely
    size_t start = token.find_first_not_of(" \t\n\r");
    if (start == std::string::npos)
    {
      // Token is all whitespace
      continue;
    }

    size_t end = token.find_last_not_of(" \t\n\r");
    token = token.substr(start, end - start + 1);

    if (token.empty())
      continue;

    char *endptr;
    guint64 timestamp = g_ascii_strtoull(token.c_str(), &endptr, 10);

    if (*endptr != '\0')
    {
      GST_ERROR("Invalid timestamp value: %s", token.c_str());
      return FALSE;
    }

    timestamps.push_back(timestamp);
  }

  /* Sort timestamps in ascending order */
  std::sort(timestamps.begin(), timestamps.end());

  GST_DEBUG("Parsed %zu timestamps", timestamps.size());
  return TRUE;
}

/* Initialize the class */
static void gst_timestamp_filter_class_init(GstTimestampFilterClass *klass)
{
  GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
  GstElementClass *gstelement_class = GST_ELEMENT_CLASS(klass);
  GstBaseTransformClass *base_transform_class = GST_BASE_TRANSFORM_CLASS(klass);

  gobject_class->set_property = gst_timestamp_filter_set_property;
  gobject_class->get_property = gst_timestamp_filter_get_property;
  gobject_class->finalize = gst_timestamp_filter_finalize;

  /* Properties */
  g_object_class_install_property(
      gobject_class, PROP_TIMESTAMPS,
      g_param_spec_string("timestamps", "Timestamps",
                         "Comma-separated list of target timestamps in nanoseconds",
                         NULL,
                         (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_SEND_EOS_WHEN_DONE,
      g_param_spec_boolean("send-eos-when-done", "Send EOS When Done",
                          "Send EOS event downstream when all timestamps are matched",
                          DEFAULT_SEND_EOS_WHEN_DONE,
                          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_DROP_BEFORE_FIRST,
      g_param_spec_boolean("drop-before-first", "Drop Before First",
                          "Drop all buffers before the first timestamp",
                          DEFAULT_DROP_BEFORE_FIRST,
                          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_BUFFERS_PASSED,
      g_param_spec_uint64("buffers-passed", "Buffers Passed",
                         "Number of buffers passed through (read-only)",
                         0, G_MAXUINT64, 0,
                         (GParamFlags)(G_PARAM_READABLE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_BUFFERS_DROPPED,
      g_param_spec_uint64("buffers-dropped", "Buffers Dropped",
                         "Number of buffers dropped (read-only)",
                         0, G_MAXUINT64, 0,
                         (GParamFlags)(G_PARAM_READABLE | G_PARAM_STATIC_STRINGS)));

  gst_element_class_set_static_metadata(
      gstelement_class,
      "Timestamp Filter",
      "Filter/Video/Audio",
      "Filters buffers based on presentation timestamps",
      "NVIDIA Corporation");

  gst_element_class_add_static_pad_template(gstelement_class, &src_template);
  gst_element_class_add_static_pad_template(gstelement_class, &sink_template);

  base_transform_class->transform_ip = GST_DEBUG_FUNCPTR(gst_timestamp_filter_transform_ip);
  base_transform_class->start = GST_DEBUG_FUNCPTR(gst_timestamp_filter_start);
  base_transform_class->stop = GST_DEBUG_FUNCPTR(gst_timestamp_filter_stop);
  base_transform_class->sink_event = GST_DEBUG_FUNCPTR(gst_timestamp_filter_sink_event);

  /* We operate in passthrough mode (in-place) */
  base_transform_class->passthrough_on_same_caps = FALSE;
  base_transform_class->transform_ip_on_passthrough = TRUE;

  GST_DEBUG_CATEGORY_INIT(gst_timestamp_filter_debug, "timestampfilter", 0,
                         "Timestamp Filter Plugin");
}

/* Initialize the instance */
static void gst_timestamp_filter_init(GstTimestampFilter *filter)
{
  filter->timestamps_str = NULL;
  filter->send_eos_when_done = DEFAULT_SEND_EOS_WHEN_DONE;
  filter->drop_before_first = DEFAULT_DROP_BEFORE_FIRST;
  filter->timestamps = new std::vector<guint64>();
  filter->current_index = 0;
  filter->eos_sent = FALSE;
  filter->segment_start = GST_CLOCK_TIME_NONE;
  filter->buffers_passed = 0;
  filter->buffers_dropped = 0;
  g_mutex_init(&filter->lock);

  /* Check environment variable to enable segment start normalization */
  const gchar *env_var = g_getenv("RTVI_RESPECT_GST_SEGMENT_START");
  filter->respect_segment_start = (env_var != NULL &&
                                   (g_strcmp0(env_var, "1") == 0 ||
                                    g_ascii_strcasecmp(env_var, "true") == 0 ||
                                    g_ascii_strcasecmp(env_var, "yes") == 0));

  if (filter->respect_segment_start)
  {
    GST_INFO_OBJECT(filter, "Timestamp normalization enabled (RTVI_RESPECT_GST_SEGMENT_START is set)");
  }
  else
  {
    GST_INFO_OBJECT(filter, "Timestamp normalization disabled (RTVI_RESPECT_GST_SEGMENT_START not set)");
  }

  /* Set transform in-place */
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(filter), TRUE);
}

/* Property setter */
static void gst_timestamp_filter_set_property(GObject *object, guint prop_id,
                                              const GValue *value, GParamSpec *pspec)
{
  GstTimestampFilter *filter = GST_TIMESTAMP_FILTER(object);

  g_mutex_lock(&filter->lock);

  switch (prop_id)
  {
  case PROP_TIMESTAMPS:
    g_free(filter->timestamps_str);
    filter->timestamps_str = g_value_dup_string(value);
    if (parse_timestamps(filter->timestamps_str, *filter->timestamps))
    {
      filter->current_index = 0;
      filter->eos_sent = FALSE;
      filter->buffers_passed = 0;
      filter->buffers_dropped = 0;
      GST_INFO_OBJECT(filter, "Loaded %zu timestamps", filter->timestamps->size());
    }
    else
    {
      GST_ERROR_OBJECT(filter, "Failed to parse timestamps");
      filter->timestamps->clear();
    }
    break;

  case PROP_SEND_EOS_WHEN_DONE:
    filter->send_eos_when_done = g_value_get_boolean(value);
    break;

  case PROP_DROP_BEFORE_FIRST:
    filter->drop_before_first = g_value_get_boolean(value);
    break;

  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
    break;
  }

  g_mutex_unlock(&filter->lock);
}

/* Property getter */
static void gst_timestamp_filter_get_property(GObject *object, guint prop_id,
                                              GValue *value, GParamSpec *pspec)
{
  GstTimestampFilter *filter = GST_TIMESTAMP_FILTER(object);

  g_mutex_lock(&filter->lock);

  switch (prop_id)
  {
  case PROP_TIMESTAMPS:
    g_value_set_string(value, filter->timestamps_str);
    break;

  case PROP_SEND_EOS_WHEN_DONE:
    g_value_set_boolean(value, filter->send_eos_when_done);
    break;

  case PROP_DROP_BEFORE_FIRST:
    g_value_set_boolean(value, filter->drop_before_first);
    break;

  case PROP_BUFFERS_PASSED:
    g_value_set_uint64(value, filter->buffers_passed);
    break;

  case PROP_BUFFERS_DROPPED:
    g_value_set_uint64(value, filter->buffers_dropped);
    break;

  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
    break;
  }

  g_mutex_unlock(&filter->lock);
}

/* Cleanup */
static void gst_timestamp_filter_finalize(GObject *object)
{
  GstTimestampFilter *filter = GST_TIMESTAMP_FILTER(object);

  g_free(filter->timestamps_str);
  delete filter->timestamps;
  g_mutex_clear(&filter->lock);

  G_OBJECT_CLASS(parent_class)->finalize(object);
}

/* Start processing */
static gboolean gst_timestamp_filter_start(GstBaseTransform *base)
{
  GstTimestampFilter *filter = GST_TIMESTAMP_FILTER(base);

  g_mutex_lock(&filter->lock);
  filter->current_index = 0;
  filter->eos_sent = FALSE;
  filter->segment_start = GST_CLOCK_TIME_NONE;
  filter->buffers_passed = 0;
  filter->buffers_dropped = 0;
  g_mutex_unlock(&filter->lock);

  GST_INFO_OBJECT(filter, "Starting with %zu timestamps", filter->timestamps->size());

  return TRUE;
}

/* Stop processing */
static gboolean gst_timestamp_filter_stop(GstBaseTransform *base)
{
  GstTimestampFilter *filter = GST_TIMESTAMP_FILTER(base);

  GST_INFO_OBJECT(filter, "Stopping: passed=%lu dropped=%lu",
                 filter->buffers_passed, filter->buffers_dropped);

  return TRUE;
}

/* Handle sink events */
static gboolean gst_timestamp_filter_sink_event(GstBaseTransform *base,
                                                 GstEvent *event)
{
  GstTimestampFilter *filter = GST_TIMESTAMP_FILTER(base);

  switch (GST_EVENT_TYPE(event))
  {
  case GST_EVENT_SEGMENT:
  {
    const GstSegment *segment;
    gst_event_parse_segment(event, &segment);

    /* Store segment start time for timestamp normalization (if enabled) */
    g_mutex_lock(&filter->lock);
    if (filter->respect_segment_start &&
        segment->format == GST_FORMAT_TIME &&
        GST_CLOCK_TIME_IS_VALID(segment->start))
    {
      filter->segment_start = segment->start;
      GST_INFO_OBJECT(filter, "Storing segment start time: %" G_GUINT64_FORMAT " (%.2f seconds)",
                     filter->segment_start, filter->segment_start / 1000000000.0);
    }
    else if (filter->respect_segment_start)
    {
      GST_INFO_OBJECT(filter, "Segment format is not TIME or start is invalid, normalization disabled");
    }
    g_mutex_unlock(&filter->lock);

    GST_INFO_OBJECT(filter, "=== SEGMENT EVENT RECEIVED ===");
    GST_INFO_OBJECT(filter, "  Format: %s", gst_format_get_name(segment->format));
    GST_INFO_OBJECT(filter, "  Rate: %f", segment->rate);
    GST_INFO_OBJECT(filter, "  Applied rate: %f", segment->applied_rate);
    GST_INFO_OBJECT(filter, "  Start: %" G_GUINT64_FORMAT " (%s)", segment->start,
                   gst_format_get_name(segment->format));
    GST_INFO_OBJECT(filter, "  Stop: %" G_GUINT64_FORMAT " (%s)", segment->stop,
                   gst_format_get_name(segment->format));
    GST_INFO_OBJECT(filter, "  Time: %" G_GUINT64_FORMAT, segment->time);
    GST_INFO_OBJECT(filter, "  Position: %" G_GUINT64_FORMAT, segment->position);
    GST_INFO_OBJECT(filter, "  Duration: %" G_GUINT64_FORMAT, segment->duration);
    GST_INFO_OBJECT(filter, "  Base: %" G_GUINT64_FORMAT, segment->base);
    GST_INFO_OBJECT(filter, "  Offset: %" G_GUINT64_FORMAT, segment->offset);
    GST_INFO_OBJECT(filter, "  Flags: 0x%x", segment->flags);
    break;
  }
  default:
    break;
  }

  /* Chain up to parent class to handle the event */
  return GST_BASE_TRANSFORM_CLASS(parent_class)->sink_event(base, event);
}

/* Main transform function - called for each buffer */
static GstFlowReturn gst_timestamp_filter_transform_ip(GstBaseTransform *base,
                                                       GstBuffer *buf)
{
  GstTimestampFilter *filter = GST_TIMESTAMP_FILTER(base);
  GstClockTime pts = GST_BUFFER_PTS(buf);
  GstClockTime normalized_pts;
  gboolean should_pass = FALSE;

  /* Drop buffers with invalid PTS */
  if (!GST_CLOCK_TIME_IS_VALID(pts))
  {
    GST_DEBUG_OBJECT(filter, "Dropping buffer with invalid PTS");
    g_mutex_lock(&filter->lock);
    filter->buffers_dropped++;
    g_mutex_unlock(&filter->lock);
    return GST_BASE_TRANSFORM_FLOW_DROPPED;
  }

  g_mutex_lock(&filter->lock);

  /* Normalize PTS by subtracting segment start if enabled and available */
  if (filter->respect_segment_start && GST_CLOCK_TIME_IS_VALID(filter->segment_start))
  {
    if (pts >= filter->segment_start)
    {
      normalized_pts = pts - filter->segment_start;
      GST_DEBUG_OBJECT(filter, "Buffer PTS: %" G_GUINT64_FORMAT " (%.2f sec), "
                      "Normalized PTS: %" G_GUINT64_FORMAT " (%.2f sec)",
                      pts, pts / 1000000000.0,
                      normalized_pts, normalized_pts / 1000000000.0);
    }
    else
    {
      /* PTS is before segment start - drop the buffer to avoid false matches at normalized_pts=0 */
      GST_WARNING_OBJECT(filter, "Dropping buffer with PTS %" G_GUINT64_FORMAT " before segment start %"
                        G_GUINT64_FORMAT " to avoid false timestamp matches", pts, filter->segment_start);
      filter->buffers_dropped++;
      g_mutex_unlock(&filter->lock);
      return GST_BASE_TRANSFORM_FLOW_DROPPED;
    }
  }
  else
  {
    /* Normalization disabled or no segment start yet, use raw PTS */
    normalized_pts = pts;
    GST_DEBUG_OBJECT(filter, "Using raw PTS: %" G_GUINT64_FORMAT, pts);
  }

  /* If no timestamps configured, pass everything */
  if (filter->timestamps->empty())
  {
    filter->buffers_passed++;
    g_mutex_unlock(&filter->lock);
    return GST_FLOW_OK;
  }

  /* If all timestamps consumed */
  if (filter->current_index >= filter->timestamps->size())
  {
    /* Send EOS once if configured */
    if (filter->send_eos_when_done && !filter->eos_sent)
    {
      GST_INFO_OBJECT(filter, "All timestamps matched, sending EOS");
      filter->eos_sent = TRUE;
      g_mutex_unlock(&filter->lock);
      gst_pad_push_event(GST_BASE_TRANSFORM_SRC_PAD(base), gst_event_new_eos());
      return GST_BASE_TRANSFORM_FLOW_DROPPED;
    }

    filter->buffers_dropped++;
    g_mutex_unlock(&filter->lock);
    return GST_BASE_TRANSFORM_FLOW_DROPPED;
  }

  guint64 target_pts = (*filter->timestamps)[filter->current_index];

  /* Check if this buffer matches the current target timestamp (using normalized PTS) */
  if (normalized_pts >= target_pts)
  {
    /* Advance past all timestamps <= current normalized PTS */
    while (filter->current_index < filter->timestamps->size() &&
           normalized_pts >= (*filter->timestamps)[filter->current_index])
    {
      GST_DEBUG_OBJECT(filter, "Matched timestamp %lu at normalized PTS %lu (raw PTS %lu)",
                      (*filter->timestamps)[filter->current_index], normalized_pts, pts);
      filter->current_index++;
    }

    should_pass = TRUE;
    filter->buffers_passed++;

    /* Check if we just consumed the last timestamp */
    if (filter->current_index >= filter->timestamps->size() &&
        filter->send_eos_when_done && !filter->eos_sent)
    {
      GST_INFO_OBJECT(filter, "Last timestamp matched, scheduling EOS after this buffer");
      filter->eos_sent = TRUE;

      /* Schedule EOS to be sent asynchronously after this buffer is pushed */
      g_idle_add_full(G_PRIORITY_HIGH,
                      [](gpointer data) -> gboolean {
                        GstElement *element = GST_ELEMENT(data);
                        GST_INFO_OBJECT(element, "Sending EOS event");
                        gst_element_send_event(element, gst_event_new_eos());
                        return G_SOURCE_REMOVE;
                      },
                      gst_object_ref(GST_ELEMENT(base)),
                      [](gpointer data) {
                        gst_object_unref(GST_OBJECT(data));
                      });
    }
  }
  else
  {
    /* Buffer is before the next target timestamp */
    if (filter->drop_before_first || filter->current_index > 0)
    {
      filter->buffers_dropped++;
    }
    else
    {
      /* Don't drop buffers before first timestamp if configured */
      should_pass = TRUE;
      filter->buffers_passed++;
    }
  }

  g_mutex_unlock(&filter->lock);

  return should_pass ? GST_FLOW_OK : GST_BASE_TRANSFORM_FLOW_DROPPED;
}

/* Plugin entry point */
static gboolean plugin_init(GstPlugin *plugin)
{
  return GST_ELEMENT_REGISTER(timestampfilter, plugin);
}

#ifndef PACKAGE
#define PACKAGE "timestampfilter"
#endif

GST_PLUGIN_DEFINE(
    GST_VERSION_MAJOR,
    GST_VERSION_MINOR,
    timestampfilter,
    "Timestamp-based buffer filter",
    plugin_init,
    "1.0",
    "Proprietary",
    "RTVI",
    "https://nvidia.com/")

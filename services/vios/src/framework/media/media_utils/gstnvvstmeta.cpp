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

#include "gstnvvstmeta.h"

static gboolean
gst_nv_vst_init_func (GstMeta * meta, gpointer params, GstBuffer * buffer)
{
  GST_DEBUG ("init called on buffer %p, meta %p", buffer, meta);
  /* nothing to init really, the init function is mostly for allocating
   * additional memory or doing special setup as part of adding the metadata to
   * the buffer*/
  return TRUE;
}

static void
gst_nv_vst_free_func (GstMeta * meta, GstBuffer * buffer)
{
  GST_DEBUG ("free called on buffer %p, meta %p", buffer, meta);
  /* nothing to free really */
}

static gboolean
gst_nv_vst_transform_func (GstBuffer * transbuf, GstMeta * meta,
    GstBuffer * buffer, GQuark type, gpointer data)
{
  GstNvVstMeta *test, *tmeta = (GstNvVstMeta *) meta;

  GST_DEBUG ("transform %s called from buffer %p to %p, meta %p",
      g_quark_to_string (type), buffer, transbuf, meta);

  if (GST_META_TRANSFORM_IS_COPY (type)) {
    test = GST_NV_VST_META_ADD (transbuf);
    if (!test)
      return FALSE;

    GST_DEBUG ("copy vst metadata");
    test->id = tmeta->id;
    test->pts = tmeta->pts;
    test->ts = tmeta->ts;
  } else {
    /* return FALSE, if transform type is not supported */
    return FALSE;
  }

  return TRUE;
}

GType
gst_nv_vst_meta_api_get_type (void)
{
  static GType type;
  static const gchar *tags[] = { GST_NV_VST_META_STRING, nullptr };

  if (g_once_init_enter (&type)) {
    GType _type = gst_meta_api_type_register ("GstNvVstMetaAPI", tags);
    g_once_init_leave (&type, _type);
  }
  return type;
}

const GstMetaInfo *
gst_nv_vst_meta_get_info (void)
{
  static const GstMetaInfo *nv_vst_meta_info = nullptr;

  if (g_once_init_enter ((GstMetaInfo **) & nv_vst_meta_info)) {
    const GstMetaInfo *mi = gst_meta_register (GST_NV_VST_META_API_TYPE,
        "GstNvVstMeta",
        sizeof (GstNvVstMeta),
        gst_nv_vst_init_func, gst_nv_vst_free_func, gst_nv_vst_transform_func);
    g_once_init_leave ((GstMetaInfo **) & nv_vst_meta_info, (GstMetaInfo *) mi);
  }
  return nv_vst_meta_info;
}

/* SPDX-License-Identifier: Apache-2.0 */
#ifndef VSS_SOURCE_IDENTITY_META_H
#define VSS_SOURCE_IDENTITY_META_H

#include "gstnvdsmeta.h"
#include "nvdsmeta.h"

/* Own the strings before a source is removed or its numeric slot is reused.
 * Gst-nvstreammux transfers this metadata to the corresponding frame. */
namespace SourceIdentity {

static NvDsMetaType type() {
  static gsize value = 0;
  if (g_once_init_enter(&value)) {
    g_once_init_leave(&value, nvds_get_user_meta_type((gchar *)"NVIDIA.VSS.SOURCE_IDENTITY"));
  }
  return (NvDsMetaType)value;
}

static gpointer copy(gpointer data, gpointer) {
  const NvDsSensorInfo_meta *src = (const NvDsSensorInfo_meta *)data;
  NvDsSensorInfo_meta *dst = g_new0(NvDsSensorInfo_meta, 1);
  dst->source_id = src->source_id;
  dst->sensor_id = g_strdup(src->sensor_id);
  dst->sensor_name = g_strdup(src->sensor_name);
  dst->uri = g_strdup(src->uri);
  return dst;
}

static void release(gpointer data, gpointer) {
  NvDsSensorInfo_meta *identity = (NvDsSensorInfo_meta *)data;
  if (!identity) return;
  g_free((gpointer)identity->sensor_id);
  g_free((gpointer)identity->sensor_name);
  g_free((gpointer)identity->uri);
  g_free(identity);
}

static void destroy(gpointer data) { release(data, NULL); }

static gpointer transform(gpointer data, gpointer) {
  return copy(((NvDsUserMeta *)data)->user_meta_data, NULL);
}

static void release_transformed(gpointer data, gpointer) {
  release(((NvDsUserMeta *)data)->user_meta_data, NULL);
}

static GstPadProbeReturn attach(GstPad *, GstPadProbeInfo *info, gpointer snapshot) {
  GstBuffer *buffer = GST_PAD_PROBE_INFO_BUFFER(info);
  if (!buffer) return GST_PAD_PROBE_OK;
  buffer = gst_buffer_make_writable(buffer);
  GST_PAD_PROBE_INFO_DATA(info) = buffer;
  gpointer owned = copy(snapshot, NULL);
  NvDsMeta *meta = gst_buffer_add_nvds_meta(buffer, owned, NULL, copy, release);
  if (!meta) {
    release(owned, NULL);
    GST_ERROR("Cannot attach source identity; refusing to publish an unidentified frame");
    return GST_PAD_PROBE_DROP;
  }
  meta->meta_type = type();
  meta->gst_to_nvds_meta_transform_func = transform;
  meta->gst_to_nvds_meta_release_func = release_transformed;
  return GST_PAD_PROBE_OK;
}

static const NvDsSensorInfo_meta *read(const NvDsFrameMeta *frame) {
  for (NvDsMetaList *item = frame->frame_user_meta_list; item; item = item->next) {
    NvDsUserMeta *meta = (NvDsUserMeta *)item->data;
    if (meta->base_meta.meta_type == type())
      return (const NvDsSensorInfo_meta *)meta->user_meta_data;
  }
  return NULL;
}

}  // namespace SourceIdentity
#endif

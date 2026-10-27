/* Regression: queued metadata must outlive source removal and slot reuse. */
#include <cassert>
#include <cstring>
#include "source_identity_meta.h"

int main(int argc, char **argv) {
  gst_init(&argc, &argv);
  NvDsSensorInfo_meta original = {7, g_strdup("file:///old"), g_strdup("old-id"), g_strdup("old-camera")};
  gpointer snapshot = SourceIdentity::copy(&original, NULL);
  g_free((gpointer)original.uri);
  g_free((gpointer)original.sensor_id);
  g_free((gpointer)original.sensor_name);
  original.uri = "file:///new";
  original.sensor_id = "new-id";
  original.sensor_name = "new-camera";

  GstBuffer *buffer = gst_buffer_new();
  GstPadProbeInfo info = {};
  info.data = buffer;
  assert(SourceIdentity::attach(NULL, &info, snapshot) == GST_PAD_PROBE_OK);
  SourceIdentity::destroy(snapshot);
  buffer = GST_PAD_PROBE_INFO_BUFFER(&info);
  NvDsMeta *gst_meta = gst_buffer_get_nvds_meta(buffer);
  assert(gst_meta && gst_meta->meta_type == SourceIdentity::type());
  NvDsUserMeta input = {};
  input.user_meta_data = gst_meta->meta_data;
  NvDsUserMeta frame_meta = {};
  frame_meta.base_meta.meta_type = SourceIdentity::type();
  frame_meta.user_meta_data = gst_meta->gst_to_nvds_meta_transform_func(&input, NULL);
  gst_buffer_unref(buffer);
  GList *items = g_list_append(NULL, &frame_meta);
  NvDsFrameMeta frame = {};
  frame.frame_user_meta_list = items;
  const NvDsSensorInfo_meta *identity = SourceIdentity::read(&frame);
  assert(identity && identity->source_id == 7);
  assert(strcmp(identity->sensor_name, "old-camera") == 0);
  assert(strcmp(identity->sensor_id, "old-id") == 0);
  assert(strcmp(identity->uri, "file:///old") == 0);
  SourceIdentity::release_transformed(&frame_meta, NULL);
  g_list_free(items);
  frame.frame_user_meta_list = NULL;
  assert(SourceIdentity::read(&frame) == NULL);
  return 0;
}

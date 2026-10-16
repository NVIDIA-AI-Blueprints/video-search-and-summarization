#include <gst/gst.h>
#include <gst/gsturi.h>

#define GST_TYPE_VSS_IPC_SRC (gst_vss_ipc_src_get_type())
G_DECLARE_FINAL_TYPE(GstVssIpcSrc, gst_vss_ipc_src, GST, VSS_IPC_SRC, GstBin)

struct _GstVssIpcSrc {
  GstBin parent;
  GstElement *source;
  gchar *uri;
  gchar *status_file;
  GstClockTime last_pts;
};

static void uri_handler_init(gpointer interface, gpointer data);
G_DEFINE_TYPE_WITH_CODE(
    GstVssIpcSrc, gst_vss_ipc_src, GST_TYPE_BIN,
    G_IMPLEMENT_INTERFACE(GST_TYPE_URI_HANDLER, uri_handler_init))

enum { PROP_0, PROP_URI };

static void write_status(GstVssIpcSrc *self, const gchar *status) {
  if (!self->status_file) return;
  gchar *body = g_strdup_printf(
      "{\"status\":\"%s\",\"last_pts_ns\":%" G_GUINT64_FORMAT "}\n",
      status, GST_CLOCK_TIME_IS_VALID(self->last_pts) ? self->last_pts : 0);
  g_file_set_contents(self->status_file, body, -1, NULL);
  g_free(body);
}

static GstPadProbeReturn source_probe(GstPad *, GstPadProbeInfo *info,
                                      gpointer user_data) {
  GstVssIpcSrc *self = GST_VSS_IPC_SRC(user_data);
  if (info->type & GST_PAD_PROBE_TYPE_BUFFER) {
    GstBuffer *buffer = GST_PAD_PROBE_INFO_BUFFER(info);
    if (GST_BUFFER_PTS_IS_VALID(buffer)) self->last_pts = GST_BUFFER_PTS(buffer);
    write_status(self, "attached");
  }
  if (info->type & GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM) {
    GstEvent *event = GST_PAD_PROBE_INFO_EVENT(info);
    if (GST_EVENT_TYPE(event) == GST_EVENT_EOS) write_status(self, "eos");
  }
  return GST_PAD_PROBE_OK;
}

static gboolean configure_uri(GstVssIpcSrc *self, const gchar *uri,
                              GError **error) {
  GUri *parsed = g_uri_parse(uri, G_URI_FLAGS_NONE, error);
  if (!parsed) return FALSE;
  const gchar *path = g_uri_get_path(parsed);
  if (!path || path[0] != '/') {
    g_set_error(error, GST_URI_ERROR, GST_URI_ERROR_BAD_URI,
                "vssipc URI requires an absolute socket path");
    g_uri_unref(parsed);
    return FALSE;
  }
  GHashTable *query = g_uri_parse_params(g_uri_get_query(parsed) ?: "", -1,
                                         "&", G_URI_PARAMS_NONE, error);
  if (!query) {
    g_uri_unref(parsed);
    return FALSE;
  }
  const gchar *gpu_text = g_hash_table_lookup(query, "gpu_id");
  const gchar *status_file = g_hash_table_lookup(query, "status_file");
  guint gpu_id = gpu_text ? (guint)g_ascii_strtoull(gpu_text, NULL, 10) : 0;
  g_object_set(self->source, "socket-path", path, "gpu-id", gpu_id,
               "buffer-timestamp-copy", TRUE, NULL);
  g_free(self->status_file);
  self->status_file = status_file ? g_strdup(status_file) : NULL;
  g_free(self->uri);
  self->uri = g_strdup(uri);
  write_status(self, "attaching");
  g_hash_table_unref(query);
  g_uri_unref(parsed);
  return TRUE;
}

static void gst_vss_ipc_src_set_property(GObject *object, guint property_id,
                                         const GValue *value,
                                         GParamSpec *pspec) {
  GstVssIpcSrc *self = GST_VSS_IPC_SRC(object);
  if (property_id == PROP_URI) {
    GError *error = NULL;
    if (!configure_uri(self, g_value_get_string(value), &error)) {
      GST_ELEMENT_ERROR(self, RESOURCE, SETTINGS, ("%s", error->message), (NULL));
      g_clear_error(&error);
    }
  } else {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, pspec);
  }
}

static void gst_vss_ipc_src_get_property(GObject *object, guint property_id,
                                         GValue *value, GParamSpec *pspec) {
  GstVssIpcSrc *self = GST_VSS_IPC_SRC(object);
  if (property_id == PROP_URI) {
    g_value_set_string(value, self->uri);
  } else {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, pspec);
  }
}

static GstURIType uri_get_type(GType) { return GST_URI_SRC; }
static const gchar *const *uri_get_protocols(GType) {
  static const gchar *protocols[] = {"vssipc", NULL};
  return protocols;
}
static gchar *uri_get_uri(GstURIHandler *handler) {
  return g_strdup(GST_VSS_IPC_SRC(handler)->uri);
}
static gboolean uri_set_uri(GstURIHandler *handler, const gchar *uri,
                            GError **error) {
  return configure_uri(GST_VSS_IPC_SRC(handler), uri, error);
}
static void uri_handler_init(gpointer interface, gpointer) {
  GstURIHandlerInterface *handler = interface;
  handler->get_type = uri_get_type;
  handler->get_protocols = uri_get_protocols;
  handler->get_uri = uri_get_uri;
  handler->set_uri = uri_set_uri;
}

static void gst_vss_ipc_src_finalize(GObject *object) {
  GstVssIpcSrc *self = GST_VSS_IPC_SRC(object);
  g_free(self->uri);
  g_free(self->status_file);
  G_OBJECT_CLASS(gst_vss_ipc_src_parent_class)->finalize(object);
}

static void gst_vss_ipc_src_class_init(GstVssIpcSrcClass *klass) {
  GObjectClass *objects = G_OBJECT_CLASS(klass);
  objects->set_property = gst_vss_ipc_src_set_property;
  objects->get_property = gst_vss_ipc_src_get_property;
  objects->finalize = gst_vss_ipc_src_finalize;
  g_object_class_install_property(
      objects, PROP_URI,
      g_param_spec_string("uri", "URI", "vssipc source URI", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(
      GST_ELEMENT_CLASS(klass), "VSS CUDA IPC source", "Source/Video",
      "URI wrapper around DeepStream nvunixfdsrc", "NVIDIA");
}

static void gst_vss_ipc_src_init(GstVssIpcSrc *self) {
  self->last_pts = GST_CLOCK_TIME_NONE;
  self->source = gst_element_factory_make("nvunixfdsrc", "ipc-source");
  if (!self->source) return;
  gst_bin_add(GST_BIN(self), self->source);
  GstPad *pad = gst_element_get_static_pad(self->source, "src");
  gst_pad_add_probe(pad,
                    GST_PAD_PROBE_TYPE_BUFFER |
                        GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM,
                    source_probe, self, NULL);
  GstPad *ghost = gst_ghost_pad_new("src", pad);
  gst_pad_set_active(ghost, TRUE);
  gst_element_add_pad(GST_ELEMENT(self), ghost);
  gst_object_unref(pad);
}

static gboolean plugin_init(GstPlugin *plugin) {
  return gst_element_register(plugin, "vssipcsrc", GST_RANK_PRIMARY + 10,
                              GST_TYPE_VSS_IPC_SRC);
}

GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, vssipcsrc,
                  "VSS CUDA IPC URI source", plugin_init, "1.0", "Proprietary",
                  "vssipcsrc", "https://nvidia.com")

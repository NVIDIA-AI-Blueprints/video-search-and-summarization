/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: MIT
 *
 * dashmp4mux wraps mp4mux for GstDashSink's browser-compatible fMP4 mode.
 */

#include <gst/gst.h>

#define GST_TYPE_DASH_MP4_MUX (gst_dash_mp4_mux_get_type())
#define GST_DASH_MP4_MUX(obj) (G_TYPE_CHECK_INSTANCE_CAST((obj), GST_TYPE_DASH_MP4_MUX, GstDashMp4Mux))

typedef struct _GstDashMp4Mux GstDashMp4Mux;
typedef struct _GstDashMp4MuxClass GstDashMp4MuxClass;

struct _GstDashMp4Mux
{
    GstBin parent;
    GstElement* mp4mux;
    guint64 fragment_duration_ns;
};

struct _GstDashMp4MuxClass
{
    GstBinClass parent_class;
};

GType gst_dash_mp4_mux_get_type(void);
G_DEFINE_TYPE(GstDashMp4Mux, gst_dash_mp4_mux, GST_TYPE_BIN)

enum
{
    PROP_0,
    PROP_FRAGMENT_DURATION
};

static void gst_dash_mp4_mux_set_property(GObject* object, guint property_id,
                                          const GValue* value, GParamSpec* parameter_spec)
{
    GstDashMp4Mux* self = GST_DASH_MP4_MUX(object);
    switch (property_id)
    {
        case PROP_FRAGMENT_DURATION:
            self->fragment_duration_ns = g_value_get_uint64(value);
            if (self->mp4mux != NULL)
            {
                guint duration_ms = (guint)(self->fragment_duration_ns / GST_MSECOND);
                if (duration_ms == 0)
                {
                    duration_ms = 5000;
                }
                g_object_set(self->mp4mux, "fragment-duration", duration_ms, NULL);
            }
            break;
        default:
            G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, parameter_spec);
    }
}

static void gst_dash_mp4_mux_get_property(GObject* object, guint property_id,
                                          GValue* value, GParamSpec* parameter_spec)
{
    GstDashMp4Mux* self = GST_DASH_MP4_MUX(object);
    switch (property_id)
    {
        case PROP_FRAGMENT_DURATION:
            g_value_set_uint64(value, self->fragment_duration_ns);
            break;
        default:
            G_OBJECT_WARN_INVALID_PROPERTY_ID(object, property_id, parameter_spec);
    }
}

static GstPad* gst_dash_mp4_mux_request_new_pad(GstElement* element, GstPadTemplate* template,
                                                const gchar* name, const GstCaps* caps)
{
    (void)template;
    (void)caps;
    GstDashMp4Mux* self = GST_DASH_MP4_MUX(element);
    if (self->mp4mux == NULL)
    {
        return NULL;
    }
    GstPad* inner_pad = gst_element_request_pad_simple(self->mp4mux, name != NULL ? name : "video_%u");
    if (inner_pad == NULL)
    {
        return NULL;
    }
    GstPad* ghost_pad = gst_ghost_pad_new(GST_PAD_NAME(inner_pad), inner_pad);
    gst_object_unref(inner_pad);
    if (ghost_pad == NULL)
    {
        return NULL;
    }
    gst_pad_set_active(ghost_pad, TRUE);
    gst_element_add_pad(element, ghost_pad);
    return ghost_pad;
}

static void gst_dash_mp4_mux_release_pad(GstElement* element, GstPad* pad)
{
    GstDashMp4Mux* self = GST_DASH_MP4_MUX(element);
    if (self->mp4mux != NULL && GST_IS_GHOST_PAD(pad))
    {
        GstPad* target_pad = gst_ghost_pad_get_target(GST_GHOST_PAD(pad));
        if (target_pad != NULL)
        {
            gst_element_release_request_pad(self->mp4mux, target_pad);
            gst_object_unref(target_pad);
        }
    }
    gst_element_remove_pad(element, pad);
}

static void gst_dash_mp4_mux_class_init(GstDashMp4MuxClass* klass)
{
    GObjectClass* object_class = G_OBJECT_CLASS(klass);
    GstElementClass* element_class = GST_ELEMENT_CLASS(klass);
    object_class->set_property = gst_dash_mp4_mux_set_property;
    object_class->get_property = gst_dash_mp4_mux_get_property;
    g_object_class_install_property(object_class, PROP_FRAGMENT_DURATION,
        g_param_spec_uint64("fragment-duration", "Fragment duration",
                             "Per-fragment duration in nanoseconds as passed by GstDashSink",
                             0, G_MAXUINT64, 5 * GST_SECOND,
                             G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
    gst_element_class_set_static_metadata(element_class, "DASH MP4 Muxer", "Codec/Muxer",
        "fMP4 DASH muxer for GstDashSink backed by mp4mux", "NVIDIA VIOS");
    static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE(
        "src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/quicktime"));
    static GstStaticPadTemplate sink_template = GST_STATIC_PAD_TEMPLATE(
        "video_%u", GST_PAD_SINK, GST_PAD_REQUEST, GST_STATIC_CAPS_ANY);
    gst_element_class_add_static_pad_template(element_class, &src_template);
    gst_element_class_add_static_pad_template(element_class, &sink_template);
    element_class->request_new_pad = gst_dash_mp4_mux_request_new_pad;
    element_class->release_pad = gst_dash_mp4_mux_release_pad;
}

static void gst_dash_mp4_mux_init(GstDashMp4Mux* self)
{
    self->fragment_duration_ns = 5 * GST_SECOND;
    self->mp4mux = gst_element_factory_make("mp4mux", NULL);
    if (self->mp4mux == NULL)
    {
        GST_ERROR_OBJECT(self, "Failed to create mp4mux");
        return;
    }
    g_object_set(self->mp4mux, "streamable", TRUE, "fragment-duration", 500U, NULL);
    gst_bin_add(GST_BIN(self), self->mp4mux);
    GstPad* source_pad = gst_element_get_static_pad(self->mp4mux, "src");
    if (source_pad != NULL)
    {
        GstPad* ghost_pad = gst_ghost_pad_new("src", source_pad);
        gst_object_unref(source_pad);
        if (ghost_pad != NULL)
        {
            gst_pad_set_active(ghost_pad, TRUE);
            gst_element_add_pad(GST_ELEMENT(self), ghost_pad);
        }
    }
}

static gboolean plugin_init(GstPlugin* plugin)
{
    return gst_element_register(plugin, "dashmp4mux", GST_RANK_NONE, GST_TYPE_DASH_MP4_MUX);
}

GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, dashmp4mux,
                  "DASH fMP4 muxer backed by mp4mux", plugin_init, "1.0.0", "MIT",
                  "NVIDIA VIOS", "https://nvidia.com")

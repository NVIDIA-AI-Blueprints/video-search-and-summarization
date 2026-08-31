//////////////////////////////////////////////////////////////////////////////////////////////////////
// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//////////////////////////////////////////////////////////////////////////////////////////////////////

#ifndef GST_NVDS_SEI_META_H_
#define GST_NVDS_SEI_META_H_

#include <gst/gst.h>

typedef struct _GstVideoSEIMeta {
    GstMeta meta;
    GQuark sei_metadata_type;
    guint sei_metadata_size;
    gpointer sei_metadata_ptr;
} GstVideoSEIMeta;

#ifdef __cplusplus
extern "C" {
#endif

GType gst_video_sei_meta_api_get_type(void);
const GstMetaInfo *gst_video_sei_meta_get_info(void);

#ifdef __cplusplus
}
#endif

#define GST_VIDEO_SEI_META_API_TYPE (gst_video_sei_meta_api_get_type())
#define GST_VIDEO_SEI_META_INFO (gst_video_sei_meta_get_info())

GstVideoSEIMeta *gst_buffer_add_video_sei_meta(GstBuffer *buffer);
GstVideoSEIMeta *gst_buffer_get_video_sei_meta(GstBuffer *buffer);

#endif  // GST_NVDS_SEI_META_H_

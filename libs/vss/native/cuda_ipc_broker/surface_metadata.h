// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <nvbufsurface.h>

inline NvBufSurfaceColorFormat vss_nv12_color_format(int color_matrix,
                                                     bool full_range) {
  if (color_matrix == 709) {
    return full_range ? NVBUF_COLOR_FORMAT_NV12_709_ER
                      : NVBUF_COLOR_FORMAT_NV12_709;
  }
  return full_range ? NVBUF_COLOR_FORMAT_NV12_ER : NVBUF_COLOR_FORMAT_NV12;
}

inline void vss_mark_surface_filled(NvBufSurface *surface) {
  surface->numFilled = 1;
}

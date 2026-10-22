// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
#include "surface_metadata.h"

#include <cassert>
#include <cstring>

int main() {
  NvBufSurface surface{};
  vss_mark_surface_filled(&surface);

  NvBufSurface published{};
  std::memcpy(&published, &surface, sizeof(NvBufSurface));
  assert(published.numFilled == 1);
  assert(vss_nv12_color_format(601, false) == NVBUF_COLOR_FORMAT_NV12);
  assert(vss_nv12_color_format(601, true) == NVBUF_COLOR_FORMAT_NV12_ER);
  assert(vss_nv12_color_format(709, false) == NVBUF_COLOR_FORMAT_NV12_709);
  assert(vss_nv12_color_format(709, true) == NVBUF_COLOR_FORMAT_NV12_709_ER);
  return 0;
}

// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

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

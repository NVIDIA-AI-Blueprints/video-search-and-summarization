/*
 * Copyright 2020 The libgav1 Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef LIBGAV1_SRC_FRAME_BUFFER_UTILS_H_
#define LIBGAV1_SRC_FRAME_BUFFER_UTILS_H_

#include <cassert>
#include <cstdint>

#include "src/gav1/decoder_buffer.h"

namespace libgav1 {

// The following table is from Section 6.4.2 of the spec.
//
// subsampling_x  subsampling_y  mono_chrome  Description
// -----------------------------------------------------------
// 0              0              0            YUV 4:4:4
// 1              0              0            YUV 4:2:2
// 1              1              0            YUV 4:2:0
// 1              1              1            Monochrome 4:0:0

inline Libgav1ImageFormat ComposeImageFormat(bool is_monochrome,
                                             int8_t subsampling_x,
                                             int8_t subsampling_y) {
  Libgav1ImageFormat image_format;
  if (subsampling_x == 0) {
    assert(subsampling_y == 0 && !is_monochrome);
    image_format = kLibgav1ImageFormatYuv444;
  } else if (subsampling_y == 0) {
    assert(!is_monochrome);
    image_format = kLibgav1ImageFormatYuv422;
  } else if (!is_monochrome) {
    image_format = kLibgav1ImageFormatYuv420;
  } else {
    image_format = kLibgav1ImageFormatMonochrome400;
  }
  return image_format;
}

inline void DecomposeImageFormat(Libgav1ImageFormat image_format,
                                 bool* is_monochrome, int8_t* subsampling_x,
                                 int8_t* subsampling_y) {
  *is_monochrome = false;
  *subsampling_x = 1;
  *subsampling_y = 1;
  switch (image_format) {
    case kLibgav1ImageFormatYuv420:
      break;
    case kLibgav1ImageFormatYuv422:
      *subsampling_y = 0;
      break;
    case kLibgav1ImageFormatYuv444:
      *subsampling_x = *subsampling_y = 0;
      break;
    default:
      assert(image_format == kLibgav1ImageFormatMonochrome400);
      *is_monochrome = true;
      break;
  }
}

}  // namespace libgav1

#endif  // LIBGAV1_SRC_FRAME_BUFFER_UTILS_H_

/*
 * Copyright 2019 The libgav1 Authors
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

#ifndef LIBGAV1_SRC_YUV_BUFFER_H_
#define LIBGAV1_SRC_YUV_BUFFER_H_

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <type_traits>

#include "src/gav1/frame_buffer.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/constants.h"

namespace libgav1 {

class YuvBuffer {
 public:
  // Allocates the buffer. Returns true on success. Returns false on failure.
  //
  // * |width| and |height| are the image dimensions in pixels.
  // * |subsampling_x| and |subsampling_y| (either 0 or 1) specify the
  //   subsampling of the width and height of the chroma planes, respectively.
  // * |left_border|, |right_border|, |top_border|, and |bottom_border| are
  //   the sizes (in pixels) of the borders on the left, right, top, and
  //   bottom sides, respectively. The four border sizes must all be a
  //   multiple of 2.
  // * If |get_frame_buffer| is not null, it is invoked to allocate the memory.
  //   If |get_frame_buffer| is null, YuvBuffer allocates the memory directly
  //   and ignores the |callback_private_data| and |buffer_private_data|
  //   parameters, which should be null.
  //
  // NOTE: The strides are a multiple of 16. Since the first row in each plane
  // is 16-byte aligned, subsequent rows are also 16-byte aligned.
  //
  // Example: bitdepth=8 width=20 height=6 left/right/top/bottom_border=2. The
  // diagram below shows how Realloc() allocates the data buffer for the Y
  // plane.
  //
  //   16-byte aligned
  //          |
  //          v
  //        ++++++++++++++++++++++++pppppppp
  //        ++++++++++++++++++++++++pppppppp
  //        ++01234567890123456789++pppppppp
  //        ++11234567890123456789++pppppppp
  //        ++21234567890123456789++pppppppp
  //        ++31234567890123456789++pppppppp
  //        ++41234567890123456789++pppppppp
  //        ++51234567890123456789++pppppppp
  //        ++++++++++++++++++++++++pppppppp
  //        ++++++++++++++++++++++++pppppppp
  //        |                              |
  //        |<-- stride (multiple of 16) ->|
  //
  // The video frame has 6 rows of 20 pixels each. Each row is shown as the
  // pattern r1234567890123456789, where |r| is 0, 1, 2, 3, 4, 5.
  //
  // Realloc() first adds a border of 2 pixels around the video frame. The
  // border pixels are shown as '+'.
  //
  // Each row is then padded to a multiple of the default alignment in bytes,
  // which is 16. The padding bytes are shown as lowercase 'p'. (Since
  // |bitdepth| is 8 in this example, each pixel is one byte.) The padded size
  // in bytes is the stride. In this example, the stride is 32 bytes.
  //
  // Finally, Realloc() aligns the first byte of frame data, which is the '0'
  // pixel/byte in the upper left corner of the frame, to the default (16-byte)
  // alignment boundary.
  //
  // TODO(wtc): Add a check for width and height limits to defend against
  // invalid bitstreams.
  bool Realloc(int bitdepth, bool is_monochrome, int width, int height,
               int8_t subsampling_x, int8_t subsampling_y, int left_border,
               int right_border, int top_border, int bottom_border,
               GetFrameBufferCallback get_frame_buffer,
               void* callback_private_data, void** buffer_private_data);

  int bitdepth() const { return bitdepth_; }

  bool is_monochrome() const { return is_monochrome_; }

  int8_t subsampling_x() const { return subsampling_x_; }
  int8_t subsampling_y() const { return subsampling_y_; }

  int width(int plane) const {
    return (plane == kPlaneY) ? y_width_ : uv_width_;
  }
  int height(int plane) const {
    return (plane == kPlaneY) ? y_height_ : uv_height_;
  }

  // Returns border sizes in pixels.
  int left_border(int plane) const { return left_border_[plane]; }
  int right_border(int plane) const { return right_border_[plane]; }
  int top_border(int plane) const { return top_border_[plane]; }
  int bottom_border(int plane) const { return bottom_border_[plane]; }

  // Returns the alignment of frame buffer row in bytes.
  int alignment() const { return kFrameBufferRowAlignment; }

  // Backup the current set of warnings and disable -Warray-bounds for the
  // following three functions as the compiler cannot, in all cases, determine
  // whether |plane| is within [0, kMaxPlanes), e.g., with a variable based for
  // loop.
#ifdef __GNUC__
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Warray-bounds"
#endif
  // Returns the data buffer for |plane|.
  uint8_t* data(int plane) {
    assert(plane >= 0);
    assert(static_cast<size_t>(plane) < std::extent<decltype(buffer_)>::value);
    return buffer_[plane];
  }
  const uint8_t* data(int plane) const {
    assert(plane >= 0);
    assert(static_cast<size_t>(plane) < std::extent<decltype(buffer_)>::value);
    return buffer_[plane];
  }

  // Returns the stride in bytes for |plane|.
  int stride(int plane) const {
    assert(plane >= 0);
    assert(static_cast<size_t>(plane) < std::extent<decltype(stride_)>::value);
    return stride_[plane];
  }
  // Restore the previous set of compiler warnings.
#ifdef __GNUC__
#pragma GCC diagnostic pop
#endif

 private:
  static constexpr int kFrameBufferRowAlignment = 16;

#if LIBGAV1_MSAN
  void InitializeFrameBorders();
#endif

  int bitdepth_ = 0;
  bool is_monochrome_ = false;

  // y_width_ and y_height_ are the |width| and |height| arguments passed to the
  // Realloc() method.
  //
  // uv_width_ and uv_height_ are computed from y_width_ and y_height_ as
  // follows:
  //   uv_width_ = (y_width_ + subsampling_x_) >> subsampling_x_
  //   uv_height_ = (y_height_ + subsampling_y_) >> subsampling_y_
  int y_width_ = 0;
  int uv_width_ = 0;
  int y_height_ = 0;
  int uv_height_ = 0;

  int left_border_[kMaxPlanes] = {};
  int right_border_[kMaxPlanes] = {};
  int top_border_[kMaxPlanes] = {};
  int bottom_border_[kMaxPlanes] = {};

  int stride_[kMaxPlanes] = {};
  uint8_t* buffer_[kMaxPlanes] = {};

  // buffer_alloc_ and buffer_alloc_size_ are only used if the
  // get_frame_buffer callback is null and we allocate the buffer ourselves.
  std::unique_ptr<uint8_t[]> buffer_alloc_;
  size_t buffer_alloc_size_ = 0;

  int8_t subsampling_x_ = 0;  // 0 or 1.
  int8_t subsampling_y_ = 0;  // 0 or 1.
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_YUV_BUFFER_H_

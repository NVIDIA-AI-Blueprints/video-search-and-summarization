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

#ifndef LIBGAV1_SRC_TILE_SCRATCH_BUFFER_H_
#define LIBGAV1_SRC_TILE_SCRATCH_BUFFER_H_

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>  // NOLINT (unapproved c++11 header)
#include <new>
#include <utility>

#include "src/dsp/constants.h"
#include "src/utils/common.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/constants.h"
#include "src/utils/memory.h"
#include "src/utils/stack.h"

namespace libgav1 {

// Buffer to facilitate decoding a superblock.
struct TileScratchBuffer : public MaxAlignedAllocable {
  static constexpr int kBlockDecodedStride = 34;

  LIBGAV1_MUST_USE_RESULT bool Init(int bitdepth) {
#if LIBGAV1_MAX_BITDEPTH >= 10
    const int pixel_size = (bitdepth == 8) ? 1 : 2;
#else
    assert(bitdepth == 8);
    static_cast<void>(bitdepth);
    const int pixel_size = 1;
#endif

    static_assert(kConvolveScaleBorderRight >= kConvolveBorderRight, "");
    constexpr int unaligned_convolve_buffer_stride =
        kMaxScaledSuperBlockSizeInPixels + kConvolveBorderLeftTop +
        kConvolveScaleBorderRight;
    convolve_block_buffer_stride = Align<ptrdiff_t>(
        unaligned_convolve_buffer_stride * pixel_size, kMaxAlignment);
    constexpr int convolve_buffer_height = kMaxScaledSuperBlockSizeInPixels +
                                           kConvolveBorderLeftTop +
                                           kConvolveBorderBottom;

    convolve_block_buffer = MakeAlignedUniquePtr<uint8_t>(
        kMaxAlignment, convolve_buffer_height * convolve_block_buffer_stride);
#if LIBGAV1_MSAN
    // Quiet msan warnings in ConvolveScale2D_NEON(). Set with random non-zero
    // value to aid in future debugging.
    memset(convolve_block_buffer.get(), 0x66,
           convolve_buffer_height * convolve_block_buffer_stride);
#endif

    return convolve_block_buffer != nullptr;
  }

  // kCompoundPredictionTypeDiffWeighted prediction mode needs a mask of the
  // prediction block size. This buffer is used to store that mask. The masks
  // will be created for the Y plane and will be re-used for the U & V planes.
  alignas(kMaxAlignment) uint8_t weight_mask[kMaxSuperBlockSizeSquareInPixels];

  // For each instance of the TileScratchBuffer, only one of the following
  // buffers will be used at any given time, so it is ok to share them in a
  // union.
  union {
    // Buffers used for prediction process.
    // Compound prediction calculations always output 16-bit values. Depending
    // on the bitdepth the values may be treated as int16_t or uint16_t. See
    // src/dsp/convolve.cc and src/dsp/warp.cc for explanations.
    // Inter/intra calculations output Pixel values.
    // These buffers always use width as the stride. This enables packing the
    // values in and simplifies loads/stores for small values.

    // 10/12 bit compound prediction and 10/12 bit inter/intra prediction.
    alignas(kMaxAlignment) uint16_t
        prediction_buffer[2][kMaxSuperBlockSizeSquareInPixels];
    // 8 bit compound prediction buffer.
    alignas(kMaxAlignment) int16_t
        compound_prediction_buffer_8bpp[2][kMaxSuperBlockSizeSquareInPixels];

    // Union usage note: This is used only by functions in the "intra"
    // prediction path.
    //
    // Buffer used for storing subsampled luma samples needed for CFL
    // prediction. This buffer is used to avoid repetition of the subsampling
    // for the V plane when it is already done for the U plane.
    int16_t cfl_luma_buffer[kCflLumaBufferStride][kCflLumaBufferStride];
  };

  // Buffer used for convolve. The maximum size required for this buffer is:
  //  maximum block height (with scaling and border) = 2 * 128 + 3 + 4 = 263.
  //  maximum block stride (with scaling and border aligned to 16) =
  //     (2 * 128 + 3 + 8 + 5) * pixel_size = 272 * pixel_size.
  //  Where pixel_size is (bitdepth == 8) ? 1 : 2.
  // Has an alignment of kMaxAlignment when allocated.
  AlignedUniquePtr<uint8_t> convolve_block_buffer;
  ptrdiff_t convolve_block_buffer_stride;

  // Flag indicating whether the data in |cfl_luma_buffer| is valid.
  bool cfl_luma_buffer_valid;

  // Equivalent to BlockDecoded array in the spec. This stores the decoded
  // state of every 4x4 block in a superblock. It has 1 row/column border on
  // all 4 sides (hence the 34x34 dimension instead of 32x32). Note that the
  // spec uses "-1" as an index to access the left and top borders. In the
  // code, we treat the index (1, 1) as equivalent to the spec's (0, 0). So
  // all accesses into this array will be offset by +1 when compared with the
  // spec.
  bool block_decoded[kMaxPlanes][kBlockDecodedStride][kBlockDecodedStride];
};

class TileScratchBufferPool {
 public:
  void Reset(int bitdepth) {
    if (bitdepth_ == bitdepth) return;
#if LIBGAV1_MAX_BITDEPTH >= 10
    if (bitdepth_ == 8 && bitdepth != 8) {
      // We are going from a pixel size of 1 to a pixel size of 2. So invalidate
      // the stack.
      std::lock_guard<std::mutex> lock(mutex_);
      while (!buffers_.Empty()) {
        buffers_.Pop();
      }
    }
#endif
    bitdepth_ = bitdepth;
  }

  std::unique_ptr<TileScratchBuffer> Get() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (buffers_.Empty()) {
      std::unique_ptr<TileScratchBuffer> scratch_buffer(new (std::nothrow)
                                                            TileScratchBuffer);
      if (scratch_buffer == nullptr || !scratch_buffer->Init(bitdepth_)) {
        return nullptr;
      }
      return scratch_buffer;
    }
    return buffers_.Pop();
  }

  void Release(std::unique_ptr<TileScratchBuffer> scratch_buffer) {
    std::lock_guard<std::mutex> lock(mutex_);
    buffers_.Push(std::move(scratch_buffer));
  }

 private:
  std::mutex mutex_;
  // We will never need more than kMaxThreads scratch buffers since that is the
  // maximum amount of work that will be done at any given time.
  Stack<std::unique_ptr<TileScratchBuffer>, kMaxThreads> buffers_
      LIBGAV1_GUARDED_BY(mutex_);
  int bitdepth_ = 0;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_TILE_SCRATCH_BUFFER_H_

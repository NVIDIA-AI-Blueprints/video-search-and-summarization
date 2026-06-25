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

#ifndef LIBGAV1_SRC_FILM_GRAIN_H_
#define LIBGAV1_SRC_FILM_GRAIN_H_

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <type_traits>

#include "src/dsp/common.h"
#include "src/dsp/dsp.h"
#include "src/dsp/film_grain_common.h"
#include "src/utils/array_2d.h"
#include "src/utils/constants.h"
#include "src/utils/cpu.h"
#include "src/utils/threadpool.h"
#include "src/utils/types.h"
#include "src/utils/vector.h"

namespace libgav1 {

// Film grain synthesis function signature. Section 7.18.3.
// This function generates film grain noise and blends the noise with the
// decoded frame.
// |source_plane_y|, |source_plane_u|, and |source_plane_v| are the plane
// buffers of the decoded frame. They are blended with the film grain noise and
// written to |dest_plane_y|, |dest_plane_u|, and |dest_plane_v| as final
// output for display. |source_plane_p| and |dest_plane_p| (where p is y, u, or
// v) may point to the same buffer, in which case the film grain noise is added
// in place.
// |film_grain_params| are parameters read from frame header.
// |is_monochrome| is true indicates only Y plane needs to be processed.
// |color_matrix_is_identity| is true if the matrix_coefficients field in the
// sequence header's color config is is MC_IDENTITY.
// |width| is the upscaled width of the frame.
// |height| is the frame height.
// |subsampling_x| and |subsampling_y| are subsamplings for UV planes, not used
// if |is_monochrome| is true.
// Returns true on success, or false on failure (e.g., out of memory).
using FilmGrainSynthesisFunc = bool (*)(
    const void* source_plane_y, ptrdiff_t source_stride_y,
    const void* source_plane_u, ptrdiff_t source_stride_u,
    const void* source_plane_v, ptrdiff_t source_stride_v,
    const FilmGrainParams& film_grain_params, bool is_monochrome,
    bool color_matrix_is_identity, int width, int height, int subsampling_x,
    int subsampling_y, void* dest_plane_y, ptrdiff_t dest_stride_y,
    void* dest_plane_u, ptrdiff_t dest_stride_u, void* dest_plane_v,
    ptrdiff_t dest_stride_v);

// Section 7.18.3.5. Add noise synthesis process.
template <int bitdepth>
class FilmGrain {
 public:
  using GrainType =
      typename std::conditional<bitdepth == 8, int8_t, int16_t>::type;

  FilmGrain(const FilmGrainParams& params, bool is_monochrome,
            bool color_matrix_is_identity, int subsampling_x, int subsampling_y,
            int width, int height, ThreadPool* thread_pool);
  ~FilmGrain();

  // Note: These static methods are declared public so that the unit tests can
  // call them.

  static void GenerateLumaGrain(const FilmGrainParams& params,
                                GrainType* luma_grain);

  // Generates white noise arrays u_grain and v_grain chroma_width samples wide
  // and chroma_height samples high.
  static void GenerateChromaGrains(const FilmGrainParams& params,
                                   int chroma_width, int chroma_height,
                                   GrainType* u_grain, GrainType* v_grain);

  // Copies rows from |noise_stripes| to |noise_image|, skipping rows that are
  // subject to overlap.
  static void ConstructNoiseImage(const Array2DView<GrainType>* noise_stripes,
                                  int width, int height, int subsampling_x,
                                  int subsampling_y, int stripe_start_offset,
                                  Array2D<GrainType>* noise_image);

  // Combines the film grain with the image data.
  bool AddNoise(const uint8_t* source_plane_y, ptrdiff_t source_stride_y,
                const uint8_t* source_plane_u, const uint8_t* source_plane_v,
                ptrdiff_t source_stride_uv, uint8_t* dest_plane_y,
                ptrdiff_t dest_stride_y, uint8_t* dest_plane_u,
                uint8_t* dest_plane_v, ptrdiff_t dest_stride_uv);

 private:
  using Pixel =
      typename std::conditional<bitdepth == 8, uint8_t, uint16_t>::type;
  static constexpr int kScalingLutLength =
      (bitdepth == 10)
          ? (kScalingLookupTableSize + kScalingLookupTablePadding) << 2
          : kScalingLookupTableSize + kScalingLookupTablePadding;

  bool Init();

  // Allocates noise_stripes_.
  bool AllocateNoiseStripes();

  bool AllocateNoiseImage();

  void BlendNoiseChromaWorker(const dsp::Dsp& dsp, const Plane* planes,
                              int num_planes, std::atomic<int>* job_counter,
                              int min_value, int max_chroma,
                              const uint8_t* source_plane_y,
                              ptrdiff_t source_stride_y,
                              const uint8_t* source_plane_u,
                              const uint8_t* source_plane_v,
                              ptrdiff_t source_stride_uv, uint8_t* dest_plane_u,
                              uint8_t* dest_plane_v, ptrdiff_t dest_stride_uv);

  void BlendNoiseLumaWorker(const dsp::Dsp& dsp, std::atomic<int>* job_counter,
                            int min_value, int max_luma,
                            const uint8_t* source_plane_y,
                            ptrdiff_t source_stride_y, uint8_t* dest_plane_y,
                            ptrdiff_t dest_stride_y);

  const FilmGrainParams& params_;
  const bool is_monochrome_;
  const bool color_matrix_is_identity_;
  const int subsampling_x_;
  const int subsampling_y_;
  // Frame width and height.
  const int width_;
  const int height_;
  // Section 7.18.3.3, Dimensions of the noise templates for chroma, which are
  // known as CbGrain and CrGrain.
  // These templates are used to construct the noise image for each plane by
  // copying 32x32 blocks with pseudorandom offsets, into "noise stripes."
  // The noise template known as LumaGrain array is an 82x73 block.
  // The height and width of the templates for chroma become 44 and 38 under
  // subsampling, respectively.
  //  For more details see:
  // A. Norkin and N. Birkbeck, "Film Grain Synthesis for AV1 Video Codec," 2018
  // Data Compression Conference, Snowbird, UT, 2018, pp. 3-12.
  const int template_uv_width_;
  const int template_uv_height_;
  // LumaGrain. The luma_grain array contains white noise generated for luma.
  // The array size is fixed but subject to further optimization for SIMD.
  GrainType luma_grain_[kLumaHeight * kLumaWidth];
  // CbGrain and CrGrain. The maximum size of the u_grain and v_grain arrays is
  // kMaxChromaHeight * kMaxChromaWidth. The actual size is
  // template_uv_height_ * template_uv_width_.
  GrainType u_grain_[kMaxChromaHeight * kMaxChromaWidth];
  GrainType v_grain_[kMaxChromaHeight * kMaxChromaWidth];
  // Scaling lookup tables.
  int16_t scaling_lut_y_[kScalingLutLength];
  int16_t* scaling_lut_u_ = nullptr;
  int16_t* scaling_lut_v_ = nullptr;
  // If allocated, this buffer is 256 * 2 values long and scaling_lut_u_ and
  // scaling_lut_v_ point into this buffer. Otherwise, scaling_lut_u_ and
  // scaling_lut_v_ point to scaling_lut_y_.
  std::unique_ptr<int16_t[]> scaling_lut_chroma_buffer_;

  // A two-dimensional array of noise data for each plane. Generated for each 32
  // luma sample high stripe of the image. The first dimension is called
  // luma_num. The second dimension is the size of one noise stripe.
  //
  // Each row of the Array2DView noise_stripes_[plane] is a conceptually
  // two-dimensional array of |GrainType|s. The two-dimensional array of
  // |GrainType|s is flattened into a one-dimensional buffer in this
  // implementation.
  //
  // noise_stripes_[kPlaneY][luma_num] is an array that has 34 rows and
  // |width_| columns and contains noise for the luma component.
  //
  // noise_stripes_[kPlaneU][luma_num] or noise_stripes_[kPlaneV][luma_num]
  // is an array that has (34 >> subsampling_y_) rows and
  // SubsampledValue(width_, subsampling_x_) columns and contains noise for the
  // chroma components.
  Array2DView<GrainType> noise_stripes_[kMaxPlanes];
  // Owns the memory that the elements of noise_stripes_ point to.
  std::unique_ptr<GrainType[]> noise_buffer_;

  Array2D<GrainType> noise_image_[kMaxPlanes];
  ThreadPool* const thread_pool_;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_FILM_GRAIN_H_

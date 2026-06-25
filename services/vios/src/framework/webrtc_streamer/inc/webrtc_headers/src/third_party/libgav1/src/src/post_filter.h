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

#ifndef LIBGAV1_SRC_POST_FILTER_H_
#define LIBGAV1_SRC_POST_FILTER_H_

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>

#include "src/dsp/common.h"
#include "src/dsp/dsp.h"
#include "src/frame_scratch_buffer.h"
#include "src/loop_restoration_info.h"
#include "src/obu_parser.h"
#include "src/utils/array_2d.h"
#include "src/utils/block_parameters_holder.h"
#include "src/utils/common.h"
#include "src/utils/constants.h"
#include "src/utils/memory.h"
#include "src/utils/threadpool.h"
#include "src/yuv_buffer.h"

namespace libgav1 {

// This class applies in-loop filtering for each frame after it is
// reconstructed. The in-loop filtering contains all post processing filtering
// for the reconstructed frame, including deblock filter, CDEF, superres,
// and loop restoration.
// Historically, for example in libaom, loop filter refers to deblock filter.
// To avoid name conflicts, we call this class PostFilter (post processing).
// In-loop post filtering order is:
// deblock --> CDEF --> super resolution--> loop restoration.
// When CDEF and super resolution is not used, we can combine deblock
// and restoration together to only filter frame buffer once.
class PostFilter {
 public:
  // This class does not take ownership of the masks/restoration_info, but it
  // may change their values.
  //
  // The overall flow of data in this class (for both single and multi-threaded
  // cases) is as follows:
  //   -> Input: |frame_buffer_|.
  //   -> Initialize |source_buffer_|, |cdef_buffer_|, |superres_buffer_| and
  //      |loop_restoration_buffer_|.
  //   -> Deblocking:
  //      * Input: |source_buffer_|
  //      * Output: |source_buffer_|
  //   -> CDEF:
  //      * Input: |source_buffer_|
  //      * Output: |cdef_buffer_|
  //   -> SuperRes:
  //      * Input: |cdef_buffer_|
  //      * Output: |superres_buffer_|
  //   -> Loop Restoration:
  //      * Input: |superres_buffer_|
  //      * Output: |loop_restoration_buffer_|.
  //   -> Now |frame_buffer_| contains the filtered frame.
  PostFilter(const ObuFrameHeader& frame_header,
             const ObuSequenceHeader& sequence_header,
             FrameScratchBuffer* frame_scratch_buffer, YuvBuffer* frame_buffer,
             const dsp::Dsp* dsp, int do_post_filter_mask);

  // non copyable/movable.
  PostFilter(const PostFilter&) = delete;
  PostFilter& operator=(const PostFilter&) = delete;
  PostFilter(PostFilter&&) = delete;
  PostFilter& operator=(PostFilter&&) = delete;

  // The overall function that applies all post processing filtering with
  // multiple threads.
  // * The filtering order is:
  //   deblock --> CDEF --> super resolution--> loop restoration.
  // * The output of each filter is the input for the following filter. A
  //   special case is that loop restoration needs a few rows of the deblocked
  //   frame and the entire cdef filtered frame:
  //   deblock --> CDEF --> super resolution --> loop restoration.
  //              |                                 ^
  //              |                                 |
  //              -----------> super resolution -----
  // * Any of these filters could be present or absent.
  // * |frame_buffer_| points to the decoded frame buffer. When
  //   ApplyFilteringThreaded() is called, |frame_buffer_| is modified by each
  //   of the filters as described below.
  // Filter behavior (multi-threaded):
  // * Deblock: In-place filtering. The output is written to |source_buffer_|.
  //            If cdef and loop restoration are both on, then 4 rows (as
  //            specified by |kLoopRestorationBorderRows|) in every 64x64 block
  //            is copied into |loop_restoration_border_|.
  // * Cdef: In-place filtering. Uses the |source_buffer_| and |cdef_border_| as
  //         the input and the output is written into |cdef_buffer_| (which is
  //         the same as |source_buffer_|).
  // * SuperRes: Near in-place filtering. Uses the |cdef_buffer_| and
  //             |superres_line_buffer_| as the input and the output is written
  //             into |superres_buffer_| (which is just |cdef_buffer_| with a
  //             shift to the top).
  // * Restoration: Near in-place filtering.
  //                Uses the |superres_buffer_| and |loop_restoration_border_|
  //                as the input and the output is written into
  //                |loop_restoration_buffer_| (which is just |superres_buffer_|
  //                with a shift to the left).
  // Section 7.4.
  void ApplyFilteringThreaded();

  // Does the overall post processing filter for one superblock row starting at
  // |row4x4| with height 4*|sb4x4|. If |do_deblock| is false, deblocking filter
  // will not be applied.
  //
  // Filter behavior (single-threaded):
  // * Deblock: In-place filtering. The output is written to |source_buffer_|.
  //            If cdef and loop restoration are both on, then 4 rows (as
  //            specified by |kLoopRestorationBorderRows|) in every 64x64 block
  //            is copied into |loop_restoration_border_|.
  // * Cdef: In-place filtering. The output is written into |cdef_buffer_|
  //         (which is just |source_buffer_| with a shift to the top-left).
  // * SuperRes: Near in-place filtering. Uses the |cdef_buffer_| as the input
  //             and the output is written into |superres_buffer_| (which is
  //             just |cdef_buffer_| with a shift to the top).
  // * Restoration: Near in-place filtering.
  //                Uses the |superres_buffer_| and |loop_restoration_border_|
  //                as the input and the output is written into
  //                |loop_restoration_buffer_| (which is just |superres_buffer_|
  //                with a shift to the left or top-left).
  // Returns the index of the last row whose post processing is complete and can
  // be used for referencing.
  // Section 7.4.
  int ApplyFilteringForOneSuperBlockRow(int row4x4, int sb4x4, bool is_last_row,
                                        bool do_deblock);

  // Apply deblocking filter in one direction (specified by |loop_filter_type|)
  // for the superblock row starting at |row4x4_start| for columns starting from
  // |column4x4_start| in increments of 16 (or 8 for chroma with subsampling)
  // until the smallest multiple of 16 that is >= |column4x4_end| or until
  // |frame_header_.columns4x4|, whichever is lower. This function must be
  // called only if |DoDeblock()| returns true.
  void ApplyDeblockFilter(LoopFilterType loop_filter_type, int row4x4_start,
                          int column4x4_start, int column4x4_end, int sb4x4);

  static bool DoCdef(const ObuFrameHeader& frame_header,
                     int do_post_filter_mask) {
    return (frame_header.cdef.bits > 0 ||
            frame_header.cdef.y_primary_strength[0] > 0 ||
            frame_header.cdef.y_secondary_strength[0] > 0 ||
            frame_header.cdef.uv_primary_strength[0] > 0 ||
            frame_header.cdef.uv_secondary_strength[0] > 0) &&
           (do_post_filter_mask & 0x02) != 0;
  }
  bool DoCdef() const { return do_cdef_; }
  // If filter levels for Y plane (0 for vertical, 1 for horizontal),
  // are all zero, deblock filter will not be applied.
  static bool DoDeblock(const ObuFrameHeader& frame_header,
                        uint8_t do_post_filter_mask) {
    return (frame_header.loop_filter.level[0] > 0 ||
            frame_header.loop_filter.level[1] > 0) &&
           (do_post_filter_mask & 0x01) != 0;
  }
  bool DoDeblock() const { return do_deblock_; }

  uint8_t GetZeroDeltaDeblockFilterLevel(int segment_id, int level_index,
                                         ReferenceFrameType type,
                                         int mode_id) const {
    return deblock_filter_levels_[segment_id][level_index][type][mode_id];
  }
  // Computes the deblock filter levels using |delta_lf| and stores them in
  // |deblock_filter_levels|.
  void ComputeDeblockFilterLevels(
      const int8_t delta_lf[kFrameLfCount],
      uint8_t deblock_filter_levels[kMaxSegments][kFrameLfCount]
                                   [kNumReferenceFrameTypes][2]) const;
  // Returns true if loop restoration will be performed for the given parameters
  // and mask.
  static bool DoRestoration(const LoopRestoration& loop_restoration,
                            uint8_t do_post_filter_mask, int num_planes) {
    if (num_planes == kMaxPlanesMonochrome) {
      return loop_restoration.type[kPlaneY] != kLoopRestorationTypeNone &&
             (do_post_filter_mask & 0x08) != 0;
    }
    return (loop_restoration.type[kPlaneY] != kLoopRestorationTypeNone ||
            loop_restoration.type[kPlaneU] != kLoopRestorationTypeNone ||
            loop_restoration.type[kPlaneV] != kLoopRestorationTypeNone) &&
           (do_post_filter_mask & 0x08) != 0;
  }
  bool DoRestoration() const { return do_restoration_; }

  // Returns a pointer to the unfiltered buffer. This is used by the Tile class
  // to determine where to write the output of the tile decoding process taking
  // in-place filtering offsets into consideration.
  uint8_t* GetUnfilteredBuffer(int plane) { return source_buffer_[plane]; }
  const YuvBuffer& frame_buffer() const { return frame_buffer_; }

  // Returns true if SuperRes will be performed for the given frame header and
  // mask.
  static bool DoSuperRes(const ObuFrameHeader& frame_header,
                         uint8_t do_post_filter_mask) {
    return frame_header.width != frame_header.upscaled_width &&
           (do_post_filter_mask & 0x04) != 0;
  }
  bool DoSuperRes() const { return do_superres_; }
  LoopRestorationInfo* restoration_info() const { return restoration_info_; }
  uint8_t* GetBufferOffset(uint8_t* base_buffer, int stride, Plane plane,
                           int row, int column) const {
    return base_buffer + (row >> subsampling_y_[plane]) * stride +
           ((column >> subsampling_x_[plane]) << pixel_size_log2_);
  }
  uint8_t* GetSourceBuffer(Plane plane, int row4x4, int column4x4) const {
    return GetBufferOffset(source_buffer_[plane], frame_buffer_.stride(plane),
                           plane, MultiplyBy4(row4x4), MultiplyBy4(column4x4));
  }
  uint8_t* GetCdefBuffer(Plane plane, int row4x4, int column4x4) const {
    return GetBufferOffset(cdef_buffer_[plane], frame_buffer_.stride(plane),
                           plane, MultiplyBy4(row4x4), MultiplyBy4(column4x4));
  }
  uint8_t* GetSuperResBuffer(Plane plane, int row4x4, int column4x4) const {
    return GetBufferOffset(superres_buffer_[plane], frame_buffer_.stride(plane),
                           plane, MultiplyBy4(row4x4), MultiplyBy4(column4x4));
  }

  template <typename Pixel>
  static void ExtendFrame(Pixel* frame_start, int width, int height,
                          ptrdiff_t stride, int left, int right, int top,
                          int bottom);

 private:
  // The type of the HorizontalDeblockFilter and VerticalDeblockFilter member
  // functions.
  using DeblockFilter = void (PostFilter::*)(int row4x4_start, int row4x4_end,
                                             int column4x4_start,
                                             int column4x4_end);
  // Functions common to all post filters.

  // Extends the frame by setting the border pixel values to the one from its
  // closest frame boundary.
  void ExtendFrameBoundary(uint8_t* frame_start, int width, int height,
                           ptrdiff_t stride, int left, int right, int top,
                           int bottom) const;
  // Extend frame boundary for referencing if the frame will be saved as a
  // reference frame.
  void ExtendBordersForReferenceFrame();
  // Copies the deblocked pixels needed for loop restoration.
  void CopyDeblockedPixels(Plane plane, int row4x4);
  // Copies the border for one superblock row. If |for_loop_restoration| is
  // true, then it assumes that the border extension is being performed for the
  // input of the loop restoration process. If |for_loop_restoration| is false,
  // then it assumes that the border extension is being performed for using the
  // current frame as a reference frame. In this case, |progress_row_| is also
  // updated.
  void CopyBordersForOneSuperBlockRow(int row4x4, int sb4x4,
                                      bool for_loop_restoration);
  // Sets up the |loop_restoration_border_| for loop restoration.
  // This is called when there is no CDEF filter. We copy rows from
  // |superres_buffer_| and do the line extension.
  void SetupLoopRestorationBorder(int row4x4_start);
  // This is called when there is CDEF filter. We copy rows from
  // |source_buffer_|, apply superres and do the line extension.
  void SetupLoopRestorationBorder(int row4x4_start, int sb4x4);
  // Returns true if we can perform border extension in loop (i.e.) without
  // waiting until the entire frame is decoded. If intra_block_copy is true, we
  // do in-loop border extension only if the upscaled_width is the same as 4 *
  // columns4x4. Otherwise, we cannot do in loop border extension since those
  // pixels may be used by intra block copy.
  bool DoBorderExtensionInLoop() const {
    return !frame_header_.allow_intrabc ||
           frame_header_.upscaled_width ==
               MultiplyBy4(frame_header_.columns4x4);
  }
  template <typename Pixel>
  void CopyPlane(const Pixel* src, ptrdiff_t src_stride, int width, int height,
                 Pixel* dst, ptrdiff_t dst_stride) {
    assert(height > 0);
    do {
      memcpy(dst, src, width * sizeof(Pixel));
      src += src_stride;
      dst += dst_stride;
    } while (--height != 0);
  }

  // Worker function used for multi-threaded implementation of Deblocking, CDEF
  // and Loop Restoration.
  using WorkerFunction = void (PostFilter::*)(std::atomic<int>* row4x4_atomic);
  // Schedules |worker| jobs to the |thread_pool_|, runs them in the calling
  // thread and returns once all the jobs are completed.
  void RunJobs(WorkerFunction worker);

  // Functions for the Deblocking filter.

  bool GetHorizontalDeblockFilterEdgeInfo(int row4x4, int column4x4,
                                          uint8_t* level, int* step,
                                          int* filter_length) const;
  void GetHorizontalDeblockFilterEdgeInfoUV(int row4x4, int column4x4,
                                            uint8_t* level_u, uint8_t* level_v,
                                            int* step,
                                            int* filter_length) const;
  bool GetVerticalDeblockFilterEdgeInfo(int row4x4, int column4x4,
                                        BlockParameters* const* bp_ptr,
                                        uint8_t* level, int* step,
                                        int* filter_length) const;
  void GetVerticalDeblockFilterEdgeInfoUV(int column4x4,
                                          BlockParameters* const* bp_ptr,
                                          uint8_t* level_u, uint8_t* level_v,
                                          int* step, int* filter_length) const;
  void HorizontalDeblockFilter(int row4x4_start, int row4x4_end,
                               int column4x4_start, int column4x4_end);
  void VerticalDeblockFilter(int row4x4_start, int row4x4_end,
                             int column4x4_start, int column4x4_end);
  // HorizontalDeblockFilter and VerticalDeblockFilter must have the correct
  // signature.
  static_assert(std::is_same<decltype(&PostFilter::HorizontalDeblockFilter),
                             DeblockFilter>::value,
                "");
  static_assert(std::is_same<decltype(&PostFilter::VerticalDeblockFilter),
                             DeblockFilter>::value,
                "");
  // Worker function used for multi-threaded deblocking.
  template <LoopFilterType loop_filter_type>
  void DeblockFilterWorker(std::atomic<int>* row4x4_atomic);
  static_assert(
      std::is_same<
          decltype(&PostFilter::DeblockFilterWorker<kLoopFilterTypeVertical>),
          WorkerFunction>::value,
      "");
  static_assert(
      std::is_same<
          decltype(&PostFilter::DeblockFilterWorker<kLoopFilterTypeHorizontal>),
          WorkerFunction>::value,
      "");

  // Functions for the cdef filter.

  // Copies the deblocked pixels necessary for use by the multi-threaded cdef
  // implementation into |cdef_border_|.
  void SetupCdefBorder(int row4x4);
  // This function prepares the input source block for cdef filtering. The input
  // source block contains a 12x12 block, with the inner 8x8 as the desired
  // filter region. It pads the block if the 12x12 block includes out of frame
  // pixels with a large value. This achieves the required behavior defined in
  // section 5.11.52 of the spec.
  template <typename Pixel>
  void PrepareCdefBlock(int block_width4x4, int block_height4x4, int row4x4,
                        int column4x4, uint16_t* cdef_source,
                        ptrdiff_t cdef_stride, bool y_plane,
                        const uint8_t border_columns[kMaxPlanes][256],
                        bool use_border_columns);
  // Applies cdef for one 64x64 block.
  template <typename Pixel>
  void ApplyCdefForOneUnit(uint16_t* cdef_block, int index, int block_width4x4,
                           int block_height4x4, int row4x4_start,
                           int column4x4_start,
                           uint8_t border_columns[2][kMaxPlanes][256],
                           bool use_border_columns[2][2]);
  // Helper function used by ApplyCdefForOneSuperBlockRow to avoid some code
  // duplication.
  void ApplyCdefForOneSuperBlockRowHelper(
      uint16_t* cdef_block, uint8_t border_columns[2][kMaxPlanes][256],
      int row4x4, int block_height4x4);
  // Applies CDEF filtering for the superblock row starting at |row4x4| with a
  // height of 4*|sb4x4|.
  void ApplyCdefForOneSuperBlockRow(int row4x4, int sb4x4, bool is_last_row);
  // Worker function used for multi-threaded CDEF.
  void ApplyCdefWorker(std::atomic<int>* row4x4_atomic);
  static_assert(std::is_same<decltype(&PostFilter::ApplyCdefWorker),
                             WorkerFunction>::value,
                "");

  // Functions for the SuperRes filter.

  // Applies super resolution for the |src| for |rows[plane]| rows of each
  // plane. If |line_buffer_row| is larger than or equal to 0, one more row will
  // be processed, the line buffer indicated by |line_buffer_row| will be used
  // as the source. If |dst_is_loop_restoration_border| is true, then it means
  // that the |dst| pointers come from |loop_restoration_border_| and the
  // strides will be populated from that buffer.
  void ApplySuperRes(
      const std::array<uint8_t*, kMaxPlanes>& src,
      const std::array<int, kMaxPlanes>& rows, int line_buffer_row,
      const std::array<uint8_t*, kMaxPlanes>& dst,
      bool dst_is_loop_restoration_border = false);  // Section 7.16.
  // Applies SuperRes for the superblock row starting at |row4x4| with a height
  // of 4*|sb4x4|.
  void ApplySuperResForOneSuperBlockRow(int row4x4, int sb4x4,
                                        bool is_last_row);
  void ApplySuperResThreaded();

  // Functions for the Loop Restoration filter.

  // Notes about Loop Restoration:
  // (1). Loop restoration processing unit size is default to 64x64.
  // Only when the remaining filtering area is smaller than 64x64, the
  // processing unit size is the actual area size.
  // For U/V plane, it is (64 >> subsampling_x) x (64 >> subsampling_y).
  // (2). Loop restoration unit size can be 64x64, 128x128, 256x256 for Y
  // plane. The unit size for chroma can be the same or half, depending on
  // subsampling. If either subsampling_x or subsampling_y is one, unit size
  // is halved on both x and y sides.
  // All loop restoration units have the same size for one plane.
  // One loop restoration unit could contain multiple processing units.
  // But they share the same sets of loop restoration parameters.
  // (3). Loop restoration has a row offset, kRestorationUnitOffset = 8. The
  // size of first row of loop restoration units and processing units is
  // shrunk by the offset.
  // (4). Loop restoration units wrap the bottom and the right of the frame,
  // if the remaining area is small. The criteria is whether the number of
  // remaining rows/columns is smaller than half of loop restoration unit
  // size.
  // For example, if the frame size is 140x140, loop restoration unit size is
  // 128x128. The size of the first loop restoration unit is 128x(128-8) =
  // 128 columns x 120 rows.
  // Since 140 - 120 < 128/2. The remaining 20 rows will be folded to the loop
  // restoration unit. Similarly, the remaining 12 columns will also be folded
  // to current loop restoration unit. So, even frame size is 140x140,
  // there's only one loop restoration unit. Suppose processing unit is 64x64,
  // then sizes of the first row of processing units are 64x56, 64x56, 12x56,
  // respectively. The second row is 64x64, 64x64, 12x64.
  // The third row is 64x20, 64x20, 12x20.

  // |stride| is shared by |src_buffer| and |dst_buffer|.
  template <typename Pixel>
  void ApplyLoopRestorationForOneRow(const Pixel* src_buffer, ptrdiff_t stride,
                                     Plane plane, int plane_height,
                                     int plane_width, int y, int unit_row,
                                     int current_process_unit_height,
                                     int plane_unit_size, Pixel* dst_buffer);
  // Applies loop restoration for the superblock row starting at |row4x4_start|
  // with a height of 4*|sb4x4|.
  template <typename Pixel>
  void ApplyLoopRestorationForOneSuperBlockRow(int row4x4_start, int sb4x4);
  // Helper function that calls the right variant of
  // ApplyLoopRestorationForOneSuperBlockRow based on the bitdepth.
  void ApplyLoopRestoration(int row4x4_start, int sb4x4);
  // Worker function used for multithreaded Loop Restoration.
  void ApplyLoopRestorationWorker(std::atomic<int>* row4x4_atomic);
  static_assert(std::is_same<decltype(&PostFilter::ApplyLoopRestorationWorker),
                             WorkerFunction>::value,
                "");

  // The lookup table for picking the deblock filter, according to deblock
  // filter type.
  const DeblockFilter deblock_filter_func_[2] = {
      &PostFilter::VerticalDeblockFilter, &PostFilter::HorizontalDeblockFilter};
  const ObuFrameHeader& frame_header_;
  const LoopRestoration& loop_restoration_;
  const dsp::Dsp& dsp_;
  const int8_t bitdepth_;
  const int8_t subsampling_x_[kMaxPlanes];
  const int8_t subsampling_y_[kMaxPlanes];
  const int8_t planes_;
  const int pixel_size_log2_;
  const uint8_t* const inner_thresh_;
  const uint8_t* const outer_thresh_;
  const bool needs_chroma_deblock_;
  const bool do_cdef_;
  const bool do_deblock_;
  const bool do_restoration_;
  const bool do_superres_;
  // This stores the deblocking filter levels assuming that the delta is zero.
  // This will be used by all superblocks whose delta is zero (without having to
  // recompute them). The dimensions (in order) are: segment_id, level_index
  // (based on plane and direction), reference_frame and mode_id.
  uint8_t deblock_filter_levels_[kMaxSegments][kFrameLfCount]
                                [kNumReferenceFrameTypes][2];
  // Stores the SuperRes info for the frame.
  struct {
    int upscaled_width;
    int initial_subpixel_x;
    int step;
  } super_res_info_[kMaxPlanes];
  const Array2D<int8_t>& cdef_index_;
  const Array2D<uint8_t>& cdef_skip_;
  const Array2D<TransformSize>& inter_transform_sizes_;
  LoopRestorationInfo* const restoration_info_;
  uint8_t* const superres_coefficients_[kNumPlaneTypes];
  // Line buffer used by multi-threaded ApplySuperRes().
  // In the multi-threaded case, this buffer will store the last downscaled row
  // input of each thread to avoid overwrites by the first upscaled row output
  // of the thread below it.
  YuvBuffer& superres_line_buffer_;
  const BlockParametersHolder& block_parameters_;
  // Frame buffer to hold cdef filtered frame.
  YuvBuffer cdef_filtered_buffer_;
  // Input frame buffer.
  YuvBuffer& frame_buffer_;
  // A view into |frame_buffer_| that points to the input and output of the
  // deblocking process.
  uint8_t* source_buffer_[kMaxPlanes];
  // A view into |frame_buffer_| that points to the output of the CDEF filtered
  // planes (to facilitate in-place CDEF filtering).
  uint8_t* cdef_buffer_[kMaxPlanes];
  // A view into |frame_buffer_| that points to the planes after the SuperRes
  // filter is applied (to facilitate in-place SuperRes).
  uint8_t* superres_buffer_[kMaxPlanes];
  // A view into |frame_buffer_| that points to the output of the Loop Restored
  // planes (to facilitate in-place Loop Restoration).
  uint8_t* loop_restoration_buffer_[kMaxPlanes];
  YuvBuffer& cdef_border_;
  // Buffer used to store the border pixels that are necessary for loop
  // restoration. This buffer will store 4 rows for every 64x64 block (4 rows
  // for every 32x32 for chroma with subsampling). The indices of the rows that
  // are stored are specified in |kLoopRestorationBorderRows|. First 4 rows of
  // this buffer are never populated and never used.
  // This buffer is used only when both of the following conditions are true:
  //   (1). Loop Restoration is on.
  //   (2). Cdef is on, or multi-threading is enabled for post filter.
  YuvBuffer& loop_restoration_border_;
  ThreadPool* const thread_pool_;

  // Tracks the progress of the post filters.
  int progress_row_ = -1;

  // A block buffer to hold the input that is converted to uint16_t before
  // cdef filtering. Only used in single threaded case. Y plane is processed
  // separately. U and V planes are processed together. So it is sufficient to
  // have this buffer to accommodate 2 planes at a time.
  uint16_t cdef_block_[kCdefUnitSizeWithBorders * kCdefUnitSizeWithBorders * 2];

  template <int bitdepth, typename Pixel>
  friend class PostFilterSuperResTest;

  template <int bitdepth, typename Pixel>
  friend class PostFilterHelperFuncTest;
};

extern template void PostFilter::ExtendFrame<uint8_t>(uint8_t* frame_start,
                                                      int width, int height,
                                                      ptrdiff_t stride,
                                                      int left, int right,
                                                      int top, int bottom);

#if LIBGAV1_MAX_BITDEPTH >= 10
extern template void PostFilter::ExtendFrame<uint16_t>(uint16_t* frame_start,
                                                       int width, int height,
                                                       ptrdiff_t stride,
                                                       int left, int right,
                                                       int top, int bottom);
#endif

}  // namespace libgav1

#endif  // LIBGAV1_SRC_POST_FILTER_H_

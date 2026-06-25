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

#ifndef LIBGAV1_SRC_TILE_H_
#define LIBGAV1_SRC_TILE_H_

#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <condition_variable>  // NOLINT (unapproved c++11 header)
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>  // NOLINT (unapproved c++11 header)

#include "src/buffer_pool.h"
#include "src/decoder_state.h"
#include "src/dsp/common.h"
#include "src/dsp/constants.h"
#include "src/dsp/dsp.h"
#include "src/frame_scratch_buffer.h"
#include "src/loop_restoration_info.h"
#include "src/obu_parser.h"
#include "src/post_filter.h"
#include "src/quantizer.h"
#include "src/residual_buffer_pool.h"
#include "src/symbol_decoder_context.h"
#include "src/tile_scratch_buffer.h"
#include "src/utils/array_2d.h"
#include "src/utils/block_parameters_holder.h"
#include "src/utils/blocking_counter.h"
#include "src/utils/common.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/constants.h"
#include "src/utils/entropy_decoder.h"
#include "src/utils/memory.h"
#include "src/utils/segmentation_map.h"
#include "src/utils/threadpool.h"
#include "src/utils/types.h"
#include "src/yuv_buffer.h"

namespace libgav1 {

// Indicates what the ProcessSuperBlock() and TransformBlock() functions should
// do. "Parse" refers to consuming the bitstream, reading the transform
// coefficients and performing the dequantization. "Decode" refers to computing
// the prediction, applying the inverse transforms and adding the residual.
enum ProcessingMode {
  kProcessingModeParseOnly,
  kProcessingModeDecodeOnly,
  kProcessingModeParseAndDecode,
};

// The alignment requirement is due to the SymbolDecoderContext member
// symbol_decoder_context_.
class Tile : public MaxAlignedAllocable {
 public:
  static std::unique_ptr<Tile> Create(
      int tile_number, const uint8_t* const data, size_t size,
      const ObuSequenceHeader& sequence_header,
      const ObuFrameHeader& frame_header, RefCountedBuffer* const current_frame,
      const DecoderState& state, FrameScratchBuffer* const frame_scratch_buffer,
      const WedgeMaskArray& wedge_masks,
      const QuantizerMatrix& quantizer_matrix,
      SymbolDecoderContext* const saved_symbol_decoder_context,
      const SegmentationMap* prev_segment_ids, PostFilter* const post_filter,
      const dsp::Dsp* const dsp, ThreadPool* const thread_pool,
      BlockingCounterWithStatus* const pending_tiles, bool frame_parallel,
      bool use_intra_prediction_buffer, bool parse_only) {
    std::unique_ptr<Tile> tile(new (std::nothrow) Tile(
        tile_number, data, size, sequence_header, frame_header, current_frame,
        state, frame_scratch_buffer, wedge_masks, quantizer_matrix,
        saved_symbol_decoder_context, prev_segment_ids, post_filter, dsp,
        thread_pool, pending_tiles, frame_parallel, use_intra_prediction_buffer,
        parse_only));
    return (tile != nullptr && tile->Init()) ? std::move(tile) : nullptr;
  }

  // Move only.
  Tile(Tile&& tile) noexcept;
  Tile& operator=(Tile&& tile) noexcept;
  Tile(const Tile&) = delete;
  Tile& operator=(const Tile&) = delete;

  struct Block;  // Defined after this class.

  // Parses the entire tile.
  bool Parse();
  // Decodes the entire tile. |superblock_row_progress| and
  // |superblock_row_progress_condvar| are arrays of size equal to the number of
  // superblock rows in the frame. Increments |superblock_row_progress[i]| after
  // each superblock row at index |i| is decoded. If the count reaches the
  // number of tile columns, then it notifies
  // |superblock_row_progress_condvar[i]|.
  bool Decode(std::mutex* mutex, int* superblock_row_progress,
              std::condition_variable* superblock_row_progress_condvar);
  // Parses and decodes the entire tile. Depending on the configuration of this
  // Tile, this function may do multithreaded decoding.
  bool ParseAndDecode();  // 5.11.2.
  // Processes all the columns of the superblock row at |row4x4| that are within
  // this Tile. If |save_symbol_decoder_context| is true, then
  // SaveSymbolDecoderContext() is invoked for the last superblock row.
  template <ProcessingMode processing_mode, bool save_symbol_decoder_context>
  bool ProcessSuperBlockRow(int row4x4, TileScratchBuffer* scratch_buffer);

  const ObuSequenceHeader& sequence_header() const { return sequence_header_; }
  const ObuFrameHeader& frame_header() const { return frame_header_; }
  const RefCountedBuffer& current_frame() const { return current_frame_; }
  const TemporalMotionField& motion_field() const { return motion_field_; }
  const std::array<bool, kNumReferenceFrameTypes>& reference_frame_sign_bias()
      const {
    return reference_frame_sign_bias_;
  }

  bool IsRow4x4Inside(int row4x4) const {
    return row4x4 >= row4x4_start_ && row4x4 < row4x4_end_;
  }

  // 5.11.51.
  bool IsInside(int row4x4, int column4x4) const {
    return IsRow4x4Inside(row4x4) && column4x4 >= column4x4_start_ &&
           column4x4 < column4x4_end_;
  }

  bool IsLeftInside(int column4x4) const {
    // We use "larger than" as the condition. Don't pass in the left column
    // offset column4x4 - 1.
    assert(column4x4 <= column4x4_end_);
    return column4x4 > column4x4_start_;
  }

  bool IsTopInside(int row4x4) const {
    // We use "larger than" as the condition. Don't pass in the top row offset
    // row4x4 - 1.
    assert(row4x4 <= row4x4_end_);
    return row4x4 > row4x4_start_;
  }

  bool IsTopLeftInside(int row4x4, int column4x4) const {
    // We use "larger than" as the condition. Don't pass in the top row offset
    // row4x4 - 1 or the left column offset column4x4 - 1.
    assert(row4x4 <= row4x4_end_);
    assert(column4x4 <= column4x4_end_);
    return row4x4 > row4x4_start_ && column4x4 > column4x4_start_;
  }

  bool IsBottomRightInside(int row4x4, int column4x4) const {
    assert(row4x4 >= row4x4_start_);
    assert(column4x4 >= column4x4_start_);
    return row4x4 < row4x4_end_ && column4x4 < column4x4_end_;
  }

  BlockParameters** BlockParametersAddress(int row4x4, int column4x4) const {
    return block_parameters_holder_.Address(row4x4, column4x4);
  }

  int BlockParametersStride() const {
    return block_parameters_holder_.columns4x4();
  }

  // Returns true if Parameters() can be called with |row| and |column| as
  // inputs, false otherwise.
  bool HasParameters(int row, int column) const {
    return block_parameters_holder_.Find(row, column) != nullptr;
  }
  const BlockParameters& Parameters(int row, int column) const {
    return *block_parameters_holder_.Find(row, column);
  }

  int number() const { return number_; }
  int superblock_rows() const { return superblock_rows_; }
  int superblock_columns() const { return superblock_columns_; }
  int row4x4_start() const { return row4x4_start_; }
  int column4x4_start() const { return column4x4_start_; }
  int column4x4_end() const { return column4x4_end_; }

  int GetTileMeanQP() const {
    return static_cast<int>(
        std::round(static_cast<float>(weighted_cumulative_block_qp_) /
                   cumulative_block_weights_));
  }

 private:
  // Stores the transform tree state when reading variable size transform trees
  // and when applying the transform tree. When applying the transform tree,
  // |depth| is not used.
  struct TransformTreeNode {
    // The default constructor is invoked by the Stack<TransformTreeNode, n>
    // constructor. Stack<> does not use the default-constructed elements, so it
    // is safe for the default constructor to not initialize the members.
    TransformTreeNode() = default;
    TransformTreeNode(int x, int y, TransformSize tx_size, int depth = -1)
        : x(x), y(y), tx_size(tx_size), depth(depth) {}

    int x;
    int y;
    TransformSize tx_size;
    int depth;
  };

  // Enum to track the processing state of a superblock.
  enum SuperBlockState : uint8_t {
    kSuperBlockStateNone,       // Not yet parsed or decoded.
    kSuperBlockStateParsed,     // Parsed but not yet decoded.
    kSuperBlockStateScheduled,  // Scheduled for decoding.
    kSuperBlockStateDecoded     // Parsed and decoded.
  };

  // Parameters used to facilitate multi-threading within the Tile.
  struct ThreadingParameters {
    std::mutex mutex;
    // 2d array of size |superblock_rows_| by |superblock_columns_| containing
    // the processing state of each superblock.
    Array2D<SuperBlockState> sb_state LIBGAV1_GUARDED_BY(mutex);
    // Variable used to indicate either parse or decode failure.
    bool abort LIBGAV1_GUARDED_BY(mutex) = false;
    int pending_jobs LIBGAV1_GUARDED_BY(mutex) = 0;
    std::condition_variable pending_jobs_zero_condvar;
  };

  // The residual pointer is used to traverse the |residual_buffer_|. It is
  // used in two different ways.
  // If |split_parse_and_decode_| is true:
  //    The pointer points to the beginning of the |residual_buffer_| when the
  //    "parse" and "decode" steps begin. It is then moved forward tx_size in
  //    each iteration of the "parse" and the "decode" steps. In this case, the
  //    ResidualPtr variable passed into various functions starting from
  //    ProcessSuperBlock is used as an in/out parameter to keep track of the
  //    residual pointer.
  // If |split_parse_and_decode_| is false:
  //    The pointer is reset to the beginning of the |residual_buffer_| for
  //    every transform block.
  using ResidualPtr = uint8_t*;

  Tile(int tile_number, const uint8_t* data, size_t size,
       const ObuSequenceHeader& sequence_header,
       const ObuFrameHeader& frame_header, RefCountedBuffer* current_frame,
       const DecoderState& state, FrameScratchBuffer* frame_scratch_buffer,
       const WedgeMaskArray& wedge_masks,
       const QuantizerMatrix& quantizer_matrix,
       SymbolDecoderContext* saved_symbol_decoder_context,
       const SegmentationMap* prev_segment_ids, PostFilter* post_filter,
       const dsp::Dsp* dsp, ThreadPool* thread_pool,
       BlockingCounterWithStatus* pending_tiles, bool frame_parallel,
       bool use_intra_prediction_buffer, bool parse_only);

  // Performs member initializations that may fail. Helper function used by
  // Create().
  LIBGAV1_MUST_USE_RESULT bool Init();

  // Saves the symbol decoder context of this tile into
  // |saved_symbol_decoder_context_| if necessary.
  void SaveSymbolDecoderContext();

  // Entry point for multi-threaded decoding. This function performs the same
  // functionality as ParseAndDecode(). The current thread does the "parse" step
  // while the worker threads do the "decode" step.
  bool ThreadedParseAndDecode();

  // Returns whether or not the prerequisites for decoding the superblock at
  // |row_index| and |column_index| are satisfied. |threading_.mutex| must be
  // held when calling this function.
  bool CanDecode(int row_index, int column_index) const;

  // This function is run by the worker threads when multi-threaded decoding is
  // enabled. Once a superblock is decoded, this function will set the
  // corresponding |threading_.sb_state| entry to kSuperBlockStateDecoded. On
  // failure, |threading_.abort| will be set to true. If at any point
  // |threading_.abort| becomes true, this function will return as early as it
  // can. If the decoding succeeds, this function will also schedule the
  // decoding jobs for the superblock to the bottom-left and the superblock to
  // the right of this superblock (if it is allowed).
  void DecodeSuperBlock(int row_index, int column_index, int block_width4x4);

  // If |use_intra_prediction_buffer_| is true, then this function copies the
  // last row of the superblockrow starting at |row4x4| into the
  // |intra_prediction_buffer_| (which may be used by the intra prediction
  // process for the next superblock row).
  void PopulateIntraPredictionBuffer(int row4x4);

  uint16_t* GetPartitionCdf(int row4x4, int column4x4, BlockSize block_size);
  bool ReadPartition(int row4x4, int column4x4, BlockSize block_size,
                     bool has_rows, bool has_columns, Partition* partition);
  // Processes the Partition starting at |row4x4_start|, |column4x4_start|
  // iteratively. It performs a DFS traversal over the partition tree to process
  // the blocks in the right order.
  bool ProcessPartition(
      int row4x4_start, int column4x4_start, TileScratchBuffer* scratch_buffer,
      ResidualPtr* residual);  // Iterative implementation of 5.11.4.
  bool ProcessBlock(int row4x4, int column4x4, BlockSize block_size,
                    TileScratchBuffer* scratch_buffer,
                    ResidualPtr* residual);   // 5.11.5.
  void ResetCdef(int row4x4, int column4x4);  // 5.11.55.

  // This function is used to decode a superblock when the parsing has already
  // been done for that superblock.
  bool DecodeSuperBlock(int sb_row_index, int sb_column_index,
                        TileScratchBuffer* scratch_buffer);
  // Helper function used by DecodeSuperBlock(). Note that the decode_block()
  // function in the spec is equivalent to ProcessBlock() in the code.
  bool DecodeBlock(int row4x4, int column4x4, BlockSize block_size,
                   TileScratchBuffer* scratch_buffer, ResidualPtr* residual);

  void ClearBlockDecoded(TileScratchBuffer* scratch_buffer, int row4x4,
                         int column4x4);  // 5.11.3.
  bool ProcessSuperBlock(int row4x4, int column4x4,
                         TileScratchBuffer* scratch_buffer,
                         ProcessingMode mode);
  void ResetLoopRestorationParams();
  void ReadLoopRestorationCoefficients(int row4x4, int column4x4,
                                       BlockSize block_size);  // 5.11.57.

  // Helper functions for DecodeBlock.
  bool ReadSegmentId(const Block& block);       // 5.11.9.
  bool ReadIntraSegmentId(const Block& block);  // 5.11.8.
  void ReadSkip(const Block& block);            // 5.11.11.
  bool ReadSkipMode(const Block& block);        // 5.11.10.
  void ReadCdef(const Block& block);            // 5.11.56.
  // Returns the new value. |cdf| is an array of size kDeltaSymbolCount + 1.
  int ReadAndClipDelta(uint16_t* cdf, int delta_small, int scale, int min_value,
                       int max_value, int value);
  void ReadQuantizerIndexDelta(const Block& block);  // 5.11.12.
  void ReadLoopFilterDelta(const Block& block);      // 5.11.13.
  // Populates |BlockParameters::deblock_filter_level| for the given |block|
  // using |deblock_filter_levels_|.
  void PopulateDeblockFilterLevel(const Block& block);
  void PopulateCdefSkip(const Block& block);
  void ReadPredictionModeY(const Block& block, bool intra_y_mode);
  void ReadIntraAngleInfo(const Block& block,
                          PlaneType plane_type);  // 5.11.42 and 5.11.43.
  void ReadPredictionModeUV(const Block& block);
  void ReadCflAlpha(const Block& block);  // 5.11.45.
  int GetPaletteCache(const Block& block, PlaneType plane_type,
                      uint16_t* cache);
  void ReadPaletteColors(const Block& block, Plane plane);
  void ReadPaletteModeInfo(const Block& block);      // 5.11.46.
  void ReadFilterIntraModeInfo(const Block& block);  // 5.11.24.
  int ReadMotionVectorComponent(const Block& block,
                                int component);                // 5.11.32.
  void ReadMotionVector(const Block& block, int index);        // 5.11.31.
  bool DecodeIntraModeInfo(const Block& block);                // 5.11.7.
  int8_t ComputePredictedSegmentId(const Block& block) const;  // 5.11.21.
  bool ReadInterSegmentId(const Block& block, bool pre_skip);  // 5.11.19.
  void ReadIsInter(const Block& block, bool skip_mode);        // 5.11.20.
  bool ReadIntraBlockModeInfo(const Block& block,
                              bool intra_y_mode);  // 5.11.22.
  CompoundReferenceType ReadCompoundReferenceType(const Block& block);
  template <bool is_single, bool is_backward, int index>
  uint16_t* GetReferenceCdf(const Block& block, CompoundReferenceType type =
                                                    kNumCompoundReferenceTypes);
  void ReadReferenceFrames(const Block& block, bool skip_mode);  // 5.11.25.
  void ReadInterPredictionModeY(const Block& block,
                                const MvContexts& mode_contexts,
                                bool skip_mode);
  void ReadRefMvIndex(const Block& block);
  void ReadInterIntraMode(const Block& block, bool is_compound,
                          bool skip_mode);        // 5.11.28.
  bool IsScaled(ReferenceFrameType type) const {  // Part of 5.11.27.
    const int index =
        frame_header_.reference_frame_index[type - kReferenceFrameLast];
    return reference_frames_[index]->upscaled_width() != frame_header_.width ||
           reference_frames_[index]->frame_height() != frame_header_.height;
  }
  void ReadMotionMode(const Block& block, bool is_compound,
                      bool skip_mode);  // 5.11.27.
  uint16_t* GetIsExplicitCompoundTypeCdf(const Block& block);
  uint16_t* GetIsCompoundTypeAverageCdf(const Block& block);
  void ReadCompoundType(const Block& block, bool is_compound, bool skip_mode,
                        bool* is_explicit_compound_type,
                        bool* is_compound_type_average);  // 5.11.29.
  uint16_t* GetInterpolationFilterCdf(const Block& block, int direction);
  void ReadInterpolationFilter(const Block& block, bool skip_mode);
  bool ReadInterBlockModeInfo(const Block& block, bool skip_mode);  // 5.11.23.
  bool DecodeInterModeInfo(const Block& block);                     // 5.11.18.
  bool DecodeModeInfo(const Block& block);                          // 5.11.6.
  bool IsMvValid(const Block& block, bool is_compound) const;       // 6.10.25.
  bool AssignInterMv(const Block& block, bool is_compound);         // 5.11.26.
  bool AssignIntraMv(const Block& block);                           // 5.11.26.
  int GetTopTransformWidth(const Block& block, int row4x4, int column4x4,
                           bool ignore_skip);
  int GetLeftTransformHeight(const Block& block, int row4x4, int column4x4,
                             bool ignore_skip);
  TransformSize ReadFixedTransformSize(const Block& block);  // 5.11.15.
  // Iterative implementation of 5.11.17.
  void ReadVariableTransformTree(const Block& block, int row4x4, int column4x4,
                                 TransformSize tx_size);
  void DecodeTransformSize(const Block& block);  // 5.11.16.
  bool ComputePrediction(const Block& block);    // 5.11.33.
  // |x4| and |y4| are the column and row positions of the 4x4 block. |w4| and
  // |h4| are the width and height in 4x4 units of |tx_size|.
  int GetTransformAllZeroContext(const Block& block, Plane plane,
                                 TransformSize tx_size, int x4, int y4, int w4,
                                 int h4);
  TransformSet GetTransformSet(TransformSize tx_size,
                               bool is_inter) const;  // 5.11.48.
  TransformType ComputeTransformType(const Block& block, Plane plane,
                                     TransformSize tx_size, int block_x,
                                     int block_y);  // 5.11.40.
  void ReadTransformType(const Block& block, int x4, int y4,
                         TransformSize tx_size);  // 5.11.47.
  template <typename ResidualType>
  void ReadCoeffBase2D(
      const uint16_t* scan, TransformSize tx_size, int adjusted_tx_width_log2,
      int eob,
      uint16_t coeff_base_cdf[kCoeffBaseContexts][kCoeffBaseSymbolCount + 1],
      uint16_t coeff_base_range_cdf[kCoeffBaseRangeContexts]
                                   [kCoeffBaseRangeSymbolCount + 1],
      ResidualType* quantized_buffer, uint8_t* level_buffer);
  template <typename ResidualType>
  void ReadCoeffBaseHorizontal(
      const uint16_t* scan, TransformSize tx_size, int adjusted_tx_width_log2,
      int eob,
      uint16_t coeff_base_cdf[kCoeffBaseContexts][kCoeffBaseSymbolCount + 1],
      uint16_t coeff_base_range_cdf[kCoeffBaseRangeContexts]
                                   [kCoeffBaseRangeSymbolCount + 1],
      ResidualType* quantized_buffer, uint8_t* level_buffer);
  template <typename ResidualType>
  void ReadCoeffBaseVertical(
      const uint16_t* scan, TransformSize tx_size, int adjusted_tx_width_log2,
      int eob,
      uint16_t coeff_base_cdf[kCoeffBaseContexts][kCoeffBaseSymbolCount + 1],
      uint16_t coeff_base_range_cdf[kCoeffBaseRangeContexts]
                                   [kCoeffBaseRangeSymbolCount + 1],
      ResidualType* quantized_buffer, uint8_t* level_buffer);
  int GetDcSignContext(int x4, int y4, int w4, int h4, Plane plane);
  void SetEntropyContexts(int x4, int y4, int w4, int h4, Plane plane,
                          uint8_t coefficient_level, int8_t dc_category);
  void InterIntraPrediction(
      uint16_t* prediction_0, const uint8_t* prediction_mask,
      ptrdiff_t prediction_mask_stride,
      const PredictionParameters& prediction_parameters, int prediction_width,
      int prediction_height, int subsampling_x, int subsampling_y,
      uint8_t* dest,
      ptrdiff_t dest_stride);  // Part of section 7.11.3.1 in the spec.
  void CompoundInterPrediction(
      const Block& block, const uint8_t* prediction_mask,
      ptrdiff_t prediction_mask_stride, int prediction_width,
      int prediction_height, int subsampling_x, int subsampling_y,
      int candidate_row, int candidate_column, uint8_t* dest,
      ptrdiff_t dest_stride);  // Part of section 7.11.3.1 in the spec.
  GlobalMotion* GetWarpParams(const Block& block, Plane plane,
                              int prediction_width, int prediction_height,
                              const PredictionParameters& prediction_parameters,
                              ReferenceFrameType reference_type,
                              bool* is_local_valid,
                              GlobalMotion* global_motion_params,
                              GlobalMotion* local_warp_params)
      const;  // Part of section 7.11.3.1 in the spec.
  bool InterPrediction(const Block& block, Plane plane, int x, int y,
                       int prediction_width, int prediction_height,
                       int candidate_row, int candidate_column,
                       bool* is_local_valid,
                       GlobalMotion* local_warp_params);  // 7.11.3.1.
  void ScaleMotionVector(const MotionVector& mv, Plane plane,
                         int reference_frame_index, int x, int y, int* start_x,
                         int* start_y, int* step_x, int* step_y);  // 7.11.3.3.
  // If the method returns false, the caller only uses the output parameters
  // *ref_block_start_x and *ref_block_start_y. If the method returns true, the
  // caller uses all four output parameters.
  static bool GetReferenceBlockPosition(
      int reference_frame_index, bool is_scaled, int width, int height,
      int ref_start_x, int ref_last_x, int ref_start_y, int ref_last_y,
      int start_x, int start_y, int step_x, int step_y, int left_border,
      int right_border, int top_border, int bottom_border,
      int* ref_block_start_x, int* ref_block_start_y, int* ref_block_end_x,
      int* ref_block_end_y);

  template <typename Pixel>
  void BuildConvolveBlock(Plane plane, int reference_frame_index,
                          bool is_scaled, int height, int ref_start_x,
                          int ref_last_x, int ref_start_y, int ref_last_y,
                          int step_y, int ref_block_start_x,
                          int ref_block_end_x, int ref_block_start_y,
                          uint8_t* block_buffer,
                          ptrdiff_t convolve_buffer_stride,
                          ptrdiff_t block_extended_width);
  bool BlockInterPrediction(const Block& block, Plane plane,
                            int reference_frame_index, const MotionVector& mv,
                            int x, int y, int width, int height,
                            int candidate_row, int candidate_column,
                            uint16_t* prediction, bool is_compound,
                            bool is_inter_intra, uint8_t* dest,
                            ptrdiff_t dest_stride);  // 7.11.3.4.
  bool BlockWarpProcess(const Block& block, Plane plane, int index,
                        int block_start_x, int block_start_y, int width,
                        int height, GlobalMotion* warp_params, bool is_compound,
                        bool is_inter_intra, uint8_t* dest,
                        ptrdiff_t dest_stride);  // 7.11.3.5.
  bool ObmcBlockPrediction(const Block& block, const MotionVector& mv,
                           Plane plane, int reference_frame_index, int width,
                           int height, int x, int y, int candidate_row,
                           int candidate_column,
                           ObmcDirection blending_direction);
  bool ObmcPrediction(const Block& block, Plane plane, int width,
                      int height);  // 7.11.3.9.
  void DistanceWeightedPrediction(void* prediction_0, void* prediction_1,
                                  int width, int height, int candidate_row,
                                  int candidate_column, uint8_t* dest,
                                  ptrdiff_t dest_stride);  // 7.11.3.15.
  // This function specializes the parsing of DC coefficient by removing some of
  // the branches when i == 0 (since scan[0] is always 0 and scan[i] is always
  // non-zero for all other possible values of i). |dc_category| is an output
  // parameter that is populated when |is_dc_coefficient| is true.
  // |coefficient_level| is an output parameter which accumulates the
  // coefficient level.
  template <typename ResidualType, bool is_dc_coefficient>
  LIBGAV1_ALWAYS_INLINE bool ReadSignAndApplyDequantization(
      const uint16_t* scan, int i, int q_value, const uint8_t* quantizer_matrix,
      int shift, int max_value, uint16_t* dc_sign_cdf, int8_t* dc_category,
      int* coefficient_level,
      ResidualType* residual_buffer);     // Part of 5.11.39.
  int ReadCoeffBaseRange(uint16_t* cdf);  // Part of 5.11.39.
  // Returns the number of non-zero coefficients that were read. |tx_type| is an
  // output parameter that stores the computed transform type for the plane
  // whose coefficients were read. Returns -1 on failure.
  template <typename ResidualType>
  int ReadTransformCoefficients(const Block& block, Plane plane, int start_x,
                                int start_y, TransformSize tx_size,
                                TransformType* tx_type);  // 5.11.39.
  bool TransformBlock(const Block& block, Plane plane, int base_x, int base_y,
                      TransformSize tx_size, int x, int y,
                      ProcessingMode mode);  // 5.11.35.
  // Iterative implementation of 5.11.36.
  bool TransformTree(const Block& block, int start_x, int start_y,
                     BlockSize plane_size, ProcessingMode mode);
  void ReconstructBlock(const Block& block, Plane plane, int start_x,
                        int start_y, TransformSize tx_size,
                        TransformType tx_type,
                        int non_zero_coeff_count);         // Part of 7.12.3.
  bool Residual(const Block& block, ProcessingMode mode);  // 5.11.34.
  // part of 5.11.5 (reset_block_context() in the spec).
  void ResetEntropyContext(const Block& block);
  // Populates the |color_context| and |color_order| for the |i|th iteration
  // with entries counting down from |start| to |end| (|start| > |end|).
  void PopulatePaletteColorContexts(
      const Block& block, PlaneType plane_type, int i, int start, int end,
      uint8_t color_order[kMaxPaletteSquare][kMaxPaletteSize],
      uint8_t color_context[kMaxPaletteSquare]);  // 5.11.50.
  bool ReadPaletteTokens(const Block& block);     // 5.11.49.
  template <typename Pixel>
  void IntraPrediction(const Block& block, Plane plane, int x, int y,
                       bool has_left, bool has_top, bool has_top_right,
                       bool has_bottom_left, PredictionMode mode,
                       TransformSize tx_size);
  int GetIntraEdgeFilterType(const Block& block,
                             Plane plane) const;  // 7.11.2.8.
  template <typename Pixel>
  void DirectionalPrediction(const Block& block, Plane plane, int x, int y,
                             bool has_left, bool has_top, bool needs_left,
                             bool needs_top, int prediction_angle, int width,
                             int height, int max_x, int max_y,
                             TransformSize tx_size, Pixel* top_row,
                             Pixel* left_column);  // 7.11.2.4.
  template <typename Pixel>
  void PalettePrediction(const Block& block, Plane plane, int start_x,
                         int start_y, int x, int y,
                         TransformSize tx_size);  // 7.11.4.
  template <typename Pixel>
  void ChromaFromLumaPrediction(const Block& block, Plane plane, int start_x,
                                int start_y,
                                TransformSize tx_size);  // 7.11.5.
  // Section 7.19. Applies some filtering and reordering to the motion vectors
  // for the given |block| and stores them into |current_frame_|.
  void StoreMotionFieldMvsIntoCurrentFrame(const Block& block);

  // SetCdfContext*() functions will populate the |left_context_| and
  // |top_context_| for the |block|.
  void SetCdfContextUsePredictedSegmentId(const Block& block,
                                          bool use_predicted_segment_id);
  void SetCdfContextCompoundType(const Block& block,
                                 bool is_explicit_compound_type,
                                 bool is_compound_type_average);
  void SetCdfContextSkipMode(const Block& block, bool skip_mode);
  void SetCdfContextPaletteSize(const Block& block);
  void SetCdfContextUVMode(const Block& block);

  // Returns the zero-based index of the super block that contains |row4x4|
  // relative to the start of this tile.
  int SuperBlockRowIndex(int row4x4) const {
    return (row4x4 - row4x4_start_) >>
           (sequence_header_.use_128x128_superblock ? 5 : 4);
  }

  // Returns the zero-based index of the super block that contains |column4x4|
  // relative to the start of this tile.
  int SuperBlockColumnIndex(int column4x4) const {
    return (column4x4 - column4x4_start_) >>
           (sequence_header_.use_128x128_superblock ? 5 : 4);
  }

  // Returns the zero-based index of the block that starts at row4x4 or
  // column4x4 relative to the start of the superblock that contains the block.
  // This is used to index into the members of |left_context_| and
  // |top_context_|.
  int CdfContextIndex(int row_or_column4x4) const {
    return row_or_column4x4 -
           (row_or_column4x4 &
            (sequence_header_.use_128x128_superblock ? ~31 : ~15));
  }

  BlockSize SuperBlockSize() const {
    return sequence_header_.use_128x128_superblock ? kBlock128x128
                                                   : kBlock64x64;
  }
  int PlaneCount() const {
    return sequence_header_.color_config.is_monochrome ? kMaxPlanesMonochrome
                                                       : kMaxPlanes;
  }

  const int number_;
  const int row_;
  const int column_;
  const uint8_t* const data_;
  size_t size_;
  int row4x4_start_;
  int row4x4_end_;
  int column4x4_start_;
  int column4x4_end_;
  int superblock_rows_;
  int superblock_columns_;
  bool read_deltas_;
  const int8_t subsampling_x_[kMaxPlanes];
  const int8_t subsampling_y_[kMaxPlanes];

  // The dimensions (in order) are: segment_id, level_index (based on plane and
  // direction), reference_frame and mode_id.
  uint8_t deblock_filter_levels_[kMaxSegments][kFrameLfCount]
                                [kNumReferenceFrameTypes][2];

  // current_quantizer_index_ is in the range [0, 255].
  uint8_t current_quantizer_index_;
  // The weighted sum of the QP values per block for a tile. The weights are in
  // terms of 4x4 blocks. E.g., a block of size 32x16 has the weight 32/4 *
  // 16/4.
  int64_t weighted_cumulative_block_qp_ = 0;
  // The sums of the weights per block in a tile.
  int64_t cumulative_block_weights_ = 0;

  // These two arrays (|coefficient_levels_| and |dc_categories_|) are used to
  // store the entropy context. Their dimensions are as follows: First -
  // left/top; Second - plane; Third - row4x4 (if first dimension is
  // left)/column4x4 (if first dimension is top).
  //
  // This is equivalent to the LeftLevelContext and AboveLevelContext arrays in
  // the spec. In the spec, it stores values from 0 through 63 (inclusive). The
  // stored values are used to compute the left and top contexts in
  // GetTransformAllZeroContext. In that function, we only care about the
  // following values: 0, 1, 2, 3 and >= 4. So instead of clamping to 63, we
  // clamp to 4 (i.e.) all the values greater than 4 are stored as 4.
  std::array<Array2D<uint8_t>, 2> coefficient_levels_;
  // This is equivalent to the LeftDcContext and AboveDcContext arrays in the
  // spec. In the spec, it can store 3 possible values: 0, 1 and 2 (where 1
  // means the value is < 0, 2 means the value is > 0 and 0 means the value is
  // equal to 0).
  //
  // The stored values are used in two places:
  //  * GetTransformAllZeroContext: Here, we only care about whether the
  //  value is 0 or not (whether it is 1 or 2 is irrelevant).
  //  * GetDcSignContext: Here, we do the following computation: if the
  //  stored value is 1, we decrement a counter. If the stored value is 2
  //  we increment a counter.
  //
  // Based on this usage, we can simply replace 1 with -1 and 2 with 1 and
  // use that value to compute the counter.
  //
  // The usage on GetTransformAllZeroContext is unaffected since there we
  // only care about whether it is 0 or not.
  std::array<Array2D<int8_t>, 2> dc_categories_;
  const ObuSequenceHeader& sequence_header_;
  const ObuFrameHeader& frame_header_;
  const std::array<bool, kNumReferenceFrameTypes>& reference_frame_sign_bias_;
  const std::array<RefCountedBufferPtr, kNumReferenceFrameTypes>&
      reference_frames_;
  TemporalMotionField& motion_field_;
  const std::array<uint8_t, kNumReferenceFrameTypes>& reference_order_hint_;
  const WedgeMaskArray& wedge_masks_;
  const QuantizerMatrix& quantizer_matrix_;
  EntropyDecoder reader_;
  SymbolDecoderContext symbol_decoder_context_;
  SymbolDecoderContext* const saved_symbol_decoder_context_;
  const SegmentationMap* prev_segment_ids_;
  const dsp::Dsp& dsp_;
  PostFilter& post_filter_;
  BlockParametersHolder& block_parameters_holder_;
  Quantizer quantizer_;
  // When there is no multi-threading within the Tile, |residual_buffer_| is
  // used. When there is multi-threading within the Tile,
  // |residual_buffer_threaded_| is used. In the following comment,
  // |residual_buffer| refers to either |residual_buffer_| or
  // |residual_buffer_threaded_| depending on whether multi-threading is enabled
  // within the Tile or not.
  // The |residual_buffer| is used to help with the dequantization and the
  // inverse transform processes. It is declared as a uint8_t, but is always
  // accessed either as an int16_t or int32_t depending on |bitdepth|. Here is
  // what it stores at various stages of the decoding process (in the order
  // which they happen):
  //   1) In ReadTransformCoefficients(), this buffer is used to store the
  //   dequantized values.
  //   2) In Reconstruct(), this buffer is used as the input to the row
  //   transform process.
  // The size of this buffer would be:
  //    For |residual_buffer_|: (4096 + 32 * |kResidualPaddingVertical|) *
  //        |residual_size_|. Where 4096 = 64x64 which is the maximum transform
  //        size, and 32 * |kResidualPaddingVertical| is the padding to avoid
  //        bottom boundary checks when parsing quantized coefficients. This
  //        memory is allocated and owned by the Tile class.
  //    For |residual_buffer_threaded_|: See the comment below. This memory is
  //        not allocated or owned by the Tile class.
  AlignedUniquePtr<uint8_t> residual_buffer_;
  // This is a 2d array of pointers of size |superblock_rows_| by
  // |superblock_columns_| where each pointer points to a ResidualBuffer for a
  // single super block. The array is populated when the parsing process begins
  // by calling |residual_buffer_pool_->Get()| and the memory is released back
  // to the pool by calling |residual_buffer_pool_->Release()| when the decoding
  // process is complete.
  Array2D<std::unique_ptr<ResidualBuffer>> residual_buffer_threaded_;
  // sizeof(int16_t or int32_t) depending on |bitdepth|.
  const size_t residual_size_;
  // Number of superblocks on the top-right that will have to be decoded before
  // the current superblock can be decoded. This will be 1 if allow_intrabc is
  // false. If allow_intrabc is true, then this value will be
  // use_128x128_superblock ? 3 : 5. This is the allowed range of reference for
  // the top rows for intrabc.
  const int intra_block_copy_lag_;

  // In the Tile class, we use the "current_frame" in two ways:
  //   1) To write the decoded output into (using the |buffer_| view).
  //   2) To read the pixels for intra block copy (using the |current_frame_|
  //      reference).
  //
  // When intra block copy is off, |buffer_| and |current_frame_| may or may not
  // point to the same plane pointers. But it is okay since |current_frame_| is
  // never used in this case.
  //
  // When intra block copy is on, |buffer_| and |current_frame_| always point to
  // the same plane pointers (since post filtering is disabled). So the usage in
  // both case 1 and case 2 remain valid.
  Array2DView<uint8_t> buffer_[kMaxPlanes];
  RefCountedBuffer& current_frame_;

  Array2D<int8_t>& cdef_index_;
  Array2D<uint8_t>& cdef_skip_;
  Array2D<TransformSize>& inter_transform_sizes_;
  std::array<RestorationUnitInfo, kMaxPlanes> reference_unit_info_;
  // If |thread_pool_| is nullptr, the calling thread will do the parsing and
  // the decoding in one pass. If |thread_pool_| is not nullptr, then the main
  // thread will do the parsing while the thread pool workers will do the
  // decoding.
  ThreadPool* const thread_pool_;
  ThreadingParameters threading_;
  ResidualBufferPool* const residual_buffer_pool_;
  TileScratchBufferPool* const tile_scratch_buffer_pool_;
  BlockingCounterWithStatus* const pending_tiles_;
  bool split_parse_and_decode_;
  // This is used only when |split_parse_and_decode_| is false.
  std::unique_ptr<PredictionParameters> prediction_parameters_ = nullptr;
  // Stores the |transform_type| for the super block being decoded at a 4x4
  // granularity. The spec uses absolute indices for this array but it is
  // sufficient to use indices relative to the super block being decoded.
  TransformType transform_types_[32][32];
  // delta_lf_[i] is in the range [-63, 63].
  int8_t delta_lf_[kFrameLfCount];
  // True if all the values in |delta_lf_| are zero. False otherwise.
  bool delta_lf_all_zero_;
  const bool frame_parallel_;
  const bool use_intra_prediction_buffer_;
  // Buffer used to store the unfiltered pixels that are necessary for decoding
  // the next superblock row (for the intra prediction process). Used only if
  // |use_intra_prediction_buffer_| is true. The |frame_scratch_buffer| contains
  // one row buffer for each tile row. This tile will have to use the buffer
  // corresponding to this tile's row.
  IntraPredictionBuffer* const intra_prediction_buffer_;
  // Stores the progress of the reference frames. This will be used to avoid
  // unnecessary calls into RefCountedBuffer::WaitUntil().
  std::array<int, kNumReferenceFrameTypes> reference_frame_progress_cache_;
  // Stores the CDF contexts necessary for the "left" block.
  BlockCdfContext left_context_;
  // Stores the CDF contexts necessary for the "top" block. The size of this
  // buffer is the number of superblock columns in this tile. For each block,
  // the access index will be the corresponding SuperBlockColumnIndex()'th
  // entry.
  DynamicBuffer<BlockCdfContext> top_context_;
  // Whether the tile should only be parsed and not decoded.
  const bool parse_only_;
};

struct Tile::Block {
  Block(Tile* tile_ptr, BlockSize size, int row4x4, int column4x4,
        TileScratchBuffer* const scratch_buffer, ResidualPtr* residual)
      : tile(*tile_ptr),
        size(size),
        row4x4(row4x4),
        column4x4(column4x4),
        width(kBlockWidthPixels[size]),
        height(kBlockHeightPixels[size]),
        width4x4(width >> 2),
        height4x4(height >> 2),
        scratch_buffer(scratch_buffer),
        residual(residual),
        top_context(tile.top_context_.get() +
                    tile.SuperBlockColumnIndex(column4x4)),
        top_context_index(tile.CdfContextIndex(column4x4)),
        left_context_index(tile.CdfContextIndex(row4x4)) {
    assert(size != kBlockInvalid);
    residual_size[kPlaneY] = kPlaneResidualSize[size][0][0];
    residual_size[kPlaneU] = residual_size[kPlaneV] =
        kPlaneResidualSize[size][tile.subsampling_x_[kPlaneU]]
                          [tile.subsampling_y_[kPlaneU]];
    assert(residual_size[kPlaneY] != kBlockInvalid);
    if (tile.PlaneCount() > 1) {
      assert(residual_size[kPlaneU] != kBlockInvalid);
    }
    if ((row4x4 & 1) == 0 &&
        (tile.sequence_header_.color_config.subsampling_y & height4x4) == 1) {
      has_chroma = false;
    } else if ((column4x4 & 1) == 0 &&
               (tile.sequence_header_.color_config.subsampling_x & width4x4) ==
                   1) {
      has_chroma = false;
    } else {
      has_chroma = !tile.sequence_header_.color_config.is_monochrome;
    }
    top_available[kPlaneY] = tile.IsTopInside(row4x4);
    left_available[kPlaneY] = tile.IsLeftInside(column4x4);
    if (has_chroma) {
      // top_available[kPlaneU] and top_available[kPlaneV] are valid only if
      // has_chroma is true.
      // The next 3 lines are equivalent to:
      // top_available[kPlaneU] = top_available[kPlaneV] =
      //     top_available[kPlaneY] &&
      //     ((tile.sequence_header_.color_config.subsampling_y & height4x4) ==
      //     0 || tile.IsTopInside(row4x4 - 1));
      top_available[kPlaneU] = top_available[kPlaneV] = tile.IsTopInside(
          row4x4 -
          (tile.sequence_header_.color_config.subsampling_y & height4x4));
      // left_available[kPlaneU] and left_available[kPlaneV] are valid only if
      // has_chroma is true.
      // The next 3 lines are equivalent to:
      // left_available[kPlaneU] = left_available[kPlaneV] =
      //     left_available[kPlaneY] &&
      //     ((tile.sequence_header_.color_config.subsampling_x & width4x4) == 0
      //      || tile.IsLeftInside(column4x4 - 1));
      left_available[kPlaneU] = left_available[kPlaneV] = tile.IsLeftInside(
          column4x4 -
          (tile.sequence_header_.color_config.subsampling_x & width4x4));
    }
    const ptrdiff_t stride = tile.BlockParametersStride();
    BlockParameters** const bps =
        tile.BlockParametersAddress(row4x4, column4x4);
    bp = *bps;
    // bp_top is valid only if top_available[kPlaneY] is true.
    if (top_available[kPlaneY]) {
      bp_top = *(bps - stride);
    }
    // bp_left is valid only if left_available[kPlaneY] is true.
    if (left_available[kPlaneY]) {
      bp_left = *(bps - 1);
    }
  }

  bool HasChroma() const { return has_chroma; }

  // These return values of these group of functions are valid only if the
  // corresponding top_available or left_available is true.
  ReferenceFrameType TopReference(int index) const {
    return bp_top->reference_frame[index];
  }

  ReferenceFrameType LeftReference(int index) const {
    return bp_left->reference_frame[index];
  }

  bool IsTopIntra() const { return TopReference(0) <= kReferenceFrameIntra; }
  bool IsLeftIntra() const { return LeftReference(0) <= kReferenceFrameIntra; }

  bool IsTopSingle() const { return TopReference(1) <= kReferenceFrameIntra; }
  bool IsLeftSingle() const { return LeftReference(1) <= kReferenceFrameIntra; }

  int CountReferences(ReferenceFrameType type) const {
    return static_cast<int>(top_available[kPlaneY] &&
                            bp_top->reference_frame[0] == type) +
           static_cast<int>(top_available[kPlaneY] &&
                            bp_top->reference_frame[1] == type) +
           static_cast<int>(left_available[kPlaneY] &&
                            bp_left->reference_frame[0] == type) +
           static_cast<int>(left_available[kPlaneY] &&
                            bp_left->reference_frame[1] == type);
  }

  // 7.10.3.
  // Checks if there are any inter blocks to the left or above. If so, it
  // returns true indicating that the block has neighbors that are suitable for
  // use by overlapped motion compensation.
  bool HasOverlappableCandidates() const {
    const ptrdiff_t stride = tile.BlockParametersStride();
    BlockParameters** const bps = tile.BlockParametersAddress(0, 0);
    if (top_available[kPlaneY]) {
      BlockParameters** bps_top = bps + (row4x4 - 1) * stride + (column4x4 | 1);
      const int columns = std::min(tile.frame_header_.columns4x4 - column4x4,
                                   static_cast<int>(width4x4));
      BlockParameters** const bps_top_end = bps_top + columns;
      do {
        if ((*bps_top)->reference_frame[0] > kReferenceFrameIntra) {
          return true;
        }
        bps_top += 2;
      } while (bps_top < bps_top_end);
    }
    if (left_available[kPlaneY]) {
      BlockParameters** bps_left = bps + (row4x4 | 1) * stride + column4x4 - 1;
      const int rows = std::min(tile.frame_header_.rows4x4 - row4x4,
                                static_cast<int>(height4x4));
      BlockParameters** const bps_left_end = bps_left + rows * stride;
      do {
        if ((*bps_left)->reference_frame[0] > kReferenceFrameIntra) {
          return true;
        }
        bps_left += 2 * stride;
      } while (bps_left < bps_left_end);
    }
    return false;
  }

  Tile& tile;
  bool has_chroma;
  const BlockSize size;
  bool top_available[kMaxPlanes];
  bool left_available[kMaxPlanes];
  BlockSize residual_size[kMaxPlanes];
  const int row4x4;
  const int column4x4;
  const int width;
  const int height;
  const int width4x4;
  const int height4x4;
  const BlockParameters* bp_top;
  const BlockParameters* bp_left;
  BlockParameters* bp;
  TileScratchBuffer* const scratch_buffer;
  ResidualPtr* const residual;
  BlockCdfContext* const top_context;
  const int top_context_index;
  const int left_context_index;
};

extern template bool
Tile::ProcessSuperBlockRow<kProcessingModeDecodeOnly, false>(
    int row4x4, TileScratchBuffer* scratch_buffer);
extern template bool
Tile::ProcessSuperBlockRow<kProcessingModeParseAndDecode, true>(
    int row4x4, TileScratchBuffer* scratch_buffer);

}  // namespace libgav1

#endif  // LIBGAV1_SRC_TILE_H_
